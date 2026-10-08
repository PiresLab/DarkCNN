"""Contexto do job em execução na thread atual (os executores não recebem o id do job)."""
from __future__ import annotations

import threading
from typing import Callable

_local = threading.local()


def set_current(job_id: str | None, set_params: Callable[..., None] | None = None) -> None:
    _local.job_id, _local.set_params = job_id, set_params


def current_job_id() -> str | None:
    return getattr(_local, "job_id", None)


def record_params(**kv) -> None:
    """Anota dados no job atual (ex.: tema sorteado pela automação). Sem job, não faz nada."""
    fn = getattr(_local, "set_params", None)
    if fn and current_job_id():
        fn(current_job_id(), **kv)
