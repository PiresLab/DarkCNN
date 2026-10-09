"""Legenda e hashtags do TikTok geradas pelo Gemini a partir do que o vídeo conta."""
from __future__ import annotations

import logging
import random
import re
import unicodedata
from typing import Any, Sequence

from pydantic import BaseModel, Field

from ..analyze import PROMPTS_DIR

log = logging.getLogger(__name__)

MAX_CAPTION = 2200
DEFAULT_TAGS = ["curiosidades", "voceSabia"]
BASE_PICK = 3  # quantas hashtags de alcance (lista editável nas configurações) entram em cada legenda
MAX_TAGS = 6


class CaptionPlan(BaseModel):
    caption: str = Field(max_length=300)
    hashtags: list[str] = Field(min_length=1, max_length=10)  # do assunto: as de alcance entram por `compose`


def clean_tag(tag: str) -> str:
    t = unicodedata.normalize("NFKD", tag.lstrip("#")).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9_]", "", t)[:40]


def compose(caption: str, ai_tags: Sequence[str], base_tags: Sequence[str] = (), rng: random.Random | None = None,
            base_n: int = BASE_PICK, max_tags: int = MAX_TAGS) -> str:
    """Legenda + hashtags: `base_n` de alcance sorteadas da lista base (primeiro), depois as do assunto, sem repetir."""
    rng = rng or random.Random()
    base = [t for t in dict.fromkeys(clean_tag(x) for x in base_tags) if t]
    picked = rng.sample(base, min(base_n, len(base)))
    seen, out = set(), []
    for t in [*picked, *(clean_tag(x) for x in ai_tags)]:
        if t and t.lower() not in seen and len(out) < max_tags:
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


def fallback(item: dict, base_tags: Sequence[str] = (), rng: random.Random | None = None) -> str:
    """Sem IA (cota esgotada, rede fora): o título, hashtags de alcance e as genéricas."""
    return compose(str(item.get("title") or "Vídeo novo"), DEFAULT_TAGS, base_tags, rng)


def generate(client: Any, item: dict, kind: str, niche: str | None = None, base_tags: Sequence[str] = (),
             rng: random.Random | None = None) -> str:
    """Nunca levanta: se a IA falhar, devolve o plano B para a postagem não travar."""
    try:
        k, title, details = describe(item, kind)
        niche_block = f"Nicho do canal: {niche}." if niche else ""
        prompt = (PROMPTS_DIR / "caption_v1.md").read_text(encoding="utf-8").format(
            kind=k, title=title, details=details, niche_block=niche_block)
        plan = client.generate_json(prompt, CaptionPlan, temperature=0.8)
        assert isinstance(plan, CaptionPlan)
        return compose(plan.caption, plan.hashtags, base_tags, rng)
    except Exception as e:  # noqa: BLE001
        log.warning("legenda por IA falhou (%s); usando o título", e)
        return fallback(item, base_tags, rng)
