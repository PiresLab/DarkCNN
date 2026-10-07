"""FFmpeg/ffprobe e identificação de arquivos."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import unicodedata
from pathlib import Path


class MediaError(RuntimeError):
    pass


def run(cmd: list, cwd: Path | None = None) -> subprocess.CompletedProcess:
    cmd = [str(c) for c in cmd]
    try:
        p = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
    except FileNotFoundError as e:
        raise MediaError(f"programa não encontrado: {cmd[0]} (instale o FFmpeg e ponha no PATH)") from e
    if p.returncode != 0:
        raise MediaError(f"falhou ({p.returncode}): {' '.join(cmd)}\n{p.stderr[-1500:]}")
    return p


def probe_duration(path: Path) -> float:
    p = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path])
    return float(p.stdout.strip())


def video_info(path: Path) -> dict:
    p = run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-show_entries", "format=duration",
            "-of", "json", path,
        ]
    )
    d = json.loads(p.stdout)
    if not d.get("streams"):
        raise MediaError(f"sem faixa de vídeo: {path}")
    return {**d["streams"][0], "duration": float(d["format"]["duration"])}


def slugify(text: str, maxlen: int = 40) -> str:
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return (s[:maxlen].strip("-")) or "corte"


def file_id(path: Path) -> str:
    """nome + hash de 1 MB iniciais + tamanho: identifica o arquivo sem reler vídeos enormes."""
    h = hashlib.sha1()
    with open(path, "rb") as f:
        h.update(f.read(1 << 20))
    h.update(str(path.stat().st_size).encode())
    return f"{slugify(path.stem, 30)}-{h.hexdigest()[:8]}"


def extract_wav(src: Path, dst: Path) -> None:
    """Áudio mono 16 kHz (formato do Whisper)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src,
         "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", dst])
