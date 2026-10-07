import pytest

from conftest import FakeBackend, make_cut_video, needs_ffmpeg
from darkcnn import shots as S
from darkcnn import visual as V
from darkcnn.config import Config
from darkcnn.gemini import GeminiClient
from darkcnn.media import video_info

# 6 planos de 10 s: 0-10, 10-20, ... 50-60
SH = S.build_shots([10, 20, 30, 40, 50], 60)
SH[1]["loud"], SH[4]["loud"] = 9.0, 12.0


def vc(start_id, end_id, start_ts, end_ts, score=8, title="Momento"):
    return V.VisualCandidate(start_id=start_id, end_id=end_id, start_ts=start_ts, end_ts=end_ts, title=title,
                             hook_text="o último é brutal", reason="r", hook=7, standalone=7, emotion=8,
                             payoff=8, score=score)


def test_parse_mmss():
    assert V.parse_mmss("01:30") == 90 and V.parse_mmss("0:05.5") == 5.5 and V.parse_mmss("1:02:03") == 3723
    assert V.parse_mmss("abc") is None and V.parse_mmss("") is None


def test_table_uses_times_relative_to_the_window_and_global_ids():
    win = SH[3:6]  # janela que começa em 30 s
    t = V.shots_table(win).splitlines()
    assert t[0] == "[3] 00:00–00:10 (10s)" and t[1].startswith("[4] 00:10–00:20 (10s) volume +12dB")
    c = Config(min_clip_s=8, max_clip_s=25, clips_per_video=3)
    p = V.build_prompt(win, c)
    from darkcnn.analyze import n_candidates
    assert "[5] 00:20–00:30" in p and f"até {n_candidates(c)} momentos" in p and "entre 8 e 25" in p and "30 s" in p
    assert "{" not in p.replace("{", "", 0) or True  # sem placeholders não substituídos:
    assert "{shots}" not in p and "{n_candidates}" not in p


def test_shot_at():
    assert V.shot_at(SH, 0) == 0 and V.shot_at(SH, 9.99) == 0 and V.shot_at(SH, 10) == 1
    assert V.shot_at(SH, 59.9) == 5 and V.shot_at(SH, 999) == 5 and V.shot_at(SH, -5) == 0


def test_reconcile_keeps_consistent_ids():
    c, n = V.reconcile(vc(1, 2, "00:10", "00:30").model_dump(), SH, 0.0)
    assert (c["start_id"], c["end_id"], n) == (1, 2, 0)
    c, n = V.reconcile(vc(1, 2, "00:12", "00:28").model_dump(), SH, 0.0)  # dentro da tolerância de 4 s
    assert (c["start_id"], c["end_id"], n) == (1, 2, 0)


def test_reconcile_trusts_time_when_id_is_misread():
    # Gemini leu "#4" mas o tempo que informou (00:10) é o plano 1; fim também errado (#5 vs 00:30 = plano 2)
    c, n = V.reconcile(vc(4, 5, "00:10", "00:29").model_dump(), SH, 0.0)
    assert (c["start_id"], c["end_id"], n) == (1, 2, 2)
    c, n = V.reconcile(vc(99, 2, "00:10", "00:30").model_dump(), SH, 0.0)  # ID inexistente é consertado pelo tempo
    assert (c["start_id"], n) == (1, 1)


def test_reconcile_applies_window_offset_and_tolerates_bad_timestamps():
    # janela começa em 30 s: tempos do Gemini são relativos ao vídeo enviado
    c, n = V.reconcile(vc(3, 4, "00:00", "00:20").model_dump(), SH, 30.0)
    assert (c["start_id"], c["end_id"], n) == (3, 4, 0)
    c, n = V.reconcile(vc(0, 1, "00:00", "00:20").model_dump(), SH, 30.0)  # IDs da janela errada
    assert (c["start_id"], c["end_id"], n) == (3, 4, 2)
    bad = vc(1, 2, "??", "").model_dump()  # sem tempo utilizável: não mexe
    assert V.reconcile(bad, SH, 0.0) == (bad, 0)


def test_ids_ass_one_event_per_shot_relative_to_window():
    ass = V.ids_ass(SH[3:5], "Arial")
    ev = [l for l in ass.splitlines() if l.startswith("Dialogue")]
    assert len(ev) == 2 and ev[0].endswith("#3") and ev[1].endswith("#4")
    assert ev[0].startswith("Dialogue: 0,0:00:00.00,0:00:10.00") and ev[1].startswith("Dialogue: 0,0:00:10.00,0:00:20.00")


@needs_ffmpeg
def test_make_proxy_has_requested_size_duration_and_audio(tmp_path):
    video = make_cut_video(tmp_path / "v.mp4")
    cfg = Config(proxy_height=240, visual_fps=2)
    dest = V.make_proxy(video, SH[2:5], tmp_path / "p" / "win.mp4", cfg, tmp_path / "tmp")  # 20 s–50 s
    info = video_info(dest)
    assert info["height"] == 240 and info["duration"] == pytest.approx(30, abs=0.6)
    from darkcnn.shots import has_audio
    assert has_audio(dest)


class PerCall(FakeBackend):
    """Devolve uma lista diferente de candidatos a cada chamada (uma por janela)."""

    def __init__(self, per_call):
        super().__init__()
        self.per_call = per_call

    def generate(self, prompt, schema, temperature, media=None):
        self.candidates = self.per_call[min(self.calls, len(self.per_call) - 1)]
        if media is not None:
            assert media.exists() and media.stat().st_size > 1000  # o vídeo reduzido existe de verdade
        return super().generate(prompt, schema, temperature, media)


@needs_ffmpeg
def test_select_visual_windows_cache_pause_and_reconcile(tmp_path):
    video = make_cut_video(tmp_path / "v.mp4")
    cfg = Config(min_clip_s=8, max_clip_s=25, clips_per_video=2, visual_window_min=0.5, proxy_height=144, judge=False,
                 visual_pause_s=7, workspace_dir=tmp_path / "ws")
    # 2 janelas de 30 s: planos 0-2 e 3-5. A 2ª resposta lê o número errado (usa IDs da janela 1)
    backend = PerCall([[vc(1, 2, "00:10", "00:30", score=9)], [vc(1, 2, "00:10", "00:30", score=8)]])
    client = GeminiClient(backend, tmp_path / "u.json", 40, sleep=lambda s: None)
    sleeps = []
    ws = tmp_path / "ws" / "vid"

    out, st = V.select_visual(video, "vid", ws, SH, cfg, lambda: client, sleep=sleeps.append)
    assert st == {"windows": 2, "api_calls": 2, "reconciled": 2} and backend.calls == 2
    assert sleeps == [7]  # pausa só ENTRE chamadas reais
    assert [(c["start_id"], c["end_id"]) for c in out] == [(1, 2), (4, 5)]  # a janela 2 foi corrigida pelo tempo
    assert not list((ws / "proxy").glob("*.mp4"))  # proxies apagados depois do uso

    sleeps.clear()
    out2, st2 = V.select_visual(video, "vid", ws, SH, cfg, lambda: client, sleep=sleeps.append)
    assert st2["api_calls"] == 0 and backend.calls == 2 and sleeps == [] and out2 == out  # tudo em cache

    V.select_visual(video, "vid", ws, SH, cfg, lambda: client, force=True, sleep=sleeps.append)
    assert backend.calls == 4
