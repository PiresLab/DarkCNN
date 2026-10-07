"""Spike 02 — whisper.cpp (Vulkan/AMD): timestamps por palavra, velocidade e qualidade.

Uso:
    python spikes/02_whisper_words.py --input video.mp4 --model C:\\whisper\\ggml-large-v3-turbo.bin \\
        --whisper-cli C:\\whisper\\whisper-cli.exe --start 0 --dur 600
Opções úteis: --mode full|ml1 (formato de timestamp), --extra "--dtw large.v3.turbo", --no-gpu.

Saída: spikes/out/whisper_words.json  ({"start_offset", "words":[{"w","start","end","p"}]})
Atenção: o formato do JSON do whisper.cpp NÃO foi confirmado em doc oficial; se o parse falhar,
o script mostra as chaves encontradas — cole esse trecho de volta para eu ajustar.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from pathlib import Path

from common import OUT, probe_duration, run, save_json


def words_from_tokens(tokens: list[dict]) -> list[dict]:
    """Junta tokens (sub-palavras) em palavras. Token que começa com espaço abre palavra nova."""
    words: list[dict] = []
    cur: dict | None = None
    for t in tokens:
        text = t.get("text", "")
        if text.startswith("[_") or not text.strip():  # tokens especiais: [_BEG_], [_TT_123]...
            continue
        off = t["offsets"]
        s, e = off["from"] / 1000, off["to"] / 1000
        p = t.get("p", 1.0)
        if cur is None or text.startswith(" "):
            if cur:
                words.append(cur)
            cur = {"w": text.strip(), "start": s, "end": e, "p": p}
        else:  # continuação (sufixo ou pontuação)
            cur["w"] += text
            cur["end"] = e
            cur["p"] = min(cur["p"], p)
    if cur:
        words.append(cur)
    return words


def words_from_ml1(segments: list[dict]) -> list[dict]:
    """Modo --max-len 1 --split-on-word: cada segmento já é (quase) uma palavra."""
    words: list[dict] = []
    for seg in segments:
        text = seg.get("text", "").strip()
        if not text or text.startswith("[_"):
            continue
        off = seg["offsets"]
        if words and not any(ch.isalnum() for ch in text):  # pontuação solta gruda na palavra anterior
            words[-1]["w"] += text
            words[-1]["end"] = off["to"] / 1000
            continue
        words.append({"w": text, "start": off["from"] / 1000, "end": off["to"] / 1000, "p": 1.0})
    return words


def parse(data: dict, mode: str) -> list[dict]:
    segs = data.get("transcription")
    if not segs:
        raise SystemExit(f"JSON inesperado. Chaves de topo: {list(data)}")
    if mode == "ml1":
        return words_from_ml1(segs)
    if "tokens" not in segs[0]:
        raise SystemExit(
            f"Sem 'tokens' no JSON (precisa de -ojf). Chaves do 1º segmento: {list(segs[0])}"
        )
    return words_from_tokens([t for s in segs for t in s["tokens"]])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--model", required=True, help="arquivo ggml-*.bin")
    ap.add_argument("--whisper-cli", default=os.environ.get("WHISPER_CLI") or shutil.which("whisper-cli"))
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--dur", type=float, default=600.0)
    ap.add_argument("--lang", default="pt")
    ap.add_argument("--mode", choices=["full", "ml1"], default="full")
    ap.add_argument("--extra", default="", help="flags extras do whisper-cli, entre aspas")
    ap.add_argument("--no-gpu", action="store_true", help="força CPU (-ng) para comparar velocidade")
    args = ap.parse_args()
    if not args.whisper_cli:
        raise SystemExit("Informe --whisper-cli ou defina WHISPER_CLI")

    OUT.mkdir(parents=True, exist_ok=True)
    wav = OUT / "clip.wav"
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", args.start, "-t", args.dur,
         "-i", args.input, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav])
    audio_s = probe_duration(wav)

    prefix = OUT / f"whisper_{args.mode}"
    cmd = [args.whisper_cli, "-m", args.model, "-f", wav, "-l", args.lang, "-of", prefix]
    cmd += ["-ojf"] if args.mode == "full" else ["-oj", "-ml", "1", "-sow"]
    if args.no_gpu:
        cmd += ["-ng"]
    cmd += args.extra.split()
    print("Executando:", " ".join(str(c) for c in cmd))

    t0 = time.perf_counter()
    p = run(cmd, check=False)
    elapsed = time.perf_counter() - t0
    log = (p.stdout + "\n" + p.stderr)
    (OUT / f"whisper_{args.mode}.log").write_text(log, encoding="utf-8")
    if p.returncode != 0:
        raise SystemExit(f"whisper-cli falhou ({p.returncode}). Fim do log:\n{log[-1500:]}")

    backend = [l.strip() for l in log.splitlines() if any(k in l.lower() for k in ("vulkan", "cuda", "ggml_", "using", "device"))][:6]
    data = json.loads(Path(f"{prefix}.json").read_text(encoding="utf-8"))
    words = parse(data, args.mode)
    if not words:
        raise SystemExit("Nenhuma palavra extraída.")

    low = sum(1 for w in words if w["p"] < 0.5)
    save_json("whisper_words.json", {"start_offset": args.start, "dur": audio_s, "words": words})

    print("\n=== RESUMO (cole isto de volta) ===")
    print(f"modo={args.mode} modelo={Path(args.model).name} gpu={'não' if args.no_gpu else 'sim/auto'}")
    print(f"áudio={audio_s:.0f}s tempo={elapsed:.1f}s  velocidade={audio_s / elapsed:.1f}x tempo real")
    print(f"palavras={len(words)}  p<0.5: {low} ({100 * low / len(words):.1f}%)")
    print("backend (linhas do log):", *backend, sep="\n  ")
    print("primeiras palavras:", " | ".join(f"{w['w']}@{w['start']:.2f}" for w in words[:8]))
    bad = sum(1 for a, b in zip(words, words[1:]) if b["start"] < a["start"])
    zero = sum(1 for w in words if w["end"] - w["start"] <= 0.001)
    print(f"timestamps fora de ordem={bad}  palavras com duração 0={zero}")


if __name__ == "__main__":
    main()
