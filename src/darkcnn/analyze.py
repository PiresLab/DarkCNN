"""Seleção dos cortes: transcrição numerada -> candidatos por ID de frase -> tempos validados.

O Gemini nunca devolve tempos (erro mediano medido ~0,5 s): devolve IDs de frase, e os tempos vêm
das palavras do Whisper. Assim todo corte começa e termina em limite de frase.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .config import Config
from .transcribe import Sentence

log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"


class Candidate(BaseModel):
    start_id: int
    end_id: int
    title: str
    hook_text: str
    reason: str
    hook: int = Field(ge=1, le=10)
    standalone: int = Field(ge=1, le=10)
    emotion: int = Field(ge=1, le=10)
    payoff: int = Field(ge=1, le=10)
    score: int = Field(ge=1, le=10)


class Selection(BaseModel):
    candidates: list[Candidate]


class TalkCandidate(Candidate):
    """Candidato do perfil de fala: + contexto para o topo da tela e a frase do gancho (verificável)."""
    context: str  # descritivo, <= 80 caracteres: situa quem chega no meio da conversa
    hook_quote: str  # frase do gancho COPIADA da transcrição


class TalkSelection(BaseModel):
    candidates: list[TalkCandidate]


def fmt_ts(sec: float) -> str:
    s = int(sec)
    return f"{s // 60:02d}:{s % 60:02d}"


def build_transcript(sentences: list[Sentence]) -> str:
    return "\n".join(f"[{s['id']}] ({fmt_ts(s['start'])}) {s['text']}" for s in sentences)


def transcript_hash(sentences: list[Sentence]) -> str:
    return hashlib.sha256(build_transcript(sentences).encode("utf-8")).hexdigest()[:16]


def n_candidates(cfg: Config) -> int:
    """Quantos candidatos pedir: com juiz, mais opções para ele comparar; sem juiz, o dobro (o código
    descarta inválidos/sobrepostos e fica com os N melhores)."""
    return cfg.clips_per_video * (cfg.judge_factor if cfg.judge else 2)


def pool_size(cfg: Config) -> int:
    """Quantos candidatos já resolvidos seguem para o juiz (ou, sem juiz, quantos cortes saem)."""
    return min(cfg.judge_max_candidates, n_candidates(cfg)) if cfg.judge else cfg.clips_per_video


def build_prompt(sentences: list[Sentence], cfg: Config) -> str:
    tpl = (PROMPTS_DIR / f"{cfg.prompt_version}.md").read_text(encoding="utf-8")
    return tpl.format(
        n_candidates=n_candidates(cfg),
        min_s=int(cfg.min_clip_s),
        max_s=int(cfg.max_clip_s),
        transcript=build_transcript(sentences),
    )


def _norm_tokens(text: str) -> list[str]:
    import re
    import unicodedata

    t = unicodedata.normalize("NFD", text.lower())
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn")
    return re.findall(r"[a-z0-9]+", t)


def hook_quote_ok(quote: str, sentences: list[Sentence], start_id: int, span: int = 2,
                  min_overlap: float = 0.7) -> bool:
    """A frase do gancho que o modelo citou está mesmo no começo do corte? (anti-alucinação)"""
    q = _norm_tokens(quote)
    if len(q) < 3 or not (0 <= start_id < len(sentences)):
        return False
    window = set(_norm_tokens(" ".join(s["text"] for s in sentences[start_id:start_id + span + 1])))
    return sum(1 for t in q if t in window) / len(q) >= min_overlap


def verify_hooks(cands: list[dict[str, Any]], sentences: list[Sentence], penalty: int = 2) -> int:
    """Marca `hook_ok` e tira `penalty` pontos da nota de quem cita um gancho que não está na transcrição.
    Devolve quantos falharam."""
    bad = 0
    for c in cands:
        ok = hook_quote_ok(c.get("hook_quote", ""), sentences, c["start_id"])
        c["hook_ok"] = ok
        if not ok:
            bad += 1
            c["score"] = max(1, c["score"] - penalty)
    return bad


def _clip(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


_NEIGHBOR_MARGIN = 0.02  # folga para não cortar rente ao início/fim da frase vizinha


def _span(sentences: list[Sentence], s: int, e: int, cfg: Config, total: float) -> tuple[float, float]:
    """Tempo do corte = frases s..e com um respiro, mas o respiro nunca invade a frase vizinha
    (o intervalo entre frases pode ser de ~0,1 s, menor que o padding)."""
    start = sentences[s]["start"] - cfg.pad_start_s
    if s > 0:
        start = max(start, sentences[s - 1]["end"] + _NEIGHBOR_MARGIN)
    end = sentences[e]["end"] + cfg.pad_end_s
    if e + 1 < len(sentences):
        end = min(end, sentences[e + 1]["start"] - _NEIGHBOR_MARGIN)
    start = max(0.0, min(start, sentences[s]["start"]))  # nunca depois do início da própria frase
    end = min(total, max(end, sentences[e]["end"]))  # nunca antes do fim da própria frase
    return start, end


def resolve(
    candidates: list[dict[str, Any]], sentences: list[Sentence], cfg: Config, total: float
) -> tuple[list[dict], list[dict]]:
    """IDs -> tempos. Devolve (aceitos ordenados por nota, rejeitados com motivo)."""
    ok: list[dict] = []
    rejected: list[dict] = []
    n = len(sentences)

    for c in candidates:
        s, e = c["start_id"], c["end_id"]
        if not (0 <= s < n and 0 <= e < n):
            rejected.append({**c, "reason_rejected": f"id fora do intervalo 0..{n - 1}"})
            continue
        if s > e:
            s, e = e, s
        start, end = _span(sentences, s, e, cfg, total)
        adjusted = False
        # curto demais: estende o fim por frases seguintes (sem passar do máximo)
        while end - start < cfg.min_clip_s and e + 1 < n:
            _, end2 = _span(sentences, s, e + 1, cfg, total)
            if end2 - start > cfg.max_clip_s:
                break
            e, end, adjusted = e + 1, end2, True
        # longo demais: tira frases do final (continua terminando em fim de frase)
        while end - start > cfg.max_clip_s and e > s:
            e -= 1
            _, end = _span(sentences, s, e, cfg, total)
            adjusted = True
        dur = end - start
        if dur < cfg.min_clip_s:
            rejected.append({**c, "reason_rejected": f"curto demais ({dur:.0f}s < {cfg.min_clip_s:.0f}s)"})
            continue
        if dur > cfg.max_clip_s:
            rejected.append({**c, "reason_rejected": f"longo demais ({dur:.0f}s > {cfg.max_clip_s:.0f}s)"})
            continue
        ok.append({
            **c,
            "start_id": s, "end_id": e,
            "title": _clip(c["title"], 60), "hook_text": _clip(c["hook_text"], 40),
            **({"context": _clip(c["context"], 80)} if c.get("context") else {}),
            "start": round(start, 3), "end": round(end, 3), "duration": round(dur, 3),
            "adjusted": adjusted,
        })

    # nota maior primeiro (desempate: gancho, depois ordem no vídeo); sem sobreposição de FRASES
    # (o respiro dos cortes vizinhos pode se tocar sem problema)
    ok.sort(key=lambda c: (-c["score"], -c["hook"], c["start"]))
    accepted: list[dict] = []
    for c in ok:
        clash = next((a for a in accepted if c["start_id"] <= a["end_id"] and a["start_id"] <= c["end_id"]), None)
        if clash is not None:
            rejected.append({**c, "reason_rejected": f"sobreposto ao corte de {fmt_ts(clash['start'])}"})
        elif len(accepted) >= cfg.clips_per_video:
            rejected.append({**c, "reason_rejected": "acima da quantidade pedida"})
        else:
            accepted.append(c)
    for rank, c in enumerate(accepted, 1):
        c["rank"] = rank
    return accepted, rejected
