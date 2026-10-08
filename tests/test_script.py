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


def _script(*lines):
    return S.Script(title="t", topic="x", lines=[S.Line(text=t) for t in lines])


def test_opening_check_accepts_a_question_to_the_viewer():
    assert S.opening_check(_script("Por que o seu cérebro te engana todo dia?", "E a resposta é o oposto.")) == []
    assert S.opening_check(_script("Tudo que te contaram sobre o sono está errado.")) == []  # 2ª pessoa, sem "?"
    assert S.opening_check(_script("Quem inventou o zero?")) == []  # pergunta, sem "você"


def test_opening_check_flags_weak_openings():
    assert any("saudação" in w for w in S.opening_check(_script("Fala galera, hoje vou te contar algo.")))
    assert any("saudação" in w for w in S.opening_check(_script("Você sabia que o polvo tem três corações?")))
    assert any("não faz pergunta" in w for w in S.opening_check(_script("O polvo tem três corações.")))
    longa = " ".join(["palavra"] * 20) + " você?"
    assert any("20 palavras" in w or "21 palavras" in w for w in S.opening_check(_script(longa)))


def test_opening_check_ignores_empty_and_choice_openers():
    assert S.opening_check(S.Script.model_construct(title="t", topic="x", lines=[])) == []
    q = S.Script(title="t", topic="x", lines=[S.Line(text="nunca mais dormir ou nunca mais comer", kind="escolha",
                                                      option_a="A", option_b="B")])
    assert S.opening_check(q) == []


def test_prompt_asks_for_a_persuasive_opening():
    p = S.build_prompt(Config())
    assert "SEGURE A RESPOSTA" in p and "CUMPRA A PROMESSA" in p and "fala diretamente com quem assiste" in p
    assert "{count}" not in p and "{target_s" not in p
