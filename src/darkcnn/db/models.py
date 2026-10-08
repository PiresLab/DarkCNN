from __future__ import annotations

import time
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Job(Base):
    """Uma execução. Status: queued -> running -> done | error | cancelled."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # reservado p/ multiusuário
    mode: Mapped[str] = mapped_column(String(16))  # run | compile | narrate | render
    label: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    force: Mapped[bool] = mapped_column(Boolean, default=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # só o que o usuário mudou
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # Config completa do job
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    review: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_dir: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    preset_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created: Mapped[float] = mapped_column(Float, default=time.time)
    started: Mapped[float | None] = mapped_column(Float, nullable=True)
    ended: Mapped[float | None] = mapped_column(Float, nullable=True)


class JobLog(Base):
    __tablename__ = "job_logs"
    __table_args__ = (Index("ix_job_logs_job_seq", "job_id", "seq"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True,
                                    autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    line: Mapped[str] = mapped_column(Text)


class Gameplay(Base):
    """Vídeo de fundo enviado pelo usuário (o arquivo mora em <gameplays>/<filename>)."""

    __tablename__ = "gameplays"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(Text)
    filename: Mapped[str] = mapped_column(Text, unique=True)
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    size: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), default=0)
    thumb: Mapped[str | None] = mapped_column(Text, nullable=True)
    created: Mapped[float] = mapped_column(Float, default=time.time)


class Preset(Base):
    """Receita reutilizável: modo + formato + voz + gameplays... `spec` é validado por PresetSpec."""

    __tablename__ = "presets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(Text)
    spec: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created: Mapped[float] = mapped_column(Float, default=time.time)
    updated: Mapped[float] = mapped_column(Float, default=time.time)


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    preset_id: Mapped[str] = mapped_column(ForeignKey("presets.id", ondelete="CASCADE"), index=True)
    cron: Mapped[str] = mapped_column(String(64))  # 5 campos, ex.: "0 18 * * *"
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run: Mapped[float | None] = mapped_column(Float, nullable=True)
    created: Mapped[float] = mapped_column(Float, default=time.time)
