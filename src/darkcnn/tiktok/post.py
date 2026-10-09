"""Publicar um vídeo no TikTok (usado pelo botão "Postar" e pelas automações)."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from . import accounts, require

log = logging.getLogger(__name__)

MIN_SCHEDULE_S = 15 * 60
MAX_SCHEDULE_S = 10 * 24 * 3600
RETRIES = 2
RETRY_WAIT_S = 20.0


class PostError(RuntimeError):
    """Falha ao postar. `uncertain`: o TikTok pode ter publicado: confira a conta antes de repetir."""

    def __init__(self, msg: str, *, uncertain: bool = False):
        super().__init__(msg)
        self.uncertain = uncertain


class PostOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account: str
    caption: str = Field(min_length=1, max_length=2200)
    visibility: Literal["public", "private"] = "public"
    schedule_s: int | None = Field(None, ge=0, le=MAX_SCHEDULE_S)  # segundos a partir de agora; vazio/0 = agora
    ai_label: bool = True  # "conteúdo gerado por IA": narração sintética deve ser rotulada
    allow_comment: bool = True
    allow_duet: bool = False
    allow_stitch: bool = False


@dataclass
class PostResult:
    video_id: str
    scheduled_for: float | None


def normalize_schedule(schedule_s: int | None) -> int | None:
    """O TikTok só agenda entre 15 min e 10 dias: um atraso menor que 15 min vira 15 min."""
    if not schedule_s:
        return None
    return max(MIN_SCHEDULE_S, int(schedule_s))


def post_video(root: Path, video: Path, opts: PostOptions, *, client_factory: Callable[[str], Any] | None = None,
               sleep: Callable[[float], None] = time.sleep) -> PostResult:
    require()
    from autotok import (AccountNotFoundError, AutotokError, NotLoggedInError, PublishError,
                         PublishUncertainError, ValidationError)

    if opts.visibility == "private" and normalize_schedule(opts.schedule_s):
        raise PostError("o TikTok não agenda vídeos privados: publique agora ou escolha público")
    video = Path(video)
    if not video.is_file():
        raise PostError(f"vídeo não encontrado: {video.name}")
    factory = client_factory or (lambda name: __import__("autotok").Client.from_account(name, store=accounts.store(root)))
    for attempt in range(RETRIES + 1):
        try:
            res = factory(opts.account).upload(
                video, opts.caption, schedule=normalize_schedule(opts.schedule_s), visibility=opts.visibility,
                allow_comment=opts.allow_comment, allow_duet=opts.allow_duet, allow_stitch=opts.allow_stitch,
                ai_label=opts.ai_label)
            when = res.scheduled_for.timestamp() if getattr(res, "scheduled_for", None) else None
            return PostResult(video_id=str(res.video_id), scheduled_for=when)
        except PublishUncertainError as e:
            raise PostError("a conexão caiu na hora de publicar: o vídeo pode ou não ter sido postado. "
                            "Confira a conta no TikTok antes de tentar de novo.", uncertain=True) from e
        except (NotLoggedInError, AccountNotFoundError) as e:
            raise PostError("a sessão do TikTok expirou ou a conta não está conectada: reconecte em Configurações") from e
        except PublishError as e:
            raise PostError(f"o TikTok recusou o vídeo: {e.status_msg or e}") from e
        except ValidationError as e:
            raise PostError(str(e)) from e
        except AutotokError as e:
            if e.retryable and attempt < RETRIES:  # nada chegou ao passo de publicar: repetir não duplica
                log.warning("falha antes de publicar (%s); nova tentativa em %.0fs", e, RETRY_WAIT_S)
                sleep(RETRY_WAIT_S)
                continue
            raise PostError(f"falha ao enviar para o TikTok: {e}") from e
    raise PostError("falha ao enviar para o TikTok")  # pragma: no cover
