import pytest

from darkcnn import script as S
from darkcnn.config import Config


def test_clean_spoken_removes_what_is_not_said_out_loud():
    assert S.clean_spoken("**Olha** isso (pausa dramática) aqui!") == "Olha isso aqui!"
    assert S.clean_spoken("legal 🔥 demais") == "legal demais"
    assert S.clean_spoken("  muitos   espaços\n\nmesmo ") == "muitos espaços mesmo"
    assert S.clean_spoken("#hashtag _itálico_ `código`") == "hashtag itálico código"


def test_clean_script_drops_empty_lines_and_fixes_broken_choices():
    s = S.Script(title="T" * 100, topic="  oceano  ", lines=[
        S.Line(text="primeira", kind="fala"),
        S.Line(text="  (só rubrica)  ", kind="fala"),          # vira vazia: sai
        S.Line(text="escolhe", kind="escolha", option_a="A" * 50, option_b="B"),
        S.Line(text="sem opções", kind="escolha", option_a="", option_b=""),  # vira fala
    ])
    out = S.clean_script(s)
    assert [(l.text, l.kind) for l in out.lines] == [
        ("primeira", "fala"), ("escolhe", "escolha"), ("sem opções", "fala")]
    assert len(out.lines[1].option_a) == 28 and out.lines[2].option_a == ""  # rótulo cortado
    assert len(out.title) <= 60 and out.topic == "oceano"


def test_prompt_has_format_block_target_and_no_placeholders():
    cfg = Config(narrate_format="voce-prefere", count=3, target_s=50)
    p = S.build_prompt(cfg)
    assert "3 roteiro(s)" in p and "voce-prefere" in p and "VOCÊ PREFERE" in p
    assert "50 segundos" in p and f"{int(50 * S.WORDS_PER_SECOND)} palavras" in p
    assert "escolha você mesmo um assunto" in p  # sem --topic, a IA escolhe
    assert "{" not in p.replace("{", "", 0) or all(x not in p for x in ("{count}", "{format_block}", "{topic_block}"))


def test_prompt_with_explicit_topic_and_free_format():
    p = S.build_prompt(Config(topic="o oceano profundo", narrate_format="mitos desmentidos"))
    assert 'TEMA OBRIGATÓRIO: "o oceano profundo"' in p and "escolha você mesmo" not in p
    assert 'Formato "mitos desmentidos"' in p  # formato livre vira instrução genérica


def test_estimated_duration_counts_words_and_pauses():
    cfg = Config(pause_s=0.5, countdown_s=3.0)
    s = S.Script(title="t", topic="t", lines=[
        S.Line(text=" ".join(["palavra"] * 26)),
        S.Line(text="escolhe", kind="escolha", option_a="a", option_b="b"),
    ])
    # 27 palavras / 2.6 + 1 pausa + 1 contagem
    assert S.estimated_duration_s(s, cfg) == pytest.approx(27 / S.WORDS_PER_SECOND + 0.5 + 3.0)
