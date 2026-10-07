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
                 info: dict, meta: dict | None = None) -> Path:
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
    L = [
        f"# Revisão — {video.name}", "",
        *([f"- **Vídeo original:** {meta.get('title')} — canal {meta.get('channel')}"] if meta else []),
        f"- **Fonte:** {cfg.source or '⚠ não informada'}",
        f"- **Licença/permissão:** {cfg.license or '⚠ não informada'}",
        f"- **Duração do vídeo:** {fmt_ts(info['duration'])} — **Cortes:** {len(clips)} "
        f"({cfg.min_clip_s:.0f}-{cfg.max_clip_s:.0f}s) — **Modelo:** {cfg.gemini_model}",
        f"- **Perfil:** {cfg.profile} — **Texto na tela:** {cfg.text_mode} — **Layout:** {cfg.layout}",
    ]
    if warn:
        L += ["", "> ⚠ **Antes de postar:** " + "; ".join(warn) + "."]
    L += ["", "| # | Nota | Duração | Na fonte | Título |", "|---|---|---|---|---|"]
    for c in clips:
        L.append(f"| {c['rank']} | {c['score']} | {c['duration']:.0f}s | {fmt_ts(c['start'])}–{fmt_ts(c['end'])} "
                 f"| {c['title']} |")
    for c in clips:
        link = source_link(cfg.source, c["start"])
        L += [
            "", f"## {c['rank']}. {c['title']}", "",
            f"- **Arquivo:** `{c['file']}`",
            f"- **Frase-gancho (tela):** {c['hook_text']}",
            f"- **Nota:** {c['score']}/10 (gancho {c['hook']}, autocontido {c['standalone']}, "
            f"emoção {c['emotion']}, payoff {c['payoff']})",
            f"- **Motivo:** {c['reason']}",
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
