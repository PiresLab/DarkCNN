import pytest
from conftest import cand, make_words
from darkcnn import analyze
from darkcnn.config import Config
from darkcnn.transcribe import split_sentences

# 12 frases de 5 s: frase i vai de 5i a 5i+4,5 s
SENT = split_sentences(make_words(12))  # frases coladas (~0,1 s entre elas): frase i = 5i .. 5i+4,9
GAP = split_sentences(make_words(12, speech_frac=0.6))  # 2 s de silêncio entre frases: frase i = 5i .. 5i+2,94
TOTAL = 60.0


def cfg(**kw):
    base = dict(min_clip_s=8, max_clip_s=20, clips_per_video=3, judge=False)
    base.update(kw)
    return Config(**base)


def run(cands, sent=None, **kw):
    return analyze.resolve([c.model_dump() for c in cands], sent or SENT, cfg(**kw), TOTAL)


def test_build_transcript_has_ids_and_clock():
    t = analyze.build_transcript(SENT)
    assert t.splitlines()[0].startswith("[0] (00:00) palavra0x0")
    assert t.splitlines()[2].startswith("[2] (00:10) ")


def test_prompt_survives_braces_in_transcript():
    s = [{"id": 0, "start": 0, "end": 3, "text": "olha {isso} aqui {0}"}]
    c = cfg()
    p = analyze.build_prompt(s, c)
    assert "olha {isso} aqui {0}" in p and f"até {analyze.n_candidates(c)} trechos" in p and "entre 8 e 20" in p


def test_times_use_full_padding_when_there_is_silence():
    acc, rej = run([cand(2, 4)], sent=GAP)  # frases 2..4 = 10.0 .. 22.94
    c = acc[0]
    assert c["start"] == pytest.approx(10.0 - 0.15) and c["end"] == pytest.approx(GAP[4]["end"] + 0.30)
    assert not rej and c["rank"] == 1 and not c["adjusted"]


def test_padding_never_invades_neighbor_sentences():
    # frases coladas: o respiro de 0,3 s passaria do começo da frase seguinte (e comeria uma palavra)
    acc, _ = run([cand(2, 4)])
    c = acc[0]
    assert SENT[1]["end"] < c["start"] <= SENT[2]["start"]
    assert SENT[4]["end"] <= c["end"] < SENT[5]["start"]


def test_pad_clamped_to_video_bounds():
    acc, _ = run([cand(0, 2), cand(9, 11, score=6)], sent=GAP)
    assert acc[0]["start"] == 0.0
    last = [c for c in acc if c["end_id"] == 11][0]
    assert last["end"] <= TOTAL


def test_swapped_ids_are_fixed_and_out_of_range_rejected():
    acc, rej = run([cand(4, 2), cand(0, 99, score=5)])
    assert acc[0]["start_id"] == 2 and acc[0]["end_id"] == 4
    assert "fora do intervalo" in rej[0]["reason_rejected"]


def test_too_short_is_extended_with_following_sentences():
    acc, _ = run([cand(1, 1)])  # uma frase de 4,5 s (+pads) < 8 s
    c = acc[0]
    assert c["duration"] >= 8 and c["adjusted"] and c["end_id"] > 1
    assert SENT[c["end_id"]]["end"] <= c["end"] < SENT[c["end_id"] + 1]["start"]  # ainda termina em fim de frase


def test_too_long_is_trimmed_at_sentence_end():
    acc, _ = run([cand(0, 9)])  # 0..49,5 s
    c = acc[0]
    assert c["duration"] <= 20 and c["adjusted"] and c["end_id"] < 9
    assert SENT[c["end_id"]]["end"] <= c["end"] < SENT[c["end_id"] + 1]["start"]


def test_back_to_back_clips_are_not_rejected_as_overlapping():
    acc, rej = run([cand(1, 2, score=8), cand(3, 4, score=7)])  # vizinhos: só o respiro se toca
    assert len(acc) == 2 and not [r for r in rej if "sobreposto" in r["reason_rejected"]]


def test_short_at_end_of_video_is_rejected():
    acc, rej = run([cand(11, 11)])  # última frase: não há como estender
    assert not acc and "curto demais" in rej[0]["reason_rejected"]


