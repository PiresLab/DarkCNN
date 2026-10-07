"""Cache por etapa: um JSON {"key", "data"}; a etapa só roda de novo se a chave mudou."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def key_of(**params: Any) -> str:
    """Hash estável (16 hex) de parâmetros serializáveis. Caminhos e outros tipos viram str."""
    blob = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def load(path: Path, key: str) -> Any | None:
    """Devolve os dados se o arquivo existe e a chave coincide; senão None."""
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(obj, dict) and obj.get("key") == key:
        return obj.get("data")
    return None


def save(path: Path, key: str, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({"key": key, "data": data}, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)  # escrita atômica: queda no meio não deixa JSON pela metade
