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


CHOICES = [{"a": "Nunca sentir dor", "b": "Nunca sentir medo", "start": 2.0, "end": 6.0, "countdown": 3.0}]


def test_choice_events_show_both_options_then_the_countdown():
    ev = T.choice_events(CHOICES)
    styles = [l.split(",")[3] for l in ev]
    assert styles == ["OptA", "OptB", "VS", "Timer", "Timer", "Timer"]  # 3 s de contagem = 3 números
    assert "NUNCA SENTIR DOR" in ev[0] and "NUNCA SENTIR MEDO" in ev[1]
    # as opções e o "OU" ficam da pergunta até o fim da contagem, juntos no centro
    assert ev[0].startswith("Dialogue: 1,0:00:02.00,0:00:09.00")
    assert ev[2].startswith("Dialogue: 1,0:00:02.00,0:00:09.00") and ev[2].rstrip().endswith("OU")
    assert ev[3].startswith("Dialogue: 2,0:00:06.00,0:00:07.00") and ev[3].rstrip().endswith("3")
    assert ev[5].rstrip().endswith("1") and ev[5].startswith("Dialogue: 2,0:00:08.00,0:00:09.00")


def test_choice_events_without_countdown():
    assert [l.split(",")[3] for l in T.choice_events([{**CHOICES[0], "countdown": 0.0}])] == \
        ["OptA", "OptB", "VS"]


def test_narration_ass_has_captions_and_choices_together():
    words = [{"w": "você", "start": 2.1, "end": 2.4, "p": 1}, {"w": "prefere", "start": 2.4, "end": 2.9, "p": 1}]
    ass = T.build_narration_ass(words, CHOICES, 10.0, Config())
    styles = [l.split(",")[3] for l in ass.splitlines() if l.startswith("Dialogue")]
    assert styles.count("Default") == 2 and "OptA" in styles and "Timer" in styles
    assert "VOCÊ" in ass and "PREFERE" in ass
    # sem legenda, as escolhas continuam aparecendo
    sem = T.build_narration_ass(words, CHOICES, 10.0, Config(text_mode="none"))
    assert "Default" not in [l.split(",")[3] for l in sem.splitlines() if l.startswith("Dialogue")]
    assert "OptA" in sem


def test_choice_block_is_compact_and_centered():
    """A opção A termina logo acima do centro e a B começa logo abaixo; o "OU" fica no meio e a contagem embaixo."""
    import re
    ev = T.choice_events(CHOICES)
    pos = [tuple(int(x) for x in re.search(r"\\pos\((\d+),(\d+)\)", l).groups()) for l in ev]
    (ax, ay), (bx, by), (vx, vy), (tx, ty) = pos[0], pos[1], pos[2], pos[3]
    assert ax == bx == vx == tx == 540  # tudo centralizado na horizontal
    assert vy == 960 and ay < vy < by  # A em cima, OU no meio, B embaixo
    assert by - ay <= 120  # juntas: antes ficavam a ~1200 px uma da outra
    assert ty > by  # a contagem não cobre as opções
    assert "\\an2" in ev[0] and "\\an8" in ev[1]  # A cresce para cima e B para baixo (rótulo de 2 linhas não invade)
