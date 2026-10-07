"""Entrada do pipeline: arquivo local OU link (yt-dlp). Devolve o caminho do vídeo e a procedência."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Config

log = logging.getLogger(__name__)

# 1080p no máximo (basta para 9:16 e poupa disco/tempo); prefere mp4/h264, aceita o que houver.
# `<=?` mantém formatos de altura desconhecida (com `<=` o yt-dlp os descarta e dá "format not available").
YDL_FORMAT = (
    "bv*[height<=?1080][ext=mp4]+ba[ext=m4a]/b[height<=?1080][ext=mp4]/"
    "bv*[height<=?1080]+ba/b[height<=?1080]"
)
YOUTUBE_STANDARD_LICENSE = "Licença padrão do YouTube (não é Creative Commons)"


class IngestError(RuntimeError):
    pass


@dataclass
class Ingested:
    path: Path
    meta: dict[str, Any] = field(default_factory=dict)  # vazio para arquivo local


def is_url(text: str) -> bool:
    return re.match(r"^https?://", text.strip(), re.I) is not None


def is_cc(license_text: str | None) -> bool:
    return bool(license_text) and "creative commons" in license_text.lower()  # type: ignore[union-attr]


def resolve_input(arg: str, cfg: Config, ydl_cls: Any = None) -> Ingested:
    """Arquivo existente -> local. Link http(s) -> baixa com yt-dlp (reaproveita se já baixado)."""
    arg = arg.strip()
    if is_url(arg):
        return _download(arg, cfg, ydl_cls)
    if "://" in arg:
        raise IngestError(f"só links http(s) são aceitos: {arg.split('://')[0]}://…")
    p = Path(arg)
    if not p.is_file():
        raise IngestError(f"arquivo não encontrado: {arg}")
    return Ingested(p.resolve(), {})


def apply_meta(cfg: Config, meta: dict[str, Any]) -> Config:
    """Preenche fonte/licença a partir do vídeo baixado, sem sobrescrever o que o usuário passou."""
    if not meta:
        return cfg
    upd: dict[str, Any] = {}
    if not cfg.source:
        upd["source"] = meta.get("webpage_url")
    if not cfg.license:
        upd["license"] = meta.get("license") or YOUTUBE_STANDARD_LICENSE
    return cfg.model_copy(update=upd) if upd else cfg


def _warn_js_runtime() -> None:
    if not any(shutil.which(x) for x in ("deno", "node", "bun")):
        log.warning(
            "nenhum runtime JavaScript encontrado: o YouTube pode entregar menos formatos. "
            "Instale o Deno: winget install DenoLand.Deno (e reabra o terminal)"
        )


def _download(url: str, cfg: Config, ydl_cls: Any) -> Ingested:
    # caminho ABSOLUTO: o render roda o ffmpeg em outra pasta (cwd), onde um caminho relativo não existe
    folder = (cfg.workspace_dir / "downloads" / hashlib.sha1(url.encode()).hexdigest()[:12]).resolve()
    meta_file = folder / "meta.json"
    cached = _find_video(folder)
    if cached and meta_file.exists():  # 2ª execução: sem rede, sem baixar
        log.info("vídeo já baixado: %s", cached.name)
        return Ingested(cached, json.loads(meta_file.read_text(encoding="utf-8")))

    if ydl_cls is None:
        try:
            from yt_dlp import YoutubeDL as ydl_cls  # noqa: N813
        except ImportError as e:
            raise IngestError("yt-dlp não instalado: rode `pip install -U yt-dlp`") from e
    _warn_js_runtime()

    folder.mkdir(parents=True, exist_ok=True)
    opts = {
        "format": YDL_FORMAT, "merge_output_format": "mp4", "noplaylist": True, "quiet": True,
        "no_warnings": True, "outtmpl": str(folder / "video.%(ext)s"), "restrictfilenames": True,
    }
    try:
        with ydl_cls(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            _check_info(info, cfg)
            log.info("baixando: %s — %s (%.1f min)", info.get("channel") or info.get("uploader"),
                     info.get("title"), (info.get("duration") or 0) / 60)
            ydl.download([url])
    except IngestError:
        raise
    except Exception as e:  # DownloadError e afins: privado, removido, bloqueio de região…
        raise IngestError(f"não consegui baixar {url}: {str(e)[:300]}. "
                          "Tente `pip install -U yt-dlp` (o YouTube muda com frequência).") from e

    video = _find_video(folder)
    if video is None:
        raise IngestError(f"download terminou mas não achei o arquivo em {folder} (o ffmpeg está no PATH?)")
    meta = {
        "id": info.get("id"), "title": info.get("title"),
        "channel": info.get("channel") or info.get("uploader"),
        "webpage_url": info.get("webpage_url") or url,
        "duration": info.get("duration"), "license": info.get("license"),
    }
    meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return Ingested(video, meta)


def _find_video(folder: Path) -> Path | None:
    if not folder.is_dir():
        return None
    files = [f for f in folder.glob("video.*") if f.suffix not in (".part", ".ytdl", ".json")]
    return max(files, key=lambda f: f.stat().st_size) if files else None


def _check_info(info: dict[str, Any] | None, cfg: Config) -> None:
    if not info:
        raise IngestError("o yt-dlp não devolveu informações do vídeo")
    if info.get("_type") == "playlist" or info.get("entries"):
        raise IngestError("o link é uma playlist/canal: passe o link de UM vídeo")
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
        raise IngestError("transmissão ao vivo/agendada não é suportada: espere terminar")
    mins = (info.get("duration") or 0) / 60
    if mins > cfg.max_download_min:
        raise IngestError(f"vídeo com {mins:.0f} min passa do limite de {cfg.max_download_min:.0f} min "
                          "(ajuste max_download_min)")
