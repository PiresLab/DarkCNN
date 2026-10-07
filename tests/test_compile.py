import json

import pytest

from conftest import SmartBackend, make_cut_video, needs_ffmpeg
from darkcnn import pipeline, review
from darkcnn import visual as V
from darkcnn.config import Config
from darkcnn.gemini import GeminiClient
from darkcnn.media import video_info
from darkcnn.shots import has_audio


def tc(start_id, end_id, start_ts, end_ts, fit, score=8, title="Momento"):
    return V.ThemeCandidate(start_id=start_id, end_id=end_id, start_ts=start_ts, end_ts=end_ts, title=title,
                            hook_text="olha isso", reason="r", hook=7, standalone=7, emotion=8, payoff=8,
                            score=score, fit=fit)


def factory(backend, cfg):
    return lambda: GeminiClient(backend, cfg.workspace_dir / "u.json", 40, sleep=lambda s: None)


def test_prepare_theme_drops_weak_fits_and_weights_fit():
    cands = [tc(0, 0, "0:0", "0:10", fit=9, score=6).model_dump(), tc(2, 2, "0:20", "0:30", fit=3, score=10).model_dump()]
    ok, rej = pipeline.prepare_theme(cands, min_fit=5)
    assert len(ok) == 1 and ok[0]["score"] == 8  # (6 + 2*9) / 3 = 8
    assert "não combina bem com o tema (encaixe 3/10)" in rej[0]["reason_rejected"]
    assert cands[0]["score"] == 6  # não altera a entrada


def test_compile_defaults_respect_explicit_choices():
    c = pipeline.compile_defaults(Config(theme="top 5"))
    assert (c.profile, c.text_mode, c.min_clip_s, c.max_clip_s) == ("visual", "ranked", 8, 25)
    e = pipeline.compile_defaults(Config(theme="top 5", min_clip_s=12, max_clip_s=40, text_mode="none"))
    assert (e.min_clip_s, e.max_clip_s, e.text_mode) == (12, 40, "none")
    assert pipeline.compile_defaults(Config(theme="x", min_clip_s=30)).max_clip_s == 30  # max nunca < min


def test_compile_requires_a_theme(tmp_path):
    with pytest.raises(ValueError, match="--theme"):
        pipeline.run_compile("x.mp4", Config())


def test_review_warns_about_long_compilation_and_missing_moments(tmp_path):
    clip = {"rank": 1, "order": 1, "at": 0, "score": 8, "duration": 10, "start": 0, "end": 10, "title": "T", "hook_text": "h",
            "reason": "r", "hook": 7, "standalone": 7, "emotion": 7, "payoff": 7, "fit": 9, "file": "c.mp4"}
    cfg = Config(theme="top 5", profile="visual", source="https://youtu.be/x", license="CC-BY 4.0")
    p = review.write_review(tmp_path, tmp_path / "v.mp4", cfg, [clip], [], {"duration": 600},
                            compilation={"file": "c.mp4", "duration": 200, "theme": "top 5", "requested": 5})
    t = p.read_text(encoding="utf-8")
    assert "passa de 3 min" in t and "só 1 momento(s) combinaram" in t and "vale para todos eles" in t
    assert "Encaixe no tema:** 9/10" in t and "No compilado" in t


