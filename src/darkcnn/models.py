"""Modelos do Gemini que a chave do usuário enxerga, separados em texto e voz."""
from __future__ import annotations

import os
import time
from typing import Any, Callable

TTL_S = 600
_cache: dict[str, Any] = {"at": 0.0, "key": None, "data": None}


# modelos que geram conteúdo, mas não servem para roteiro/análise de texto
NOT_TEXT = ("image", "computer-use", "robotics", "transcribe", "embedding", "omni", "nano-banana", "live",
            "native-audio", "customtools", "veo", "imagen", "aqa")


def classify(models: list[tuple[str, list[str]]]) -> dict[str, list[str]]:
    """[(nome, ações)] -> {text, tts}. TTS = "tts" no nome; texto = gera conteúdo e não é TTS."""
    text, tts = set(), set()
    for raw, actions in models:
        name = raw.removeprefix("models/")
        if "tts" in name.lower():
            tts.add(name)
        elif ("generateContent" in actions and name.lower().startswith("gemini")
              and not any(w in name.lower() for w in NOT_TEXT)):
            text.add(name)
    return {"text": sorted(text), "tts": sorted(tts)}


def _fetch() -> list[tuple[str, list[str]]]:
    from google import genai

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])  # referenciado: a lista é paginada
    return [(m.name or "", list(m.supported_actions or [])) for m in client.models.list()]


def list_models(refresh: bool = False, fetch: Callable[[], list[tuple[str, list[str]]]] | None = None,
                now: Callable[[], float] = time.time) -> dict[str, Any]:
    """Nunca levanta: sem chave/rede devolve listas vazias e `error`, e a interface cai no campo livre."""
    key = os.environ.get("GEMINI_API_KEY")
    if fetch is None and not key:
        return {"text": [], "tts": [], "error": "Chave do Gemini não configurada"}
    if not refresh and _cache["data"] is not None and _cache["key"] == key and now() - _cache["at"] < TTL_S:
        return _cache["data"]
    try:
        data = {**classify((fetch or _fetch)()), "error": None}
    except Exception as e:  # noqa: BLE001 - rede fora, chave recusada...
        return {"text": [], "tts": [], "error": f"{type(e).__name__}: {str(e)[:160]}"}
    _cache.update(at=now(), key=key, data=data)
    return data
