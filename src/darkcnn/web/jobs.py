"""Execuções do painel: cada geração roda numa thread, com log ao vivo e interrupção.

O painel não reimplementa nada: chama as mesmas funções do `pipeline`/`narrate` que a linha de comando usa.
O log é capturado por thread, então dois jobs ao mesmo tempo não misturam as linhas.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .. import media
from ..config import Config

MAX_LINES = 4000  # o log vive em memória; o arquivo completo fica no run.log do workspace
RUNS_FILE = "web_runs.json"  # lembra a fonte de cada saída, para o botão de re-renderizar


@dataclass
class Job:
    id: str
    mode: str  # run | compile | narrate | render
    label: str
    params: dict
    status: str = "running"  # running | done | error | cancelled
    created: float = field(default_factory=time.time)
    ended: float | None = None
    lines: list[str] = field(default_factory=list)
    review: str | None = None
    error: str | None = None
    thread_ident: int | None = None

    def summary(self) -> dict:
        return {"id": self.id, "mode": self.mode, "label": self.label, "status": self.status,
                "created": self.created, "ended": self.ended, "review": self.review,
                "error": self.error, "lines": len(self.lines), "params": self.params}

    def detail(self, since: int = 0) -> dict:
        return {**self.summary(), "log": self.lines[since:], "next": len(self.lines)}


class _ThreadLogHandler(logging.Handler):
    """Manda cada linha de log para o job cuja thread a emitiu."""

    def __init__(self, sink: Callable[[int, str], None]):
        super().__init__()
        self.sink = sink
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.sink(threading.get_ident(), self.format(record))
        except Exception:
            pass


class JobManager:
    def __init__(self, cfg: Config, runners: dict[str, Callable[..., Path]] | None = None):
        self.cfg = cfg
        self.jobs: dict[str, Job] = {}
        self.order: list[str] = []
        self._by_thread: dict[int, str] = {}
        self._subs: dict[str, list[queue.Queue]] = {}
        self._lock = threading.Lock()
        self._runners = runners or {}
        handler = _ThreadLogHandler(self._log_line)
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.INFO)

    # ---------------------------------------------------------------- log
    def _log_line(self, ident: int, line: str) -> None:
        with self._lock:
            jid = self._by_thread.get(ident)
            job = self.jobs.get(jid) if jid else None
            if job is None:
                return
            job.lines.append(line)
            if len(job.lines) > MAX_LINES:
                del job.lines[: len(job.lines) - MAX_LINES]
            subs = list(self._subs.get(job.id, []))
        for q in subs:
            q.put(line)

    def subscribe(self, jid: str) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subs.setdefault(jid, []).append(q)
        return q

    def unsubscribe(self, jid: str, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs.get(jid, []):
                self._subs[jid].remove(q)

    # ---------------------------------------------------------------- execução
    def runners(self) -> dict[str, Callable[..., Path]]:
        if not self._runners:
            from .. import narrate as narratelib, pipeline
            self._runners = {
                "run": lambda inp, cfg, force: pipeline.run_pipeline(inp, cfg, force=force),
                "compile": lambda inp, cfg, force: pipeline.run_compile(inp, cfg, force=force),
                "render": lambda inp, cfg, force: pipeline.render_from_selection(inp, cfg),
                "narrate": lambda inp, cfg, force: narratelib.run_narrate(cfg, force=force),
            }
        return self._runners

    def start(self, mode: str, cfg: Config, source: str | None, label: str, params: dict,
              force: bool = False) -> Job:
        runner = self.runners().get(mode)
        if runner is None:
            raise ValueError(f"modo desconhecido: {mode}")
        if mode != "narrate" and not source:
            raise ValueError("informe o link ou o arquivo de origem")
        job = Job(id=uuid.uuid4().hex[:12], mode=mode, label=label, params=params)
        with self._lock:
            self.jobs[job.id] = job
            self.order.insert(0, job.id)
        threading.Thread(target=self._run, args=(job, runner, cfg, source, force), daemon=True).start()
        return job

    def _run(self, job: Job, runner: Callable[..., Path], cfg: Config, source: str | None,
             force: bool) -> None:
        ident = threading.get_ident()
        job.thread_ident = ident
        with self._lock:
            self._by_thread[ident] = job.id
        try:
            review = runner(source or "", cfg, force)
            job.review = str(review)
            job.status = "done"
            self._remember(cfg, Path(review).parent, job, source)
        except BaseException as e:  # noqa: BLE001 - a falha é do job, não do servidor
            job.status = "cancelled" if job.status == "cancelling" else "error"
            job.error = f"{type(e).__name__}: {e}"
            self._log_line(ident, f"ERRO {job.error}")
        finally:
            job.ended = time.time()
            with self._lock:
                self._by_thread.pop(ident, None)
                subs = list(self._subs.get(job.id, []))
            for q in subs:
                q.put(None)  # fecha o stream

    def cancel(self, jid: str) -> bool:
        job = self.jobs.get(jid)
        if job is None or job.ended is not None:
            return False
        job.status = "cancelling"
        self._log_line(job.thread_ident or 0, "AVISO interrupção pedida pelo painel")
        return media.kill_thread_process(job.thread_ident or 0) or True

    # ---------------------------------------------------------------- memória das saídas
    def _remember(self, cfg: Config, out_dir: Path, job: Job, source: str | None) -> None:
        """Guarda a fonte de cada pasta de saída: sem isso o painel não sabe o que re-renderizar."""
        path = Path(cfg.workspace_dir) / RUNS_FILE
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except ValueError:
            data = {}
        data[str(out_dir)] = {"mode": job.mode, "source": source, "params": job.params,
                              "label": job.label, "at": time.time()}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    def remembered(self, out_dir: Path) -> dict[str, Any]:
        path = Path(self.cfg.workspace_dir) / RUNS_FILE
        try:
            return json.loads(path.read_text(encoding="utf-8")).get(str(out_dir), {})
        except (OSError, ValueError):
            return {}

    def list(self, limit: int = 30) -> list[dict]:
        with self._lock:
            ids = self.order[:limit]
        return [self.jobs[i].summary() for i in ids if i in self.jobs]
