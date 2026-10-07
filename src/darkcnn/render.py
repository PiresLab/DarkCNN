"""Render de um corte: seek + reenquadramento 9:16 + texto (.ass) + marca d'água em UM encode."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from . import cache, textlayers
from .config import Config
from .media import run

log = logging.getLogger(__name__)


def video_chain(layout: str) -> str:
    """Termina no rótulo [v0] (1080x1920)."""
    if layout == "crop":
        return "[0:v]crop='trunc(ih*9/16/2)*2':ih,scale=1080:1920,setsar=1[v0]"
    return (
        "[0:v]split[a][b];"
        "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:3[bg];"
        "[b]scale=1080:-2[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v0]"
    )


def build_filter(cfg: Config, has_ass: bool, has_fonts: bool, has_wm: bool) -> tuple[str, str]:
    """(filter_complex, rótulo final). Nomes relativos: o ffmpeg roda com cwd=workdir, o que evita o
    escape de 'C:\\...' dentro do filtro no Windows."""
    parts = [video_chain(cfg.layout)]
    last = "v0"
    if has_ass:
        arg = "subs.ass" + (":fontsdir=fonts" if has_fonts else "")
        parts.append(f"[{last}]ass={arg}[v1]")
        last = "v1"
    if has_wm:
        w = cfg.watermark
        parts.append(f"[1:v]format=rgba,colorchannelmixer=aa={w.opacity:.2f}[wm]")
        parts.append(f"[{last}][wm]overlay={w.x}:{w.y}[v]")
        last = "v"
    return ";".join(parts), last


def render_key(src_id: str, clip: dict, words: list[dict], cfg: Config) -> str:
    wm = cfg.watermark.path
    wm_sig = None
    if wm is not None and wm.exists():
        wm_sig = [str(wm), wm.stat().st_size, int(wm.stat().st_mtime)]
    inside = [w for w in words if clip["start"] - 0.05 <= w["start"] and w["end"] <= clip["end"] + 0.05]
    return cache.key_of(
        v=1, src=src_id, start=clip["start"], end=clip["end"], title=clip["title"], hook=clip["hook_text"],
        text_mode=cfg.text_mode, layout=cfg.layout, font=cfg.font, preset=cfg.preset, crf=cfg.crf,
        wm=wm_sig, wm_cfg=cfg.watermark.model_dump(mode="json", exclude={"path"}),
        words=inside if cfg.text_mode == "captions" else None,
    )


def render_clip(src: Path, clip: dict, words: list[dict], cfg: Config, dest: Path, workdir: Path,
                key: str) -> bool:
    """Renderiza `clip` em `dest`. Devolve False se já estava pronto (mesma chave)."""
    keyfile = workdir / "render.key"
    if dest.exists() and keyfile.exists() and keyfile.read_text().strip() == key:
        return False

    shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True, exist_ok=True)

    ass = textlayers.build_ass(clip, words, cfg)
    if ass is not None:
        (workdir / "subs.ass").write_text(ass, encoding="utf-8")
    has_fonts = bool(cfg.fonts_dir and Path(cfg.fonts_dir).is_dir())
    if has_fonts:
        shutil.copytree(cfg.fonts_dir, workdir / "fonts")
    wm = cfg.watermark.path
    has_wm = wm is not None
    if has_wm:
        if not wm.exists():
            raise FileNotFoundError(f"marca d'água não encontrada: {wm}")
        shutil.copyfile(wm, workdir / "wm.png")

    fc, last = build_filter(cfg, ass is not None, has_fonts, has_wm)
    tmp = workdir / "out.mp4"
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    # -ss/-t são opções de ENTRADA: precisam vir ANTES do -i a que se referem
    cmd += ["-ss", f"{clip['start']:.3f}", "-t", f"{clip['end'] - clip['start']:.3f}", "-i", src]
    if has_wm:
        cmd += ["-i", "wm.png"]
    cmd += [
        "-filter_complex", fc, "-map", f"[{last}]", "-map", "0:a?",
        "-c:v", "libx264", "-crf", str(cfg.crf), "-preset", cfg.preset, "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "out.mp4",
    ]
    run(cmd, cwd=workdir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.unlink(missing_ok=True)  # no Windows, mover sobre arquivo existente falha
    shutil.move(str(tmp), dest)
    keyfile.write_text(key)
    return True
