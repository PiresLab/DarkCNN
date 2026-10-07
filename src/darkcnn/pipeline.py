"""Orquestra as etapas: transcrever -> selecionar -> renderizar -> revisar. Cada etapa usa cache."""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Callable

from . import analyze, cache, media, render, review
from .config import Config
from .gemini import GenaiBackend, GeminiClient, GeminiError
from .transcribe import Transcriber, normalize_words, split_sentences, transcribe_words

log = logging.getLogger(__name__)

WORDS_VERSION = 1


def make_client(cfg: Config) -> GeminiClient:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise GeminiError("GEMINI_API_KEY não definida (crie o .env a partir do .env.example)")
    return GeminiClient(
        GenaiBackend(key, cfg.gemini_model),
        usage_path=cfg.workspace_dir / "usage.json",
        daily_budget=cfg.daily_request_budget,
        retries=cfg.gemini_retries,
        backoff_s=cfg.gemini_backoff_s,
    )


def _setup_logging(ws: Path) -> None:
    ws.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        if getattr(h, "_darkcnn", False):
            root.removeHandler(h)
    for h in (logging.StreamHandler(), logging.FileHandler(ws / "run.log", encoding="utf-8")):
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
        h._darkcnn = True  # type: ignore[attr-defined]
        root.addHandler(h)


def step_words(video: Path, vid: str, ws: Path, cfg: Config, transcriber: Transcriber, force: bool) -> list[dict]:
    path = ws / "words.json"
    key = cache.key_of(v=WORDS_VERSION, vid=vid, model=cfg.whisper_model, beam=cfg.whisper_beam,
                       lang=cfg.language, compute=cfg.whisper_compute_type, min_word=cfg.min_word_s)
    if not force:
        hit = cache.load(path, key)
        if hit is not None:
            log.info("transcrição em cache (%d palavras)", len(hit))
            return hit
    wav = ws / "audio.wav"
    log.info("extraindo áudio…")
    media.extract_wav(video, wav)
    log.info("transcrevendo (pode demorar: ~16 min por hora de vídeo no i7-10510U)…")
    words = normalize_words(transcriber(wav, cfg), cfg.min_word_s)
    if not words:
        raise RuntimeError("nenhuma palavra reconhecida (vídeo sem fala? para esse caso use o perfil visual, Fase 2)")
    cache.save(path, key, words)
    return words


def step_select(ws: Path, sentences: list[dict], cfg: Config, client_factory: Callable[[], GeminiClient],
                force: bool) -> list[dict]:
    path = ws / "analysis.json"
    key = cache.key_of(v=1, transcript=analyze.transcript_hash(sentences), model=cfg.gemini_model,
                       prompt=cfg.prompt_version, n=analyze.n_candidates(cfg), min=cfg.min_clip_s, max=cfg.max_clip_s)
    if not force:
        hit = cache.load(path, key)
        if hit is not None:
            log.info("análise em cache (%d candidatos), sem chamar o Gemini", len(hit))
            return hit
    log.info("pedindo ao Gemini (%s) %d candidatos…", cfg.gemini_model, analyze.n_candidates(cfg))
    sel = client_factory().generate_json(analyze.build_prompt(sentences, cfg), analyze.Selection)
    cands = [c.model_dump() for c in sel.candidates]
    cache.save(path, key, cands)
    return cands


def step_render(video: Path, vid: str, ws: Path, out: Path, clips: list[dict], words: list[dict],
                cfg: Config) -> None:
    keep: set[str] = set()
    for c in clips:
        name = f"{c['rank']:02d}_{media.slugify(c['title'])}.mp4"
        c["file"] = name
        keep.add(name)
        key = render.render_key(vid, c, words, cfg)
        log.info("corte %d/%d: %s (%.0fs)", c["rank"], len(clips), name, c["duration"])
        if not render.render_clip(video, c, words, cfg, out / name, ws / "render" / f"{c['rank']:02d}", key):
            log.info("  já renderizado (mesma configuração), pulando")
    for f in out.glob("*.mp4"):  # tira cortes de execuções anteriores que saíram da seleção
        if re.match(r"^\d\d_.*\.mp4$", f.name) and f.name not in keep:
            f.unlink()


def _paths(video: Path, cfg: Config) -> tuple[str, Path, Path]:
    vid = media.file_id(video)
    return vid, cfg.workspace_dir / vid, cfg.output_dir / vid


def run_pipeline(video: Path, cfg: Config, *, transcriber: Transcriber = transcribe_words,
                 client_factory: Callable[[], GeminiClient] | None = None, force: bool = False) -> Path:
    video = video.resolve()
    vid, ws, out = _paths(video, cfg)
    _setup_logging(ws)
    info = media.video_info(video)
    log.info("vídeo %s (%.1f min) -> id %s", video.name, info["duration"] / 60, vid)
    if info["duration"] / 60 > cfg.max_single_window_min:
        log.warning("vídeo > %.0f min: a análise em janelas só chega na Fase 3; a qualidade pode cair",
                    cfg.max_single_window_min)

    words = step_words(video, vid, ws, cfg, transcriber, force)
    sentences = split_sentences(words)
    log.info("%d frases", len(sentences))
    cands = step_select(ws, sentences, cfg, client_factory or (lambda: make_client(cfg)), force)

    clips, rejected = analyze.resolve(cands, sentences, cfg, info["duration"])
    log.info("%d cortes aceitos, %d descartados", len(clips), len(rejected))
    if not clips:
        log.warning("nenhum corte válido; veja rejected.json (ajuste min/max ou rode com --force)")
    out.mkdir(parents=True, exist_ok=True)
    step_render(video, vid, ws, out, clips, words, cfg)
    return review.write_review(out, video, cfg, clips, rejected, info)


def render_from_selection(video: Path, cfg: Config) -> Path:
    """Re-renderiza a partir do selection.json editado (início/fim/título/gancho), sem Whisper nem Gemini."""
    video = video.resolve()
    vid, ws, out = _paths(video, cfg)
    _setup_logging(ws)
    sel = out / "selection.json"
    words = cache.load(ws / "words.json", cache.key_of(v=WORDS_VERSION, vid=vid, model=cfg.whisper_model,
                       beam=cfg.whisper_beam, lang=cfg.language, compute=cfg.whisper_compute_type,
                       min_word=cfg.min_word_s))
    if not sel.exists() or words is None:
        raise FileNotFoundError("rode `run` antes: faltam selection.json ou a transcrição em cache")
    clips = json.loads(sel.read_text(encoding="utf-8"))
    for c in clips:
        c["duration"] = round(c["end"] - c["start"], 3)
    rejected = json.loads((out / "rejected.json").read_text(encoding="utf-8")) if (out / "rejected.json").exists() else []
    step_render(video, vid, ws, out, clips, words, cfg)
    return review.write_review(out, video, cfg, clips, rejected, media.video_info(video))
