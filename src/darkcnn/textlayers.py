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
Style: Context,{font},54,{white},{white},&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,5,2,8,80,80,270,1
Style: Theme,{font},46,{yellow},{yellow},&H00000000,&H64000000,-1,0,0,0,100,100,2,0,1,5,2,8,70,70,225,1
Style: Rank,{font},300,{yellow},{yellow},&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,12,3,5,60,60,0,1
Style: OptA,{font},62,{white},{white},&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,6,2,8,70,70,330,1
Style: OptB,{font},62,{white},{white},&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,6,2,2,70,70,170,1
Style: VS,{font},52,{yellow},{yellow},&H00000000,&H00000000,-1,0,0,0,100,100,4,0,1,5,2,5,60,60,0,1
Style: Timer,{font},260,{yellow},{yellow},&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,10,3,5,60,60,0,1
Style: Badge,{font},84,{yellow},{yellow},&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,6,2,7,50,50,95,1

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
    mode = cfg.text_mode
    if mode == "none":
        return None
    head = _HEADER.format(font=cfg.font, white=WHITE, yellow=HIGHLIGHT)
    dur = clip["end"] - clip["start"]
    end = ass_time(dur)
    start = ass_time(0)
    if mode == "captions":
        return head + "".join(_caption_events(words, clip["start"], clip["end"]))
    if mode == "both":  # contexto no topo (quem chega no meio da conversa entende) + legenda por palavra
        ctx = esc(clip.get("context") or clip["title"])
        return (head + f"Dialogue: 0,{start},{end},Context,,0,0,0,,{{\\fad(200,0)}}{ctx}\n"
                + "".join(_caption_events(words, clip["start"], clip["end"])))
    if mode == "ranked":  # compilado "Top N": tema fixo + título, número grande na entrada e selo fixo
        pos = clip.get("rank_pos", 1)
        theme = esc(clip.get("theme") or "").upper()
        return (
            head
            + (f"Dialogue: 0,{start},{end},Theme,,0,0,0,,{theme}\n" if theme else "")
            + f"Dialogue: 0,{start},{end},Title,,0,0,0,,{esc(clip['title'])}\n"
            + f"Dialogue: 2,{start},{ass_time(min(1.4, dur))},Rank,,0,0,0,,"
              f"{{\\fad(120,500)\\fscx150\\fscy150\\t(0,260,\\fscx100\\fscy100)}}#{pos}\n"
            + f"Dialogue: 1,{start},{end},Badge,,0,0,0,,#{pos}\n"
            + f"Dialogue: 1,{start},{end},Hook,,0,0,0,,{{\\fad(250,0)}}{esc(clip['hook_text']).upper()}\n"
        )
    # titled: título no topo + gancho embaixo, durante todo o corte
    return (
        head
        + f"Dialogue: 0,{start},{end},Title,,0,0,0,,{esc(clip['title'])}\n"
        + f"Dialogue: 1,{start},{end},Hook,,0,0,0,,{{\\fad(250,0)}}{esc(clip['hook_text']).upper()}\n"
    )


def choice_events(choices: list[dict]) -> list[str]:
    """Formato "você prefere": as duas opções na tela durante a pergunta e, depois, a contagem.
    `choices`: {a, b, start, end, countdown}, com tempos na linha do tempo do vídeo."""
    out = []
    for c in choices:
        start, done = ass_time(c["start"]), ass_time(c["end"] + c["countdown"])
        out.append(f"Dialogue: 1,{start},{done},OptA,,0,0,0,,{{\\fad(200,0)}}{esc(c['a']).upper()}\n")
        out.append(f"Dialogue: 1,{start},{done},OptB,,0,0,0,,{{\\fad(200,0)}}{esc(c['b']).upper()}\n")
        out.append(f"Dialogue: 1,{start},{ass_time(c['end'])},VS,,0,0,0,,OU\n")
        total = int(c["countdown"])
        for k in range(total):  # 3, 2, 1 — um por segundo
            t0 = c["end"] + k
            out.append(f"Dialogue: 2,{ass_time(t0)},{ass_time(min(t0 + 1, c['end'] + c['countdown']))},"
                       f"Timer,,0,0,0,,{{\\fad(0,250)\\fscx130\\fscy130\\t(0,250,\\fscx100\\fscy100)}}"
                       f"{total - k}\n")
    return out


def build_narration_ass(words: list[dict], choices: list[dict], total_s: float, cfg: Config) -> str:
    """Legenda por palavra da narração inteira + o visual das escolhas."""
    head = _HEADER.format(font=cfg.font, white=WHITE, yellow=HIGHLIGHT)
    events = [] if cfg.text_mode == "none" else _caption_events(words, 0.0, total_s)
    return head + "".join(events + choice_events(choices))
