"""FFmpeg/ffprobe e identificação de arquivos."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import threading
import unicodedata
from pathlib import Path


class MediaError(RuntimeError):
    pass


# Processos em andamento por thread: deixa o painel web interromper o FFmpeg de um job sem tocar nos outros.
_running: dict[int, subprocess.Popen] = {}
_running_lock = threading.Lock()


def kill_thread_process(ident: int) -> bool:
    """Mata o processo que a thread `ident` está esperando. Devolve False se não havia nenhum."""
    with _running_lock:
        p = _running.get(ident)
    if p is None:
        return False
    p.kill()
    return True


def run(cmd: list, cwd: Path | None = None) -> subprocess.CompletedProcess:
    cmd = [str(c) for c in cmd]
    ident = threading.get_ident()
    try:
        p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError as e:
        raise MediaError(f"programa não encontrado: {cmd[0]} (instale o FFmpeg e ponha no PATH)") from e
    with _running_lock:
        _running[ident] = p
    try:
        out, err = p.communicate()
    finally:
        with _running_lock:
            _running.pop(ident, None)
    if p.returncode != 0:
        raise MediaError(f"falhou ({p.returncode}): {' '.join(cmd)}\n{(err or '')[-1500:]}")
    return subprocess.CompletedProcess(cmd, p.returncode, out, err)


def probe_duration(path: Path) -> float:
    p = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path])
    return float(p.stdout.strip())


def has_audio(path: Path) -> bool:
    p = run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
             "-of", "csv=p=0", path])
    return bool(p.stdout.strip())


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


def concat_copy(parts: list[Path], dest: Path, reencode_audio: bool = False) -> Path:
    """Junta arquivos de MESMA codificação sem recodificar o vídeo (demuxer concat). Todos os trechos
    precisam ter sido gerados com os mesmos parâmetros. `reencode_audio` refaz só o áudio (AAC)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    listfile = dest.with_suffix(".txt")
    lines = []
    for p in parts:
        posix = Path(p).resolve().as_posix().replace("'", "'\\''")
        lines.append(f"file '{posix}'\n")
    listfile.write_text("".join(lines), encoding="utf-8")
    tmp = dest.with_name(dest.stem + ".tmp" + dest.suffix)
    audio = ["-c:a", "aac", "-b:a", "192k"] if reencode_audio else ["-c:a", "copy"]
    try:
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
             "-i", listfile, "-c:v", "copy", *audio, "-movflags", "+faststart", tmp])
        dest.unlink(missing_ok=True)
        tmp.replace(dest)
    finally:
        listfile.unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)
    return dest
