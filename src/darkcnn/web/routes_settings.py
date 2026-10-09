"""Rotas de configurações: modelos, marca d'água e prévia de agenda."""
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .. import media, models as modelslib, scheduler

WM_EXTS = {".png", ".webp", ".jpg", ".jpeg"}
MAX_WM_BYTES = 10 * 1024 * 1024


class PreviewBody(BaseModel):
    cron: str
    count: int = 3


def _wm_dir(st: Any) -> Path:
    return st.assets_dir()


def _current(st: Any) -> Path | None:
    p = (st.saved().get("watermark") or {}).get("path")
    return Path(p) if p else None


def _dims(path: Path) -> tuple[int, int]:
    out = media.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                     "stream=width,height", "-of", "csv=p=0", path]).stdout.strip()
    w, h = (int(x) for x in out.split(",")[:2])
    if w <= 0 or h <= 0:  # ffprobe sai com 0 em arquivo que não é imagem
        raise ValueError("sem dimensões")
    return w, h


def register(app: Any, st: Any) -> None:
    from fastapi import File, HTTPException, UploadFile
    from fastapi.responses import FileResponse

    @app.get("/api/models")
    def get_models(refresh: bool = False) -> dict:
        return modelslib.list_models(refresh=refresh)

    @app.post("/api/schedule/preview")
    def schedule_preview(body: PreviewBody) -> dict:
        import time
        try:
            scheduler.validate_cron(body.cron.strip())
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        base, out = time.time(), []
        for _ in range(max(1, min(body.count, 10))):
            base = scheduler.next_fire(body.cron.strip(), base)
            out.append(base)
        return {"next": out}

    # ---------------------------------------------------------------- marca d'água
    @app.get("/api/watermark")
    def get_watermark() -> dict:
        p = _current(st)
        if p is None or not p.is_file():
            return {"exists": False}
        w, h = _dims(p)
        return {"exists": True, "width": w, "height": h, "version": int(p.stat().st_mtime)}

    @app.get("/api/watermark/file")
    def watermark_file() -> Any:
        p = _current(st)
        if p is None or not p.is_file():
            raise HTTPException(404, "sem marca d'água")
        return FileResponse(p)

    @app.post("/api/watermark")
    def upload_watermark(file: UploadFile = File(...)) -> dict:
        ext = Path(file.filename or "").suffix.lower()
        if ext not in WM_EXTS:
            raise HTTPException(400, "use uma imagem PNG, WEBP ou JPG (PNG com fundo transparente é o ideal)")
        folder = _wm_dir(st)
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / f"watermark.upload{ext}"
        size = 0
        try:
            with tmp.open("wb") as out:
                while chunk := file.file.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_WM_BYTES:
                        raise HTTPException(400, "imagem maior que 10 MB")
                    out.write(chunk)
            try:
                w, h = _dims(tmp)
            except (media.MediaError, ValueError) as e:
                raise HTTPException(400, "o arquivo não é uma imagem válida") from e
            dest = folder / f"watermark{ext}"
            for old in folder.glob("watermark.*"):
                if old != tmp:
                    old.unlink(missing_ok=True)
            tmp.replace(dest)
        finally:
            tmp.unlink(missing_ok=True)
        wm = {**(st.saved().get("watermark") or {}), "path": str(dest)}
        st.save({"watermark": wm})
        return {"exists": True, "width": w, "height": h, "version": int(dest.stat().st_mtime)}

    @app.delete("/api/watermark")
    def delete_watermark() -> dict:
        p = _current(st)
        if p is not None:
            p.unlink(missing_ok=True)
        wm = {k: v for k, v in (st.saved().get("watermark") or {}).items() if k != "path"}
        st.save({"watermark": wm})
        return {"ok": True}
