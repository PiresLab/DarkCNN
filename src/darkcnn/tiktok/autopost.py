"""Depois que uma automação termina de gerar, enfileira uma postagem no TikTok para cada vídeo novo."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

from ..config import Config
from . import captions
from .post import PostOptions

log = logging.getLogger(__name__)


def read_items(folder: Path) -> tuple[str, list[dict]]:
    """(tipo, itens) da pasta de saída: narração (scripts.json) ou cortes (selection.json)."""
    for name, kind in (("scripts.json", "narration"), ("selection.json", "cuts")):
        p = folder / name
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except ValueError:
                continue
            return kind, [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []
    return "cuts", []


def post_params(item: dict, kind: str, spec: Any, index: int, caption: str) -> PostOptions:
    t = spec.tiktok
    delay = t.delay_min * 60
    if index:  # a partir do 2º vídeo, escalonado: postar tudo junto parece spam
        delay += index * t.stagger_min * 60
    if t.visibility == "private":
        delay = 0  # o TikTok não agenda vídeo privado
    return PostOptions(account=t.account or "", caption=caption, visibility=t.visibility, schedule_s=delay or None,
                       ai_label=t.ai_label if kind == "narration" else False, allow_comment=t.allow_comment,
                       allow_duet=t.allow_duet, allow_stitch=t.allow_stitch,
                       allow_content_reuse=t.allow_content_reuse, allow_ai_remix=t.allow_ai_remix)


def enqueue_posts(store: Any, cfg: Config, spec: Any, review: str, preset_id: str | None,
                  client_factory: Callable[[], Any] | None = None) -> list[dict]:
    """Um job `post` por vídeo gerado. Devolve os jobs criados."""
    t = spec.tiktok
    if t is None or not t.enabled:
        return []
    folder = Path(review).parent
    kind, items = read_items(folder)
    items = [i for i in items if i.get("file") and (folder / str(i["file"])).is_file()]
    if not items:
        log.warning("automação sem vídeos para postar em %s", folder)
        return []
    client = None
    if t.caption_ai:
        try:
            client = (client_factory or (lambda: __import__("darkcnn.pipeline", fromlist=["make_client"]).make_client(cfg)))()
        except Exception as e:  # noqa: BLE001 - sem chave/cota, cai no título
            log.warning("sem IA para legendas (%s); usando o título", e)
    try:
        review_id = folder.relative_to(Path(cfg.output_dir)).as_posix()
    except ValueError:
        review_id = folder.name
    jobs = []
    for i, item in enumerate(items):
        caption = captions.generate(client, item, kind, spec.niche, cfg.tiktok_base_tags) if client \
            else captions.fallback(item, cfg.tiktok_base_tags)
        opts = post_params(item, kind, spec, i, caption)
        jobs.append(store.enqueue(
            "post", cfg, str(folder / str(item["file"])), f"TikTok: {item.get('title') or item['file']}",
            {"post": opts.model_dump(), "review_id": review_id, "file": item["file"], "rank": item.get("rank")},
            preset_id=preset_id))
    log.info("%d vídeo(s) na fila para o TikTok (%s)", len(jobs), t.account)
    return jobs
