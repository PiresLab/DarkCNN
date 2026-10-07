import json
import os
import re

import pytest

from conftest import FakeBackend, cand, make_video, make_watermark, make_words, needs_ffmpeg
from darkcnn import pipeline
from darkcnn.cli import build_parser
from darkcnn.config import Config
from darkcnn.gemini import GeminiClient
from darkcnn.media import video_info


def setup(tmp_path, cfg, backend):
    video = make_video(tmp_path / "aula.mp4", dur=60)
    calls = {"whisper": 0}

    def fake_whisper(wav, c):
        calls["whisper"] += 1
        assert wav.exists()  # o áudio foi extraído de verdade pelo ffmpeg
        return make_words(12)  # 12 frases de 5 s = 60 s

    factory = lambda: GeminiClient(backend, cfg.workspace_dir / "usage.json", cfg.daily_request_budget,
                                   sleep=lambda s: None)
    return video, fake_whisper, factory, calls


@needs_ffmpeg
def test_end_to_end_and_cache(tmp_path, cfg):
    cfg = cfg.model_copy(update={"text_mode": "titled", "layout": "blur", "source": "https://www.youtube.com/watch?v=abc123",
                                 "license": "CC-BY 4.0",
                                 "watermark": cfg.watermark.model_copy(update={"path": make_watermark(tmp_path / "wm.png")})})
    backend = FakeBackend([
        cand(0, 2, score=9, title="Corte A: o começo", hook_text="Olha só"),
        cand(3, 5, score=8, title="Corte B", hook_text="Não acredito"),
        cand(6, 8, score=7, title="Corte C", hook_text="Fim"),
        cand(9, 11, score=6, title="Corte D (sobra)", hook_text="x"),
        cand(50, 60, score=10, title="Inválido", hook_text="x"),
    ])
    video, whisper, factory, calls = setup(tmp_path, cfg, backend)

    review = pipeline.run_pipeline(video, cfg, transcriber=whisper, client_factory=factory)
    out = review.parent
    mp4s = sorted(out.glob("*.mp4"))
    assert [m.name[:2] for m in mp4s] == ["01", "02", "03"]
    for m in mp4s:
        info = video_info(m)
        assert (info["width"], info["height"]) == (1080, 1920) and 8 <= info["duration"] <= 20.5
    assert backend.calls == 1 and calls["whisper"] == 1

    text = review.read_text(encoding="utf-8")
    assert "Corte A: o começo" in text and "CC-BY 4.0" in text and "&t=" in text  # link com tempo no YouTube
    assert "Inválido" in text and "fora do intervalo" in text  # descartados explicados
    assert "⚠" not in text  # fonte e licença informadas: sem aviso
    sel = json.loads((out / "selection.json").read_text(encoding="utf-8"))
    assert [c["score"] for c in sel] == [9, 8, 7] and all(c["file"] for c in sel)
    usage = json.loads((cfg.workspace_dir / "usage.json").read_text())
    assert next(iter(usage.values()))["requests"] == 1

    # --- 2ª execução idêntica: zero Gemini, zero Whisper, zero re-render ---
    before = {m.name: m.stat().st_mtime_ns for m in mp4s}
    pipeline.run_pipeline(video, cfg, transcriber=whisper, client_factory=factory)
    assert backend.calls == 1 and calls["whisper"] == 1
    assert {m.name: m.stat().st_mtime_ns for m in out.glob("*.mp4")} == before

    # --- edição manual do selection.json: só o corte editado é renderizado de novo ---
    sel[0]["start"] += 1.0
    (out / "selection.json").write_text(json.dumps(sel), encoding="utf-8")
    pipeline.render_from_selection(video, cfg)
    after = {m.name: m.stat().st_mtime_ns for m in out.glob("*.mp4")}
    changed = [n for n in after if after[n] != before[n]]
    assert changed == [mp4s[0].name] and backend.calls == 1

    # --- --force chama o Gemini e o Whisper de novo ---
    pipeline.run_pipeline(video, cfg, transcriber=whisper, client_factory=factory, force=True)
    assert backend.calls == 2 and calls["whisper"] == 2


@needs_ffmpeg
def test_missing_source_and_license_are_flagged_in_review(tmp_path, cfg):
    cfg = cfg.model_copy(update={"text_mode": "none"})
    backend = FakeBackend([cand(0, 2, score=9)])
    video, whisper, factory, _ = setup(tmp_path, cfg, backend)
    text = pipeline.run_pipeline(video, cfg, transcriber=whisper, client_factory=factory).read_text(encoding="utf-8")
    assert "não informada" in text and "Antes de postar" in text


@needs_ffmpeg
def test_stale_clips_are_removed_when_selection_shrinks(tmp_path, cfg):
    cfg = cfg.model_copy(update={"text_mode": "none"})
    backend = FakeBackend([cand(0, 2, score=9, title="Primeiro"), cand(6, 8, score=8, title="Segundo")])
    video, whisper, factory, _ = setup(tmp_path, cfg, backend)
    out = pipeline.run_pipeline(video, cfg, transcriber=whisper, client_factory=factory).parent
    assert len(list(out.glob("*.mp4"))) == 2
    backend.candidates = [cand(6, 8, score=9, title="Segundo")]
    pipeline.run_pipeline(video, cfg, transcriber=whisper, client_factory=factory, force=True)
    names = [m.name for m in out.glob("*.mp4")]
    assert len(names) == 1 and names[0].startswith("01_segundo")


@needs_ffmpeg
def test_no_valid_clip_still_writes_review(tmp_path, cfg):
    cfg = cfg.model_copy(update={"text_mode": "none"})
    video, whisper, factory, _ = setup(tmp_path, cfg, FakeBackend([cand(70, 80)]))
    review = pipeline.run_pipeline(video, cfg, transcriber=whisper, client_factory=factory)
    assert "fora do intervalo" in review.read_text(encoding="utf-8")
    assert not list(review.parent.glob("*.mp4"))


def test_gemini_key_is_required_only_when_needed(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    from darkcnn.gemini import GeminiError
    with pytest.raises(GeminiError, match="GEMINI_API_KEY"):
        pipeline.make_client(Config())


def test_cli_overrides_map_to_config():
    a = build_parser().parse_args(["run", "v.mp4", "--clips", "2", "--min", "10", "--max", "25",
                                   "--watermark", "wm.png", "--text-mode", "titled", "--layout", "blur"])
    assert (a.clips_per_video, a.min_clip_s, a.max_clip_s, a.text_mode, a.layout) == (2, 10.0, 25.0, "titled", "blur")
    from darkcnn.config import load_config
    cfg = load_config(None, {k: v for k, v in vars(a).items() if k not in ("cmd", "video", "config", "force")})
    assert cfg.watermark.path.name == "wm.png" and cfg.clips_per_video == 2
