from darkcnn import textlayers as T
from darkcnn.config import Config

CLIP = {"start": 10.0, "end": 20.0, "title": "Título {perigoso} \\N aqui", "hook_text": "o último é brutal"}
WORDS = [{"w": "fora", "start": 5.0, "end": 5.4, "p": 1}, {"w": "olá", "start": 10.2, "end": 10.5, "p": 1},
         {"w": "mundo.", "start": 10.5, "end": 11.0, "p": 1}, {"w": "depois", "start": 25.0, "end": 25.4, "p": 1}]


def test_ass_time():
    assert T.ass_time(0) == "0:00:00.00" and T.ass_time(61.5) == "0:01:01.50" and T.ass_time(3725.04) == "1:02:05.04"
    assert T.ass_time(-3) == "0:00:00.00"


def test_esc_removes_ass_control_chars():
    out = T.esc("a {\\b1}b \\N c\nd")
    assert "{" not in out and "}" not in out and "\\" not in out and "\n" not in out


def test_none_mode_returns_none():
    assert T.build_ass(CLIP, WORDS, Config(text_mode="none")) is None


def test_titled_has_title_top_and_hook_bottom_for_whole_clip():
    ass = T.build_ass(CLIP, WORDS, Config(text_mode="titled"))
    d = [l for l in ass.splitlines() if l.startswith("Dialogue")]
    assert len(d) == 2
    assert ",Title," in d[0] and ",Hook," in d[1]
    assert d[0].startswith("Dialogue: 0,0:00:00.00,0:00:10.00")
    assert "O ÚLTIMO É BRUTAL" in d[1]
    assert "perigoso" in d[0] and "{perigoso}" not in d[0]


def test_captions_only_words_inside_clip_with_relative_times():
    ass = T.build_ass(CLIP, WORDS, Config(text_mode="captions"))
    d = [l for l in ass.splitlines() if l.startswith("Dialogue")]
    assert len(d) == 2  # "olá" e "mundo." (as de fora são ignoradas)
    assert "FORA" not in ass and "DEPOIS" not in ass
    assert d[0].startswith("Dialogue: 0,0:00:00.20,0:00:00.50")  # 10.2 - 10.0
    assert T.HIGHLIGHT in d[0] and "MUNDO." in d[0]


def test_group_words_respects_width_budget():
    ws = [{"w": w} for w in ["responsabilidade", "legenda", "com", "palavra", "destacada"]]
    for g in T.group_words(ws):
        assert len(g) <= 3 and (len(g) == 1 or sum(len(x["w"]) for x in g) + len(g) - 1 <= 16)
