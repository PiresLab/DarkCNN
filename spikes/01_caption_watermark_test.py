"""Spike 01 — reenquadramento 9:16 + legenda .ass + marca d'água em UM único encode FFmpeg.

Sem internet e sem chave. Valida: libass, fontes, filter_complex único, saída 1080x1920.

Modo sintético (padrão):
    python spikes/01_caption_watermark_test.py
Modo real (usa as palavras geradas pelo spike 02, para checar sincronia da legenda):
    python spikes/01_caption_watermark_test.py --source video.mp4 --words spikes/out/whisper_words.json --dur 30
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from common import OUT, probe_video, run, save_json

HIGHLIGHT = "&H0000FFFF&"  # amarelo (ASS usa BGR)
WHITE = "&H00FFFFFF&"

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},84,{white},{white},&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,7,2,2,60,60,520,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def ass_time(t: float) -> str:
    cs = max(0, round(t * 100))
    return f"{cs // 360000}:{cs % 360000 // 6000:02d}:{cs % 6000 // 100:02d}.{cs % 100:02d}"


def group_words(words: list[dict], max_words: int = 3, max_chars: int = 16) -> list[list[dict]]:
    """Agrupa palavras sem estourar a largura: no máximo `max_words` e ~`max_chars` caracteres."""
    groups: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        size = sum(len(x["w"]) for x in cur) + len(cur) + len(w["w"])
        if cur and (len(cur) >= max_words or size > max_chars):
            groups.append(cur)
            cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def build_ass(words: list[dict], font: str) -> str:
    """words: [{'w','start','end'}] -> um evento por palavra, mostrando o grupo com a palavra ativa destacada."""
    lines = [ASS_HEADER.format(font=font, white=WHITE)]
    for chunk in group_words(words):
        for i, cur in enumerate(chunk):
            start = cur["start"]
            end = chunk[i + 1]["start"] if i + 1 < len(chunk) else cur["end"]
            parts = []
            for j, w in enumerate(chunk):
                txt = w["w"].upper().replace("{", "(").replace("}", ")")
                if j == i:
                    parts.append(f"{{\\c{HIGHLIGHT}\\fscx115\\fscy115}}{txt}{{\\r}}")
                else:
                    parts.append(txt)
            lines.append(
                f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{' '.join(parts)}\n"
            )
    return "".join(lines)


def make_synthetic(work: Path) -> tuple[Path, list[dict]]:
    words_txt = "isso aqui é um teste de legenda com palavra destacada e fonte bem grande para ler no celular".split()
    words = [
        {"w": w, "start": round(0.3 + i * 0.42, 2), "end": round(0.3 + (i + 1) * 0.42, 2)}
        for i, w in enumerate(words_txt)
    ]
    src = work / "source.mp4"
    run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=8",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", src,
        ]
    )
    return src, words


def make_watermark(work: Path) -> Path:
    wm = work / "watermark.png"
    run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i",
            "color=c=white:s=360x90,format=rgba,"
            "drawbox=x=6:y=6:w=348:h=78:color=black@0:t=fill:replace=1",
            "-frames:v", "1", wm,
        ]
    )
    return wm


def video_chain(mode: str) -> str:
    if mode == "crop":
        return "[0:v]crop='trunc(ih*9/16/2)*2':ih,scale=1080:1920,setsar=1[v0]"
    return (
        "[0:v]split[a][b];"
        "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:3[bg];"
        "[b]scale=1080:-2[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v0]"
    )


def render(work: Path, src: Path, wm: Path, ass: Path, fonts: Path | None, mode: str,
           start: float, dur: float | None, out_name: str) -> Path:
    """Um comando só: seek -> crop/blur-fit -> legenda -> marca d'água -> encode.

    Roda com cwd=work e caminhos relativos no filtro 'ass' para evitar o inferno de escape
    de 'C:\\...' no Windows.
    """
    ass_arg = f"{ass.name}" + (f":fontsdir={fonts.name}" if fonts else "")
    fc = (
        f"{video_chain(mode)};"
        f"[v0]ass={ass_arg}[v1];"
        "[1:v]format=rgba,colorchannelmixer=aa=0.6[wm];"
        "[v1][wm]overlay=W-w-48:200[v]"
    )
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    if start:
        cmd += ["-ss", str(start)]
    cmd += ["-i", str(src)]
    if dur:
        cmd += ["-t", str(dur)]
    cmd += [
        "-i", wm.name,
        "-filter_complex", fc, "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out_name,
    ]
    run(cmd, cwd=work)
    return work / out_name


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", help="vídeo real (padrão: sintético)")
    ap.add_argument("--words", help="JSON de palavras do spike 02")
    ap.add_argument("--dur", type=float, default=None, help="duração do trecho (modo real)")
    ap.add_argument("--font", default="Arial" if os.name == "nt" else "DejaVu Sans")
    ap.add_argument("--fonts-dir", default=None, help="pasta com .ttf (opcional)")
    ap.add_argument("--watermark", default=None, help="PNG com alpha (padrão: gerado)")
    args = ap.parse_args()

    work = OUT / "spike01"
    work.mkdir(parents=True, exist_ok=True)

    start = 0.0
    if args.source:
        if not args.words:
            raise SystemExit("--source exige --words")
        data = json.loads(Path(args.words).read_text(encoding="utf-8"))
        start = data.get("start_offset", 0.0)
        dur = args.dur or 30.0
        words = [w for w in data["words"] if w["end"] <= dur]
        src = Path(args.source).resolve()
    else:
        src, words = make_synthetic(work)
        dur = None

    ass = work / "subs.ass"
    ass.write_text(build_ass(words, args.font), encoding="utf-8")
    wm = Path(args.watermark).resolve() if args.watermark else make_watermark(work)
    if args.watermark:
        (work / wm.name).write_bytes(wm.read_bytes())
    fonts = None
    if args.fonts_dir:
        fonts = work / "fonts"
        fonts.mkdir(exist_ok=True)
        for f in Path(args.fonts_dir).glob("*.[to]tf"):
            (fonts / f.name).write_bytes(f.read_bytes())

    summary = {}
    for mode in ("crop", "blur"):
        out = render(work, src, Path(work / wm.name), ass, fonts, mode, start, dur, f"out_{mode}.mp4")
        info = probe_video(out)
        frame = work / f"frame_{mode}.png"
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", "3", "-i", out,
             "-frames:v", "1", frame])
        ok = info["width"] == 1080 and info["height"] == 1920
        summary[mode] = {**info, "ok_1080x1920": ok, "frame": str(frame)}
        print(f"[{'OK' if ok else 'ERRO'}] {mode}: {info['width']}x{info['height']} "
              f"{info['codec_name']} {info['pix_fmt']} {info['duration']:.2f}s -> {out}")

    save_json("spike01_summary.json", summary)
    print("\nAbra os frames e confira: legenda legível, palavra destacada, marca d'água fora da UI.")
    for m in summary.values():
        print("  ", m["frame"])


if __name__ == "__main__":
    main()
