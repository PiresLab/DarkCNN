"""API do painel: HTTP em volta do mesmo pipeline da linha de comando.

Roda só na sua máquina (127.0.0.1 por padrão) e não tem login: é uma ferramenta local, não um serviço público.
"""
from __future__ import annotations

import json
import logging
import queue
import time
from pathlib import Path
from typing import Any, Iterator

import yaml
from pydantic import BaseModel, ValidationError

from .. import media
from ..config import Config, load_config
from .jobs import JobManager

log = logging.getLogger(__name__)

SELECTION = "selection.json"
SCRIPTS = "scripts.json"


class AppState:
    """Config lida do disco a cada pedido: salvar no painel vale para a próxima geração."""

    def __init__(self, config_path: Path | None, overrides: dict | None = None,
                 runners: dict | None = None):
        self.config_path = config_path or Path("config.yaml")
        # as flags da CLI chegam com None quando não foram passadas; None aqui quebraria o Config
        self.overrides = {k: v for k, v in (overrides or {}).items() if v is not None}
        self.jobs = JobManager(self.cfg(), runners)

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
    from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
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

    @app.get("/api/env")
    def env() -> dict:
        import os
        cfg = cfg_or_400()
        try:
            media.run(["ffmpeg", "-version"])
            ffmpeg = True
        except media.MediaError:
            ffmpeg = False
        games = 0
        if cfg.gameplay_dir and Path(cfg.gameplay_dir).is_dir():
            games = sum(1 for p in Path(cfg.gameplay_dir).rglob("*")
                        if p.is_file() and p.suffix.lower() in {".mp4", ".mkv", ".mov", ".webm", ".avi"})
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
            "api_key": bool(os.environ.get("GEMINI_API_KEY")),
            "gameplays": games,
            "config_path": str(st.config_path),
            "config_exists": st.config_path.exists(),
            "requests_today": int(today.get("requests", 0)),
            "daily_budget": cfg.daily_request_budget,
            "tokens_today": int(today.get("prompt_tokens", 0)) + int(today.get("output_tokens", 0)),
            "output_dir": str(cfg.output_dir),
        }

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
            job = st.jobs.start(body.mode, cfg, body.source, label, extra, body.force)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return job.summary()

    @app.get("/api/jobs/{jid}")
    def job_detail(jid: str, since: int = 0) -> dict:
        job = st.jobs.jobs.get(jid)
        if job is None:
            raise HTTPException(404, "execução não encontrada")
        return job.detail(since)

    @app.post("/api/jobs/{jid}/cancel")
    def cancel_job(jid: str) -> dict:
        if not st.jobs.cancel(jid):
            raise HTTPException(409, "a execução já terminou")
        return {"ok": True}

    @app.get("/api/jobs/{jid}/stream")
    def stream_job(jid: str) -> Any:
        job = st.jobs.jobs.get(jid)
        if job is None:
            raise HTTPException(404, "execução não encontrada")

        def events() -> Iterator[str]:
            sent = len(job.lines)
            for line in job.lines:
                yield _sse("line", line)
            if job.ended is not None:
                yield _sse("end", job.status)
                return
            q = st.jobs.subscribe(jid)
            try:
                while True:
                    try:
                        line = q.get(timeout=20)
                    except queue.Empty:
                        yield ": ping\n\n"
                        continue
                    if line is None:
                        yield _sse("end", job.status)
                        return
                    sent += 1
                    yield _sse("line", line)
            finally:
                st.jobs.unsubscribe(jid, q)

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

    static = Path(__file__).parent / "static"
    app.mount("/", StaticFiles(directory=static, html=True), name="static")
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
          overrides: dict | None = None, open_browser: bool = True) -> None:
    import uvicorn

    app = create_app(AppState(config_path, overrides))
    url = f"http://{host}:{port}/"
    print(f"\nDarkCNN Studio em {url}  (Ctrl+C para parar)\n")
    if open_browser:
        import threading
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")
