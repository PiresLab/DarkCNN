"""Juiz: 2ª passada que ASSISTE aos candidatos já com o corte exato e os compara entre si.

A 1ª passada escolhe de longe (lendo a transcrição ou vendo janelas de 10 min) e dá notas auto-declaradas,
sem comparação. Aqui o Gemini recebe os candidatos num único vídeo (rótulo A1, A2… gravado em cada um) e ordena
por qualidade percebida. Se falhar, mantém a ordem da 1ª passada.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

from . import cache
from .analyze import PROMPTS_DIR, fmt_ts
from .config import Config
from .gemini import GeminiClient, GeminiError
from .media import concat_copy, probe_duration, run
from .visual import ids_ass

log = logging.getLogger(__name__)


class JudgeItem(BaseModel):
    label: str  # A1, A2, …
    final_score: int = Field(ge=1, le=100)
    keep: bool
    strength: str
    weakness: str


class JudgeVerdict(BaseModel):
    ranking: list[JudgeItem]


def make_reel(video: Path, clips: list[dict], dest: Path, cfg: Config, workdir: Path) -> list[tuple[str, float, float]]:
    """Vídeo único (360p, poucos fps, com áudio) com os candidatos em sequência e o rótulo gravado no canto.
    Devolve [(rótulo, início no reel, fim no reel)]."""
    shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    spans: list[tuple[str, float, float]] = []
    t = 0.0
    for i, c in enumerate(clips, 1):
        label = f"A{i}"
        # reaproveita o desenho de "#id" dos planos: um único evento com o rótulo durante o trecho todo
        ass = ids_ass([{"id": label, "start": 0.0, "end": c["end"] - c["start"]}], cfg.font).replace("#A", "A")
        (workdir / f"l{i}.ass").write_text(ass, encoding="utf-8")
        seg = workdir / f"s{i}.mp4"  # caminho completo (para medir e juntar)
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-ss", f"{c['start']:.3f}", "-t", f"{c['end'] - c['start']:.3f}", "-i", Path(video).resolve(),
             "-vf", f"scale=-2:{cfg.proxy_height},fps={cfg.visual_fps},ass=l{i}.ass",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "32", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "32k", "-ac", "1", "-ar", "16000", seg.name], cwd=workdir)  # nome simples: o ffmpeg roda em workdir
        d = probe_duration(seg)
        spans.append((label, t, t + d))
        t += d
        parts.append(seg)
    concat_copy(parts, dest, reencode_audio=False)
    return spans


def build_prompt(clips: list[dict], spans: list[tuple[str, float, float]], cfg: Config, theme: str | None) -> str:
    tpl = (PROMPTS_DIR / f"{cfg.judge_prompt_version}.md").read_text(encoding="utf-8")
    lines = []
    for c, (label, a, b) in zip(clips, spans):
        line = (f"{label} — no vídeo anexo {fmt_ts(a)}–{fmt_ts(b)} — 1ª análise: nota {c['score']}/10, "
                f"título \"{c['title']}\" — motivo: {c['reason']}")
        if c.get("transcript"):
            line += f"\n    transcrição: {c['transcript'][:700]}"
        lines.append(line)
    block = (f"\nTEMA DO COMPILADO: \"{theme}\". Candidatos que não combinam de verdade com o tema devem ter "
             "`keep` falso, e o ranking deve refletir o quanto cada um é um EXEMPLO FORTE desse tema.\n") if theme else ""
    return tpl.format(n=len(clips), theme_block=block, candidates="\n".join(lines))


def run_judge(video: Path, vid: str, ws: Path, clips: list[dict], cfg: Config,
              client_factory: Callable[[], GeminiClient], theme: str | None = None,
              force: bool = False) -> tuple[list[dict], list[dict], dict]:
    """clips: candidatos já resolvidos, na ordem da 1ª passada. Devolve (mantidos, descartados, estatísticas):
    mantidos ordenados pela nota do juiz; descartados com `reason_rejected`."""
    if len(clips) < 2:
        return clips, [], {"judged": False}
    pool, rest = clips[:cfg.judge_max_candidates], clips[cfg.judge_max_candidates:]
    model = cfg.judge_model or cfg.gemini_model
    path = ws / "judge.json"
    key = cache.key_of(v=1, vid=vid, cands=[(c["start"], c["end"]) for c in pool], model=model,
                       prompt=cfg.judge_prompt_version, thinking=cfg.thinking_level, theme=theme,
                       h=cfg.proxy_height, fps=cfg.visual_fps)
    verdict = None if force else cache.load(path, key)
    stats = {"judged": True, "api_calls": 0}
    if verdict is None:
        reel_dir = ws / "judge_tmp"
        try:
            reel = reel_dir / "reel.mp4"
            spans = make_reel(video, pool, reel, cfg, reel_dir / "parts")
            log.info("juiz (%s): assistindo a %d candidatos (%.0f s de vídeo)…", model, len(pool), spans[-1][2])
            v = client_factory().generate_json(build_prompt(pool, spans, cfg, theme), JudgeVerdict, media=reel)
            verdict = [i.model_dump() for i in v.ranking]
            cache.save(path, key, verdict)
            stats["api_calls"] = 1
        except GeminiError as e:  # inclui estouro de cota: não derruba o pipeline, só perde o refinamento
            log.warning("juiz indisponível (%s): mantendo a ordem da 1ª passada", e)
            return clips, [], {"judged": False, "error": str(e)}
        finally:
            shutil.rmtree(reel_dir, ignore_errors=True)
    else:
        log.info("juiz em cache")

    by_label = {f"A{i}": c for i, c in enumerate(pool, 1)}
    for item in verdict:
        c = by_label.get(item["label"])
        if c is None or "judge_score" in c:  # rótulo inventado ou repetido
            continue
        c.update(judge_score=item["final_score"], judge_keep=item["keep"],
                 judge_strength=item["strength"], judge_weakness=item["weakness"])
    judged = [c for c in pool if "judge_score" in c]
    missing = [c for c in pool if "judge_score" not in c]  # o juiz esqueceu: fica depois, sem ser descartado
    if missing:
        log.warning("o juiz não avaliou %d candidato(s); ficam no fim, na ordem da 1ª passada", len(missing))
    kept = sorted((c for c in judged if c["judge_keep"]), key=lambda c: -c["judge_score"]) + missing
    dropped = [{**c, "reason_rejected": f"juiz: {c['judge_weakness']}"} for c in judged if not c["judge_keep"]]
    dropped += [{**c, "reason_rejected": "fora do limite de candidatos do juiz"} for c in rest]
    stats["reordered"] = [c["start"] for c in kept] != [c["start"] for c in pool if c in kept]
    return kept, dropped, stats
