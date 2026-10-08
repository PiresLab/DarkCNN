"""Agendamento: dispara presets pelo cron. Roda dentro do worker (um único processo consome a fila)."""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from croniter import croniter
from sqlalchemy import select

from .db import Database, Job, Schedule

log = logging.getLogger(__name__)


def validate_cron(expr: str) -> None:
    if len(expr.split()) != 5 or not croniter.is_valid(expr):
        raise ValueError(f"agendamento inválido: {expr!r} (use 5 campos, ex.: 0 18 * * *)")


def next_fire(expr: str, base: float) -> float:
    return croniter(expr, base).get_next(float)


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
