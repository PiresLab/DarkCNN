"""Spike 04 — mede o erro dos timestamps do Gemini usando o Whisper (por palavra) como referência.

Uso: python spikes/04_compare_timestamps.py
Lê spikes/out/gemini_transcript.json e spikes/out/whisper_words.json (mesmo --start/--dur).
Referência = Whisper, que também erra; o número que importa é a ORDEM DE GRANDEZA do erro
do Gemini (décimos de segundo vs. segundos) e se ele AUMENTA ao longo do áudio (deriva).
"""
from __future__ import annotations

import json
import statistics as st
import unicodedata

from common import OUT, parse_ts, save_json


def norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(ch for ch in s if ch.isalnum())


def best_match(keys: list[str], wn: list[str], expected: float, times: list[float], window: float = 25.0):
    """Índice i onde wn[i:i+k] melhor casa com `keys`, perto do tempo esperado (>= 2 acertos)."""
    k = len(keys)
    best = (0, 1e9, None)
    for i in range(len(wn) - k + 1):
        if abs(times[i] - expected) > window:
            continue
        score = sum(1 for j in range(k) if wn[i + j] == keys[j])
        dist = abs(times[i] - expected)
        if score > best[0] or (score == best[0] and dist < best[1]):
            best = (score, dist, i)
    return best[2] if best[0] >= min(2, k) else None


def stats(errs: list[float]) -> dict:
    a = sorted(abs(e) for e in errs)
    return {
        "n": len(errs),
        "mediana_abs_s": round(st.median(a), 2),
        "p90_abs_s": round(a[int(0.9 * (len(a) - 1))], 2),
        "max_abs_s": round(a[-1], 2),
        "vies_medio_s": round(st.mean(errs), 2),
    }


def slope_per_hour(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 3:
        return float("nan")
    mx, my = st.mean(xs), st.mean(ys)
    den = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den * 3600 if den else float("nan")


def main() -> None:
    g = json.loads((OUT / "gemini_transcript.json").read_text(encoding="utf-8"))
    w = json.loads((OUT / "whisper_words.json").read_text(encoding="utf-8"))
    if g["start_offset"] != w["start_offset"]:
        raise SystemExit(f"--start diferente: gemini={g['start_offset']} whisper={w['start_offset']}")

    words = w["words"]
    wn = [norm(x["w"]) for x in words]
    starts = [x["start"] for x in words]

    se, ee, xs_s, xs_e = [], [], [], []
    for seg in g["segments"]:
        toks = [norm(t) for t in seg["text"].split() if norm(t)]
        if len(toks) < 3:
            continue
        gs, ge = parse_ts(seg["start"]), parse_ts(seg["end"])
        i = best_match(toks[:3], wn, gs, starts)
        if i is not None:
            se.append(gs - words[i]["start"])
            xs_s.append(gs)
        j = best_match(toks[-3:], wn, ge, starts)
        if j is not None:
            ee.append(ge - words[j + 2]["end"])
            xs_e.append(ge)

    if not se or not ee:
        raise SystemExit("Poucos segmentos casaram; confira se os dois spikes usaram o mesmo trecho.")

    res = {
        "inicio_dos_segmentos": stats(se),
        "fim_dos_segmentos": stats(ee),
        "deriva_inicio_s_por_hora": round(slope_per_hour(xs_s, se), 1),
        "segmentos_total": len(g["segments"]),
    }
    save_json("compare.json", res)

    print("=== RESUMO (cole isto de volta) ===")
    print(json.dumps(res, ensure_ascii=False, indent=2))
    med = res["inicio_dos_segmentos"]["mediana_abs_s"]
    print(
        "\nLeitura: erro mediano de início",
        f"{med:.2f}s ->",
        "serve p/ localizar, mas NÃO p/ cortar entre palavras (precisa ±0,1-0,2s)" if med > 0.25
        else "razoável, ainda assim confirme com legenda por palavra do Whisper",
    )


if __name__ == "__main__":
    main()
