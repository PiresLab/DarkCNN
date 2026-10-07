"""Perfil visual: escolhe momentos em vídeos sem fala mostrando ao Gemini um vídeo em baixa resolução
com o número de cada plano gravado no canto, e pedindo IDs de plano (tempos exatos vêm do código)."""
from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel

from . import cache
from .analyze import PROMPTS_DIR, Candidate, fmt_ts, n_candidates
from .config import Config
from .gemini import GeminiClient
from .media import run
from .shots import Shot, make_windows

log = logging.getLogger(__name__)

RECONCILE_TOL_S = 4.0  # ID e tempo informados pelo Gemini mais distantes que isso -> confia no tempo
TOKENS_PER_VIDEO_SECOND = 103  # medido no spike 03 (vídeo 360p com áudio)


class VisualCandidate(Candidate):
    start_ts: str  # MM:SS relativo ao início do vídeo da janela: conferência dos IDs
    end_ts: str


class VisualSelection(BaseModel):
    candidates: list[VisualCandidate]


# ---------------------------------------------------------------- texto
def parse_mmss(text: str) -> float | None:
    try:
        sec = 0.0
        for x in text.strip().split(":"):
            sec = sec * 60 + float(x)
        return sec
    except ValueError:
        return None


def shots_table(win: list[Shot]) -> str:
    """Planos da janela com tempos RELATIVOS ao vídeo enviado (que começa em 0)."""
    t0 = win[0]["start"]
    lines = []
    for s in win:
        vol = "" if s.get("loud") is None else f" volume {s['loud']:+.0f}dB"
        lines.append(f"[{s['id']}] {fmt_ts(s['start'] - t0)}–{fmt_ts(s['end'] - t0)} "
                     f"({s['end'] - s['start']:.0f}s){vol}")
    return "\n".join(lines)


def build_prompt(win: list[Shot], cfg: Config) -> str:
    tpl = (PROMPTS_DIR / "select_visual_v1.md").read_text(encoding="utf-8")
    dur = win[-1]["end"] - win[0]["start"]
    return tpl.format(window_len=f"{dur / 60:.0f} min" if dur >= 90 else f"{dur:.0f} s",
                      shots=shots_table(win), n_candidates=n_candidates(cfg),
                      min_s=int(cfg.min_clip_s), max_s=int(cfg.max_clip_s))


# ---------------------------------------------------------------- proxy
_ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Id,{font},84,&H0000FFFF,&H0000FFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,7,0,7,24,24,16,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def ids_ass(win: list[Shot], font: str) -> str:
    from .textlayers import ass_time

    t0 = win[0]["start"]
    ev = [f"Dialogue: 0,{ass_time(s['start'] - t0)},{ass_time(s['end'] - t0)},Id,,0,0,0,,#{s['id']}\n" for s in win]
    return _ASS.format(font=font) + "".join(ev)


def make_proxy(video: Path, win: list[Shot], dest: Path, cfg: Config, workdir: Path) -> Path:
    """Vídeo pequeno da janela (baixa resolução, poucos fps) com `#id` do plano gravado no canto."""
    shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "ids.ass").write_text(ids_ass(win, cfg.font), encoding="utf-8")
    t0, dur = win[0]["start"], win[-1]["end"] - win[0]["start"]
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-ss", f"{t0:.3f}", "-t", f"{dur:.3f}", "-i", Path(video).resolve(),
         "-vf", f"scale=-2:{cfg.proxy_height},fps={cfg.visual_fps},ass=ids.ass",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "32", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "32k", "-ac", "1", "-ar", "16000", "-movflags", "+faststart", "proxy.mp4"],
        cwd=workdir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.unlink(missing_ok=True)
    shutil.move(str(workdir / "proxy.mp4"), dest)
    return dest


# ---------------------------------------------------------------- reconciliação
def shot_at(shots: list[Shot], t: float) -> int:
    """ID do plano que contém o instante t (absoluto), com limites."""
    for s in shots:
        if s["start"] <= t < s["end"]:
            return s["id"]
    return shots[0]["id"] if t < shots[0]["start"] else shots[-1]["id"]


