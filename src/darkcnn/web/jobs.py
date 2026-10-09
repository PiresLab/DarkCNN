"""Execuções: fila persistente no banco + worker.

O painel (API) só enfileira e lê; quem executa é o `Worker`, que pode rodar embutido no mesmo processo
(uso local) ou num processo/container à parte (`darkcnn worker`). Estado, log e histórico vivem no banco,
então sobrevivem a reinício. As funções chamadas são as mesmas do `pipeline`/`narrate` da linha de comando.
O log é capturado por thread, então dois jobs ao mesmo tempo não misturam as linhas.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import func, select, update

from .. import media, presets as presetlib, runctx, storage
from ..config import Config
from ..db import Database, Job, JobLog

log = logging.getLogger(__name__)

MODES = {"run", "compile", "render", "narrate", "auto", "post"}
ACTIVE = ("queued", "running")
ADHOC = "adhoc"


def default_runners(store: "JobStore | None" = None) -> dict[str, Callable[..., Path]]:
    from .. import autopilot, narrate as narratelib, pipeline

    def auto(preset_id: str, cfg: Config, force: bool) -> Path:
        assert store is not None
        if preset_id == ADHOC:  # geração avulsa: a receita viaja nos params do próprio job
            job = store.get(runctx.current_job_id() or "")
            raw = (job or {}).get("params", {}).get("spec")
            if not raw:
                raise ValueError("execução avulsa sem receita")
            spec, pid = presetlib.PresetSpec(**raw), None
        else:
            p = presetlib.get(store.db, preset_id)
            if p is None:
                raise ValueError("o preset desta execução foi apagado")
            spec, pid = presetlib.PresetSpec(**p.spec), preset_id
        return autopilot.run_auto(preset_id, cfg, force, spec=spec,
                                  history=store.recent_topics(pid), used_sources=store.used_sources())

    def post(source: str, cfg: Config, force: bool) -> Path:
        """Publica um vídeo no TikTok. As opções viajam nos params do job (`post`)."""
        from ..tiktok import post as tkpost
        job = store.get(runctx.current_job_id() or "") if store else None
        raw = (job or {}).get("params", {}).get("post")
        if not raw:
            raise ValueError("postagem sem opções")
        try:
            res = tkpost.post_video(storage.tiktok_dir(cfg.workspace_dir), Path(source), tkpost.PostOptions(**raw))
        except tkpost.PostError as e:
            if e.uncertain:
                runctx.record_params(uncertain=True)
            raise
        runctx.record_params(video_id=res.video_id, scheduled_for=res.scheduled_for)
        return Path(source)

    return {
        "auto": auto,
        "post": post,
        "run": lambda inp, cfg, force: pipeline.run_pipeline(inp, cfg, force=force),
        "compile": lambda inp, cfg, force: pipeline.run_compile(inp, cfg, force=force),
        "render": lambda inp, cfg, force: pipeline.render_from_selection(inp, cfg),
        "narrate": lambda inp, cfg, force: narratelib.run_narrate(cfg, force=force),
    }


def _summary(j: Job, lines: int = 0) -> dict:
    return {"id": j.id, "mode": j.mode, "label": j.label, "status": j.status, "created": j.created,
            "started": j.started, "ended": j.ended, "review": j.review, "error": j.error, "lines": lines,
            "params": j.params, "source": j.source, "preset_id": j.preset_id,
            "cancel_requested": j.cancel_requested}


class JobStore:
    """Tudo que toca a tabela de jobs. Usado pela API (enfileirar/ler) e pelo worker (executar)."""

    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------ API
    def enqueue(self, mode: str, cfg: Config, source: str | None, label: str, params: dict,
                force: bool = False, preset_id: str | None = None) -> dict:
        if mode not in MODES:
            raise ValueError(f"modo desconhecido: {mode}")
        if mode not in ("narrate", "auto") and not source:
            raise ValueError("informe o link ou o arquivo de origem")
        job = Job(id=uuid.uuid4().hex[:12], mode=mode, label=label, source=source, force=force,
                  params=params, config_snapshot=cfg.model_dump(mode="json"), status="queued",
                  preset_id=preset_id, created=time.time())
        with self.db.session() as s:
            s.add(job)
        return _summary(job)

    def get(self, jid: str) -> dict | None:
        with self.db.session() as s:
            j = s.get(Job, jid)
            if j is None:
                return None
            n = s.scalar(select(func.count()).select_from(JobLog).where(JobLog.job_id == jid)) or 0
            return _summary(j, n)

    def list(self, limit: int = 30) -> list[dict]:
        with self.db.session() as s:
            rows = s.scalars(select(Job).order_by(Job.created.desc()).limit(limit)).all()
            counts = dict(s.execute(select(JobLog.job_id, func.count()).where(
                JobLog.job_id.in_([r.id for r in rows])).group_by(JobLog.job_id)).all()) if rows else {}
            return [_summary(r, counts.get(r.id, 0)) for r in rows]

    def logs(self, jid: str, since: int = 0) -> tuple[list[str], int]:
        with self.db.session() as s:
            rows = s.scalars(select(JobLog.line).where(JobLog.job_id == jid, JobLog.seq >= since)
                             .order_by(JobLog.seq)).all()
        return list(rows), since + len(rows)

    def cancel(self, jid: str) -> bool:
        with self.db.session() as s:
            if s.execute(update(Job).where(Job.id == jid, Job.status == "queued")
                         .values(status="cancelled", cancel_requested=True, ended=time.time())).rowcount:
                return True
            return bool(s.execute(update(Job).where(Job.id == jid, Job.status == "running")
                                  .values(cancel_requested=True)).rowcount)

    def set_params(self, jid: str, **kv: Any) -> None:
        with self.db.session() as s:
            j = s.get(Job, jid)
            if j is not None:
                j.params = {**(j.params or {}), **kv}

    def recent_topics(self, preset_id: str | None, limit: int = 30) -> list[str]:
        """Temas já sorteados (do preset; sem preset, de qualquer execução automática)."""
        with self.db.session() as s:
            q = select(Job).where(Job.mode == "auto", Job.status != "cancelled")
            if preset_id:
                q = q.where(Job.preset_id == preset_id)
            rows = s.scalars(q.order_by(Job.created.desc()).limit(limit)).all()
            return [t for t in ((r.params or {}).get("topic") for r in rows) if t]

    def used_sources(self) -> set[str]:
        with self.db.session() as s:
            rows = s.scalars(select(Job).where(Job.mode.in_(("run", "compile", "auto")), Job.status == "done")).all()
            out = {r.source for r in rows if r.source and r.mode != "auto"}
            out |= {(r.params or {}).get("source") for r in rows}
            return {x for x in out if x}

    def enqueue_preset(self, preset_id: str, base: dict, gameplay_dir: Path | None = None,
                       force: bool = False) -> dict:
        """Enfileira uma execução automática do preset (botão 'rodar agora' ou agendamento)."""
        p = presetlib.get(self.db, preset_id)
        if p is None:
            raise ValueError("preset não encontrado")
        spec = presetlib.PresetSpec(**p.spec)
        cfg = presetlib.build_config(base, spec, gameplay_dir, self.db)
        return self.enqueue("auto", cfg, preset_id, p.name, {"preset": p.name}, force, preset_id=p.id)

    def enqueue_spec(self, spec: "presetlib.PresetSpec", base: dict, gameplay_dir: Path | None,
                     label: str) -> dict:
        """Geração avulsa feita pelo assistente 'Criar' (a receita não vira preset)."""
        cfg = presetlib.build_config(base, spec, gameplay_dir, self.db)
        return self.enqueue("auto", cfg, ADHOC, label or spec.narrate_format,
                            {"spec": spec.model_dump(mode="json")})

    def post_jobs(self, limit: int = 300) -> list[dict]:
        """Postagens no TikTok (mais recentes primeiro), no formato que a biblioteca usa."""
        with self.db.session() as s:
            rows = s.scalars(select(Job).where(Job.mode == "post").order_by(Job.created.desc()).limit(limit)).all()
            out = []
            for r in rows:
                p = r.params or {}
                out.append({"job_id": r.id, "status": r.status, "review_id": p.get("review_id"), "file": p.get("file"),
                            "account": (p.get("post") or {}).get("account"), "video_id": p.get("video_id"),
                            "scheduled_for": p.get("scheduled_for"), "uncertain": bool(p.get("uncertain")),
                            "error": r.error, "created": r.created, "ended": r.ended})
            return out

    def remembered(self, out_dir: Path) -> dict[str, Any]:
        """Fonte/rótulo da última execução que gerou essa pasta (alimenta o botão de re-renderizar)."""
        with self.db.session() as s:
            j = s.scalars(select(Job).where(Job.output_dir == str(out_dir), Job.status == "done",
                                            Job.mode != "post")
                          .order_by(Job.created.desc()).limit(1)).first()
            if j is None:
                return {}
            return {"mode": j.mode, "source": j.source, "params": j.params, "label": j.label, "at": j.ended}

    # ------------------------------------------------------------ worker
    def recover_orphans(self) -> int:
        """Jobs que ficaram 'running' porque o processo caiu viram erro."""
        with self.db.session() as s:
            return s.execute(update(Job).where(Job.status == "running").values(
                status="error", ended=time.time(), error="interrompido: o servidor reiniciou")).rowcount

    def claim(self) -> Job | None:
        """Pega o job queued mais antigo. UPDATE condicional: só um worker vence a disputa."""
        with self.db.session() as s:
            for jid in s.scalars(select(Job.id).where(Job.status == "queued")
                                 .order_by(Job.created).limit(5)).all():
                if s.execute(update(Job).where(Job.id == jid, Job.status == "queued")
                             .values(status="running", started=time.time())).rowcount:
                    s.commit()
                    return s.get(Job, jid)
        return None

    def append_log(self, jid: str, line: str) -> None:
        with self.db.session() as s:
            seq = s.scalar(select(func.count()).select_from(JobLog).where(JobLog.job_id == jid)) or 0
            s.add(JobLog(job_id=jid, seq=seq, line=line[:4000]))

    def cancel_requested(self, jid: str) -> bool:
        with self.db.session() as s:
            return bool(s.scalar(select(Job.cancel_requested).where(Job.id == jid)))

    def finish(self, jid: str, status: str, review: str | None = None, error: str | None = None) -> None:
        with self.db.session() as s:
            s.execute(update(Job).where(Job.id == jid).values(
                status=status, review=review, error=error, ended=time.time(),
                output_dir=str(Path(review).parent) if review else None))


class _ThreadLogHandler(logging.Handler):
    """Manda cada linha de log para o job cuja thread a emitiu."""

    def __init__(self) -> None:
        super().__init__()
        self.sinks: dict[int, Callable[[str], None]] = {}
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        sink = self.sinks.get(threading.get_ident())
        if sink is None:
            return
        try:
            sink(self.format(record))
        except Exception:
            pass


_HANDLER = _ThreadLogHandler()


class Worker:
    """Consome a fila. `concurrency` jobs ao mesmo tempo (Whisper/ffmpeg são pesados: o padrão é 1)."""

    def __init__(self, store: JobStore, runners: dict[str, Callable[..., Path]] | None = None,
                 concurrency: int | None = None, poll_s: float = 0.4,
                 scheduler: Any = None):
        self.store = store
        self.scheduler = scheduler
        self._runners = runners
        self.concurrency = max(1, concurrency or int(os.environ.get("DARKCNN_WORKER_CONCURRENCY", "1")))
        self.poll_s = poll_s
        self._stop = threading.Event()
        self._running: dict[str, int] = {}  # job id -> ident da thread
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def runners(self) -> dict[str, Callable[..., Path]]:
        if self._runners is None:
            self._runners = default_runners(self.store)
        return self._runners

    def start(self) -> None:
        root = logging.getLogger()
        if _HANDLER not in root.handlers:
            root.addHandler(_HANDLER)
        root.setLevel(logging.INFO)
        self.store.recover_orphans()
        self._thread = threading.Thread(target=self.loop, name="darkcnn-worker", daemon=True)
        self._thread.start()
        if self.scheduler is not None:
            self.scheduler.start()

    def stop(self) -> None:
        self._stop.set()
        if self.scheduler is not None:
            self.scheduler.stop()
        if self._thread:
            self._thread.join(timeout=5)

    def loop(self) -> None:
        while not self._stop.is_set():
            self._enforce_cancels()
            with self._lock:
                free = len(self._running) < self.concurrency
            job = self.store.claim() if free else None
            if job is None:
                self._stop.wait(self.poll_s)
                continue
            t = threading.Thread(target=self._run, args=(job,), daemon=True)
            with self._lock:
                self._running[job.id] = 0
            t.start()

    def _enforce_cancels(self) -> None:
        with self._lock:
            running = dict(self._running)
        for jid, ident in running.items():
            if ident and self.store.cancel_requested(jid):
                media.kill_thread_process(ident)

    def _run(self, job: Job) -> None:
        ident = threading.get_ident()
        with self._lock:
            self._running[job.id] = ident
        _HANDLER.sinks[ident] = lambda line: self.store.append_log(job.id, line)
        runctx.set_current(job.id, self.store.set_params)
        storage.apply_secrets()
        status, review, error = "done", None, None
        try:
            runner = self.runners().get(job.mode)
            if runner is None:
                raise ValueError(f"sem executor para o modo {job.mode}")
            cfg = Config(**job.config_snapshot)
            review = str(runner(job.source or "", cfg, job.force))
        except BaseException as e:  # noqa: BLE001 - a falha é do job, não do servidor
            status = "cancelled" if self.store.cancel_requested(job.id) else "error"
            error = f"{type(e).__name__}: {e}"
            self.store.append_log(job.id, f"ERRO {error}")
        finally:
            _HANDLER.sinks.pop(ident, None)
            runctx.set_current(None)
            with self._lock:
                self._running.pop(job.id, None)
        self.store.finish(job.id, status, review, error)
        if job.mode == "auto" and status == "done" and review:
            self._queue_tiktok(job, review)

    def _queue_tiktok(self, job: Job, review: str) -> None:
        """Automação com 'postar no TikTok' ligado: enfileira uma postagem por vídeo gerado."""
        try:
            raw = (job.params or {}).get("spec")
            if raw is None and job.preset_id:
                p = presetlib.get(self.store.db, job.preset_id)
                raw = p.spec if p else None
            if not raw or not (raw.get("tiktok") or {}).get("enabled"):
                return
            from ..tiktok import autopost
            autopost.enqueue_posts(self.store, Config(**job.config_snapshot), presetlib.PresetSpec(**raw), review,
                                   job.preset_id)
        except Exception:  # noqa: BLE001 - falhar em postar nunca desfaz o vídeo gerado
            log.exception("não consegui enfileirar as postagens do TikTok")


class JobManager:
    """Fachada usada pela API: fila (JobStore) + worker embutido opcional."""

    def __init__(self, db: Database, runners: dict[str, Callable[..., Path]] | None = None,
                 embedded_worker: bool = True, concurrency: int | None = None,
                 base_config: Callable[[], dict] | None = None, gameplay_dir: Callable[[], Path | None] | None = None):
        self.store = JobStore(db)
        scheduler = None
        if base_config is not None:
            from ..scheduler import Scheduler
            scheduler = Scheduler(db, lambda pid: self.store.enqueue_preset(
                pid, base_config(), gameplay_dir() if gameplay_dir else None))
        self.worker = Worker(self.store, runners, concurrency, scheduler=scheduler)
        if embedded_worker:
            self.worker.start()

    def close(self) -> None:
        self.worker.stop()

    def __getattr__(self, name: str) -> Any:  # enqueue/get/list/logs/cancel/remembered
        return getattr(self.store, name)
