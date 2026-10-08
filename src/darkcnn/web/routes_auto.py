"""Rotas de gameplays, presets e agendamentos."""

from typing import Any

from pydantic import BaseModel, ValidationError

from .. import gameplays as gp, presets as pr
from ..storage import VIDEO_EXTS  # noqa: F401  (documenta o que o upload aceita)


class PresetBody(BaseModel):
    name: str
    spec: dict[str, Any] = {}


class RunBody(BaseModel):
    name: str = ""
    spec: dict[str, Any] = {}


class ScheduleBody(BaseModel):
    cron: str
    enabled: bool = True


def register(app: Any, st: Any) -> None:
    from fastapi import File, HTTPException, UploadFile
    from fastapi.responses import FileResponse

    def spec_or_400(raw: dict) -> pr.PresetSpec:
        try:
            return pr.PresetSpec(**raw)
        except ValidationError as e:
            msg = "; ".join(f"{'.'.join(str(x) for x in err['loc']) or 'preset'}: {err['msg']}" for err in e.errors())
            raise HTTPException(400, msg) from e

    # ---------------------------------------------------------------- gameplays
    @app.get("/api/gameplays")
    def list_gameplays() -> dict:
        folder = st.gameplay_dir()
        gp.sync_folder(st.db, folder)
        return {"gameplays": [gp.view(g) for g in gp.list_all(st.db)], "folder": str(folder)}

    @app.post("/api/gameplays")
    def upload_gameplay(file: UploadFile = File(...)) -> dict:
        try:
            g = gp.add_upload(st.db, st.gameplay_dir(), file.filename or "gameplay.mp4", file.file)
        except gp.GameplayError as e:
            raise HTTPException(400, str(e)) from e
        return gp.view(g)

    @app.delete("/api/gameplays/{gid}")
    def delete_gameplay(gid: str) -> dict:
        if not gp.remove(st.db, st.gameplay_dir(), gid):
            raise HTTPException(404, "gameplay não encontrada")
        return {"ok": True}

    @app.get("/api/gameplays/{gid}/thumb")
    def gameplay_thumb(gid: str) -> Any:
        g = gp.get(st.db, gid)
        path = st.gameplay_dir() / gp.THUMBS / g.thumb if g and g.thumb else None
        if path is None or not path.is_file():
            raise HTTPException(404, "sem miniatura")
        return FileResponse(path)

    @app.get("/api/gameplays/{gid}/file")
    def gameplay_file(gid: str) -> Any:
        g = gp.get(st.db, gid)
        path = st.gameplay_dir() / g.filename if g else None
        if path is None or not path.is_file():
            raise HTTPException(404, "arquivo não encontrado")
        return FileResponse(path)

    # ---------------------------------------------------------------- presets
    @app.get("/api/presets")
    def list_presets() -> dict:
        return {"presets": [pr.view(p, s) for p, s in pr.list_all(st.db)]}

    @app.post("/api/presets")
    def create_preset(body: PresetBody) -> dict:
        return pr.view(pr.create(st.db, body.name, spec_or_400(body.spec)))

    @app.put("/api/presets/{pid}")
    def update_preset(pid: str, body: PresetBody) -> dict:
        p = pr.update(st.db, pid, body.name, spec_or_400(body.spec))
        if p is None:
            raise HTTPException(404, "preset não encontrado")
        return pr.view(p)

    @app.delete("/api/presets/{pid}")
    def delete_preset(pid: str) -> dict:
        if not pr.delete(st.db, pid):
            raise HTTPException(404, "preset não encontrado")
        return {"ok": True}

    @app.post("/api/presets/{pid}/run")
    def run_preset(pid: str) -> dict:
        try:
            return st.jobs.enqueue_preset(pid, st.saved(), st.gameplay_dir())
        except (ValueError, ValidationError) as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/runs")
    def run_adhoc(body: RunBody) -> dict:
        spec = spec_or_400(body.spec)
        try:
            return st.jobs.enqueue_spec(spec, st.saved(), st.gameplay_dir(), body.name.strip())
        except (ValueError, ValidationError) as e:
            raise HTTPException(400, str(e)) from e

    @app.put("/api/presets/{pid}/schedule")
    def put_schedule(pid: str, body: ScheduleBody) -> dict:
        if pr.get(st.db, pid) is None:
            raise HTTPException(404, "preset não encontrado")
        try:
            return pr.schedule_view(pr.set_schedule(st.db, pid, body.cron.strip(), body.enabled))
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.delete("/api/presets/{pid}/schedule")
    def delete_schedule(pid: str) -> dict:
        pr.remove_schedule(st.db, pid)
        return {"ok": True}
