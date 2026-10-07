"""Regressão: o uso normal da CLI tem workspace/output RELATIVOS. O ffmpeg do juiz roda em outra pasta (cwd)
e um caminho de saída relativo deixava de existir ("Error opening output ... parts\\s1.mp4")."""
import json
from pathlib import Path

from conftest import SmartBackend, cand, make_cut_video, make_video, make_words, needs_ffmpeg
from darkcnn import pipeline
from darkcnn.config import Config
from darkcnn.gemini import GeminiClient
from test_compile import tc
from test_visual import vc


def rel_cfg(**kw) -> Config:
    base = dict(preset="ultrafast", proxy_height=144, judge=True, workspace_dir=Path("workspace"),
                output_dir=Path("output"))
    base.update(kw)
    return Config(**base)


def factory(backend):
    return lambda: GeminiClient(backend, Path("workspace") / "u.json", 40, sleep=lambda s: None)


@needs_ffmpeg
def test_talk_judge_with_relative_dirs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    video = make_video(Path("aula.mp4"), dur=60)
    backend = SmartBackend([cand(0, 2, score=9, title="A"), cand(3, 5, score=8, title="B"), cand(6, 8, score=7, title="C")],
                           judge_plan={"A1": (70, True, ""), "A2": (90, True, ""), "A3": (50, True, "")})
    cfg = rel_cfg(min_clip_s=8, max_clip_s=20, clips_per_video=2)
    review = pipeline.run_pipeline(video, cfg, transcriber=lambda *a: make_words(12), client_factory=factory(backend))
    sel = json.loads((review.parent / "selection.json").read_text(encoding="utf-8"))
    assert [c["title"] for c in sel] == ["B", "A"] and backend.judge_calls == 1
    assert len(list(review.parent.glob("*.mp4"))) == 2


@needs_ffmpeg
def test_visual_judge_with_relative_dirs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    video = make_cut_video(Path("lutas.mp4"))
    backend = SmartBackend([vc(0, 1, "00:00", "00:20", score=9, title="Um"), vc(2, 3, "00:20", "00:40", score=8, title="Dois")],
                           judge_plan={"A1": (60, True, ""), "A2": (90, True, "")})
    cfg = rel_cfg(profile="visual", min_clip_s=8, max_clip_s=25, clips_per_video=2)
    review = pipeline.run_pipeline(video, cfg, client_factory=factory(backend))
    sel = json.loads((review.parent / "selection.json").read_text(encoding="utf-8"))
    assert [c["title"] for c in sel] == ["Dois", "Um"] and backend.judge_calls == 1


@needs_ffmpeg
def test_compile_judge_with_relative_dirs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    video = make_cut_video(Path("lutas.mp4"))
    backend = SmartBackend([tc(0, 0, "00:00", "00:10", fit=9, title="Um"), tc(2, 2, "00:20", "00:30", fit=8, title="Dois")],
                           judge_plan={"A1": (60, True, ""), "A2": (90, True, "")})
    cfg = rel_cfg(theme="top 2 lances", clips_per_video=2)
    review = pipeline.run_compile(video, cfg, client_factory=factory(backend))
    assert backend.judge_calls == 1 and (review.parent / "compilado_top-2-lances.mp4").exists()
