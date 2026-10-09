"""Legenda e hashtags do TikTok geradas pelo Gemini a partir do que o vídeo conta."""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

from pydantic import BaseModel, Field

from ..analyze import PROMPTS_DIR

log = logging.getLogger(__name__)

MAX_CAPTION = 2200
DEFAULT_TAGS = ["curiosidades", "voceSabia"]


class CaptionPlan(BaseModel):
    caption: str = Field(max_length=300)
    hashtags: list[str] = Field(min_length=1, max_length=10)


def clean_tag(tag: str) -> str:
    t = unicodedata.normalize("NFKD", tag.lstrip("#")).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9_]", "", t)[:40]


def compose(caption: str, tags: list[str]) -> str:
    seen, out = set(), []
    for t in (clean_tag(x) for x in tags):
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(f"#{t}")
    return f"{' '.join(caption.split())} {' '.join(out)}".strip()[:MAX_CAPTION]


def describe(item: dict, kind: str) -> tuple[str, str, str]:
    """(tipo, título, detalhes) a partir de um item do selection.json/scripts.json."""
    title = str(item.get("title") or "").strip()
    if kind == "narration":
        spoken = " ".join(str(ln.get("text", "")) for ln in (item.get("lines") or []))
        return "narração sobre um assunto", title, f"Roteiro: {spoken[:700]}"
    bits = []
    if item.get("hook_text"):
        bits.append(f"Gancho: {item['hook_text']}")
    if item.get("context"):
        bits.append(f"Contexto: {item['context']}")
    return "corte de um vídeo", title, "\n".join(bits)


def fallback(item: dict) -> str:
    """Sem IA (cota esgotada, rede fora): o título e hashtags genéricas."""
    return compose(str(item.get("title") or "Vídeo novo"), DEFAULT_TAGS)


def generate(client: Any, item: dict, kind: str, niche: str | None = None) -> str:
    """Nunca levanta: se a IA falhar, devolve o plano B para a postagem não travar."""
    try:
        k, title, details = describe(item, kind)
        niche_block = f"Nicho do canal: {niche}." if niche else ""
        prompt = (PROMPTS_DIR / "caption_v1.md").read_text(encoding="utf-8").format(
            kind=k, title=title, details=details, niche_block=niche_block)
        plan = client.generate_json(prompt, CaptionPlan, temperature=0.8)
        assert isinstance(plan, CaptionPlan)
        return compose(plan.caption, plan.hashtags)
    except Exception as e:  # noqa: BLE001
        log.warning("legenda por IA falhou (%s); usando o título", e)
        return fallback(item)
