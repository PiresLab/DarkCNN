"""Roteiro: o Gemini escreve o texto que a voz vai narrar (nada de vídeo-fonte aqui)."""
from __future__ import annotations

import logging
import re
from typing import Literal

from pydantic import BaseModel, Field

from .analyze import PROMPTS_DIR
from .config import Config

log = logging.getLogger(__name__)

# Narração em PT-BR, ritmo de vídeo curto: ~2,6 palavras por segundo (estimativa; o tempo real vem do TTS)
WORDS_PER_SECOND = 2.6

FORMATS = {
    "curiosidade": (
        "Formato CURIOSIDADE: um fato pouco conhecido, surpreendente e verdadeiro, explicado de um jeito "
        "que dá vontade de contar para alguém. Comece pelo fato mais chocante, depois explique o porquê e "
        "feche com a consequência ou com o detalhe mais impressionante. Todas as linhas têm kind=\"fala\"."
    ),
    "voce-prefere": (
        "Formato VOCÊ PREFERE: um dilema atrás do outro, cada um com duas opções difíceis de escolher e "
        "mais ou menos equilibradas (se uma for obviamente melhor, não tem graça). De 3 a 5 dilemas.\n"
        "- Para CADA dilema, uma linha com kind=\"escolha\": o `text` é a pergunta falada "
        "(ex.: \"você prefere nunca mais sentir dor ou nunca mais sentir medo?\"), e `option_a` e `option_b` "
        "são os rótulos CURTOS que aparecem na tela (até 28 caracteres, sem \"você prefere\", só a opção: "
        "\"Nunca sentir dor\" / \"Nunca sentir medo\").\n"
        "- Depois de cada escolha, uma linha kind=\"fala\" curta comentando a consequência de cada lado, "
        "o que torna a decisão mais difícil.\n"
        "- Comece direto no primeiro dilema, sem introdução. Guarde o dilema mais pesado para o fim."
    ),
    "e-se": (
        "Formato E SE: uma hipótese absurda levada a sério (ex.: como seria um humano de hoje na época dos "
        "dinossauros). Descreva a cena com detalhes concretos e consequências em cadeia, uma puxando a outra, "
        "do detalhe mais óbvio ao mais inesperado. Feche com a conclusão mais surpreendente. "
        "Todas as linhas têm kind=\"fala\"."
    ),
}


class Line(BaseModel):
    text: str  # exatamente o que a voz fala
    kind: Literal["fala", "escolha"] = "fala"
    option_a: str = ""  # só em kind="escolha": rótulo curto na tela
    option_b: str = ""


class Script(BaseModel):
    title: str
    topic: str
    lines: list[Line] = Field(min_length=1)


class ScriptBatch(BaseModel):
    scripts: list[Script]


def format_block(fmt: str) -> str:
    """Instruções do formato pedido; um formato livre vira instrução genérica."""
    known = FORMATS.get(fmt)
    if known:
        return known
    return (f'Formato "{fmt}": siga essa ideia à risca, mantendo o estilo de vídeo curto narrado. '
            'Todas as linhas têm kind="fala".')


def build_prompt(cfg: Config) -> str:
    tpl = (PROMPTS_DIR / f"{cfg.script_prompt_version}.md").read_text(encoding="utf-8")
    topic = (
        f'TEMA OBRIGATÓRIO: "{cfg.topic}". Todos os roteiros são sobre isso (ângulos diferentes).'
        if cfg.topic else
        "TEMA: escolha você mesmo um assunto com apelo popular e que renda imagem mental forte "
        "(espaço, corpo humano, oceano profundo, animais, história, dinheiro, tecnologia, mistérios). "
        "Evite assunto datado, que envelhece rápido, e evite polêmica política ou religiosa."
    )
    return tpl.format(
        count=cfg.count, format_name=cfg.narrate_format, format_block=format_block(cfg.narrate_format),
        topic_block=topic, target_s=cfg.target_s, target_words=int(cfg.target_s * WORDS_PER_SECOND),
    )


_SPOKEN_JUNK = re.compile(r"[*#_`~\[\]<>|]|\((?:[^()]*)\)")


def clean_spoken(text: str) -> str:
    """Tira o que não se fala em voz alta (markdown, emoji, rubrica entre parênteses) e espaços sobrando."""
    t = _SPOKEN_JUNK.sub(" ", text)
    keep = []
    for ch in t:
        if 0x1F000 <= ord(ch) <= 0x1FAFF or 0x2600 <= ord(ch) <= 0x27BF:
            continue  # emoji
        keep.append(ch if ch.isprintable() else " ")  # quebra de linha vira espaço, não some
    return " ".join("".join(keep).split())


def clean_script(s: Script) -> Script:
    """Normaliza o roteiro: texto falável, sem linhas vazias, e opções só onde fazem sentido."""
    lines = []
    for ln in s.lines:
        text = clean_spoken(ln.text)
        if not text:
            continue
        a, b = (clean_spoken(ln.option_a)[:28], clean_spoken(ln.option_b)[:28]) if ln.kind == "escolha" else ("", "")
        kind = "escolha" if (ln.kind == "escolha" and a and b) else "fala"  # escolha sem as duas opções vira fala
        lines.append(Line(text=text, kind=kind, option_a=a if kind == "escolha" else "",
                          option_b=b if kind == "escolha" else ""))
    return Script(title=clean_spoken(s.title)[:60] or s.topic[:60] or "Sem título",
                  topic=clean_spoken(s.topic) or "sem tema", lines=lines or list(s.lines))


def estimated_duration_s(script: Script, cfg: Config) -> float:
    """Estimativa antes do TTS (palavras + pausas), só para avisar se o roteiro saiu fora do alvo."""
    words = sum(len(ln.text.split()) for ln in script.lines)
    pauses = cfg.pause_s * max(0, len(script.lines) - 1)
    pauses += cfg.countdown_s * sum(1 for ln in script.lines if ln.kind == "escolha")
    return words / WORDS_PER_SECOND + pauses
