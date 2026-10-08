"""API do painel: HTTP em volta do mesmo pipeline da linha de comando.

Roda só na sua máquina (127.0.0.1 por padrão) e não tem login: é uma ferramenta local, não um serviço público.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Iterator

import yaml
from pydantic import BaseModel, ValidationError

from .. import media
from .. import storage
from ..config import Config, load_config
from ..db import Database
from .jobs import JobManager

log = logging.getLogger(__name__)

SELECTION = "selection.json"
SCRIPTS = "scripts.json"


class AppState:
    """Config lida do disco a cada pedido: salvar no painel vale para a próxima geração."""

    def __init__(self, config_path: Path | None, overrides: dict | None = None,
                 runners: dict | None = None, embedded_worker: bool = True,
                 concurrency: int | None = None, database_url: str | None = None):
        self.config_path = config_path or storage.default_config_path()
        # as flags da CLI chegam com None quando não foram passadas; None aqui quebraria o Config
        self.overrides = {k: v for k, v in (overrides or {}).items() if v is not None}
        self.db = Database(database_url or storage.default_database_url(self.cfg().workspace_dir))
        self.db.init()
        try:  # vídeos já presentes na pasta (ex.: ./gameplays do uso antigo) entram na biblioteca
            from .. import gameplays as gp
            gp.sync_folder(self.db, self.gameplay_dir())
        except Exception:  # noqa: BLE001 - sem ffprobe/pasta o painel ainda deve abrir
            log.warning("não consegui varrer a pasta de gameplays", exc_info=True)
        self.jobs = JobManager(self.db, runners, embedded_worker=embedded_worker, concurrency=concurrency,
                               base_config=self.saved if embedded_worker else None,
                               gameplay_dir=self.gameplay_dir)

    def gameplay_dir(self) -> Path:
        """Onde a biblioteca de gameplays mora: o configurado, o volume Docker ou ./gameplays."""
        try:
            cfg = self.cfg()
        except (ValidationError, ValueError, yaml.YAMLError):
            return Path("gameplays")
        return Path(cfg.gameplay_dir) if cfg.gameplay_dir else Path(cfg.workspace_dir).parent / "gameplays"

    def cfg(self) -> Config:
        path = self.config_path if self.config_path.exists() else None
        return load_config(path, self.overrides)

    def saved(self) -> dict:
        if not self.config_path.exists():
            return {}
        return yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}

    def save(self, values: dict) -> Config:
        merged = {**self.saved(), **values}
        merged = {k: v for k, v in merged.items() if v is not None}
        cfg = Config(**merged)  # extra="forbid": chave desconhecida é recusada aqui
        self.config_path.write_text(yaml.safe_dump(merged, allow_unicode=True, sort_keys=False),
                                    encoding="utf-8")
        return cfg


# ---------------------------------------------------------------- leitura das saídas
def review_dirs(out_root: Path) -> list[Path]:
    if not out_root.exists():
        return []
    found = {p.parent for p in out_root.rglob(SELECTION)} | {p.parent for p in out_root.rglob(SCRIPTS)}
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def _load(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def read_review(out_root: Path, folder: Path, extra: dict | None = None) -> dict:
    """Uma pasta de saída vira um item de revisão: cortes/compilado (selection.json) ou narração (scripts.json)."""
    rid = folder.relative_to(out_root).as_posix()
    narration = (folder / SCRIPTS).exists() and not (folder / SELECTION).exists()
    items = _load(folder / (SCRIPTS if narration else SELECTION))
    compilation = any(f.name.startswith("compilado") for f in folder.glob("compilado*.mp4"))
    for i, it in enumerate(items, 1):
        it.setdefault("rank", i)
        it.setdefault("status", "pending")
    return {
        "id": rid,
        "kind": "narration" if narration else ("compilation" if compilation else "cuts"),
        "title": (extra or {}).get("label") or folder.name,
        "updated": folder.stat().st_mtime,
        "source": (extra or {}).get("source"),
        "has_review_md": (folder / "review.md").exists(),
        "rejected": _load(folder / "rejected.json"),
        "items": items,
    }


def safe_under(root: Path, rel: str) -> Path:
    """Caminho dentro de `root` ou erro: o painel nunca serve arquivo de fora da pasta de saída."""
    root = root.resolve()
    target = (root / rel).resolve()
    if root != target and root not in target.parents:
        raise ValueError("caminho fora da pasta de saída")
    return target


# ---------------------------------------------------------------- corpo dos pedidos
class JobRequest(BaseModel):
    mode: str
    source: str | None = None
    label: str | None = None
    force: bool = False
    overrides: dict[str, Any] = {}


class ConfigRequest(BaseModel):
    values: dict[str, Any]


class KeyRequest(BaseModel):
    key: str


class ItemPatch(BaseModel):
    status: str | None = None
    start: float | None = None
    end: float | None = None
    title: str | None = None
    hook_text: str | None = None
    context: str | None = None


class SampleRequest(BaseModel):
    text: str = "Testando a voz do painel, em português do Brasil."
    voice: str | None = None


def create_app(state: AppState | None = None) -> Any:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, StreamingResponse
    from fastapi.staticfiles import StaticFiles

    st = state or AppState(None)
    app = FastAPI(title="DarkCNN Studio", docs_url=None, redoc_url=None)
    app.state.dc = st

    def cfg_or_400() -> Config:
        try:
            return st.cfg()
        except (ValidationError, ValueError, yaml.YAMLError) as e:
            raise HTTPException(400, f"config.yaml inválido: {e}") from e

    def saved_or_400() -> dict:
        try:
            return st.saved()
        except (ValueError, yaml.YAMLError) as e:  # arquivo editado à mão e quebrado
            raise HTTPException(400, f"config.yaml inválido: {e}") from e

    @app.get("/api/health")
    def health() -> dict:
        from sqlalchemy import text
        with st.db.session() as sess:
            sess.execute(text("SELECT 1"))
        return {"ok": True}

    @app.get("/api/env")
    def env() -> dict:
        import os
        cfg = cfg_or_400()
        try:
            media.run(["ffmpeg", "-version"])
            ffmpeg = True
        except media.MediaError:
            ffmpeg = False
        from .. import gameplays as gp
        games = len(gp.list_all(st.db))
        usage = {}
        upath = Path(cfg.workspace_dir) / "usage.json"
        if upath.exists():
            try:
                usage = json.loads(upath.read_text(encoding="utf-8"))
            except ValueError:
                usage = {}
        today = usage.get(time.strftime("%Y-%m-%d"), {})
        return {
            "ffmpeg": ffmpeg,
            "whisper_model": cfg.whisper_model,
            "api_key": bool(os.environ.get("GEMINI_API_KEY")) or storage.secret_file().exists(),
            "gameplays": games,
            "config_path": str(st.config_path),
            "config_exists": st.config_path.exists(),
            "requests_today": int(today.get("requests", 0)),
            "daily_budget": cfg.daily_request_budget,
            "tokens_today": int(today.get("prompt_tokens", 0)) + int(today.get("output_tokens", 0)),
            "output_dir": str(cfg.output_dir),
        }

    @app.put("/api/secrets/gemini")
    def put_gemini_key(body: KeyRequest) -> dict:
        key = body.key.strip()
        if len(key) < 20 or any(c.isspace() for c in key):
            raise HTTPException(400, "essa chave não parece válida")
        path = storage.secret_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(key, encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        storage.apply_secrets()
        return {"ok": True}

    @app.delete("/api/secrets/gemini")
    def delete_gemini_key() -> dict:
        storage.secret_file().unlink(missing_ok=True)
        return {"ok": True}

    @app.get("/api/config")
    def get_config() -> dict:
        return {"saved": saved_or_400(), "effective": cfg_or_400().model_dump(mode="json"),
                "path": str(st.config_path)}

    @app.put("/api/config")
    def put_config(body: ConfigRequest) -> dict:
        try:
            cfg = st.save(body.values)
        except ValidationError as e:
            raise HTTPException(400, _pretty_errors(e)) from e
        return {"saved": st.saved(), "effective": cfg.model_dump(mode="json")}

    @app.get("/api/jobs")
    def list_jobs() -> dict:
        return {"jobs": st.jobs.list()}

    @app.post("/api/jobs")
    def start_job(body: JobRequest) -> dict:
        base = saved_or_400()
        extra = {k: v for k, v in body.overrides.items() if v is not None and v != ""}
        try:
            cfg = Config(**{**base, **st.overrides, **extra})
        except ValidationError as e:
            raise HTTPException(400, _pretty_errors(e)) from e
        label = body.label or body.source or cfg.narrate_format
        try:
            return st.jobs.enqueue(body.mode, cfg, body.source, label, extra, body.force)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/api/jobs/{jid}")
    def job_detail(jid: str, since: int = 0) -> dict:
        job = st.jobs.get(jid)
        if job is None:
            raise HTTPException(404, "execução não encontrada")
        lines, nxt = st.jobs.logs(jid, since)
        return {**job, "log": lines, "next": nxt}

    @app.post("/api/jobs/{jid}/cancel")
    def cancel_job(jid: str) -> dict:
        if not st.jobs.cancel(jid):
            raise HTTPException(409, "a execução já terminou")
        return {"ok": True}

    @app.get("/api/jobs/{jid}/stream")
    def stream_job(jid: str) -> Any:
        if st.jobs.get(jid) is None:
            raise HTTPException(404, "execução não encontrada")

        def events() -> Iterator[str]:
            seq, idle = 0, 0.0
            while True:
                lines, seq = st.jobs.logs(jid, seq)
                for line in lines:
                    yield _sse("line", line)
                job = st.jobs.get(jid)
                if job is None or job["status"] not in ("queued", "running"):
                    lines, seq = st.jobs.logs(jid, seq)  # o que chegou entre a leitura e o status
                    for line in lines:
                        yield _sse("line", line)
                    yield _sse("end", job["status"] if job else "error")
                    return
                idle = 0.0 if lines else idle + 0.5
                if idle >= 20:
                    yield ": ping\n\n"
                    idle = 0.0
                time.sleep(0.5)

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/reviews")
    def list_reviews() -> dict:
        cfg = cfg_or_400()
        root = Path(cfg.output_dir)
        out = []
        for folder in review_dirs(root):
            out.append(read_review(root, folder, st.jobs.remembered(folder)))
        return {"reviews": out, "output_dir": str(root)}

    @app.patch("/api/reviews/{rid:path}/items/{rank}")
    def patch_item(rid: str, rank: int, body: ItemPatch) -> dict:
        cfg = cfg_or_400()
        root = Path(cfg.output_dir)
        try:
            folder = safe_under(root, rid)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        path = folder / (SELECTION if (folder / SELECTION).exists() else SCRIPTS)
        items = _load(path)
        if not items:
            raise HTTPException(404, "revisão não encontrada")
        changes = {k: v for k, v in body.model_dump().items() if v is not None}
        hit = None
        for i, it in enumerate(items, 1):
            if int(it.get("rank", i)) == rank:
                it.update(changes)
                if "start" in changes or "end" in changes:
                    it["duration"] = round(float(it["end"]) - float(it["start"]), 3)
                hit = it
        if hit is None:
            raise HTTPException(404, f"item {rank} não existe nessa revisão")
        path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        return hit

    @app.get("/api/reviews/{rid:path}/markdown")
    def review_md(rid: str) -> Any:
        cfg = cfg_or_400()
        try:
            path = safe_under(Path(cfg.output_dir), rid) / "review.md"
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        if not path.exists():
            raise HTTPException(404, "esta revisão não tem review.md")
        return PlainTextResponse(path.read_text(encoding="utf-8"))

    @app.get("/api/media/{rel:path}")
    def get_media(rel: str) -> Any:
        cfg = cfg_or_400()
        try:
            path = safe_under(Path(cfg.output_dir), rel)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        if not path.is_file():
            raise HTTPException(404, "arquivo não encontrado")
        return FileResponse(path)

    @app.get("/api/voices")
    def voices() -> dict:
        from .. import tts
        cfg = cfg_or_400()
        return {"known": tts.KNOWN_VOICES, "voice": cfg.tts_voice, "pool": cfg.tts_voices_pool,
                "model": cfg.tts_model, "speed": cfg.tts_speed}

    @app.get("/api/voices/models")
    def voice_models() -> dict:
        from .. import tts
        try:
            return {"models": tts.list_tts_models()}
        except Exception as e:  # chave ausente, rede fora, API recusando
            raise HTTPException(400, f"{type(e).__name__}: {e}") from e

    @app.post("/api/voices/sample")
    def voice_sample(body: SampleRequest) -> dict:
        from .. import tts
        cfg = cfg_or_400()
        voice = body.voice or cfg.tts_voice
        dest = Path(cfg.output_dir) / "voices" / f"{voice}.wav"
        t0 = time.perf_counter()
        try:
            tts.make_backend(cfg).say(body.text, voice, dest)
        except tts.TTSError as e:
            raise HTTPException(400, str(e)) from e
        return {"voice": voice, "file": dest.relative_to(Path(cfg.output_dir)).as_posix(),
                "seconds": round(media.probe_duration(dest), 2), "took": round(time.perf_counter() - t0, 1)}

    from . import routes_auto
    routes_auto.register(app, st)

    static = Path(__file__).parent / "static"
    if (static / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> Any:
        if path.startswith("api/"):
            raise HTTPException(404, "rota não encontrada")
        target = (static / path).resolve()
        if path and static.resolve() in target.parents and target.is_file():
            return FileResponse(target)
        index = static / "index.html"
        if index.is_file():
            return FileResponse(index)
        return HTMLResponse("<h1>DarkCNN Studio</h1><p>Interface não compilada. Rode "
                            "<code>npm install &amp;&amp; npm run build</code> em <code>src/darkcnn/web/ui</code>.</p>",
                            status_code=200)
    return app


def _sse(event: str, data: str) -> str:
    body = "\n".join(f"data: {part}" for part in str(data).split("\n"))
    return f"event: {event}\n{body}\n\n"


def _pretty_errors(e: ValidationError) -> str:
    out = []
    for err in e.errors():
        field = ".".join(str(p) for p in err.get("loc", ())) or "(raiz)"
        msg = err.get("msg", "")
        if err.get("type") == "extra_forbidden":
            msg = "campo desconhecido: confira o nome (o config.yaml não aceita chave extra)"
        out.append(f"{field}: {msg}")
    return "; ".join(out)


def serve(config_path: Path | None, host: str = "127.0.0.1", port: int = 8765,
          overrides: dict | None = None, open_browser: bool = True, embedded_worker: bool = True) -> None:
    import uvicorn

    app = create_app(AppState(config_path, overrides, embedded_worker=embedded_worker))
    url = f"http://{host}:{port}/"
    print(f"\nDarkCNN Studio em {url}  (Ctrl+C para parar)\n")
    if open_browser:
        import threading
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")


def run_worker(config_path: Path | None, concurrency: int | None = None) -> int:
    """Processo só de execução: consome a fila do banco até Ctrl+C / SIGTERM."""
    import signal
    import threading

    from .jobs import JobStore, Worker

    cfg_path = config_path or storage.default_config_path()
    cfg = load_config(cfg_path if cfg_path.exists() else None)
    db = Database(storage.default_database_url(cfg.workspace_dir))
    db.init()
    from ..scheduler import Scheduler

    store = JobStore(db)

    def base() -> dict:
        return (yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}) if cfg_path.exists() else {}

    def gdir() -> Path:
        c = load_config(cfg_path if cfg_path.exists() else None)
        return Path(c.gameplay_dir) if c.gameplay_dir else Path(c.workspace_dir).parent / "gameplays"

    scheduler = Scheduler(db, lambda pid: store.enqueue_preset(pid, base(), gdir()))
    worker = Worker(store, concurrency=concurrency, scheduler=scheduler)
    worker.start()
    print("worker DarkCNN rodando (Ctrl+C para parar)")
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    stop.wait()
    worker.stop()
    return 0
