"""Cliente do autotok com o corpo de publicação que o TikTok Studio envia hoje.

O `autotok` 2.0.1 monta o `privacy_setting_info` só com visibilidade, comentário, dueto e stitch e não manda metadados
do vídeo. O Studio atual (bundle `creator_center`, funções que montam `feature_common_info_list` e
`single_post_req_list`) manda também `allow_content_reuse` e `allow_ai_remix` (1 = permitir, 2 = não permitir) e, nos
dados do vídeo, `cloud_edit_video_width/height`, `has_original_audio` e `is_upload_audio_track`. Com o corpo do
autotok puro o TikTok recusava a publicação (`status_code 5 / Invalid parameters`); com estes campos ela passa.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import require

require()
import autotok  # noqa: E402


class StudioClient(autotok.Client):
    allow_content_reuse: bool = True
    allow_ai_remix: bool = True
    video_meta: dict[str, Any] | None = None

    def configure(self, *, allow_content_reuse: bool, allow_ai_remix: bool, video: Path) -> "StudioClient":
        self.allow_content_reuse, self.allow_ai_remix = allow_content_reuse, allow_ai_remix
        try:
            from .. import media
            self.video_meta = media.video_info(Path(video))
        except Exception:  # noqa: BLE001 - sem ffprobe a publicação vai sem as dimensões
            self.video_meta = None
        return self

    # o autotok chama self._payload(...) com argumentos posicionais; aqui vira método de instância
    def _payload(self, creation_id, video_id, caption, markup, text_extra, visibility,  # type: ignore[override]
                 allow_comment, allow_duet, allow_stitch, ai_label) -> dict:
        data = autotok.Client._payload(creation_id, video_id, caption, markup, text_extra, visibility,
                                       allow_comment, allow_duet, allow_stitch, ai_label)
        privacy = data["feature_common_info_list"][0]["privacy_setting_info"]
        privacy["allow_content_reuse"] = int(bool(self.allow_content_reuse))
        privacy["allow_ai_remix"] = 1 if self.allow_ai_remix else 2
        post = data["single_post_req_list"][0]["single_post_feature_info"]
        meta = self.video_meta or {}
        if meta.get("width") and meta.get("height"):
            post["cloud_edit_video_width"] = int(meta["width"])
            post["cloud_edit_video_height"] = int(meta["height"])
        post["cloud_edit_is_use_video_canvas"] = False
        post["has_original_audio"] = 1
        post["is_upload_audio_track"] = False
        return data
