"""Orquestra as etapas: transcrever -> selecionar -> renderizar -> revisar. Cada etapa usa cache."""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Callable

from . import analyze, cache, media, render, review, shots as shotlib, visual
from .config import Config
from .gemini import GenaiBackend, GeminiClient, GeminiError
from .ingest import apply_meta, resolve_input
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


def _setup_logging(ws: Path | None = None) -> None:
    """Console sempre; run.log na pasta do vídeo quando `ws` é conhecido."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        if getattr(h, "_darkcnn", False):
            root.removeHandler(h)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if ws is not None:
        ws.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(ws / "run.log", encoding="utf-8"))
    for h in handlers:
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
        h._darkcnn = True  # type: ignore[attr-defined]
        root.addHandler(h)
    for noisy in ("httpx", "httpcore", "huggingface_hub", "google_genai", "urllib3", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


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


SHOTS_VERSION = 1


def effective_cfg(cfg: Config, meta: dict) -> Config:
    """Procedência do link + padrões do perfil: no perfil visual o texto padrão é título+gancho (não há fala)."""
    cfg = apply_meta(cfg, meta)
    if cfg.profile == "visual" and "text_mode" not in cfg.model_fields_set:
        cfg = cfg.model_copy(update={"text_mode": "titled"})
    return cfg


def step_shots(video: Path, vid: str, ws: Path, cfg: Config, force: bool = False) -> list[dict]:
    """Planos (cortes de edição) + volume por plano, em cache."""
    path = ws / "shots.json"
    key = cache.key_of(v=SHOTS_VERSION, vid=vid, thr=cfg.scene_threshold, min=cfg.min_shot_s, max=cfg.max_shot_s)
    if not force:
        hit = cache.load(path, key)
        if hit is not None:
            log.info("planos em cache (%d)", len(hit))
            return hit
    duration = media.probe_duration(video)
    log.info("detectando cortes de cena (limiar %.2f)… pode levar alguns minutos", cfg.scene_threshold)
    sh = shotlib.build_shots(shotlib.detect_cuts(video, cfg.scene_threshold), duration,
                             cfg.min_shot_s, cfg.max_shot_s)
    if shotlib.has_audio(video):
        log.info("medindo o volume de cada plano…")
        shotlib.annotate_loudness(sh, shotlib.loudness_series(video))
    else:
        log.info("vídeo sem áudio: sem medição de volume")
    log.info("%d planos (duração mediana %.1fs)", len(sh), sorted(x["end"] - x["start"] for x in sh)[len(sh) // 2])
    cache.save(path, key, sh)
    return sh


def shots_report(input_arg: str | Path, cfg: Config, ydl_cls: Any = None) -> str:
    """Diagnóstico sem gastar o Gemini: quantos planos, tamanho típico e os mais barulhentos."""
    _setup_logging()
    ing = resolve_input(str(input_arg), cfg, ydl_cls)
    vid, ws, _ = _paths(ing.path, cfg)
    _setup_logging(ws)
    sh = step_shots(ing.path, vid, ws, cfg)
    lens = sorted(x["end"] - x["start"] for x in sh)
    lines = [f"{len(sh)} planos; duração mediana {lens[len(lens) // 2]:.1f}s, menor {lens[0]:.1f}s, maior {lens[-1]:.1f}s",
             f"limiar de cena {cfg.scene_threshold} (use --scene-threshold: menor = mais cortes, maior = menos)"]
    loud = sorted((x for x in sh if x.get("loud") is not None), key=lambda x: -x["loud"])[:5]
    if loud:
        lines.append("planos mais barulhentos: " + ", ".join(
            f"#{x['id']} em {analyze.fmt_ts(x['start'])} ({x['loud']:+.0f}dB)" for x in loud))
    return "\n".join(lines)


def run_pipeline(input_arg: str | Path, cfg: Config, *, transcriber: Transcriber = transcribe_words,
                 client_factory: Callable[[], GeminiClient] | None = None, force: bool = False,
                 ydl_cls: Any = None) -> Path:
    _setup_logging()
    ing = resolve_input(str(input_arg), cfg, ydl_cls)  # arquivo local ou download do link
    video, meta = ing.path, ing.meta
    cfg = effective_cfg(cfg, meta)
    vid, ws, out = _paths(video, cfg)
    _setup_logging(ws)
    info = media.video_info(video)
    log.info("vídeo %s (%.1f min) -> id %s", video.name, info["duration"] / 60, vid)
    log.info("config: perfil=%s layout=%s texto=%s modelo=%s cortes=%d (%.0f-%.0fs)", cfg.profile, cfg.layout,
             cfg.text_mode, cfg.gemini_model, cfg.clips_per_video, cfg.min_clip_s, cfg.max_clip_s)
    factory = client_factory or (lambda: make_client(cfg))

    if cfg.profile == "visual":  # sem fala: planos + vídeo reduzido para o Gemini
        words: list[dict] = []
        units = step_shots(video, vid, ws, cfg, force)
        cands, vstats = visual.select_visual(video, vid, ws, units, cfg, factory, force)
        log.info("Gemini: %d chamada(s) novas, %d ID(s) corrigido(s)", vstats["api_calls"], vstats["reconciled"])
    else:
        if info["duration"] / 60 > cfg.max_single_window_min:
            log.warning("vídeo > %.0f min: a análise em janelas só chega na Fase 3; a qualidade pode cair",
                        cfg.max_single_window_min)
        words = step_words(video, vid, ws, cfg, transcriber, force)
        units = split_sentences(words)
        log.info("%d frases", len(units))
        cands = step_select(ws, units, cfg, factory, force)

    clips, rejected = analyze.resolve(cands, units, cfg, info["duration"])
    log.info("%d cortes aceitos, %d descartados", len(clips), len(rejected))
    if not clips:
        log.warning("nenhum corte válido; veja rejected.json (ajuste min/max ou rode com --force)")
    out.mkdir(parents=True, exist_ok=True)
    step_render(video, vid, ws, out, clips, words, cfg)
    return review.write_review(out, video, cfg, clips, rejected, info, meta)


def render_from_selection(input_arg: str | Path, cfg: Config, ydl_cls: Any = None) -> Path:
    """Re-renderiza a partir do selection.json editado (início/fim/título/gancho), sem Whisper nem Gemini."""
    _setup_logging()
    ing = resolve_input(str(input_arg), cfg, ydl_cls)
    video, meta = ing.path, ing.meta
    cfg = effective_cfg(cfg, meta)
    vid, ws, out = _paths(video, cfg)
    _setup_logging(ws)
    sel = out / "selection.json"
    words = [] if cfg.profile == "visual" else cache.load(ws / "words.json", cache.key_of(v=WORDS_VERSION, vid=vid, model=cfg.whisper_model,
                       beam=cfg.whisper_beam, lang=cfg.language, compute=cfg.whisper_compute_type,
                       min_word=cfg.min_word_s))
    if not sel.exists() or words is None:
        raise FileNotFoundError("rode `run` antes: faltam selection.json ou a transcrição em cache")
    clips = json.loads(sel.read_text(encoding="utf-8"))
    for c in clips:
        c["duration"] = round(c["end"] - c["start"], 3)
    rejected = json.loads((out / "rejected.json").read_text(encoding="utf-8")) if (out / "rejected.json").exists() else []
    step_render(video, vid, ws, out, clips, words, cfg)
    return review.write_review(out, video, cfg, clips, rejected, media.video_info(video), meta)
