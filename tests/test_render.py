import pytest

from conftest import make_video, make_watermark, make_words, needs_ffmpeg
from darkcnn import render
from darkcnn.config import Config
from darkcnn.media import video_info

CLIP = {"rank": 1, "start": 3.0, "end": 9.0, "title": "Um título bem chamativo para o corte",
        "hook_text": "o último é brutal"}
WORDS = make_words(3, sent_s=3.0, words_per=3)


def test_filter_graph_variants(tmp_path):
    base = Config()
    fc, last = render.build_filter(base, has_ass=False, has_fonts=False, has_wm=False)
    assert last == "v0" and "crop=" in fc and "ass=" not in fc and "overlay=" not in fc
    fc, last = render.build_filter(Config(layout="blur"), True, True, True)
    assert last == "v" and "boxblur" in fc and "ass=subs.ass:fontsdir=fonts" in fc
    assert "[1:v]format=rgba,colorchannelmixer=aa=0.60[wm]" in fc and "overlay=W-w-48:120[v]" in fc
    # um único grafo, um único encode: nenhuma etapa intermediária fora do filter_complex
    assert fc.count("[0:v]") >= 1 and ";" in fc


def test_render_key_depends_on_what_matters(tmp_path):
    cfg = Config(text_mode="captions")
    k = render.render_key("vid", CLIP, WORDS, cfg)
    assert k == render.render_key("vid", dict(CLIP), WORDS, cfg)
    assert k != render.render_key("vid", {**CLIP, "start": 3.5}, WORDS, cfg)
    assert k != render.render_key("vid", CLIP, WORDS, Config(text_mode="captions", layout="blur"))
    inside = next(i for i, w in enumerate(WORDS) if CLIP["start"] <= w["start"] and w["end"] <= CLIP["end"])
    changed = WORDS[:inside] + [{**WORDS[inside], "w": "outra"}] + WORDS[inside + 1:]
    assert k != render.render_key("vid", CLIP, changed, cfg)  # palavra dentro do corte: re-renderiza
    outside = [{**WORDS[0], "w": "outra"}] + WORDS[1:]  # palavra fora do corte (0-0,9 s): não muda nada
    assert WORDS[0]["end"] < CLIP["start"] and k == render.render_key("vid", CLIP, outside, cfg)
    # no modo titled as palavras não aparecem na tela: mudar a transcrição não precisa re-renderizar
    t = Config(text_mode="titled")
    assert render.render_key("vid", CLIP, WORDS, t) == render.render_key("vid", CLIP, [], t)


@needs_ffmpeg
@pytest.mark.parametrize("text_mode,layout,wm", [("captions", "crop", False), ("titled", "blur", True),
                                                 ("none", "crop", True), ("captions", "blur", True)])
def test_render_clip_outputs_exact_1080x1920_with_requested_duration(tmp_path, text_mode, layout, wm):
    src = make_video(tmp_path / "src.mp4", dur=12)
    cfg = Config(text_mode=text_mode, layout=layout, preset="ultrafast",
                 watermark={"path": make_watermark(tmp_path / "wm.png") if wm else None})
    dest, work = tmp_path / "out" / "01.mp4", tmp_path / "work"
    key = render.render_key("vid", CLIP, WORDS, cfg)

    assert render.render_clip(src, CLIP, WORDS, cfg, dest, work, key) is True
    info = video_info(dest)
    assert (info["width"], info["height"]) == (1080, 1920)
    # -t estava no lugar errado no spike 01 (renderizava o vídeo inteiro): aqui tem de ser os 6 s pedidos
    assert info["duration"] == pytest.approx(6.0, abs=0.3)
    assert (work / "subs.ass").exists() == (text_mode != "none")

    # mesma configuração: pula
    assert render.render_clip(src, CLIP, WORDS, cfg, dest, work, key) is False
    # configuração diferente: renderiza de novo
    assert render.render_clip(src, CLIP, WORDS, cfg, dest, work, key + "x") is True


@needs_ffmpeg
def test_missing_watermark_is_a_clear_error(tmp_path):
    src = make_video(tmp_path / "src.mp4", dur=8)
    cfg = Config(preset="ultrafast", watermark={"path": tmp_path / "nao_existe.png"})
    with pytest.raises(FileNotFoundError, match="marca d'água"):
        render.render_clip(src, CLIP, WORDS, cfg, tmp_path / "o.mp4", tmp_path / "w", "k")
