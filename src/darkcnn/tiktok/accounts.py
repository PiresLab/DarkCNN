"""Contas do TikTok conectadas. A sessão de cada conta é um arquivo JSON 0600 gerido pelo autotok."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from . import require


def slugify(label: str) -> str:
    """Nome de conta aceito pelo autotok (letras, números, '.', '_' e '-')."""
    s = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9_.-]+", "-", s).strip("-.")[:60]
    return s or "conta"


def store(root: Path):
    require()
    from autotok import AccountStore
    return AccountStore(Path(root) / "accounts", legacy_dirs=[])


def view(acc) -> dict:
    proxy = acc.get_proxy()
    return {"name": acc.name, "connected": acc.has_session, "proxy": proxy.masked() if proxy else None,
            "created_at": acc.created_at, "updated_at": acc.updated_at}


def list_accounts(root: Path) -> list[dict]:
    st = store(root)
    out = []
    for name in st.list():
        try:
            out.append(view(st.load(name)))
        except Exception as e:  # noqa: BLE001 - arquivo quebrado não derruba a lista
            out.append({"name": name, "connected": False, "proxy": None, "error": str(e)[:120]})
    return out


def check(root: Path, name: str) -> bool:
    """O TikTok ainda aceita a sessão? (uma chamada de rede)"""
    from autotok import Client
    return Client.from_account(name, store=store(root)).check_session()


def remove(root: Path, name: str) -> bool:
    return store(root).delete(name)


def import_session(root: Path, name: str, session_id: str, datacenter: str | None, proxy: str | None) -> dict:
    """Plano B sem navegador: o `sessionid` copiado dos cookies do TikTok."""
    from autotok import import_session as _import
    acc = _import(slugify(name), session_id, datacenter=datacenter or None, proxy=proxy or None, store=store(root))
    return view(acc)
