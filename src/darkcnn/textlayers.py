"""Texto na tela em ASS (libass): legenda por palavra, ou título no topo + frase-gancho embaixo.
Posição e tamanho de cada camada vêm de `layout.py` + `Config.text_layout` (editor em Configurações)."""
from __future__ import annotations

from . import layout as L
from .config import Config

HIGHLIGHT = "&H0000FFFF&"  # amarelo (ASS usa BGR)
WHITE = "&H00FFFFFF&"
_Y, _W = "&H0000FFFF", "&H00FFFFFF"
_SHADE, _DARK, _NONE = "&H64000000", "&H96000000", "&H00000000"

_HEAD = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
"""
_EVENTS = """
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

# estilo -> (tamanho base, cor, contorno base, sombra, fundo, espaçamento, alinhamento ASS, margem lateral)
_STYLES: dict[str, tuple[int, str, int, int, str, int, int, int]] = {
    "Default": (84, _W, 7, 2, _SHADE, 0, 2, 60),
    "Title": (66, _W, 6, 2, _SHADE, 0, 8, 70),
    "Hook": (86, _Y, 7, 2, _SHADE, 0, 2, 60),
    "Context": (54, _W, 5, 2, _SHADE, 0, 8, 80),
    "About": (72, _Y, 6, 2, _SHADE, 0, 8, 70),
    "Clip": (86, _Y, 7, 2, _SHADE, 0, 2, 60),
    "Rank": (300, _Y, 12, 3, _SHADE, 0, 5, 60),
    "Badge": (84, _Y, 6, 2, _SHADE, 0, 7, 50),
    "OptA": (70, _W, 7, 2, _DARK, 0, 2, 90),
    "OptB": (70, _W, 7, 2, _DARK, 0, 8, 90),
    "VS": (50, _Y, 5, 2, _NONE, 4, 5, 60),
    "Timer": (180, _Y, 10, 3, _NONE, 0, 5, 60),
}

# camada do layout -> estilo(s) que ela controla (com a proporção de tamanho de cada um)
_LAYER_STYLES: dict[str, dict[str, dict[str, float]]] = {
    "cuts": {"legenda": {"Default": 1}, "contexto": {"Context": 1}, "titulo": {"Title": 1}, "gancho": {"Hook": 1}},
    "compile": {"apresentacao": {"About": 1}, "clipe": {"Clip": 1}, "numero": {"Rank": 1}, "selo": {"Badge": 1}},
    "narrate": {"legenda": {"Default": 1}, "opcoes": {"OptA": 1, "OptB": 1, "VS": 50 / 70}, "contagem": {"Timer": 1}},
}

# padrões (pixels do vídeo), mantidos como constantes para quem só quer o layout de fábrica
CENTER_X = L.WIDTH // 2
CENTER_Y = L.LAYERS["narrate"]["opcoes"]["y"]
OPT_GAP = 46  # distância do centro até a borda de cada opção (no tamanho padrão 70)
TIMER_Y = L.LAYERS["narrate"]["contagem"]["y"]  # entre o bloco das opções e a legenda


def ass_time(t: float) -> str:
    cs = max(0, round(t * 100))
    return f"{cs // 360000}:{cs % 360000 // 6000:02d}:{cs % 6000 // 100:02d}.{cs % 100:02d}"


def esc(text: str) -> str:
    """Tira o que o ASS interpretaria como comando/quebra de linha."""
    return " ".join(text.replace("\\", " ").replace("{", "(").replace("}", ")").split())


def header(cfg: Config, kind: str) -> tuple[str, dict[str, tuple[int, int]]]:
    """Cabeçalho ASS do tipo de vídeo com o layout do usuário aplicado. Devolve (texto, camada -> (y, tamanho))."""
    boxes = L.resolve(cfg.text_layout, kind)
    size = {n: s[0] for n, s in _STYLES.items()}
    mv = {n: 0 for n in _STYLES}
    for layer, styles in _LAYER_STYLES[kind].items():
        y, sz = boxes[layer]
        anchor = L.LAYERS[kind][layer]["anchor"]
        for name, ratio in styles.items():
            size[name] = max(8, round(sz * ratio))
            mv[name] = L.margin_v(anchor, y)
    lines = []
    for name, (base, color, outline, shadow, back, spacing, align, ml) in _STYLES.items():
        out = max(2, round(outline * size[name] / base))
        lines.append(f"Style: {name},{cfg.font},{size[name]},{color},{color},&H00000000,{back},-1,0,0,0,100,100,"
                     f"{spacing},0,1,{out},{shadow},{align},{ml},{ml},{mv[name]},1\n")
    return _HEAD + "".join(lines) + _EVENTS, boxes


