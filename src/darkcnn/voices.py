"""Diagnóstico das vozes do Gemini: modelos TTS disponíveis e amostras para ouvir (não usa o modelo de roteiro)."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from . import tts
from .config import Config
from .media import probe_duration


def sample_voices(cfg: Config, all_voices: bool) -> list[str]:
    if not all_voices:
        return [cfg.tts_voice]
    if cfg.tts_voices_pool:
        return list(dict.fromkeys(cfg.tts_voices_pool))
    others = [v for v in tts.KNOWN_VOICES if v != cfg.tts_voice]
    return [cfg.tts_voice, *others[:3]]


def voices_report(cfg: Config, say: str | None = None, *, all_voices: bool = False, models: bool = False,
                  backend: tts.Backend | None = None,
                  lister: Callable[[], list[str]] | None = None) -> str:
    lines: list[str] = []
    if models:
        names = (lister or tts.list_tts_models)()
        lines.append(f"{len(names)} modelo(s) TTS visíveis para a sua chave:")
        lines += [f"  - {n}" for n in names] or ["  (nenhum: o TTS pode não estar liberado para o projeto)"]
        warn = "  ⚠ não está na lista acima: ajuste `tts_model` no config.yaml" \
            if names and cfg.tts_model not in names else ""
        lines += [f"modelo configurado: {cfg.tts_model}{warn}", ""]

    pool = f", pool: {', '.join(cfg.tts_voices_pool)}" if cfg.tts_voices_pool else ""
    lines += [f"voz padrão: {cfg.tts_voice}{pool} — velocidade {cfg.tts_speed} — modelo {cfg.tts_model}",
              "vozes do Gemini (confira a lista atual na documentação): " + ", ".join(tts.KNOWN_VOICES)]
    if not say:
        return "\n".join(lines + ["", 'Para ouvir uma amostra: darkcnn voices --say "testando a voz" '
                                      '(--all para comparar algumas vozes; cada voz custa 1 requisição)'])

    backend = backend or tts.make_backend(cfg)
    folder = Path(cfg.output_dir) / "voices"
    folder.mkdir(parents=True, exist_ok=True)
    lines.append("")
    for voice in sample_voices(cfg, all_voices):
        dest = folder / f"{voice}.wav"
        t0 = time.perf_counter()
        backend.say(say, voice, dest)
        took = time.perf_counter() - t0
        lines.append(f"{voice}: {dest} ({probe_duration(dest):.1f}s de áudio, gerado em {took:.1f}s)")
    return "\n".join(lines)
