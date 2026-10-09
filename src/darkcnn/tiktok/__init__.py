"""Integração com o TikTok via `autotok` (https://github.com/makiisthenes/TiktokAutoUploader, AGPL-3.0).

Tudo que importa `autotok` fica neste pacote e é importado só quando usado, então o resto do DarkCNN funciona sem ele
(`pip install "darkcnn[tiktok]"`). É uma integração NÃO oficial (usa a API web do TikTok): a conta pode ser limitada.
"""
from __future__ import annotations


class TikTokUnavailable(RuntimeError):
    pass


def require() -> None:
    """Falha com uma mensagem clara se o autotok não estiver instalado."""
    try:
        import autotok  # noqa: F401
    except ImportError as e:
        raise TikTokUnavailable('o pacote "autotok" não está instalado: pip install "darkcnn[tiktok]"') from e


def available() -> bool:
    try:
        require()
    except TikTokUnavailable:
        return False
    return True
