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


def test_both_has_context_on_top_and_word_captions_below():
    clip = {**CLIP, "context": "Pesquisadora explica por que cerveja zero dá positivo"}
    ass = T.build_ass(clip, WORDS, Config(text_mode="both"))
    d = [l for l in ass.splitlines() if l.startswith("Dialogue")]
    assert any(",Context," in l and "Pesquisadora explica" in l for l in d)  # contexto no topo, a clipe todo
    assert any(",Default," in l and "MUNDO." in l for l in d)  # e a legenda por palavra
    assert not any(",Hook," in l for l in d)  # embaixo é só legenda (sem gancho)
    assert d[0].startswith("Dialogue: 0,0:00:00.00,0:00:10.00")


def test_both_falls_back_to_title_without_context():
    ass = T.build_ass({**CLIP, "title": "Título só"}, WORDS, Config(text_mode="both"))
    assert "Título só" in ass


def test_ranked_has_theme_title_big_rank_badge_and_hook():
    clip = {**CLIP, "rank_pos": 3, "theme": "top 5 finalizações", "title": "Mata-leão no 2º round"}
    ass = T.build_ass(clip, [], Config(text_mode="ranked"))
    d = [l for l in ass.splitlines() if l.startswith("Dialogue")]
    styles = [l.split(",")[3] for l in d]
    assert styles == ["Theme", "Title", "Rank", "Badge", "Hook"]
    assert "TOP 5 FINALIZAÇÕES" in d[0] and "Mata-leão" in d[1]
    assert d[2].startswith("Dialogue: 2,0:00:00.00,0:00:01.40") and "#3" in d[2]  # número grande só na entrada
    assert d[3].startswith("Dialogue: 1,0:00:00.00,0:00:10.00") and d[3].endswith("#3")  # selo fixo