def test_overlap_keeps_higher_score_and_respects_limit():
    acc, rej = run([cand(0, 2, score=6), cand(1, 3, score=9), cand(6, 8, score=5), cand(9, 11, score=4),
                    cand(4, 5, score=3)], clips_per_video=2)
    # (1,3) nota 9 fica; (0,2) nota 6 colide com ela; (6,8) nota 5 fica; as demais passam do limite de 2
    assert [(c["start_id"], c["end_id"], c["score"]) for c in acc] == [(1, 3, 9), (6, 8, 5)]
    reasons = {(r["start_id"], r["end_id"]): r["reason_rejected"] for r in rej}
    assert "sobreposto" in reasons[(0, 2)]
    assert "acima da quantidade" in reasons[(9, 11)] and "acima da quantidade" in reasons[(4, 5)]
    for a in acc:
        for b in acc:
            if a is not b:
                assert not (a["start"] < b["end"] and b["start"] < a["end"])


def test_ranks_follow_score_and_text_is_truncated():
    acc, _ = run([cand(0, 2, score=5, title="x" * 100, hook_text="y" * 100), cand(6, 8, score=9)])
    assert [c["rank"] for c in acc] == [1, 2] and acc[0]["score"] == 9
    low = [c for c in acc if c["score"] == 5][0]
    assert len(low["title"]) <= 60 and len(low["hook_text"]) <= 40 and low["title"].endswith("…")


def test_candidate_counts_depend_on_judge():
    on, off = Config(clips_per_video=5, judge=True), Config(clips_per_video=5, judge=False)
    assert analyze.n_candidates(on) == 15 and analyze.n_candidates(off) == 10
    assert analyze.pool_size(on) == 12 and analyze.pool_size(off) == 5  # teto de 12 para o juiz assistir
    assert analyze.pool_size(Config(clips_per_video=2, judge=True)) == 6


S2 = [{"id": 0, "start": 0, "end": 4, "text": "Você sabia que a cerveja sem álcool pode dar positivo?"},
      {"id": 1, "start": 4, "end": 8, "text": "Pois é, eu também não acreditava."},
      {"id": 2, "start": 8, "end": 12, "text": "Aí fui pesquisar a fundo."}]


def test_hook_quote_matches_even_with_accents_punctuation_and_small_errors():
    ok = analyze.hook_quote_ok
    assert ok("Você sabia que a cerveja sem álcool pode dar positivo", S2, 0)
    assert ok("voce sabia que a cerveja sem alcool pode dar positivo?", S2, 0)  # sem acento/pontuação
    assert ok("sabia que a cerveja sem álcool pode dar um positivo", S2, 0)  # 1 palavra a mais
    assert ok("Pois é eu também não acreditava", S2, 0)  # na frase seguinte ainda conta (janela de 2)
    assert not ok("A inflação subiu forte no último trimestre", S2, 0)  # inventada
    assert not ok("sim sim", S2, 0) and not ok("", S2, 0)  # curta/vazia não verifica
    assert not ok("Você sabia que a cerveja", S2, 99)  # id inválido


def test_verify_hooks_penalizes_invented_quotes_only():
    cands = [{"start_id": 0, "score": 8, "hook_quote": "Você sabia que a cerveja sem álcool pode dar positivo"},
             {"start_id": 1, "score": 8, "hook_quote": "frase totalmente inventada pelo modelo agora"},
             {"start_id": 2, "score": 1, "hook_quote": ""}]
    assert analyze.verify_hooks(cands, S2) == 2
    assert [(c["hook_ok"], c["score"]) for c in cands] == [(True, 8), (False, 6), (False, 1)]  # nota nunca < 1


def test_rejection_summary_names_the_reasons():
    rej = [{"reason_rejected": "curto demais (38s < 40s)"}, {"reason_rejected": "longo demais (51s > 45s)"},
           {"reason_rejected": "curto demais (30s < 40s)"}, {"reason_rejected": "sobreposto ao corte de 00:10"},
           {"reason_rejected": "id fora do intervalo 0..9".replace("id fora", "fora")}, {"reason_rejected": "algo novo"}]
    s = analyze.summarize_rejections(rej)
    assert s.startswith("2 curtos demais") and "1 longos demais" in s and "1 sobrepostos" in s
    assert "1 com ID inválido" in s and "1 outros" in s
    assert analyze.summarize_rejections([]) == ""
