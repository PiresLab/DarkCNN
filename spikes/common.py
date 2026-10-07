"""Helpers compartilhados pelos spikes da Fase 0 (código descartável)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent / "out"


def run(cmd, cwd=None, check=True) -> subprocess.CompletedProcess:
    cmd = [str(c) for c in cmd]
    p = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if check and p.returncode != 0:
        sys.exit(f"Falhou ({p.returncode}): {' '.join(cmd)}\n{p.stderr[-2000:]}")
    return p


def load_env() -> None:
    """Lê o .env da raiz (KEY=VALUE). Variáveis já definidas no ambiente têm prioridade."""
    f = ROOT / ".env"
    if not f.exists():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def probe_duration(path) -> float:
    p = run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path]
    )
    return float(p.stdout.strip())


def probe_video(path) -> dict:
    p = run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,codec_name,pix_fmt",
            "-show_entries", "format=duration", "-of", "json", path,
        ]
    )
    d = json.loads(p.stdout)
    return {**d["streams"][0], "duration": float(d["format"]["duration"])}


def parse_ts(s: str) -> float:
    """'MM:SS', 'HH:MM:SS' ou 'MM:SS.s' -> segundos."""
    parts = [float(x) for x in s.strip().split(":")]
    sec = 0.0
    for x in parts:
        sec = sec * 60 + x
    return sec


def save_json(name: str, data) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    f = OUT / name
    f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return f
