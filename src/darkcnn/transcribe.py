"""Transcrição local (faster-whisper, CPU) com tempo por palavra + divisão em frases."""
from __future__ import annotations

import logging
import wave
from pathlib import Path
from typing import Callable

from .config import Config

log = logging.getLogger(__name__)

Word = dict  # {"w": str, "start": float, "end": float, "p": float}
Sentence = dict  # {"id": int, "start": float, "end": float, "text": str}
Transcriber = Callable[[Path, Config], "list[Word]"]

_TERMINAL = (".", "?", "!", "…")
_TRAILING = "\"'”’)»]"


def load_wav(path: Path):
    """WAV 16 kHz mono -> float32. Entregar array ao faster-whisper evita o decoder PyAV dele, que
    quebra em algumas versões (TypeError: open() ... 'metadata_errors')."""
    import numpy as np

    with wave.open(str(path), "rb") as w:
        if not (w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2):
            raise ValueError("esperava WAV 16 kHz mono 16-bit")
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def transcribe_words(wav: Path, cfg: Config) -> list[Word]:
    """Transcreve com faster-whisper e devolve palavras com tempo (segundos desde o início do áudio)."""
    from faster_whisper import WhisperModel

    log.info("carregando Whisper '%s' (%s, CPU)…", cfg.whisper_model, cfg.whisper_compute_type)
    model = WhisperModel(cfg.whisper_model, device="cpu", compute_type=cfg.whisper_compute_type)
    segments, info = model.transcribe(
        load_wav(wav), language=cfg.language, word_timestamps=True,
        beam_size=cfg.whisper_beam, condition_on_previous_text=False,
    )
    words: list[Word] = []
    for seg in segments:  # gerador: só decodifica ao ser consumido
        for w in seg.words or []:
            if w.word.strip():
                words.append({"w": w.word.strip(), "start": float(w.start), "end": float(w.end),
                              "p": float(w.probability)})
    log.info("%d palavras (idioma %s, prob %.2f)", len(words), info.language, info.language_probability)
    return words


def normalize_words(words: list[Word], min_dur: float = 0.08) -> list[Word]:
    """Garante início estritamente crescente e duração mínima (o Whisper devolve palavras com
    duração 0 e pares com o mesmo início), sem deixar uma palavra invadir a seguinte."""
    out: list[Word] = []
    for w in words:
        s = float(w["start"])
        if out and s < out[-1]["start"] + min_dur:
            s = out[-1]["start"] + min_dur
        e = max(float(w["end"]), s + min_dur)
        if out and out[-1]["end"] > s:
            out[-1]["end"] = s
        out.append({**w, "start": round(s, 3), "end": round(e, 3)})
    return out


def split_sentences(words: list[Word], max_gap: float = 0.7, max_words: int = 25) -> list[Sentence]:
    """Frase termina em pontuação final, pausa >= max_gap ou max_words palavras."""
    sentences: list[Sentence] = []
    cur: list[Word] = []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        ends = w["w"].rstrip(_TRAILING).endswith(_TERMINAL)
        pause = nxt is not None and nxt["start"] - w["end"] >= max_gap
        if nxt is None or ends or pause or len(cur) >= max_words:
            sentences.append({
                "id": len(sentences),
                "start": cur[0]["start"],
                "end": cur[-1]["end"],
                "text": " ".join(x["w"] for x in cur),
            })
            cur = []
    return sentences