def group_words(words: list[dict], max_words: int = 3, max_chars: int = 16) -> list[list[dict]]:
    """Agrupa palavras sem estourar a largura: no máximo `max_words` e ~`max_chars` caracteres."""
    groups: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        size = sum(len(x["w"]) for x in cur) + len(cur) + len(w["w"])
        if cur and (w.get("brk") or len(cur) >= max_words or size > max_chars):
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


def ranked_events(clip: dict, dur: float, boxes: dict[str, tuple[int, int]]) -> str:
    """Compilado "Top N": no topo o que é o vídeo (tema + contagem); embaixo, o que acontece neste clipe;
    número grande na entrada e selo fixo no canto."""
    start, end = ass_time(0), ass_time(dur)
    pos = clip.get("rank_pos", 1)
    theme = esc(clip.get("theme") or "").upper()
    total = clip.get("total")
    sub = f"OS {total} MELHORES MOMENTOS · DO #{total} AO #1" if total and total > 1 else "O MELHOR MOMENTO"
    about_sz, clip_sz = boxes["apresentacao"][1], boxes["clipe"][1]
    about = (f"{theme}\\N" if theme else "") + f"{{\\fs{round(about_sz * 0.66)}\\c{WHITE}}}{sub}"
    title = f"{{\\fs{round(clip_sz * 0.72)}\\c{WHITE}}}{esc(clip['title'])}\\N{{\\fs{clip_sz}\\c{HIGHLIGHT}}}"
    return (
        f"Dialogue: 0,{start},{end},About,,0,0,0,,{about}\n"
        + f"Dialogue: 2,{start},{ass_time(min(1.4, dur))},Rank,,0,0,0,,"
          f"{{\\an5\\pos({CENTER_X},{boxes['numero'][0]})\\fad(120,500)\\fscx150\\fscy150"
          f"\\t(0,260,\\fscx100\\fscy100)}}#{pos}\n"
        + f"Dialogue: 1,{start},{end},Badge,,0,0,0,,#{pos}\n"
        + f"Dialogue: 1,{start},{end},Clip,,0,0,0,,{{\\fad(250,0)}}{title}{esc(clip['hook_text']).upper()}\n"
    )


def build_ass(clip: dict, words: list[dict], cfg: Config) -> str | None:
    """Conteúdo do .ass do corte, ou None se text_mode == 'none'."""
    mode = cfg.text_mode
    if mode == "none":
        return None
    head, boxes = header(cfg, "compile" if mode == "ranked" else "cuts")
    dur = clip["end"] - clip["start"]
    end = ass_time(dur)
    start = ass_time(0)
    if mode == "captions":
        return head + "".join(_caption_events(words, clip["start"], clip["end"]))
    if mode == "both":  # contexto no topo (quem chega no meio da conversa entende) + legenda por palavra
        ctx = esc(clip.get("context") or clip["title"])
        return (head + f"Dialogue: 0,{start},{end},Context,,0,0,0,,{{\\fad(200,0)}}{ctx}\n"
                + "".join(_caption_events(words, clip["start"], clip["end"])))
    if mode == "ranked":
        return head + ranked_events(clip, dur, boxes)
    # titled: título no topo + gancho embaixo, durante todo o corte
    return (
        head
        + f"Dialogue: 0,{start},{end},Title,,0,0,0,,{esc(clip['title'])}\n"
        + f"Dialogue: 1,{start},{end},Hook,,0,0,0,,{{\\fad(250,0)}}{esc(clip['hook_text']).upper()}\n"
    )


