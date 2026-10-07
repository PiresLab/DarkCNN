"""Spike 03 — Gemini: modelos disponíveis, tokens reais de áudio/vídeo e qualidade da transcrição.

Uso (cada flag liga uma parte; todas gastam pouca cota):
    python spikes/03_gemini_probe.py --list-models
    python spikes/03_gemini_probe.py --input video.mp4 --model <ID> --count-tokens
    python spikes/03_gemini_probe.py --input video.mp4 --model <ID> --transcribe --start 0 --dur 600
Use o MESMO --start/--dur do spike 02 para o spike 04 poder comparar.
"""
from __future__ import annotations

import argparse
import os
import time

from common import OUT, load_env, run, save_json
from pydantic import BaseModel


class Seg(BaseModel):
    start: str  # MM:SS
    end: str
    text: str


PROMPT = (
    "Transcreva fielmente o áudio em português do Brasil, sem resumir e sem traduzir. "
    "Devolva uma lista de segmentos curtos (uma frase cada). Cada segmento tem 'start' e 'end' "
    "no formato MM:SS (minutos:segundos desde o INÍCIO deste áudio) e 'text'."
)


def client():
    from google import genai

    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise SystemExit("Defina GEMINI_API_KEY (veja .env.example)")
    return genai.Client(api_key=key)


def list_models(c) -> None:
    rows = []
    for m in c.models.list():
        if "generateContent" in (m.supported_actions or []) and "flash" in m.name.lower():
            rows.append(
                {"name": m.name.removeprefix("models/"), "in": m.input_token_limit, "out": m.output_token_limit}
            )
    rows.sort(key=lambda r: r["name"])
    for r in rows:
        print(f"  {r['name']:<45} in={r['in']}  out={r['out']}")
    save_json("gemini_models.json", rows)
    print(f"\n{len(rows)} modelos 'flash' com generateContent. Escolha um e passe em --model.")


def upload(c, path, label: str):
    f = c.files.upload(file=str(path))
    while f.state is not None and f.state.name == "PROCESSING":
        time.sleep(2)
        f = c.files.get(name=f.name)
    if f.state is not None and f.state.name != "ACTIVE":
        raise SystemExit(f"Upload de {label} falhou: estado {f.state.name} {f.error}")
    print(f"  {label}: {f.name} ({(f.size_bytes or 0) / 1e6:.1f} MB) ativo")
    return f


def cut_audio(src: str, start: float, dur: float):
    out = OUT / "gemini_clip.mp3"
    OUT.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", start, "-t", dur, "-i", src,
         "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", out])
    return out


def cut_video(src: str, start: float, dur: float):
    out = OUT / "gemini_clip_video.mp4"
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", start, "-t", dur, "-i", src,
         "-vf", "scale=-2:360", "-r", "5", "-c:v", "libx264", "-crf", "32", "-c:a", "aac", "-b:a", "48k", out])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-models", action="store_true")
    ap.add_argument("--input")
    ap.add_argument("--model")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--dur", type=float, default=600.0)
    ap.add_argument("--count-tokens", action="store_true")
    ap.add_argument("--video-dur", type=float, default=120.0, help="trecho de vídeo para contar tokens")
    ap.add_argument("--transcribe", action="store_true")
    args = ap.parse_args()
    load_env()
    c = client()

    if args.list_models:
        from google.genai import errors as _errors

        try:
            list_models(c)
        except _errors.APIError as e:
            raise SystemExit(f"ERRO da API: código={e.code} status={e.status}\n{(e.message or '')[:600]}")
    if not (args.count_tokens or args.transcribe):
        return
    if not (args.input and args.model):
        raise SystemExit("--input e --model são obrigatórios para --count-tokens/--transcribe")

    from google.genai import errors, types

    summary: dict = {"model": args.model, "start_offset": args.start, "dur": args.dur}
    uploaded = []
    try:
        audio = cut_audio(args.input, args.start, args.dur)
        print("Enviando áudio…")
        af = upload(c, audio, "áudio")
        uploaded.append(af)

        if args.count_tokens:
            n = c.models.count_tokens(model=args.model, contents=[af]).total_tokens
            summary["audio_tokens"] = n
            print(f"  tokens de áudio: {n}  ({n / args.dur:.1f}/s; esperado ~32/s; 1h ≈ {n / args.dur * 3600:,.0f})")
            video = cut_video(args.input, args.start, args.video_dur)
            print("Enviando vídeo (360p, só para contar tokens)…")
            vf = upload(c, video, "vídeo")
            uploaded.append(vf)
            nv = c.models.count_tokens(model=args.model, contents=[vf]).total_tokens
            summary["video_tokens"] = nv
            print(f"  tokens de vídeo: {nv}  ({nv / args.video_dur:.0f}/s; esperado ~300/s; 1h ≈ {nv / args.video_dur * 3600:,.0f})")

        if args.transcribe:
            print("Transcrevendo…")
            t0 = time.perf_counter()
            r = c.models.generate_content(
                model=args.model,
                contents=[af, PROMPT],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json", response_schema=list[Seg], temperature=0
                ),
            )
            elapsed = time.perf_counter() - t0
            segs = [s.model_dump() for s in (r.parsed or [])]
            u = r.usage_metadata
            summary.update(
                transcribe_s=round(elapsed, 1), segments=len(segs),
                prompt_tokens=u.prompt_token_count, output_tokens=u.candidates_token_count,
                thought_tokens=u.thoughts_token_count,
            )
            save_json("gemini_transcript.json", {"start_offset": args.start, "dur": args.dur, "segments": segs})
            print(f"  {len(segs)} segmentos em {elapsed:.1f}s; primeiros:")
            for s in segs[:3]:
                print(f"    {s['start']}-{s['end']}  {s['text'][:70]}")
    except errors.APIError as e:
        print(f"\nERRO da API: código={e.code} status={e.status}\n{e.message[:1200]}")
        print("(Se for 429, copie esta mensagem: ela diz qual cota estourou.)")
        summary["error"] = {"code": e.code, "status": e.status, "message": e.message[:1200]}
    finally:
        for f in uploaded:
            try:
                c.files.delete(name=f.name)
            except Exception:
                pass

    save_json("gemini_summary.json", summary)
    print("\n=== RESUMO (cole isto de volta) ===")
    for k, v in summary.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
