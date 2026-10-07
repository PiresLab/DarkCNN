import pytest

from darkcnn import align


def W(w, s, e, p=0.9):
    return {"w": w, "start": s, "end": e, "p": p}


def mono(words):
    """Tempos crescentes, sem invadir a palavra seguinte, com duração mínima."""
    for a, b in zip(words, words[1:]):
        assert a["start"] < b["start"] and a["end"] <= b["start"] + 1e-9
    assert all(w["end"] - w["start"] >= 0.079 for w in words)


def test_norm():
    assert align.norm("Ação,") == "acao" and align.norm("VINTE") == "vinte" and align.norm("...") == ""


def test_perfect_match_uses_whisper_times():
    heard = [W("o", 0.0, 0.2), W("mar", 0.3, 0.8), W("é", 0.9, 1.0), W("fundo", 1.1, 1.6)]
    out = align.align_line("o mar é fundo", heard, start=10.0, dur=2.0)
    assert [w["w"] for w in out] == ["o", "mar", "é", "fundo"]
    assert [w["start"] for w in out] == [10.0, 10.3, 10.9, 11.1]  # deslocado pelo início da linha
    assert out[-1]["end"] == pytest.approx(11.6)
    mono(out)


def test_script_text_wins_over_what_whisper_heard():
    # o TTS falou "vinte e três", o Whisper ouviu "23"; a legenda tem de mostrar o texto do roteiro
    heard = [W("tem", 0.0, 0.3), W("23", 0.4, 1.0), W("ossos", 1.1, 1.5)]
    out = align.align_line("tem vinte e três ossos", heard, start=0.0, dur=1.8)
    assert [w["w"] for w in out] == ["tem", "vinte", "e", "três", "ossos"]
    # "tem" e "ossos" casaram: mantêm o tempo do Whisper; o miolo é interpolado entre eles
    assert out[0]["start"] == 0.0 and out[-1]["start"] == pytest.approx(1.1)
    assert all(0.3 <= w["start"] <= 1.1 for w in out[1:4])
    mono(out)


def test_missing_and_extra_words():
    heard = [W("um", 0.0, 0.2), W("dois", 0.3, 0.5), W("tres", 0.6, 0.8), W("quatro", 0.9, 1.1)]
    faltando = align.align_line("um dois tres quatro", heard[:2] + heard[3:], start=0.0, dur=1.2)
    assert [w["w"] for w in faltando] == ["um", "dois", "tres", "quatro"]
    mono(faltando)
    sobrando = align.align_line("um quatro", heard, start=0.0, dur=1.2)
    assert [w["w"] for w in sobrando] == ["um", "quatro"]
    assert sobrando[1]["start"] == pytest.approx(0.9)  # casou com a palavra certa, não com a 2ª
    mono(sobrando)


def test_no_match_or_no_audio_distributes_evenly():
    for heard in ([], [W("xyz", 0.0, 0.5), W("abc", 0.5, 1.0)]):
        out = align.align_line("uma frase bem diferente", heard, start=5.0, dur=3.0)
        assert [w["w"] for w in out] == ["uma", "frase", "bem", "diferente"]
        assert out[0]["start"] == 5.0 and out[-1]["end"] == pytest.approx(8.0, abs=0.01)
        mono(out)


def test_punctuation_and_accents_do_not_break_matching():
    heard = [W("ola", 0.0, 0.3), W("mundo", 0.4, 0.9)]
    out = align.align_line("Olá, mundo!", heard, start=0.0, dur=1.0)
    assert [w["w"] for w in out] == ["Olá,", "mundo!"]  # texto original preservado, com pontuação
    assert out[0]["start"] == 0.0 and out[1]["start"] == pytest.approx(0.4)


def test_empty_text_and_single_word():
    assert align.align_line("", [W("a", 0, 1)], 0, 1) == []
    one = align.align_line("oi", [], 2.0, 0.5)
    assert len(one) == 1 and one[0]["start"] == 2.0


def test_align_script_concatenates_lines_in_order():
    lines = [("primeira linha", 0.0, 1.0), ("segunda linha", 1.5, 1.0)]
    heard = [[W("primeira", 0.0, 0.5), W("linha", 0.5, 1.0)], [W("segunda", 0.0, 0.5), W("linha", 0.5, 1.0)]]
    out = align.align_script(lines, heard)
    assert [w["w"] for w in out] == ["primeira", "linha", "segunda", "linha"]
    assert out[2]["start"] == pytest.approx(1.5)  # a 2ª linha começa depois da pausa
    mono(out)


def test_matched_fraction_measures_confidence():
    heard = [W("o", 0, .2), W("mar", .3, .8)]
    assert align.matched_fraction("o mar", heard) == 1.0
    assert align.matched_fraction("o mar é fundo", heard) == pytest.approx(0.5)
    assert align.matched_fraction("nada a ver aqui", heard) < 0.3
    assert align.matched_fraction("", heard) == 1.0
