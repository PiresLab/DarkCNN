"""Único ponto de caminhos de dados.

Com `DATA_DIR` definido (Docker: /data) tudo mora lá: gameplays, saídas, workspace e fontes.
Sem `DATA_DIR` o comportamento antigo é mantido: pastas relativas ao diretório atual.
"""
from __future__ import annotations

import os
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".avi"}


def data_root() -> Path | None:
    raw = os.environ.get("DATA_DIR", "").strip()
    return Path(raw) if raw else None


def _sub(name: str) -> Path:
    root = data_root()
    return root / name if root else Path(name)


def default_workspace() -> Path:
    return _sub("workspace")


def default_output() -> Path:
    return _sub("outputs") if data_root() else Path("output")


def default_gameplays() -> Path | None:
    """Só há padrão no modo Docker; fora dele a pasta continua sendo escolha do usuário."""
    return _sub("gameplays") if data_root() else None


BUNDLED_FONTS = Path(__file__).parent / "assets" / "fonts"


def default_fonts() -> Path | None:
    """Fontes extras do usuário (volume Docker) se existirem; senão as empacotadas (DejaVu Sans)."""
    user = _sub("fonts") if data_root() else None
    if user is not None and user.is_dir() and any(user.iterdir()):
        return user
    return BUNDLED_FONTS if BUNDLED_FONTS.is_dir() else None


def default_database_url(workspace: Path | None = None) -> str:
    """DATABASE_URL, ou um SQLite ao lado dos dados (uso local sem Docker)."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url
    base = data_root() or (workspace or default_workspace())
    base.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{(base / 'darkcnn.db').resolve().as_posix()}"


def default_config_path() -> Path:
    """No Docker o config.yaml fica no volume (senão se perde ao recriar o container)."""
    return _sub("config.yaml")


def secret_file() -> Path:
    """Chave do Gemini salva pela interface (fica no volume, junto dos dados)."""
    return (data_root() or default_workspace()) / "gemini.key"


def apply_secrets() -> None:
    """A chave salva na interface vale mais que a do .env/ambiente."""
    try:
        key = secret_file().read_text(encoding="utf-8").strip()
    except OSError:
        return
    if key:
        os.environ["GEMINI_API_KEY"] = key


def tiktok_dir(workspace: Path) -> Path:
    """Sessões do TikTok (cada arquivo equivale à senha da conta: fica no volume, com permissão 0600)."""
    return (data_root() or Path(workspace).parent) / "tiktok"