def choice_events(choices: list[dict], boxes: dict[str, tuple[int, int]] | None = None) -> list[str]:
    """Formato "você prefere": as duas opções juntas no centro durante a pergunta e a contagem.
    O bloco fica junto: a opção A termina um pouco acima do "OU" e a B começa um pouco abaixo; a A cresce
    para cima e a B para baixo, então rótulo de duas linhas não invade o vizinho.
    `choices`: {a, b, start, end, countdown}, com tempos na linha do tempo do vídeo."""
    cy, osz = (boxes or {}).get("opcoes", (CENTER_Y, 70))
    ty = (boxes or {}).get("contagem", (TIMER_Y, 180))[0]
    gap = round(OPT_GAP * osz / 70)
    out = []
    for c in choices:
        start, done = ass_time(c["start"]), ass_time(c["end"] + c["countdown"])
        out.append(f"Dialogue: 1,{start},{done},OptA,,0,0,0,,"
                   f"{{\\an2\\pos({CENTER_X},{cy - gap})\\fad(200,0)}}{esc(c['a']).upper()}\n")
        out.append(f"Dialogue: 1,{start},{done},OptB,,0,0,0,,"
                   f"{{\\an8\\pos({CENTER_X},{cy + gap})\\fad(200,0)}}{esc(c['b']).upper()}\n")
        out.append(f"Dialogue: 1,{start},{done},VS,,0,0,0,,{{\\an5\\pos({CENTER_X},{cy})}}OU\n")
        total = int(c["countdown"])
        for k in range(total):  # 3, 2, 1 — um por segundo
            t0 = c["end"] + k
            out.append(f"Dialogue: 2,{ass_time(t0)},{ass_time(min(t0 + 1, c['end'] + c['countdown']))},"
                       f"Timer,,0,0,0,,{{\\an5\\pos({CENTER_X},{ty})\\fad(0,250)\\fscx130\\fscy130"
                       f"\\t(0,250,\\fscx100\\fscy100)}}{total - k}\n")
    return out


def _clear_countdown(words: list[dict], choices: list[dict]) -> list[dict]:
    """Nenhuma palavra da legenda vive durante a contagem: corta o que invade a janela e descarta o que nasce nela."""
    out = []
    for w in words:
        keep = True
        for c in choices:
            lo, hi = c["end"], c["end"] + c["countdown"]
            if lo <= w["start"] < hi:
                keep = False
                break
            if w["start"] < lo < w["end"]:
                w = {**w, "end": lo}
        if keep:
            out.append(w)
    # a 1ª palavra depois de cada contagem abre um grupo novo: senão a anterior ficaria na tela até ela
    for c in choices:
        hi = c["end"] + c["countdown"]
        for i, w in enumerate(out):
            if w["start"] >= hi and (i == 0 or out[i - 1]["start"] < c["end"]):
                out[i] = {**w, "brk": True}
    return out


def build_narration_ass(words: list[dict], choices: list[dict], total_s: float, cfg: Config) -> str:
    """Legenda por palavra da narração inteira + o visual das escolhas."""
    head, boxes = header(cfg, "narrate")
    events = [] if cfg.text_mode == "none" else _caption_events(_clear_countdown(words, choices), 0.0, total_s)
    return head + "".join(events + choice_events(choices, boxes))


# ---------------------------------------------------------------- prévia do editor
def preview_ass(cfg: Config, kind: str, variant: str | None = None) -> str:
    """Um quadro de exemplo com todas as camadas do tipo (o texto é o `sample` de layout.py)."""
    head, boxes = header(cfg, kind)
    s = {k: v["sample"] for k, v in L.LAYERS[kind].items()}
    t0, t1 = ass_time(0), ass_time(5)
    if kind == "compile":
        clip = {"rank_pos": 3, "theme": s["apresentacao"], "total": 5, "title": "Mata-leão no último segundo",
                "hook_text": s["clipe"]}
        # o número grande só aparece na entrada: na prévia ele fica parado no meio do tempo
        ev = ranked_events(clip, 5.0, boxes).replace("\\fad(120,500)\\fscx150\\fscy150\\t(0,260,\\fscx100\\fscy100)", "")
        ev = ev.replace(f"Dialogue: 2,{t0},{ass_time(1.4)},Rank", f"Dialogue: 2,{t0},{t1},Rank")
        return head + ev
    if kind == "narrate":
        ch = [{"a": s["opcoes"], "b": "NUNCA SENTIR MEDO", "start": 0.0, "end": 0.0, "countdown": 5.0}]
        return (head + f"Dialogue: 0,{t0},{t1},Default,,0,0,0,,{esc(s['legenda'])}\n"
                + "".join(choice_events(ch, boxes)))  # o quadro da prévia (t=1 s) pega o "4" da contagem
    layers = L.CUTS_VARIANTS.get(variant or "both", L.CUTS_VARIANTS["both"])
    ev = []
    if "contexto" in layers:
        ev.append(f"Dialogue: 0,{t0},{t1},Context,,0,0,0,,{esc(s['contexto'])}\n")
    if "titulo" in layers:
        ev.append(f"Dialogue: 0,{t0},{t1},Title,,0,0,0,,{esc(s['titulo'])}\n")
    if "gancho" in layers:
        ev.append(f"Dialogue: 1,{t0},{t1},Hook,,0,0,0,,{esc(s['gancho'])}\n")
    if "legenda" in layers:
        ev.append(f"Dialogue: 0,{t0},{t1},Default,,0,0,0,,{esc(s['legenda'])}\n")
    return head + "".join(ev)
