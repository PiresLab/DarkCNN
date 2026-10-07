"""Spike 02b — faster-whisper (só pip, CPU): timestamps por palavra, velocidade e qualidade.

Alternativa ao spike 02 (whisper.cpp) para quem não quer baixar .exe. Gera o MESMO arquivo
spikes/out/whisper_words.json, então os spikes 01 (modo real) e 04 funcionam igual.

Uso:
    pip install faster-whisper
    python spikes/02b_faster_whisper_words.py --input video.mp4 --model small --dur 300
Modelos: tiny, base, small, medium, large-v3-turbo. O 1º uso baixa o modelo sozinho
(small ≈ 0,5 GB, medium ≈ 1,5 GB) para a pasta de cache do usuário.
"""
from __future__ import annotations

import argparse
import os
import time
import wave

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")  # aviso inofensivo no Windows

from common import OUT, probe_duration, run, save_json


def load_wav(path):
    """WAV 16 kHz mono -> float32. Entregar array ao faster-whisper evita o decoder PyAV dele,
    que quebra em algumas combinações de versões (TypeError: open() ... 'metadata_errors')."""
    import numpy as np

    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--model", default="small")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--dur", type=float, default=300.0)
    ap.add_argument("--lang", default="pt")
    ap.add_argument("--device", default="cpu", help="cpu | cuda | auto")
    ap.add_argument("--compute-type", default="int8", help="int8 é o mais rápido em CPU")
    ap.add_argument("--threads", type=int, default=0, help="0 = automático")
    ap.add_argument("--beam", type=int, default=1, help="1 = rápido (greedy); 5 = mais preciso e mais lento")
    ap.add_argument("--vad", action="store_true", help="pula silêncios (mais rápido; pode deslocar tempos)")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    wav = OUT / "clip.wav"
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", args.start, "-t", args.dur,
         "-i", args.input, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav])
    audio_s = probe_duration(wav)

    from faster_whisper import WhisperModel

    print(f"Carregando modelo '{args.model}' (o 1º uso baixa o arquivo; isso não entra no tempo medido)…")
    t0 = time.perf_counter()
    model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type,
                         cpu_threads=args.threads)
    load_s = time.perf_counter() - t0

    print(f"Transcrevendo {audio_s:.0f}s de áudio… (um ponto a cada 10 segmentos)")
    t0 = time.perf_counter()
    segments, info = model.transcribe(
        load_wav(wav), language=args.lang, word_timestamps=True, beam_size=args.beam,
        vad_filter=args.vad, condition_on_previous_text=False,
    )
    words, nseg = [], 0
    for seg in segments:  # o gerador só decodifica quando é consumido
        nseg += 1
        if nseg % 10 == 0:
            print(".", end="", flush=True)
        for w in seg.words or []:
            if w.word.strip():
                words.append({"w": w.word.strip(), "start": round(w.start, 3),
                              "end": round(w.end, 3), "p": round(w.probability, 3)})
    elapsed = time.perf_counter() - t0
    print()
    if not words:
        raise SystemExit("Nenhuma palavra extraída (áudio sem fala?).")

    save_json("whisper_words.json", {"start_offset": args.start, "dur": audio_s, "words": words})

    low = sum(1 for w in words if w["p"] < 0.5)
    bad = sum(1 for a, b in zip(words, words[1:]) if b["start"] < a["start"])
    zero = sum(1 for w in words if w["end"] - w["start"] <= 0.001)
    speed = audio_s / elapsed
    print("\n=== RESUMO (cole isto de volta) ===")
    print(f"engine=faster-whisper modelo={args.model} device={args.device} compute={args.compute_type} "
          f"threads={args.threads or 'auto'} beam={args.beam} vad={args.vad}")
    print(f"idioma detectado={info.language} (prob {info.language_probability:.2f})")
    print(f"carregar modelo={load_s:.1f}s | áudio={audio_s:.0f}s transcrição={elapsed:.1f}s "
          f"-> {speed:.2f}x tempo real")
    print(f"estimativa p/ 1 hora de vídeo: {3600 / speed / 60:.0f} min")
    print(f"palavras={len(words)}  segmentos={nseg}  p<0.5: {low} ({100 * low / len(words):.1f}%)")
    print(f"timestamps fora de ordem={bad}  palavras com duração 0={zero}")
    print("primeiras palavras:", " | ".join(f"{w['w']}@{w['start']:.2f}" for w in words[:8]))


if __name__ == "__main__":
    main()
