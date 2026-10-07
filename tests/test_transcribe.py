from darkcnn.transcribe import normalize_words, split_sentences


def W(w, s, e):
    return {"w": w, "start": s, "end": e, "p": 0.9}


def test_normalize_fixes_zero_duration_and_same_start():
    # caso real do Whisper: "que" e "vocês" com o mesmo início, "que" com duração 0
    out = normalize_words([W("que", 1.72, 1.72), W("vocês", 1.72, 2.0), W("estão", 2.0, 2.3)], min_dur=0.08)
    assert all(w["end"] - w["start"] >= 0.08 - 1e-9 for w in out)
    assert [w["start"] for w in out] == sorted(set(w["start"] for w in out))  # estritamente crescente
    for a, b in zip(out, out[1:]):
        assert a["end"] <= b["start"] + 1e-9  # sem invadir a próxima


def test_normalize_keeps_good_words_untouched():
    ws = [W("a", 0.0, 0.3), W("b", 0.4, 0.8)]
    assert normalize_words(ws) == ws


def test_split_on_terminal_punctuation_and_trailing_quote():
    ws = [W("Oi", 0, .3), W("gente.", .3, .6), W('"Sério?"', .7, 1.0), W("sim", 1.0, 1.3)]
    s = split_sentences(ws)
    assert [x["text"] for x in s] == ["Oi gente.", '"Sério?"', "sim"]
    assert [x["id"] for x in s] == [0, 1, 2]
    assert s[0]["start"] == 0 and s[0]["end"] == .6


def test_split_on_long_pause():
    ws = [W("um", 0, .3), W("dois", .3, .6), W("três", 2.0, 2.3)]  # pausa de 1,4 s sem pontuação
    assert [x["text"] for x in split_sentences(ws)] == ["um dois", "três"]


def test_split_caps_run_on_sentences():
    ws = [W(f"w{i}", i * .3, i * .3 + .25) for i in range(60)]
    s = split_sentences(ws, max_words=25)
    assert [len(x["text"].split()) for x in s] == [25, 25, 10]


def test_split_empty():
    assert split_sentences([]) == []
