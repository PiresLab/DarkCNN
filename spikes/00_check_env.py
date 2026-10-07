"""Spike 00 — checa o ambiente (FFmpeg/libass/x264/AMF, whisper.cpp, chave Gemini, SDK).

Uso: python spikes/00_check_env.py [--whisper-cli CAMINHO]
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import sys

from common import load_env, run

ok_all = True


def row(name: str, ok: bool, detail: str = "", required: bool = True) -> None:
    global ok_all
    mark = "OK   " if ok else ("FALTA" if required else "aviso")
    if required and not ok:
        ok_all = False
    print(f"[{mark}] {name:<28} {detail}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--whisper-cli", default=os.environ.get("WHISPER_CLI"))
    args = ap.parse_args()
    load_env()

    row("Python >= 3.10", sys.version_info >= (3, 10), sys.version.split()[0])

    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    row("ffmpeg no PATH", bool(ffmpeg), ffmpeg or "instale a build 'full' (gyan.dev) e ponha no PATH")
    row("ffprobe no PATH", bool(ffprobe), ffprobe or "")
    if ffmpeg:
        ver = run(["ffmpeg", "-hide_banner", "-version"]).stdout.splitlines()[0]
        print(f"        {ver}")
        flt = run(["ffmpeg", "-hide_banner", "-filters"]).stdout
        enc = run(["ffmpeg", "-hide_banner", "-encoders"]).stdout
        row("filtro 'ass' (libass)", " ass " in flt, "necessário para legenda queimada")
        row("filtro 'overlay'", " overlay " in flt)
        row("encoder libx264", "libx264" in enc)
        row("encoder aac", " aac " in enc)
        row("encoder libmp3lame", "libmp3lame" in enc, "usado no áudio enviado ao Gemini")
        row("encoder h264_amf (AMD)", "h264_amf" in enc, "opcional (encode por GPU)", required=False)

    cli = args.whisper_cli or shutil.which("whisper-cli") or shutil.which("main")
    row("whisper.cpp (whisper-cli)", bool(cli and os.path.exists(cli)), cli or "passe --whisper-cli ou defina WHISPER_CLI")
    if cli and os.path.exists(cli):
        h = run([cli, "--help"], check=False)
        text = (h.stdout + h.stderr)
        row("  flag --output-json-full", "-ojf" in text, "tokens com timestamps")
        row("  flag --max-len / -sow", "--max-len" in text and "--split-on-word" in text)
        row("  flag --dtw", "--dtw" in text, "timestamps por DTW (melhor precisão)", required=False)

    key = os.environ.get("GEMINI_API_KEY")
    row("GEMINI_API_KEY", bool(key), "definida" if key else "crie .env (veja .env.example)")
    row("pacote google-genai", importlib.util.find_spec("google.genai") is not None if importlib.util.find_spec("google") else False, "pip install -r spikes/requirements.txt")
    row("yt-dlp", bool(shutil.which("yt-dlp")), "só necessário na Fase 2", required=False)

    print("\nRESULTADO:", "tudo certo" if ok_all else "há itens obrigatórios faltando")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
