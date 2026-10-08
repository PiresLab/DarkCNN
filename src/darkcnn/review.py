"""Material de revisão manual: review.md, selection.json (editável) e rejected.json."""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .analyze import fmt_ts
from .config import Config


def source_link(source: str | None, t: float) -> str | None:
    """Link com tempo (&t=) quando a fonte é um vídeo do YouTube; senão None."""
    if not source or not source.startswith(("http://", "https://")):
        return None
    u = urlparse(source)
    host = (u.hostname or "").removeprefix("www.")
    if host not in ("youtube.com", "m.youtube.com", "youtu.be"):
        return None
    q = dict(parse_qsl(u.query))
    q["t"] = f"{int(t)}s"
    return urlunparse(u._replace(query=urlencode(q)))


def license_warnings(lic: str | None) -> list[str]:
    """Avisos sobre a licença informada/detectada (não é parecer jurídico)."""
    l = (lic or "").lower()
    out = []
    if "padrão do youtube" in l:
        out.append("licença padrão do YouTube: reutilizar o vídeo exige permissão do canal")
    if re.search(r"-nd\b|noderiv|no derivat|sem derivad", l):
        out.append("licença com ND (sem obras derivadas): editar/cortar o vídeo não é permitido")
    if re.search(r"-nc\b|noncommercial|non-commercial|não comercial|nao comercial", l):
        out.append("licença com NC (não comercial): não serve para canal monetizado")
    return out


