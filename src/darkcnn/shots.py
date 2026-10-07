"""Planos (trechos entre dois cortes de edição) e volume por plano, só com FFmpeg.

Para vídeos sem fala o "corte natural" é o corte de edição: o código numera os planos e o Gemini escolhe
por ID, do mesmo jeito que escolhe frases no perfil de fala. Os planos usam o mesmo formato das frases
({id, start, end, text}) para reaproveitar `analyze.resolve`.
"""
from __future__ import annotations

import math
import re
import statistics

from pathlib import Path

from .media import run

Shot = dict  # {"id", "start", "end", "text", "loud"}  (loud = dB relativo à mediana; None sem áudio)


def has_audio(video: Path) -> bool:
    p = run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
             "-of", "csv=p=0", video])
    return bool(p.stdout.strip())


def detect_cuts(video: Path, threshold: float = 0.30) -> list[float]:
    """Instantes (s) em que a cena muda. Analisa em 320 px de largura: bem mais rápido e basta para cortes."""
    p = run(["ffmpeg", "-hide_banner", "-loglevel", "info", "-i", video, "-an", "-vf",
             f"scale=320:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"])
    return sorted({float(t) for t in re.findall(r"pts_time:([0-9]+(?:\.[0-9]+)?)", p.stderr)})


def build_shots(cuts: list[float], duration: float, min_shot_s: float = 1.5,
                max_shot_s: float = 12.0) -> list[Shot]:
    """Cortes -> planos numerados. Funde planos curtos ao vizinho e divide os contínuos longos demais."""
    bounds = [0.0] + [c for c in cuts if 0.0 < c < duration] + [duration]
    segs = [[a, b] for a, b in zip(bounds, bounds[1:]) if b - a > 1e-3]
    merged: list[list[float]] = []
    for s in segs:
        if merged and s[1] - s[0] < min_shot_s:
            merged[-1][1] = s[1]  # plano curto é absorvido pelo anterior
        else:
            merged.append(s)
    if len(merged) > 1 and merged[0][1] - merged[0][0] < min_shot_s:
        merged[1][0] = merged[0][0]  # primeiro plano curto é absorvido pelo seguinte
        merged.pop(0)

    shots: list[Shot] = []
    for a, b in merged:
        parts = max(1, math.ceil((b - a) / max_shot_s))  # plano contínuo longo: pedaços iguais
        step = (b - a) / parts
        for k in range(parts):
            shots.append({"id": len(shots), "start": round(a + k * step, 3),
                          "end": round(b if k == parts - 1 else a + (k + 1) * step, 3),
                          "text": "", "loud": None})
    return shots


def loudness_series(video: Path) -> list[tuple[float, float]]:
    """(tempo, loudness momentâneo em LUFS), 10 medições por segundo. Silêncio total vira -120."""
    p = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", video, "-vn", "-af",
             "ebur128=metadata=1,ametadata=mode=print:key=lavfi.r128.M:file=-", "-f", "null", "-"])
    out = []
    for t, m in re.findall(r"pts_time:([0-9.]+)\s+lavfi\.r128\.M=(-?[0-9.]+|-?inf)", p.stdout + p.stderr):
        out.append((float(t), -120.0 if "inf" in m else max(-120.0, float(m))))
    return out


_LOUDNESS_WINDOW_S = 0.4  # o loudness "momentâneo" do EBU R128 olha os 400 ms anteriores


def _p90(vals: list[float]) -> float:
    v = sorted(vals)
    return v[min(len(v) - 1, int(0.9 * len(v)))]


def annotate_loudness(shots: list[Shot], series: list[tuple[float, float]]) -> None:
    """Preenche `loud` = volume típico alto do plano (percentil 90) menos a mediana entre os planos, em dB:
    >0 = mais alto que o normal do vídeo. Ignora os primeiros 400 ms do plano, onde a janela do medidor
    ainda contém o som do plano anterior (com o pico puro, um plano calmo logo após um barulhento parecia alto)."""
    stat: list[float | None] = []
    for s in shots:
        vals = [m for t, m in series if s["start"] + _LOUDNESS_WINDOW_S <= t < s["end"]]
        if not vals:  # plano curtíssimo: usa tudo o que houver
            vals = [m for t, m in series if s["start"] <= t < s["end"]]
        stat.append(_p90(vals) if vals else None)
    known = [x for x in stat if x is not None]
    if not known:
        return
    med = statistics.median(known)
    for s, x in zip(shots, stat):
        s["loud"] = None if x is None else round(x - med, 1)


def make_windows(shots: list[Shot], window_s: float, min_tail_s: float | None = None) -> list[list[Shot]]:
    """Agrupa planos consecutivos em janelas de ~window_s, sempre fechando na fronteira de um plano.
    Uma última janela menor que `min_tail_s` (padrão: 2 min, ou 1/4 da janela se for menor) vai junto da anterior."""
    if min_tail_s is None:
        min_tail_s = min(120.0, window_s / 4)
    windows: list[list[Shot]] = []
    cur: list[Shot] = []
    for s in shots:
        if cur and s["end"] - cur[0]["start"] > window_s:
            windows.append(cur)
            cur = []
        cur.append(s)
    if cur:
        windows.append(cur)
    if len(windows) > 1 and windows[-1][-1]["end"] - windows[-1][0]["start"] < min_tail_s:
        windows[-2].extend(windows.pop())  # sobra pequena vai junto da janela anterior
    return windows