@needs_ffmpeg
def test_compile_end_to_end_countdown_and_rebuild(tmp_path):
    video = make_cut_video(tmp_path / "lutas.mp4")  # 6 planos de 10 s
    cfg = Config(theme="top 3 finalizações", clips_per_video=3, judge=True, preset="ultrafast", proxy_height=144,
                 source="https://www.youtube.com/watch?v=abc", license="CC-BY 4.0",
                 workspace_dir=tmp_path / "ws", output_dir=tmp_path / "out")
    backend = SmartBackend(
        [tc(0, 0, "00:00", "00:10", fit=9, score=8, title="Um"), tc(2, 2, "00:20", "00:30", fit=8, score=9, title="Dois"),
         tc(4, 4, "00:40", "00:50", fit=7, score=7, title="Tres"), tc(5, 5, "00:50", "01:00", fit=3, score=9, title="Fraco")],
        judge_plan={"A3": (95, True, ""), "A1": (80, True, ""), "A2": (60, True, "")})
    review_path = pipeline.run_compile(video, cfg, client_factory=factory(backend, cfg))
    out = review_path.parent

    # 1 janela de seleção + 1 do juiz; o de encaixe fraco nem chega ao juiz
    assert backend.calls == 2 and backend.judge_calls == 1
    assert "A4" not in backend.prompts[1]
    final = out / "compilado_top-3-finalizacoes.mp4"
    assert final.exists() and len(list(out.glob("*.mp4"))) == 1  # UM vídeo só
    info = video_info(final)
    assert (info["width"], info["height"]) == (1080, 1920) and info["duration"] == pytest.approx(30, abs=0.8)
    assert has_audio(final)

    sel = json.loads((out / "selection.json").read_text(encoding="utf-8"))
    by_order = sorted(sel, key=lambda c: c["order"])
    # contagem regressiva: Dois (#3) -> Um (#2) -> Tres (#1, o melhor, por último), juiz mandou: Tres 95 > Um 80 > Dois 60
    assert [(c["title"], c["rank"]) for c in by_order] == [("Dois", 3), ("Um", 2), ("Tres", 1)]
    assert [c["at"] for c in by_order] == pytest.approx([0, 10, 20], abs=0.6)
    assert all(c["theme"] == "top 3 finalizações" and c["file"] == final.name for c in sel)
    ws = next(p for p in cfg.workspace_dir.iterdir() if p.is_dir() and p.name != "downloads")
    first, last = (ws / "render" / "01" / "subs.ass").read_text(encoding="utf-8"), (ws / "render" / "03" / "subs.ass").read_text(encoding="utf-8")
    assert "#3" in first and "#1" in last and "TOP 3 FINALIZAÇÕES" in first and ",Rank," in first

    text = review_path.read_text(encoding="utf-8")
    assert "Compilado:" in text and "contagem regressiva" in text and "Encaixe no tema" in text
    rej = json.loads((out / "rejected.json").read_text(encoding="utf-8"))
    assert [r["title"] for r in rej] == ["Fraco"] and "não combina bem com o tema" in rej[0]["reason_rejected"]

    # 2ª execução: nada de Gemini nem de re-render dos trechos
    seg_mtimes = {p.name: p.stat().st_mtime_ns for p in (ws / "segments").glob("*.mp4")}
    pipeline.run_compile(video, cfg, client_factory=factory(backend, cfg))
    assert backend.calls == 2
    assert {p.name: p.stat().st_mtime_ns for p in (ws / "segments").glob("*.mp4")} == seg_mtimes

    # o usuário troca a ordem no selection.json (Dois vira #1, Tres vira #3) e remonta
    for c in sel:
        c["rank"] = {"Dois": 1, "Um": 2, "Tres": 3}[c["title"]]
    (out / "selection.json").write_text(json.dumps(sel), encoding="utf-8")
    pipeline.render_from_selection(video, cfg)
    sel2 = sorted(json.loads((out / "selection.json").read_text(encoding="utf-8")), key=lambda c: c["order"])
    assert [(c["title"], c["rank"]) for c in sel2] == [("Tres", 3), ("Um", 2), ("Dois", 1)]
    assert backend.calls == 2
    assert video_info(final)["duration"] == pytest.approx(30, abs=0.8)


@needs_ffmpeg
def test_compile_with_fewer_matches_than_requested_warns_and_nothing_matching_fails(tmp_path):
    video = make_cut_video(tmp_path / "lutas.mp4")
    cfg = Config(theme="top 5 reviravoltas", clips_per_video=5, judge=False, preset="ultrafast", proxy_height=144,
                 workspace_dir=tmp_path / "ws", output_dir=tmp_path / "out")
    backend = SmartBackend([tc(0, 0, "00:00", "00:10", fit=9, title="Um"), tc(2, 2, "00:20", "00:30", fit=8, title="Dois")])
    path = pipeline.run_compile(video, cfg, client_factory=factory(backend, cfg))
    text = path.read_text(encoding="utf-8")
    assert "só 2 momento(s) combinaram com o tema (pedido: 5)" in text
    assert len(json.loads((path.parent / "selection.json").read_text(encoding="utf-8"))) == 2

    nothing = SmartBackend([tc(0, 0, "00:00", "00:10", fit=2), tc(2, 2, "00:20", "00:30", fit=4)])
    cfg2 = cfg.model_copy(update={"theme": "top 5 outro tema", "workspace_dir": tmp_path / "ws2"})
    with pytest.raises(RuntimeError, match='nenhum momento combina com "top 5 outro tema"'):
        pipeline.run_compile(video, cfg2, client_factory=factory(nothing, cfg2))
