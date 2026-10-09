"""Presets: receitas reutilizáveis (manual ou automáticas) que viram uma execução."""
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from . import gameplays
from .config import Config
from .db import Database, Preset, Schedule


class TikTokSpec(BaseModel):
    """Postar no TikTok ao terminar de gerar."""
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    account: str | None = None
    visibility: Literal["public", "private"] = "public"
    ai_label: bool = True  # rotula como "gerado por IA" (voz sintética)
    caption_ai: bool = True  # legenda e hashtags escritas pelo Gemini; senão, só o título
    delay_min: int = Field(0, ge=0, le=14400)  # espera antes do 1º post (0 = na hora; mínimo prático 15)
    stagger_min: int = Field(60, ge=15, le=1440)  # intervalo entre os vídeos de uma mesma execução
    allow_comment: bool = True
    allow_duet: bool = False
    allow_stitch: bool = False
    allow_content_reuse: bool = True
    allow_ai_remix: bool = True

    @model_validator(mode="after")
    def _check(self) -> "TikTokSpec":
        if self.enabled and not self.account:
            raise ValueError("escolha a conta do TikTok em que a automação vai postar")
        return self


class PresetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["narrate", "run", "compile"] = "narrate"  # run = cortes de um vídeo
    # --- conteúdo ---
    narrate_format: str = "curiosidade"
    count: int = Field(1, ge=1, le=10)  # narração: quantos vídeos por execução
    clips: int | None = Field(None, ge=1, le=12)  # cortes / momentos do top; vazio = o valor de Configurações
    target_s: float = Field(45.0, gt=5)
    niche: str | None = None  # dica livre para a IA: "ciência e espaço", "futebol", "dinheiro"...
    auto_topic: bool = True  # a IA propõe o tema a cada execução
    topic: str | None = None  # tema fixo (se auto_topic = False)
    theme: str | None = None  # compilado "Top N": o tema (vazio + auto_topic = a IA sugere)
    # --- fonte do vídeo (cortes/compilado) ---
    auto_source: bool = True  # a IA procura o vídeo no YouTube
    source: str | None = None  # link/arquivo fixo (se auto_source = False)
    profile: Literal["talk", "visual"] = "talk"
    # --- voz e fundo ---
    tts_voice: str | None = None
    tts_voices_pool: list[str] = []
    tts_speed: float | None = Field(None, ge=0.8, le=1.5)
    gameplay_ids: list[str] = []  # vazio = qualquer gameplay da biblioteca
    # --- qualquer outro campo da Config (avançado), validado na hora de rodar ---
    overrides: dict[str, Any] = {}
    tiktok: TikTokSpec | None = None

    @model_validator(mode="after")
    def _check(self) -> "PresetSpec":
        if self.mode in ("run", "compile") and not self.auto_source and not self.source:
            raise ValueError("informe o link/arquivo do vídeo ou deixe a IA procurar (auto_source)")
        if self.mode in ("run", "compile") and self.auto_source and not self.auto_topic                 and not (self.theme or self.topic):
            raise ValueError("para a IA procurar o vídeo sem propor o tema, informe o tema")
        if self.mode == "compile" and not self.auto_topic and not self.theme:
            raise ValueError("informe o tema do compilado ou deixe a IA sugerir (auto_topic)")
        return self


def schedule_view(s: Schedule) -> dict:
    from .scheduler import next_fire
    try:
        nxt = next_fire(s.cron, s.last_run if s.last_run is not None else s.created) if s.enabled else None
    except (ValueError, KeyError):
        nxt = None
    return {"id": s.id, "preset_id": s.preset_id, "cron": s.cron, "enabled": s.enabled, "last_run": s.last_run,
            "next_run": nxt}


def view(p: Preset, schedule: Schedule | None = None) -> dict:
    return {"id": p.id, "name": p.name, "spec": p.spec, "created": p.created, "updated": p.updated,
            "schedule": schedule_view(schedule) if schedule else None}


def build_config(base: dict, spec: PresetSpec, gameplay_dir: Path | None, db: Database | None) -> Config:
    """Config salva + o que o preset define. O tema/fonte da IA entram depois, já na execução."""
    data: dict[str, Any] = dict(base)
    data.update({"narrate_format": spec.narrate_format, "count": spec.count, "target_s": spec.target_s,
                 "profile": spec.profile})
    if spec.mode in ("run", "compile") and spec.clips:
        data["clips_per_video"] = spec.clips
    if spec.tts_voice:
        data["tts_voice"] = spec.tts_voice
    if spec.tts_voices_pool:
        data["tts_voices_pool"] = spec.tts_voices_pool
    if spec.tts_speed:
        data["tts_speed"] = spec.tts_speed
    if spec.topic and not spec.auto_topic:
        data["topic"] = spec.topic
    if spec.theme and not spec.auto_topic:
        data["theme"] = spec.theme
    if gameplay_dir is not None:
        data["gameplay_dir"] = str(gameplay_dir)
        if db is not None and spec.mode == "narrate":
            files = gameplays.paths_for(db, gameplay_dir, spec.gameplay_ids)
            if spec.gameplay_ids and not files:
                raise ValueError("as gameplays escolhidas no preset não existem mais")
            if files:
                data["gameplay_files"] = [str(f) for f in files]
    data.update({k: v for k, v in spec.overrides.items() if v is not None and v != ""})
    return Config(**{k: v for k, v in data.items() if v is not None})


# ---------------------------------------------------------------- CRUD
def create(db: Database, name: str, spec: PresetSpec) -> Preset:
    p = Preset(id=uuid.uuid4().hex[:12], name=name.strip() or "Sem nome", spec=spec.model_dump(mode="json"))
    with db.session() as s:
        s.add(p)
    return p


def update(db: Database, pid: str, name: str | None, spec: PresetSpec | None) -> Preset | None:
    with db.session() as s:
        p = s.get(Preset, pid)
        if p is None:
            return None
        if name is not None:
            p.name = name.strip() or p.name
        if spec is not None:
            p.spec = spec.model_dump(mode="json")
        p.updated = time.time()
        return p


def get(db: Database, pid: str) -> Preset | None:
    with db.session() as s:
        return s.get(Preset, pid)


def list_all(db: Database) -> list[tuple[Preset, Schedule | None]]:
    with db.session() as s:
        presets = list(s.scalars(select(Preset).order_by(Preset.created.desc())).all())
        scheds = {x.preset_id: x for x in s.scalars(select(Schedule)).all()}
        return [(p, scheds.get(p.id)) for p in presets]


def delete(db: Database, pid: str) -> bool:
    with db.session() as s:
        s.query(Schedule).filter(Schedule.preset_id == pid).delete()
        p = s.get(Preset, pid)
        if p is None:
            return False
        s.delete(p)
    return True


def set_schedule(db: Database, pid: str, cron: str, enabled: bool) -> Schedule:
    from .scheduler import validate_cron
    validate_cron(cron)
    with db.session() as s:
        sch = s.scalars(select(Schedule).where(Schedule.preset_id == pid)).first()
        if sch is None:
            sch = Schedule(id=uuid.uuid4().hex[:12], preset_id=pid, cron=cron, enabled=enabled)
            s.add(sch)
        else:
            sch.cron, sch.enabled = cron, enabled
        return sch


def remove_schedule(db: Database, pid: str) -> bool:
    with db.session() as s:
        return bool(s.query(Schedule).filter(Schedule.preset_id == pid).delete())
