"""Posição e tamanho dos textos na tela, por tipo de vídeo (editável em Configurações > Posição dos textos).

Tudo em pixels do vídeo 1080x1920. `y` depende da âncora da camada:
- top: borda de CIMA do texto (o texto cresce para baixo);
- bottom: borda de BAIXO do texto (o texto cresce para cima);
- center: centro do texto.
"""
from __future__ import annotations

from typing import Literal

WIDTH, HEIGHT = 1080, 1920

Anchor = Literal["top", "bottom", "center"]

# tipo -> camada -> padrão. `sample` é só para a prévia do editor.
LAYERS: dict[str, dict[str, dict]] = {
    "cuts": {
        "contexto": {"label": "Contexto (topo)", "anchor": "top", "y": 270, "size": 54,
                     "sample": "Ele conta como perdeu tudo em um dia"},
        "legenda": {"label": "Legenda da fala", "anchor": "bottom", "y": 1400, "size": 84,
                    "sample": "E AÍ EU PERCEBI"},
        "titulo": {"label": "Título (topo)", "anchor": "top", "y": 300, "size": 66,
                   "sample": "O lance mais difícil do ano"},
        "gancho": {"label": "Frase-gancho", "anchor": "bottom", "y": 1590, "size": 86,
                   "sample": "OLHA ISSO"},
    },
    "compile": {
        "selo": {"label": "Selo #N (canto)", "anchor": "top", "y": 95, "size": 84, "sample": "#3"},
        "apresentacao": {"label": "Apresentação do vídeo", "anchor": "top", "y": 340, "size": 72,
                         "sample": "TOP 5 FINALIZAÇÕES"},
        "numero": {"label": "Número grande (entrada)", "anchor": "center", "y": 960, "size": 300, "sample": "#3"},
        "clipe": {"label": "Legenda do clipe", "anchor": "bottom", "y": 1520, "size": 86,
                  "sample": "QUE GOLPE"},
    },
    "narrate": {
        "opcoes": {"label": "Opções (você prefere)", "anchor": "center", "y": 960, "size": 70,
                   "sample": "NUNCA SENTIR DOR"},
        "contagem": {"label": "Contagem", "anchor": "center", "y": 1170, "size": 180, "sample": "3"},
        "legenda": {"label": "Legenda da narração", "anchor": "bottom", "y": 1400, "size": 84,
                    "sample": "VOCÊ SABIA"},
    },
}

KINDS = {"cuts": "Cortes", "compile": "Compilado", "narrate": "Narração"}

# camadas que aparecem juntas em cada modo de texto dos cortes (as outras ficam fora da prévia)
CUTS_VARIANTS = {"both": ["contexto", "legenda"], "captions": ["legenda"], "titled": ["titulo", "gancho"]}


def check(text_layout: dict) -> None:
    """Erro claro para tipo/camada desconhecidos (um typo no config.yaml não pode ser ignorado)."""
    for kind, layers in text_layout.items():
        if kind not in LAYERS:
            raise ValueError(f"text_layout: tipo desconhecido '{kind}' (use {', '.join(LAYERS)})")
        for name in layers:
            if name not in LAYERS[kind]:
                raise ValueError(f"text_layout.{kind}: camada desconhecida '{name}' (use {', '.join(LAYERS[kind])})")


def resolve(text_layout: dict, kind: str) -> dict[str, tuple[int, int]]:
    """camada -> (y, tamanho), com o que o usuário salvou por cima do padrão."""
    saved = text_layout.get(kind) or {}
    out = {}
    for name, d in LAYERS[kind].items():
        box = saved.get(name)
        if box is None:
            out[name] = (d["y"], d["size"])
        else:
            y = box["y"] if isinstance(box, dict) else box.y
            size = box["size"] if isinstance(box, dict) else box.size
            out[name] = (int(y), int(size))
    return out


def margin_v(anchor: str, y: int) -> int:
    """MarginV do ASS para a âncora: top mede do topo, bottom mede da base."""
    if anchor == "top":
        return max(0, y)
    if anchor == "bottom":
        return max(0, HEIGHT - y)
    return 0


def spec() -> dict:
    """Para o editor da UI: tipos, camadas e padrões."""
    return {"width": WIDTH, "height": HEIGHT, "kinds": KINDS, "layers": LAYERS, "cuts_variants": CUTS_VARIANTS}
