"""Texto na tela em ASS (libass): legenda por palavra, ou título no topo + frase-gancho embaixo."""
from __future__ import annotations

from .config import Config

HIGHLIGHT = "&H0000FFFF&"  # amarelo (ASS usa BGR)
WHITE = "&H00FFFFFF&"

_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},84,{white},{white},&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,7,2,2,60,60,520,1
Style: Title,{font},66,{white},{white},&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,6,2,8,70,70,300,1
Style: Hook,{font},86,{yellow},{yellow},&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,7,2,2,60,60,330,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def ass_time(t: float) -> str:
    cs = max(0, round(t * 100))
    return f"{cs // 360000}:{cs % 360000 // 6000:02d}:{cs % 6000 // 100:02d}.{cs % 100:02d}"


def esc(text: str) -> str:
    """Tira o que o ASS interpretaria como comando/quebra de linha."""
    return " ".join(text.replace("\\", " ").replace("{", "(").replace("}", ")").split())


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


def _caption_events(words: list[dict], clip_start: float, clip_end: float) -> list[str]:
    inside = [w for w in words if w["start"] >= clip_start - 0.05 and w["end"] <= clip_end + 0.05]
    lines = []
    for chunk in group_words(inside):
        for i, cur in enumerate(chunk):
            start = max(0.0, cur["start"] - clip_start)
            end = (chunk[i + 1]["start"] if i + 1 < len(chunk) else cur["end"]) - clip_start
            parts = []
            for j, w in enumerate(chunk):
                txt = esc(w["w"]).upper()
                parts.append(f"{{\\c{HIGHLIGHT}\\fscx115\\fscy115}}{txt}{{\\r}}" if j == i else txt)
            lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{' '.join(parts)}\n")
    return lines


def build_ass(clip: dict, words: list[dict], cfg: Config) -> str | None:
    """Conteúdo do .ass do corte, ou None se text_mode == 'none'."""
    if cfg.text_mode == "none":
        return None
    head = _HEADER.format(font=cfg.font, white=WHITE, yellow=HIGHLIGHT)
    dur = clip["end"] - clip["start"]
    if cfg.text_mode == "captions":
        return head + "".join(_caption_events(words, clip["start"], clip["end"]))
    # titled: título no topo + gancho embaixo, durante todo o corte
    end = ass_time(dur)
    return (
        head
        + f"Dialogue: 0,{ass_time(0)},{end},Title,,0,0,0,,{esc(clip['title'])}\n"
        + f"Dialogue: 1,{ass_time(0)},{end},Hook,,0,0,0,,{{\\fad(250,0)}}{esc(clip['hook_text']).upper()}\n"
    )
