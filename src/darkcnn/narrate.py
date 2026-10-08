"""Vídeos narrados sobre gameplay: roteiro (Gemini) -> voz (TTS) -> legenda alinhada -> render.

Diferente dos outros comandos, aqui não existe vídeo-fonte para analisar: o conteúdo nasce do roteiro e a
gameplay é só o fundo que segura o olho de quem assiste.
"""
from __future__ import annotations

import json
import logging
import random
from pathlib import Path
from typing import Any, Callable

from . import align, cache, media, render, review, script as scriptlib, tts
from .config import Config
from .gemini import GeminiClient
from .transcribe import Transcriber, Word, normalize_words, transcribe_words

log = logging.getLogger(__name__)

VIDEO_EXT = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".mpg", ".ts"}


# ---------------------------------------------------------------- gameplay de fundo
def list_gameplays(folder: Path) -> list[Path]:
    files = sorted(p for p in Path(folder).rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXT)
    if not files:
        raise FileNotFoundError(f"nenhum vídeo em {folder} (extensões aceitas: "
                                f"{', '.join(sorted(VIDEO_EXT))})")
    return files


def pick_gameplay(files: list[Path], needed_s: float, rng: random.Random) -> tuple[Path, float, bool]:
    """Sorteia um arquivo e um ponto de entrada. Se o vídeo for curto demais, ele é repetido."""
    game = rng.choice(files)
    dur = media.probe_duration(game)
    if dur >= needed_s + 0.5:
        return game, round(rng.uniform(0, dur - needed_s - 0.25), 3), False
    log.info("  a gameplay %s tem %.0fs e a narração %.0fs: o vídeo vai repetir", game.name, dur, needed_s)
    return game, 0.0, True


# ---------------------------------------------------------------- etapas
def run_id(cfg: Config) -> str:
    """Mesmo comando = mesma pasta (o cache vale entre execuções)."""
    h = cache.key_of(fmt=cfg.narrate_format, topic=cfg.topic, count=cfg.count, target=cfg.target_s,
                     model=cfg.gemini_model, prompt=cfg.script_prompt_version, thinking=cfg.thinking_level)
    return f"{media.slugify(cfg.narrate_format, 24)}-{h[:8]}"


def step_scripts(ws: Path, cfg: Config, client_factory: Callable[[], GeminiClient],
                 force: bool) -> list[scriptlib.Script]:
    path = ws / "scripts.json"
    key = cache.key_of(v=1, prompt=scriptlib.build_prompt(cfg), model=cfg.gemini_model,
                       thinking=cfg.thinking_level)
    if not force:
        hit = cache.load(path, key)
        if hit is not None:
            log.info("roteiros em cache (%d), sem chamar o Gemini", len(hit))
            return [scriptlib.Script(**s) for s in hit]
    log.info("pedindo %d roteiro(s) ao Gemini (%s, formato %s)…", cfg.count, cfg.gemini_model,
             cfg.narrate_format)
    batch = client_factory().generate_json(scriptlib.build_prompt(cfg), scriptlib.ScriptBatch,
                                           temperature=0.9)  # roteiro pede variedade
    scripts = [scriptlib.clean_script(s) for s in batch.scripts][: cfg.count]
    if not scripts:
        raise RuntimeError("o Gemini não devolveu nenhum roteiro")
    cache.save(path, key, [s.model_dump() for s in scripts])
    return scripts


def heard_per_line(track: Path, spokens: list[tts.Spoken], cfg: Config,
                   transcriber: Transcriber, tmp: Path) -> list[list[Word]]:
    """Transcreve a trilha UMA vez e reparte as palavras ouvidas por linha (tempo relativo à linha)."""
    wav = tmp / "track16k.wav"
    media.extract_wav(track, wav)
    heard = normalize_words(transcriber(wav, cfg), cfg.min_word_s)
    out = []
    for sp in spokens:
        inside = [w for w in heard if sp.start - 0.15 <= w["start"] < sp.end + 0.15]
        out.append([{**w, "start": max(0.0, w["start"] - sp.start), "end": max(0.0, w["end"] - sp.start)}
                    for w in inside])
    return out


def make_blocks(s: scriptlib.Script) -> list[list[int]]:
    """Índices das linhas de cada bloco. Uma chamada de TTS fala o bloco inteiro (o TTS tem limite de
    requisições); a contagem regressiva de uma pergunta "você prefere" fecha o bloco."""
    blocks: list[list[int]] = []
    cur: list[int] = []
    for i, ln in enumerate(s.lines):
        cur.append(i)
        if ln.kind == "escolha":
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    return blocks


def line_times(s: scriptlib.Script, blocks: list[list[int]], words: list[Word],
               spokens: list[tts.Spoken]) -> list[tuple[float, float]]:
    """Início/fim de cada linha, achados nas palavras já alinhadas (uma palavra por token do roteiro)."""
    out: list[tuple[float, float]] = [(0.0, 0.0)] * len(s.lines)
    k = 0
    for blk, sp in zip(blocks, spokens):
        for i in blk:
            n = len(s.lines[i].text.split())
            start = words[k]["start"] if n and k < len(words) else sp.start
            k += n
            end = words[k - 1]["end"] if n and 0 < k <= len(words) else start
            out[i] = (start, end)
        out[blk[-1]] = (out[blk[-1]][0], sp.end)  # a última fala do bloco vai até o fim do áudio
    return out


