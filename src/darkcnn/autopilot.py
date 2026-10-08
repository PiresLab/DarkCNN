"""Modo automático: a IA propõe o tema, o yt-dlp acha candidatos, a IA escolhe, e o pipeline roda."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field

from . import runctx
from .analyze import PROMPTS_DIR
from .config import Config
from .gemini import GeminiClient
from .presets import PresetSpec

log = logging.getLogger(__name__)

TOPIC_PROMPT = "topic_v1"
PICK_PROMPT = "pick_video_v1"
MIN_SOURCE_MIN = 3.0  # abaixo disso não há o que cortar
N_QUERIES = 5
PER_QUERY = 8

MODE_DESC = {
    "narrate": "vídeo narrado por voz sobre gameplay (curiosidade, \"você prefere\", \"e se\"...): o assunto vira o roteiro",
    "run": "cortes virais de um vídeo longo do YouTube: o assunto guia a busca do vídeo-fonte",
    "compile": "compilado \"Top N\" com os melhores momentos de um tema: o assunto é o tema do compilado",
}


class TopicPlan(BaseModel):
    topic: str = Field(max_length=200)
    angle: str = ""
    queries: list[str] = Field(min_length=1, max_length=8)


class VideoPick(BaseModel):
    index: int
    reason: str = ""


@dataclass
class Candidate:
    title: str
    url: str
    channel: str
    duration_min: float


# ---------------------------------------------------------------- tema
def propose_topic(client: GeminiClient, spec: PresetSpec, history: list[str]) -> TopicPlan:
    niche = f"NICHO DO CANAL: {spec.niche}. O assunto tem que combinar com isso." if spec.niche else \
        "NICHO DO CANAL: livre."
    hist = ("ASSUNTOS JÁ USADOS (não repita):\n" + "\n".join(f"- {h}" for h in history[:30])) if history else \
        "ASSUNTOS JÁ USADOS: nenhum ainda."
    prompt = (PROMPTS_DIR / f"{TOPIC_PROMPT}.md").read_text(encoding="utf-8").format(
        mode_desc=MODE_DESC[spec.mode], niche_block=niche, history_block=hist, n_queries=N_QUERIES)
    plan = client.generate_json(prompt, TopicPlan, temperature=0.9)
    assert isinstance(plan, TopicPlan)
    plan.topic = plan.topic.strip().strip('"')
    plan.queries = [q.strip() for q in plan.queries if q.strip()][:N_QUERIES] or [plan.topic]
    return plan


# ---------------------------------------------------------------- busca de vídeo
def search_videos(queries: list[str], cfg: Config, exclude: set[str], ydl_cls: Any = None) -> list[Candidate]:
    if ydl_cls is None:
        from yt_dlp import YoutubeDL as ydl_cls  # noqa: N813
    found: dict[str, Candidate] = {}
    opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True}
    with ydl_cls(opts) as ydl:
        for q in queries:
            try:
                info = ydl.extract_info(f"ytsearch{PER_QUERY}:{q}", download=False)
            except Exception as e:  # noqa: BLE001 - uma busca ruim não derruba as outras
                log.warning("busca falhou (%s): %s", q, str(e)[:120])
                continue
            for e in (info or {}).get("entries") or []:
                url = e.get("webpage_url") or e.get("url")
                if not url and e.get("id"):
                    url = e["id"]
                if not url:
                    continue
                if not url.startswith("http"):
                    url = f"https://www.youtube.com/watch?v={e.get('id') or url}"
                mins = (e.get("duration") or 0) / 60
                if url in exclude or url in found or e.get("live_status") in ("is_live", "is_upcoming"):
                    continue
                if mins < MIN_SOURCE_MIN or mins > cfg.max_download_min:
                    continue
                found[url] = Candidate(e.get("title") or "?", url, e.get("channel") or e.get("uploader") or "?", mins)
    return list(found.values())


def pick_video(client: GeminiClient, topic: str, candidates: list[Candidate], mode: str) -> Candidate | None:
    if not candidates:
        return None
    listing = "\n".join(f"[{i}] {c.title} — {c.channel} ({c.duration_min:.0f} min)" for i, c in enumerate(candidates))
    rule = ("Para um compilado, prefira vídeos com vários momentos distintos do tema."
            if mode == "compile" else "Prefira um vídeo com falas e histórias fortes, que renda vários cortes.")
    prompt = (PROMPTS_DIR / f"{PICK_PROMPT}.md").read_text(encoding="utf-8").format(
        topic=topic, candidates=listing, kind_rule=rule)
    pick = client.generate_json(prompt, VideoPick, temperature=0.2)
    assert isinstance(pick, VideoPick)
    if 0 <= pick.index < len(candidates):
        log.info("vídeo escolhido: %s (%s)", candidates[pick.index].title, pick.reason)
        return candidates[pick.index]
    return None


# ---------------------------------------------------------------- execução
def run_auto(preset_id: str, cfg: Config, force: bool, *, spec: PresetSpec, history: list[str],
             used_sources: set[str], client_factory: Callable[[], GeminiClient] | None = None,
             runners: dict[str, Callable[..., Path]] | None = None, ydl_cls: Any = None) -> Path:
    """Resolve o que a IA deve decidir (tema, vídeo) e chama o executor normal do modo."""
    from . import narrate as narratelib, pipeline

    make = client_factory or (lambda: pipeline.make_client(cfg))
    runners = runners or {
        "narrate": lambda src, c, f: narratelib.run_narrate(c, force=f),
        "run": lambda src, c, f: pipeline.run_pipeline(src, c, force=f),
        "compile": lambda src, c, f: pipeline.run_compile(src, c, force=f),
    }
    plan: TopicPlan | None = None
    if spec.mode != "narrate" and spec.auto_source and not spec.auto_topic:
        fixed = spec.theme or spec.topic or ""  # tema do usuário vira a própria busca
        plan = TopicPlan(topic=fixed, queries=[fixed])
    elif spec.auto_topic and (spec.mode != "run" or spec.auto_source):
        log.info("automático: propondo o tema (%d já usados)", len(history))
        plan = propose_topic(make(), spec, history)
        log.info("tema: %s — %s", plan.topic, plan.angle)
        runctx.record_params(topic=plan.topic)

    source = ""
    if spec.mode == "narrate":
        if plan:
            cfg = cfg.model_copy(update={"topic": plan.topic})
    else:
        if spec.auto_source:
            assert plan is not None
            cands = search_videos(plan.queries, cfg, used_sources, ydl_cls)
            log.info("automático: %d candidatos", len(cands))
            chosen = pick_video(make(), plan.topic, cands, spec.mode)
            if chosen is None:
                raise RuntimeError(f"nenhum vídeo adequado achado para '{plan.topic}'; tente de novo ou ajuste o nicho")
            source = chosen.url
            runctx.record_params(source=source)
            cfg = cfg.model_copy(update={"source": chosen.url})
        else:
            source = spec.source or ""
        if spec.mode == "compile" and plan and not cfg.theme:
            cfg = cfg.model_copy(update={"theme": plan.topic})
    return Path(runners[spec.mode](source, cfg, force))
