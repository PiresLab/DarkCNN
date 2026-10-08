"""Cliente Gemini: JSON estruturado, retry com backoff, orçamento diário e log de uso.

`GeminiClient` não conhece o SDK: recebe um backend com `generate(prompt, schema, temperature)`.
Em produção o backend é `GenaiBackend`; nos testes, um falso.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Protocol

from pydantic import BaseModel

log = logging.getLogger(__name__)

TRANSIENT_CODES = (429, 500, 502, 503, 504)


class GeminiError(RuntimeError):
    pass


class TransientError(GeminiError):
    """429/5xx: vale tentar de novo depois de esperar."""


class InvalidResponse(GeminiError):
    """A API respondeu, mas o JSON não bate com o schema."""


class BudgetExceeded(GeminiError):
    pass


class Backend(Protocol):
    def generate(self, prompt: str, schema: type[BaseModel], temperature: float,
                 media: Path | None = None) -> tuple[BaseModel, dict]: ...


class GenaiBackend:
    def __init__(self, api_key: str, model: str, thinking: str = "off"):
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self.model = model
        self.thinking = thinking  # off | low | medium | high

    def _config(self, schema: type[BaseModel], temperature: float):
        from google.genai import types

        kw: dict[str, Any] = dict(
            response_mime_type="application/json", response_schema=schema, temperature=temperature,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        if self.thinking != "off":
            level = getattr(types.ThinkingLevel, self.thinking.upper())
            kw["thinking_config"] = types.ThinkingConfig(thinking_level=level)
        return types.GenerateContentConfig(**kw)

    def generate(self, prompt: str, schema: type[BaseModel], temperature: float,
                 media: Path | None = None) -> tuple[BaseModel, dict]:
        from google.genai import errors

        uploaded = None
        try:
            contents: Any = prompt
            if media is not None:  # vídeo/áudio pela File API (arquivos grandes não cabem inline)
                uploaded = self._client.files.upload(file=str(media))
                while uploaded.state is not None and uploaded.state.name == "PROCESSING":
                    time.sleep(2)
                    uploaded = self._client.files.get(name=uploaded.name)
                if uploaded.state is not None and uploaded.state.name != "ACTIVE":
                    raise GeminiError(f"upload falhou: estado {uploaded.state.name} {uploaded.error}")
                contents = [uploaded, prompt]
            try:
                r = self._client.models.generate_content(model=self.model, contents=contents,
                                                         config=self._config(schema, temperature))
            except errors.APIError as e:
                if self.thinking != "off" and e.code == 400 and "think" in (e.message or "").lower():
                    log.warning("o modelo %s não aceitou o nível de raciocínio '%s'; seguindo sem raciocínio",
                                self.model, self.thinking)
                    self.thinking = "off"
                    r = self._client.models.generate_content(model=self.model, contents=contents,
                                                             config=self._config(schema, temperature))
                else:
                    raise
        except errors.APIError as e:
            msg = f"{e.code} {e.status}: {(e.message or '')[:400]}"
            if e.code in TRANSIENT_CODES:
                raise TransientError(msg) from e
            raise GeminiError(msg) from e
        finally:
            if uploaded is not None:  # o arquivo expira em ~48 h, mas não deixamos lixo
                try:
                    self._client.files.delete(name=uploaded.name)
                except Exception:
                    pass

        parsed = r.parsed
        if not isinstance(parsed, schema):
            try:
                parsed = schema.model_validate_json(r.text or "")
            except Exception as e:  # JSON truncado, campos faltando, notas fora de 1..10…
                raise InvalidResponse(f"resposta fora do schema: {str(e)[:300]}") from e
        u = r.usage_metadata
        usage = {
            "prompt_tokens": (u.prompt_token_count or 0) if u else 0,
            "output_tokens": ((u.candidates_token_count or 0) + (u.thoughts_token_count or 0)) if u else 0,
        }
        return parsed, usage


_USAGE_LOCK = threading.Lock()


class GeminiClient:
    def __init__(
        self,
        backend: Backend,
        usage_path: Path,
        daily_budget: int,
        retries: int = 3,
        backoff_s: float = 5.0,
        sleep: Callable[[float], None] = time.sleep,
        today: Callable[[], str] = lambda: dt.date.today().isoformat(),
    ):
        self.backend, self.usage_path = backend, usage_path
        self.daily_budget, self.retries, self.backoff_s = daily_budget, retries, backoff_s
        self._sleep, self._today = sleep, today

    # --- uso (global: a cota é por projeto/dia, não por vídeo) ---
    def _load_usage(self) -> dict[str, Any]:
        try:
            return json.loads(self.usage_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def requests_today(self) -> int:
        return int(self._load_usage().get(self._today(), {}).get("requests", 0))

    def _record(self, usage: dict | None) -> None:
        with _USAGE_LOCK:  # jobs simultâneos fazem read-modify-write no mesmo arquivo
            data = self._load_usage()
            day = data.setdefault(self._today(), {"requests": 0, "prompt_tokens": 0, "output_tokens": 0})
            day["requests"] += 1
            for k in ("prompt_tokens", "output_tokens"):
                day[k] += (usage or {}).get(k, 0)
            self.usage_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.usage_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
            tmp.replace(self.usage_path)

    def generate_json(self, prompt: str, schema: type[BaseModel], temperature: float = 0.2,
                      media: Path | None = None) -> BaseModel:
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            if self.requests_today() >= self.daily_budget:
                raise BudgetExceeded(
                    f"orçamento diário de {self.daily_budget} requisições esgotado (ajuste daily_request_budget)"
                )
            try:
                parsed, usage = self.backend.generate(prompt, schema, temperature, media)
            except TransientError as e:
                self._record(None)  # a tentativa conta na cota
                last = e
                if attempt == self.retries:
                    break
                wait = self.backoff_s * 2**attempt
                log.warning("Gemini indisponível (%s); nova tentativa em %.0fs", e, wait)
                self._sleep(wait)
                continue
            except InvalidResponse as e:
                self._record(None)
                last = e
                if attempt == self.retries:
                    break
                log.warning("resposta inválida (%s); tentando de novo", e)
                prompt += f"\n\nATENÇÃO: sua resposta anterior foi rejeitada ({e}). Siga o schema à risca."
                continue
            self._record(usage)
            log.info("Gemini ok: %s tokens entrada, %s saída", usage["prompt_tokens"], usage["output_tokens"])
            return parsed
        raise GeminiError(f"falhou após {self.retries + 1} tentativas: {last}")