def write_review(out_dir: Path, video: Path, cfg: Config, clips: list[dict], rejected: list[dict],
                 info: dict, meta: dict | None = None, compilation: dict | None = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "selection.json").write_text(json.dumps(clips, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "rejected.json").write_text(json.dumps(rejected, ensure_ascii=False, indent=2), encoding="utf-8")

    warn = []
    if not cfg.source:
        warn.append("fonte não informada (`--source`)")
    if not cfg.license:
        warn.append("licença/permissão não informada (`--license`)")
    warn += license_warnings(cfg.license)
    if cfg.profile == "visual":
        warn.append("nomes de lutadores/eventos/pessoas no título vêm do Gemini e podem estar errados: confira")
    if compilation:
        if compilation["duration"] > 180:
            warn.append(f"o compilado tem {compilation['duration']:.0f}s e passa de 3 min (limite do YouTube Shorts)")
        if len(clips) < compilation["requested"]:
            warn.append(f"só {len(clips)} momento(s) combinaram com o tema (pedido: {compilation['requested']})")
        warn.append("o compilado junta vários trechos de UM vídeo de terceiros: a licença acima vale para todos eles")
    L = [
        f"# Revisão — {video.name}", "",
        *([f"- **Compilado:** `{compilation['file']}` — tema \"{compilation['theme']}\" — "
           f"{fmt_ts(compilation['duration'])} (contagem regressiva: o #1 aparece por último)"] if compilation else []),
        *([f"- **Vídeo original:** {meta.get('title')} — canal {meta.get('channel')}"] if meta else []),
        f"- **Fonte:** {cfg.source or '⚠ não informada'}",
        f"- **Licença/permissão:** {cfg.license or '⚠ não informada'}",
        f"- **Duração do vídeo:** {fmt_ts(info['duration'])} — **Cortes:** {len(clips)} "
        f"({cfg.min_clip_s:.0f}-{cfg.max_clip_s:.0f}s) — **Modelo:** {cfg.gemini_model}",
        f"- **Perfil:** {cfg.profile} — **Texto na tela:** {cfg.text_mode} — **Layout:** {cfg.layout}",
    ]
    if warn:
        L += ["", "> ⚠ **Antes de postar:** " + "; ".join(warn) + "."]
    if compilation:
        clips = sorted(clips, key=lambda c: c.get("order", c["rank"]))  # ordem em que aparecem no vídeo
    judged = any("judge_score" in c for c in clips)
    at_col = "No compilado | " if compilation else ""
    L += ["", "| # | Nota 1ª passada | " + ("Nota do juiz | " if judged else "") + f"Duração | {at_col}Na fonte | Título |",
          "|---|---|" + ("---|" if judged else "") + "---|" + ("---|" if compilation else "") + "---|---|"]
    for c in clips:
        L.append(f"| {c['rank']} | {c['score']} | " + (f"{c.get('judge_score', '—')} | " if judged else "")
                 + f"{c['duration']:.0f}s | " + (f"{fmt_ts(c.get('at', 0))} | " if compilation else "")
                 + f"{fmt_ts(c['start'])}–{fmt_ts(c['end'])} | {c['title']} |")
    for c in clips:
        link = source_link(cfg.source, c["start"])
        L += [
            "", f"## {c['rank']}. {c['title']}", "",
            f"- **Arquivo:** `{c['file']}`",
            *([f"- **Contexto (topo da tela):** {c['context']}"] if c.get("context") else []),
            f"- **Frase-gancho (tela):** {c['hook_text']}",
            f"- **Nota:** {c['score']}/10 (gancho {c['hook']}, autocontido {c['standalone']}, "
            f"emoção {c['emotion']}, payoff {c['payoff']})",
            f"- **Motivo:** {c['reason']}",
            *([f"- **Encaixe no tema:** {c['fit']}/10"] if c.get("fit") else []),
            *([f"- **Juiz ({c['judge_score']}/100):** + {c['judge_strength']} / − {c['judge_weakness']}"]
              if "judge_score" in c else []),
            *(["- ⚠ O gancho citado pelo modelo não confere com a transcrição (nota reduzida): confira o início."]
              if c.get("hook_ok") is False else []),
            f"- **Na fonte:** {fmt_ts(c['start'])}–{fmt_ts(c['end'])}" + (f" — [abrir no ponto]({link})" if link else ""),
        ]
        if c.get("adjusted"):
            L.append("- _Início/fim ajustados pelo código para caber na duração pedida (continua em limite de frase)._")
    if rejected:
        L += ["", "## Candidatos descartados", "",
              "Estão em `rejected.json`. Para resgatar um, copie para `selection.json` e rode `render`.", ""]
        for r in rejected:
            L.append(f"- {r.get('title', '?')} — {r['reason_rejected']}")
    L += ["", "---",
          "Para ajustar um corte, edite `start`/`end`/`title`/`hook_text` em `selection.json` e rode "
          "`python -m darkcnn render <video>`.", ""]
    p = out_dir / "review.md"
    p.write_text("\n".join(L), encoding="utf-8")
    return p


NARRATION_WARNINGS = [
    "narração gerada por IA sobre gameplay em série é o caso que as plataformas tratam como conteúdo "
    "em massa: varie formato, voz e tema, e acrescente algo seu",
    "a voz é sintética: marque o vídeo como conteúdo alterado/sintético onde a plataforma pedir",
    "o roteiro é escrito por IA e pode conter erro de fato: confira os dados antes de publicar",
]


def write_narration_review(out_dir: Path, cfg: Config, videos: list[dict]) -> Path:
    """review.md do comando `narrate`: roteiro completo, voz, gameplay usada e os avisos de risco."""
    out_dir.mkdir(parents=True, exist_ok=True)
    L = [
        f"# Revisão — narração sobre gameplay ({cfg.narrate_format})", "",
        f"- **Vídeos:** {len(videos)} — **Voz:** {', '.join(sorted({v['voice'] for v in videos}))} — "
        f"**Modelo do roteiro:** {cfg.gemini_model} — **Modelo da voz:** {cfg.tts_model}",
        f"- **Tema:** {cfg.topic or 'escolhido pela IA'} — **Alvo de duração:** {cfg.target_s:.0f}s",
        f"- **Texto na tela:** {cfg.text_mode} — **Layout:** {cfg.layout} — "
        f"**Volume da gameplay:** {cfg.game_volume:.0%}",
        "", "> ⚠ **Antes de postar:** " + "; ".join(NARRATION_WARNINGS) + ".",
        "", "| # | Duração | Título | Tema | Gameplay |", "|---|---|---|---|---|",
    ]
    for v in videos:
        L.append(f"| {v['rank']} | {v['duration']:.0f}s | {v['title']} | {v['topic']} | "
                 f"{Path(v['gameplay']).name} |")
    for v in videos:
        L += ["", f"## {v['rank']}. {v['title']}", "",
              f"- **Arquivo:** `{v['file']}` — {v['duration']:.0f}s",
              f"- **Tema:** {v['topic']}",
              f"- **Gameplay:** `{Path(v['gameplay']).name}` a partir de {fmt_ts(v['gameplay_start'])}"
              + (" (repetindo)" if v.get("gameplay_loop") else ""),
              "", "**Roteiro falado:**", ""]
        for ln in v["lines"]:
            mark = f"**[{ln['option_a']} × {ln['option_b']}]** " if ln["kind"] == "escolha" else ""
            L.append(f"- `{fmt_ts(ln['start'])}` {mark}{ln['text']}")
        if v.get("match", 1) < 0.5:
            L.append("")
            L.append("- ⚠ O Whisper reconheceu pouco desta narração: confira se a legenda está em sincronia.")
    L += ["", "---",
          "Para mudar um roteiro, edite `scripts.json` e rode `darkcnn narrate` de novo: só o que mudou "
          "é sintetizado e renderizado.", ""]
    p = out_dir / "review.md"
    p.write_text("\n".join(L), encoding="utf-8")
    return p