def reconcile(c: dict[str, Any], shots: list[Shot], window_start: float,
              tol: float = RECONCILE_TOL_S) -> tuple[dict[str, Any], int]:
    """O Gemini pode ler o número do plano errado no quadro. Se o plano do ID estiver longe do tempo
    informado (start_ts/end_ts), o tempo vence e o ID é trocado pelo plano que contém aquele tempo."""
    c = dict(c)
    fixed = 0
    by_id = {s["id"]: s for s in shots}
    ts = parse_mmss(c.get("start_ts", ""))
    te = parse_mmss(c.get("end_ts", ""))
    if ts is not None:
        ts += window_start
        s = by_id.get(c["start_id"])
        if s is None or abs(s["start"] - ts) > tol:
            c["start_id"], fixed = shot_at(shots, ts), fixed + 1
    if te is not None:
        te += window_start
        e = by_id.get(c["end_id"])
        if e is None or abs(e["end"] - te) > tol:
            c["end_id"], fixed = shot_at(shots, max(te - 1e-3, 0.0)), fixed + 1
    return c, fixed


# ---------------------------------------------------------------- seleção por janela
def select_visual(video: Path, vid: str, ws: Path, shots: list[Shot], cfg: Config,
                  client_factory: Callable[[], GeminiClient], force: bool = False,
                  sleep: Callable[[float], None] = time.sleep) -> tuple[list[dict], dict]:
    """Uma chamada ao Gemini por janela (com cache por janela: falha na janela 3 não refaz a 1 e a 2).
    Devolve (candidatos com IDs reconciliados, estatísticas)."""
    windows = make_windows(shots, cfg.visual_window_min * 60)
    total_min = (shots[-1]["end"] - shots[0]["start"]) / 60
    log.info("%d janela(s) de vídeo; enviar tudo ≈ %.0f mil tokens do Gemini",
             len(windows), total_min * 60 * TOKENS_PER_VIDEO_SECOND / 1000)
    out: list[dict] = []
    stats = {"windows": len(windows), "api_calls": 0, "reconciled": 0}
    client: GeminiClient | None = None

    for i, win in enumerate(windows):
        path = ws / "analysis" / f"win_{i:02d}.json"
        key = cache.key_of(v=1, vid=vid, win=i, table=shots_table(win), model=cfg.gemini_model,
                           prompt="select_visual_v1", n=n_candidates(cfg), min=cfg.min_clip_s,
                           max=cfg.max_clip_s, fps=cfg.visual_fps, h=cfg.proxy_height)
        cands = None if force else cache.load(path, key)
        if cands is None:
            if stats["api_calls"]:
                log.info("aguardando %.0fs (limite de tokens por minuto)…", cfg.visual_pause_s)
                sleep(cfg.visual_pause_s)
            log.info("janela %d/%d: %s–%s, preparando vídeo reduzido…", i + 1, len(windows),
                     fmt_ts(win[0]["start"]), fmt_ts(win[-1]["end"]))
            proxy = make_proxy(video, win, ws / "proxy" / f"win_{i:02d}.mp4", cfg, ws / "proxy" / f"tmp_{i:02d}")
            log.info("enviando %.1f MB ao Gemini (%s)…", proxy.stat().st_size / 1e6, cfg.gemini_model)
            client = client or client_factory()
            sel = client.generate_json(build_prompt(win, cfg), VisualSelection, media=proxy)
            cands = [c.model_dump() for c in sel.candidates]
            cache.save(path, key, cands)
            stats["api_calls"] += 1
            proxy.unlink(missing_ok=True)
            shutil.rmtree(ws / "proxy" / f"tmp_{i:02d}", ignore_errors=True)
        else:
            log.info("janela %d/%d em cache", i + 1, len(windows))
        for c in cands:
            c2, n = reconcile(c, shots, win[0]["start"])
            stats["reconciled"] += n
            out.append(c2)
    if stats["reconciled"]:
        log.warning("%d ID(s) de plano corrigido(s) pelo tempo informado: o Gemini errou a leitura do número",
                    stats["reconciled"])
    return out, stats
