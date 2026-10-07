import pytest

from conftest import make_cut_video, needs_ffmpeg
from darkcnn import shots as S


def covers(sh, dur):
    assert sh[0]["start"] == 0 and sh[-1]["end"] == pytest.approx(dur)
    for a, b in zip(sh, sh[1:]):
        assert a["end"] == pytest.approx(b["start"])  # sem buracos nem sobreposição
    assert [x["id"] for x in sh] == list(range(len(sh)))


def test_build_shots_basic_and_ignores_out_of_range_cuts():
    sh = S.build_shots([10, 20, 99, 0], 30, min_shot_s=1.5, max_shot_s=50)
    assert [(x["start"], x["end"]) for x in sh] == [(0, 10), (10, 20), (20, 30)]
    covers(sh, 30)


def test_short_shots_are_absorbed():
    sh = S.build_shots([10, 10.5, 20], 30, min_shot_s=1.5, max_shot_s=50)  # [10,10.5) é curto demais
    assert [(x["start"], x["end"]) for x in sh] == [(0, 10.5), (10.5, 20), (20, 30)]
    covers(sh, 30)
    first = S.build_shots([0.5, 10], 30, min_shot_s=1.5, max_shot_s=50)  # primeiro plano curto vai para o seguinte
    assert [(x["start"], x["end"]) for x in first] == [(0, 10), (10, 30)]


def test_long_continuous_shots_are_split_evenly():
    sh = S.build_shots([], 30, min_shot_s=1.5, max_shot_s=12)
    assert len(sh) == 3 and all(x["end"] - x["start"] == pytest.approx(10) for x in sh)
    covers(sh, 30)


def test_annotate_loudness_relative_to_median():
    sh = S.build_shots([10, 20], 30)
    series = [(t / 10, -40.0) for t in range(0, 100)] + [(t / 10, -20.0) for t in range(100, 200)] + \
             [(t / 10, -40.0) for t in range(200, 300)]
    S.annotate_loudness(sh, series)
    assert [x["loud"] for x in sh] == [0.0, 20.0, 0.0]
    S.annotate_loudness(sh := S.build_shots([10], 20), [])  # sem áudio: não mexe
    assert all(x["loud"] is None for x in sh)


def test_windows_close_on_shot_boundaries_and_merge_small_tail():
    sh = S.build_shots([10 * i for i in range(1, 12)], 120)  # 12 planos de 10 s
    w = S.make_windows(sh, window_s=50, min_tail_s=30)
    assert [len(x) for x in w] == [5, 5, 2] or [len(x) for x in w] == [5, 7]
    assert sum(len(x) for x in w) == 12
    for a, b in zip(w, w[1:]):
        assert a[-1]["end"] == b[0]["start"]
    tail = S.make_windows(sh, window_s=50, min_tail_s=60)  # cauda de 20 s < 60 s: vai junto da anterior
    assert len(tail) == 2 and tail[-1][-1]["end"] == 120


@needs_ffmpeg
def test_detect_cuts_finds_the_real_cuts(tmp_path):
    video = make_cut_video(tmp_path / "v.mp4")
    cuts = S.detect_cuts(video, 0.30)
    assert len(cuts) == 5
    for got, want in zip(cuts, [10, 20, 30, 40, 50]):
        assert got == pytest.approx(want, abs=0.15)


@needs_ffmpeg
def test_loudness_marks_the_loud_shots(tmp_path):
    video = make_cut_video(tmp_path / "v.mp4")
    assert S.has_audio(video)
    sh = S.build_shots(S.detect_cuts(video), 60)
    S.annotate_loudness(sh, S.loudness_series(video))
    loud = [x["loud"] for x in sh]
    assert len(sh) == 6 and loud[1] > 10 and loud[4] > 10  # planos 1 e 4 (índices) são os altos
    assert max(loud[0], loud[2], loud[3], loud[5]) <= 0.5


@needs_ffmpeg
def test_video_without_audio(tmp_path):
    video = make_cut_video(tmp_path / "mudo.mp4", audio=False)
    assert S.has_audio(video) is False
