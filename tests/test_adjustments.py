import re

from darkcnn import render, storage, textlayers
from darkcnn.config import Config, WatermarkCfg


def words(*spans):
    return [{"w": f"p{i}", "start": s, "end": e} for i, (s, e) in enumerate(spans)]


def test_fixed_defaults_are_the_agreed_ones():
    cfg = Config()
    assert cfg.countdown_s == 5.0 and cfg.tick_volume == 0.35 and cfg.font == "DejaVu Sans"
    assert cfg.fonts_dir and (cfg.fonts_dir / "DejaVuSans-Bold.ttf").is_file()  # empacotada: igual em qualquer SO


def test_timer_sits_between_options_and_captions():
    assert 1060 < textlayers.TIMER_Y < 1250  # opções terminam ~1050; legenda começa ~1300
    ass = textlayers.build_narration_ass([], [{"a": "A", "b": "B", "start": 0, "end": 3, "countdown": 5}], 12, Config())
    assert ass.count(f"\\pos(540,{textlayers.TIMER_Y})") == 5


def test_captions_never_live_inside_the_countdown_window():
    choice = {"a": "A", "b": "B", "start": 0.0, "end": 3.0, "countdown": 5}
    ws = words((0.0, 0.5), (2.6, 3.4), (4.0, 4.5), (8.0, 8.5))  # a 2ª invade a contagem, a 3ª nasce nela
    ass = textlayers.build_narration_ass(ws, [choice], 12.0, Config())
    caps = re.findall(r"Dialogue: 0,(\d+:\d\d:\d\d\.\d\d),(\d+:\d\d:\d\d\.\d\d),Default", ass)

    def sec(t):
        h, m, s = t.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)

    for a, b in caps:
        assert not (sec(a) < 8.0 and sec(b) > 3.0), (a, b)  # nenhum evento cruza 3.0–8.0
    assert len(caps) == 3  # p0, p1 (cortada em 3.0) e p3; a que nasceu na contagem some


def test_watermark_scales_to_percent_of_video_width():
    off = render.wm_chain("1:v", WatermarkCfg())
    on = render.wm_chain("2:v", WatermarkCfg(width_pct=25, opacity=0.5))
    assert "scale=" not in off
    assert on.startswith("[2:v]scale=270:-1,format=rgba,colorchannelmixer=aa=0.50")


def test_user_fonts_win_over_bundled(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert storage.default_fonts() == storage.BUNDLED_FONTS
    (tmp_path / "fonts").mkdir()
    (tmp_path / "fonts" / "x.ttf").write_bytes(b"0")
    assert storage.default_fonts() == tmp_path / "fonts"


def test_schedule_formats():
    import pytest
    from darkcnn import scheduler as sch
    for ok in ("*/5 * * * *", "@every 90m", "@every 6h", "30 8,19 * * 1,3,5;0 12 * * 6"):
        sch.validate_cron(ok)
    for bad in ("", "@every 2m", "@every 0h", "@every 90", "* * *", "x" * 70):
        with pytest.raises(ValueError):
            sch.validate_cron(bad)
    assert sch.next_fire("@every 90m", 1000.0) == 1000.0 + 5400
    base = 1_700_000_000.0
    both = sch.next_fire("0 0 1 1 *;@every 10m", base)
    assert both == base + 600  # vale o que dispara primeiro
    assert sch.is_due("@every 90m", None, base, base + 5400) and not sch.is_due("@every 90m", None, base, base + 5399)


def test_cron_runs_in_the_server_local_timezone(monkeypatch):
    import datetime as dt
    import time

    from darkcnn import scheduler as sch
    monkeypatch.setenv("TZ", "America/Sao_Paulo")
    if not hasattr(time, "tzset"):
        import pytest
        pytest.skip("tzset indisponível (Windows)")
    time.tzset()
    base = dt.datetime(2026, 10, 9, 10, 0, tzinfo=dt.timezone.utc).timestamp()
    nxt = dt.datetime.fromtimestamp(sch.next_fire("0 18 * * *", base))
    assert (nxt.hour, nxt.minute) == (18, 0)  # 18:00 locais, não 18:00 UTC
