"""Biblioteca de gameplays: upload, validação (ffprobe), miniatura e remoção."""
from __future__ import annotations

import logging
import re
import time
import uuid
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import select

from . import media
from .db import Database, Gameplay
from .storage import VIDEO_EXTS

log = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 4 * 1024**3  # 4 GB
THUMBS = ".thumbs"


class GameplayError(ValueError):
    pass


def _safe_stem(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(name).stem).strip("-.") or "gameplay"
    return stem[:60]


def view(g: Gameplay) -> dict:
    return {"id": g.id, "name": g.name, "filename": g.filename, "duration": round(g.duration, 1),
            "size": g.size, "has_thumb": bool(g.thumb), "created": g.created}


def add_upload(db: Database, folder: Path, original_name: str, stream: BinaryIO) -> Gameplay:
    ext = Path(original_name).suffix.lower()
    if ext not in VIDEO_EXTS:
        raise GameplayError(f"formato não aceito ({ext or 'sem extensão'}); use {', '.join(sorted(VIDEO_EXTS))}")
    folder.mkdir(parents=True, exist_ok=True)
    gid = uuid.uuid4().hex[:12]
    dest = folder / f"{_safe_stem(original_name)}-{gid[:6]}{ext}"
    tmp = dest.with_suffix(dest.suffix + ".part")
    size = 0
    try:
        with tmp.open("wb") as out:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise GameplayError("arquivo maior que 4 GB")
                out.write(chunk)
        try:
            duration = media.probe_duration(tmp)
        except (media.MediaError, ValueError) as e:
            raise GameplayError("o arquivo não é um vídeo válido") from e
        if duration < 5:
            raise GameplayError("vídeo curto demais (mínimo 5 segundos)")
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
    thumb = _make_thumb(dest, folder / THUMBS / f"{gid}.jpg", duration)
    g = Gameplay(id=gid, name=Path(original_name).stem[:80], filename=dest.name, duration=duration,
                 size=size, thumb=thumb.name if thumb else None, created=time.time())
    with db.session() as s:
        s.add(g)
    return g


def _make_thumb(video: Path, dest: Path, duration: float) -> Path | None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        media.run(["ffmpeg", "-y", "-ss", f"{min(1.0, duration / 2):.2f}", "-i", video, "-frames:v", "1",
                   "-vf", "scale=320:-2", dest])
        return dest if dest.exists() else None
    except media.MediaError as e:
        log.warning("sem miniatura para %s: %s", video.name, e)
        return None


def list_all(db: Database) -> list[Gameplay]:
    with db.session() as s:
        return list(s.scalars(select(Gameplay).order_by(Gameplay.created.desc())).all())


def get(db: Database, gid: str) -> Gameplay | None:
    with db.session() as s:
        return s.get(Gameplay, gid)


def remove(db: Database, folder: Path, gid: str) -> bool:
    with db.session() as s:
        g = s.get(Gameplay, gid)
        if g is None:
            return False
        (folder / g.filename).unlink(missing_ok=True)
        if g.thumb:
            (folder / THUMBS / g.thumb).unlink(missing_ok=True)
        s.delete(g)
    return True


def paths_for(db: Database, folder: Path, ids: list[str]) -> list[Path]:
    """Arquivos das gameplays escolhidas; sem ids = todas as cadastradas."""
    rows = list_all(db)
    if ids:
        rows = [g for g in rows if g.id in set(ids)]
    return [folder / g.filename for g in rows if (folder / g.filename).is_file()]


def sync_folder(db: Database, folder: Path) -> int:
    """Cadastra vídeos que já estão na pasta (ex.: copiados à mão para o volume). Devolve quantos entraram."""
    if not folder.is_dir():
        return 0
    known = {g.filename for g in list_all(db)}
    added = 0
    for p in sorted(folder.iterdir()):
        if not p.is_file() or p.suffix.lower() not in VIDEO_EXTS or p.name in known:
            continue
        try:
            dur = media.probe_duration(p)
        except (media.MediaError, ValueError):
            continue
        gid = uuid.uuid4().hex[:12]
        thumb = _make_thumb(p, folder / THUMBS / f"{gid}.jpg", dur)
        with db.session() as s:
            s.add(Gameplay(id=gid, name=p.stem[:80], filename=p.name, duration=dur, size=p.stat().st_size,
                           thumb=thumb.name if thumb else None))
        added += 1
    return added
