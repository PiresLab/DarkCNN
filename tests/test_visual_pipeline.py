import json

import pytest

from conftest import FakeBackend, make_cut_video, needs_ffmpeg
from darkcnn import pipeline
from darkcnn.config import Config
from darkcnn.gemini import GeminiClient
from darkcnn.media import video_info
from test_visual import vc


def factory(backend, cfg):
    return lambda: GeminiClient(backend, cfg.workspace_dir / "usage.json", cfg.daily_request_budget,
                                sleep=lambda s: None)


def test_visual_profile_defaults_to_titled_unless_user_chose():
    assert pipeline.effective_cfg(Config(profile="visual"), {}).text_mode == "titled"
    assert pipeline.effective_cfg(Config(profile="visual", text_mode="none"), {}).text_mode == "none"
    assert pipeline.effective_cfg(Config(profile="visual", text_mode="captions"), {}).text_mode == "captions"
    assert pipeline.effective_cfg(Config(), {}).text_mode == "captions"  # perfil de fala não muda


@needs_ffmpeg
def test_visual_end_to_end_cuts_land_on_shot_boundaries(tmp_path):
    video = make_cut_video(tmp_path / "nocautes.mp4")  # 6 planos de 10 s; 1 e 4 são altos
    cfg = Config(profile="visual", min_clip_s=8, max_clip_s=25, clips_per_video=2, preset="ultrafast",
                 proxy_height=144, source="https://www.youtube.com/watch?v=abc", license="CC-BY 4.0",
                 workspace_dir=tmp_path / "ws", output_dir=tmp_path / "out")
    backend = FakeBackend([
        vc(1, 2, "00:10", "00:30", score=9, title="O nocaute brutal"),
        vc(4, 5, "00:40", "00:60", score=8, title="Finalização perfeita"),
        vc(2, 3, "00:20", "00:40", score=5, title="Sobreposto"),  # divide o plano 2 com o primeiro
    ])
    whisper_called = []

    review = pipeline.run_pipeline(video, cfg, transcriber=lambda *a: whisper_called.append(1) or [],
                                   client_factory=factory(backend, cfg))
    out = review.parent
    assert not whisper_called  # sem fala: Whisper nem é carregado
    assert backend.calls == 1 and backend.media[0] is not None  # 1 janela, com o vídeo reduzido

    sel = json.loads((out / "selection.json").read_text(encoding="utf-8"))
    assert [(c["start_id"], c["end_id"]) for c in sel] == [(1, 2), (4, 5)]
    # o corte cai exatamente na fronteira dos planos (10–30 s e 40–60 s), sem respiro invadindo o vizinho
    assert sel[0]["start"] == pytest.approx(10.0, abs=0.15) and sel[0]["end"] == pytest.approx(30.0, abs=0.15)
    assert sel[1]["start"] == pytest.approx(40.0, abs=0.15) and sel[1]["end"] == pytest.approx(60.0, abs=0.15)
    for m in out.glob("*.mp4"):
        info = video_info(m)
        assert (info["width"], info["height"]) == (1080, 1920) and info["duration"] == pytest.approx(20, abs=0.4)
    assert len(list(out.glob("*.mp4"))) == 2

    # texto padrão do perfil visual: título no topo + gancho embaixo (e nenhuma legenda de fala)
    ass = (cfg.workspace_dir / next(p.name for p in cfg.workspace_dir.iterdir() if p.is_dir() and p.name != "downloads")
           / "render" / "01" / "subs.ass").read_text(encoding="utf-8")
    assert ",Title," in ass and ",Hook," in ass and "O ÚLTIMO É BRUTAL" in ass

    text = review.read_text(encoding="utf-8")
    assert "Perfil:** visual" in text and "podem estar errados" in text and "O nocaute brutal" in text
    assert "Sobreposto" in text and "sobreposto" in text  # descartado e explicado

    # 2ª execução: planos, análise e render em cache -> zero Gemini, zero re-render
    mtimes = {m.name: m.stat().st_mtime_ns for m in out.glob("*.mp4")}
    pipeline.run_pipeline(video, cfg, transcriber=lambda *a: [], client_factory=factory(backend, cfg))
    assert backend.calls == 1
    assert {m.name: m.stat().st_mtime_ns for m in out.glob("*.mp4")} == mtimes

    # `render` a partir do selection.json funciona sem transcrição no perfil visual
    sel[0]["title"] = "Título editado à mão"
    (out / "selection.json").write_text(json.dumps(sel), encoding="utf-8")
    pipeline.render_from_selection(video, cfg)
    assert backend.calls == 1 and any("titulo-editado" in m.name for m in out.glob("*.mp4"))


@needs_ffmpeg
def test_shots_report_runs_without_gemini(tmp_path):
    video = make_cut_video(tmp_path / "v.mp4")
    cfg = Config(workspace_dir=tmp_path / "ws")
    rep = pipeline.shots_report(video, cfg)
    assert "6 planos" in rep and "mediana 10.0s" in rep and "limiar de cena 0.3" in rep
    assert "#1 em 00:10" in rep and "#4 em 00:40" in rep  # os planos barulhentos


@needs_ffmpeg
def test_visual_profile_on_a_video_without_audio_and_with_no_cuts(tmp_path):
    """Vídeo mudo e contínuo (ex.: um vídeo satisfatório de um plano só): planos forçados e sem volume."""
    import subprocess
    video = tmp_path / "continuo.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=320x180:rate=25:duration=40", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", str(video)], check=True)
    cfg = Config(profile="visual", min_clip_s=8, max_clip_s=25, clips_per_video=1, preset="ultrafast",
                 proxy_height=144, max_shot_s=10, workspace_dir=tmp_path / "ws", output_dir=tmp_path / "out")
    backend = FakeBackend([vc(1, 2, "00:10", "00:30", score=8, title="Satisfatório")])
    review = pipeline.run_pipeline(video, cfg, client_factory=factory(backend, cfg))
    sel = json.loads((review.parent / "selection.json").read_text(encoding="utf-8"))
    assert len(sel) == 1 and (sel[0]["start"], sel[0]["end"]) == (pytest.approx(10.0), pytest.approx(30.0))
    assert "volume" not in backend.prompts[0].split("<planos>")[1].split("</planos>")[0]  # sem áudio, sem volume