def build_one(s: scriptlib.Script, idx: int, cfg: Config, backend: tts.Backend, transcriber: Transcriber,
              ws: Path, out: Path, rng: random.Random) -> dict[str, Any]:
    """Um roteiro -> um vídeo. Devolve os metadados para o review."""
    work = ws / f"{idx:02d}"
    work.mkdir(parents=True, exist_ok=True)
    voice = tts.pick_voice(cfg, rng)
    blocks = make_blocks(s)
    texts = [" ".join(s.lines[i].text for i in blk) for blk in blocks]
    log.info("roteiro %d: %s (%d linhas, %d bloco(s) de voz, voz %s)", idx, s.title, len(s.lines),
             len(blocks), voice)

    wavs, made = tts.synthesize(texts, voice, cfg, backend, ws / "voice")
    log.info("  %d bloco(s) sintetizado(s), %d reaproveitado(s) do cache", made, len(texts) - made)
    pauses = [cfg.pause_s + (cfg.countdown_s if s.lines[blk[-1]].kind == "escolha" else 0.0)
              for blk in blocks]
    pauses[-1] = 0.0  # sem silêncio depois da última fala
    spokens = tts.place(wavs, pauses)
    track = work / "voice.wav"
    dur = tts.build_track(spokens, track)
    log.info("  narração: %.1fs (alvo %.0fs)", dur, cfg.target_s)
    if dur > cfg.target_s * 1.6:
        log.warning("  a narração ficou bem mais longa que o alvo; considere --target-s menor")

    heard = heard_per_line(track, spokens, cfg, transcriber, work)
    words = align.align_script([(t, sp.start, sp.end - sp.start) for t, sp in zip(texts, spokens)], heard)
    conf = min((align.matched_fraction(t, h) for t, h in zip(texts, heard)), default=1.0)
    if conf < 0.5:
        log.warning("  em algum trecho o Whisper reconheceu pouco do texto (%.0f%%): a legenda pode "
                    "sair fora de sincronia ali", conf * 100)

    times = line_times(s, blocks, words, spokens)
    choices = [{"a": ln.option_a, "b": ln.option_b, "start": t0, "end": t1, "countdown": cfg.countdown_s}
               for ln, (t0, t1) in zip(s.lines, times) if ln.kind == "escolha"]
    game, offset, loop = pick_gameplay(list_gameplays(cfg.gameplay_dir), dur, rng)
    name = f"{idx:02d}_{media.slugify(s.title)}.mp4"
    key = render.narration_key(game, offset, loop, words, choices, dur, cfg)
    log.info("  gameplay: %s (a partir de %s)%s", game.name, review.fmt_ts(offset), " repetindo" if loop else "")
    if not render.render_narration(game, offset, loop, track, words, choices, dur, cfg, out / name,
                                   work / "render", key):
        log.info("  já renderizado (mesma configuração), pulando")
    return {
        "rank": idx, "file": name, "title": s.title, "topic": s.topic, "duration": round(dur, 2),
        "voice": voice, "format": cfg.narrate_format, "gameplay": str(game), "gameplay_start": offset,
        "gameplay_loop": loop, "match": round(conf, 2),
        "lines": [{"text": ln.text, "kind": ln.kind, "option_a": ln.option_a, "option_b": ln.option_b,
                   "start": round(t0, 2), "end": round(t1, 2)} for ln, (t0, t1) in zip(s.lines, times)],
    }


def run_narrate(cfg: Config, *, client_factory: Callable[[], GeminiClient] | None = None,
                backend: tts.Backend | None = None, transcriber: Transcriber = transcribe_words,
                force: bool = False) -> Path:
    from .pipeline import _setup_logging, make_client

    if not cfg.gameplay_dir:
        raise ValueError("informe a pasta das gameplays de fundo: --gameplay-dir gameplays/")
    rid = run_id(cfg)
    ws, out = cfg.workspace_dir / "narrate" / rid, cfg.output_dir / "narrate" / rid
    _setup_logging(ws)
    log.info("narração: formato=%s roteiros=%d alvo=%.0fs modelo=%s tts=%s", cfg.narrate_format,
             cfg.count, cfg.target_s, cfg.gemini_model, cfg.tts_model)
    scripts = step_scripts(ws, cfg, client_factory or (lambda: make_client(cfg)), force)
    backend = backend or tts.make_backend(cfg)
    rng = random.Random(cfg.seed)

    videos = [build_one(s, i, cfg, backend, transcriber, ws, out, rng) for i, s in enumerate(scripts, 1)]
    keep = {v["file"] for v in videos}
    for f in out.glob("*.mp4"):  # sobras de uma execução com mais roteiros
        if f.name not in keep:
            f.unlink()
    (out / "scripts.json").write_text(json.dumps(videos, ensure_ascii=False, indent=2), encoding="utf-8")
    return review.write_narration_review(out, cfg, videos)
