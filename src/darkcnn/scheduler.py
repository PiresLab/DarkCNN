"""Agendamento: dispara presets pelo cron. Roda dentro do worker (um único processo consome a fila)."""
from __future__ import annotations

import datetime as dt
import logging
import re
import threading
import time
from typing import Callable

from croniter import croniter
from sqlalchemy import select

from .db import Database, Job, Schedule

log = logging.getLogger(__name__)


MAX_LEN = 64
MIN_EVERY_MIN = 5
_EVERY = re.compile(r"^@every\s+(\d+)\s*([mh])$", re.IGNORECASE)


def _every_s(part: str) -> float | None:
    """`@every 90m` / `@every 6h` -> segundos entre execuções (sem relação com o relógio de parede)."""
    m = _EVERY.match(part.strip())
    if not m:
        return None
    return int(m.group(1)) * (60 if m.group(2).lower() == "m" else 3600)


def _parts(expr: str) -> list[str]:
    return [p.strip() for p in expr.split(";") if p.strip()]


def validate_cron(expr: str) -> None:
    """Aceita cron de 5 campos, `@every Nm|Nh`, ou vários separados por `;` (vale o que disparar primeiro)."""
    if len(expr) > MAX_LEN:
        raise ValueError(f"agenda longa demais ({len(expr)} caracteres; máximo {MAX_LEN}): use menos horários")
    parts = _parts(expr)
    if not parts:
        raise ValueError("agenda vazia")
    for part in parts:
        every = _every_s(part)
        if every is not None:
            if every < MIN_EVERY_MIN * 60:
                raise ValueError(f"intervalo mínimo é {MIN_EVERY_MIN} minutos")
        elif len(part.split()) != 5 or not croniter.is_valid(part):
            raise ValueError(f"agenda inválida: {part!r} (use 5 campos, ex.: 0 18 * * *, ou @every 90m)")


def next_fire(expr: str, base: float) -> float:
    fires = []
    for part in _parts(expr):
        every = _every_s(part)
        if every is not None:
            fires.append(base + every)
        else:  # cron vale no fuso local do servidor (TZ); um float cru seria lido como UTC
            start = dt.datetime.fromtimestamp(base).astimezone()
            fires.append(croniter(part, start).get_next(dt.datetime).timestamp())
    return min(fires)


def is_due(expr: str, last: float | None, created: float, now: float) -> bool:
    """Devida se o próximo disparo depois da última execução (ou da criação) já passou. Perdidos viram 1 só."""
    return next_fire(expr, last if last is not None else created) <= now


class Scheduler:
    def __init__(self, db: Database, enqueue: Callable[[str], dict], tick_s: float = 30.0,
                 now: Callable[[], float] = time.time):
        self.db, self.enqueue, self.tick_s, self._now = db, enqueue, tick_s, now
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="darkcnn-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - o agendador nunca pode morrer por um preset quebrado
                log.exception("falha no agendador")
            self._stop.wait(self.tick_s)

    def tick(self) -> list[str]:
        """Enfileira os presets devidos. Devolve os ids de preset disparados."""
        now = self._now()
        fired: list[str] = []
        with self.db.session() as s:
            schedules = list(s.scalars(select(Schedule).where(Schedule.enabled.is_(True))).all())
        for sch in schedules:
            try:
                if not is_due(sch.cron, sch.last_run, sch.created, now):
                    continue
            except (ValueError, KeyError):
                continue
            with self.db.session() as s:
                busy = s.scalars(select(Job.id).where(Job.preset_id == sch.preset_id,
                                                      Job.status.in_(("queued", "running"))).limit(1)).first()
                row = s.get(Schedule, sch.id)
                if row is not None:
                    row.last_run = now  # marca mesmo se pulou: não acumula execuções atrasadas
            if busy:
                log.info("preset %s ainda em execução; disparo pulado", sch.preset_id)
                continue
            try:
                self.enqueue(sch.preset_id)
                fired.append(sch.preset_id)
            except Exception:  # noqa: BLE001
                log.exception("não consegui enfileirar o preset %s", sch.preset_id)
        return fired
