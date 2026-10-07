"""Alinhamento: tempos do Whisper + texto exato do roteiro = legenda certa e sincronizada.

O Whisper às vezes ouve errado o que o TTS falou ("vinte e três" vira "23", um nome sai trocado). Como
aqui NÓS sabemos o texto exato, usamos o Whisper só para os TEMPOS e casamos palavra a palavra com o
roteiro. Palavra que não casa recebe tempo interpolado entre as vizinhas que casaram.
"""
from __future__ import annotations

import logging
import unicodedata
from difflib import SequenceMatcher

from .transcribe import Word, normalize_words

log = logging.getLogger(__name__)


def norm(word: str) -> str:
    """Forma comparável: sem acento, sem pontuação, minúscula."""
    w = unicodedata.normalize("NFD", word.lower())
    w = "".join(ch for ch in w if unicodedata.category(ch) != "Mn")
    return "".join(ch for ch in w if ch.isalnum())


def _even(tokens: list[str], start: float, dur: float, p: float) -> list[Word]:
    """Distribui as palavras por igual (usado quando não há nada em que se apoiar)."""
    step = dur / len(tokens) if tokens else 0.0
    return [{"w": t, "start": round(start + i * step, 3), "end": round(start + (i + 1) * step, 3), "p": p}
            for i, t in enumerate(tokens)]


def align_line(text: str, heard: list[Word], start: float, dur: float) -> list[Word]:
    """`text`: o que foi mandado para o TTS. `heard`: palavras do Whisper, com tempo relativo ao áudio da
    linha. Devolve uma palavra por token de `text`, já com o tempo absoluto na trilha."""
    tokens = text.split()
    if not tokens:
        return []
    if not heard:
        log.debug("linha sem reconhecimento: distribuindo as palavras por igual")
        return _even(tokens, start, dur, 0.3)

    a = [norm(t) for t in tokens]
    b = [norm(w["w"]) for w in heard]
    anchors: dict[int, tuple[float, float]] = {}
    for i, j, size in SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
        for k in range(size):
            if a[i + k]:  # token sem letra nem número (só pontuação) não serve de âncora
                anchors[i + k] = (float(heard[j + k]["start"]), float(heard[j + k]["end"]))
    if not anchors:
        log.debug("nenhuma palavra casou com o áudio: distribuindo por igual")
        return _even(tokens, start, dur, 0.3)

    out: list[Word] = [None] * len(tokens)  # type: ignore[list-item]
    for i, (s, e) in anchors.items():
        out[i] = {"w": tokens[i], "start": s, "end": e, "p": 1.0}

    # buracos entre âncoras (e nas pontas): reparte o intervalo disponível entre as palavras não casadas
    idx = sorted(anchors)
    gaps = [(-1, idx[0])] + list(zip(idx, idx[1:])) + [(idx[-1], len(tokens))]
    for prev, nxt in gaps:
        missing = [i for i in range(prev + 1, min(nxt, len(tokens))) if out[i] is None]
        if not missing:
            continue
        t0 = anchors[prev][1] if prev >= 0 else 0.0
        t1 = anchors[nxt][0] if nxt < len(tokens) and nxt in anchors else dur
        if t1 <= t0:  # âncoras coladas ou fora de ordem: abre um espaço mínimo
            t1 = t0 + 0.05 * len(missing)
        step = (t1 - t0) / len(missing)
        for k, i in enumerate(missing):
            out[i] = {"w": tokens[i], "start": round(t0 + k * step, 3),
                      "end": round(t0 + (k + 1) * step, 3), "p": 0.4}

    words = [{**w, "start": round(w["start"] + start, 3), "end": round(w["end"] + start, 3)} for w in out]
    return normalize_words(words)  # garante ordem crescente e duração mínima


def align_script(lines: list[tuple[str, float, float]], heard_per_line: list[list[Word]]) -> list[Word]:
    """Alinha o roteiro inteiro. `lines`: (texto, início na trilha, duração da fala)."""
    words: list[Word] = []
    for (text, start, dur), heard in zip(lines, heard_per_line):
        words += align_line(text, heard, start, dur)
    return normalize_words(words)


def matched_fraction(text: str, heard: list[Word]) -> float:
    """Quanto do roteiro o Whisper confirmou (0 a 1). Baixo = voz ruim ou texto difícil: vale avisar."""
    a = [t for t in (norm(x) for x in text.split()) if t]
    b = [t for t in (norm(w["w"]) for w in heard) if t]
    if not a:
        return 1.0
    matched = sum(size for _, _, size in SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks())
    return min(1.0, matched / len(a))
