"""Diagnóstico das vozes: lista o que está configurado e sintetiza uma frase de teste (sem Gemini)."""
from __future__ import annotations

import time
from pathlib import Path

from . import tts
from .config import Config
from .media import probe_duration


def voices_report(cfg: Config, say: str | None = None) -> str:
    if not cfg.voices:
        return ('Nenhuma voz configurada. No config.yaml:\n\n'
                'voices:\n'
                '  gumball:\n'
                '    ref_audio: C:/vozes/gumball.wav   # 3-10 s, caminho que o SERVIDOR do GPT-SoVITS lê\n'
                '    prompt_text: "o que é falado nesse áudio"\n'
                '    lang: pt\n')
    lines = [f"{len(cfg.voices)} voz(es) no config (servidor: {cfg.tts_url}):"]
    for name, v in sorted(cfg.voices.items()):
        falta = "" if Path(v.ref_audio).exists() else "  ⚠ áudio de referência não encontrado AQUI " \
                                                      "(pode existir na máquina do servidor)"
        marca = " (padrão)" if name == cfg.voice else ""
        lines.append(f"  - {name}{marca}: {v.ref_audio} [{v.lang}, velocidade {v.speed}]{falta}")
        if not v.prompt_text:
            lines.append("      ⚠ sem prompt_text: a imitação costuma ficar bem pior sem a transcrição")
    if not say:
        return "\n".join(lines + ['', 'Para ouvir uma amostra: darkcnn voices --say "testando a voz"'])

    voice = tts.get_voice(cfg)
    dest = Path(cfg.output_dir) / "voices" / f"{cfg.voice}.wav"
    dest.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    tts.make_backend(cfg).say(say, voice, dest)
    took = time.perf_counter() - t0
    dur = probe_duration(dest)
    lines += ["", f'Amostra da voz "{cfg.voice}": {dest}',
              f"  {dur:.1f}s de áudio em {took:.1f}s ({dur / took:.1f}x tempo real)",
              f"  nesse ritmo, uma narração de {cfg.target_s:.0f}s leva ~{cfg.target_s / (dur / took):.0f}s"]
    return "\n".join(lines)
