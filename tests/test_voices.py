
from conftest import needs_ffmpeg
from darkcnn import voices
from darkcnn.config import Config
from test_tts import FakeBackend


def test_report_without_say_lists_config_and_known_voices(tmp_path):
    cfg = Config(tts_voice="Charon", tts_voices_pool=["Puck", "Kore"], output_dir=tmp_path)
    text = voices.voices_report(cfg)
    assert "voz padrão: Charon" in text and "pool: Puck, Kore" in text and "Zephyr" in text
    assert "--say" in text


def test_models_flag_lists_and_warns_when_the_configured_model_is_missing(tmp_path):
    cfg = Config(tts_model="gemini-antigo-tts", output_dir=tmp_path)
    text = voices.voices_report(cfg, models=True, lister=lambda: ["gemini-novo-tts", "gemini-outro-tts"])
    assert "2 modelo(s) TTS" in text and "gemini-novo-tts" in text and "não está na lista" in text
    ok = voices.voices_report(cfg.model_copy(update={"tts_model": "gemini-novo-tts"}), models=True,
                              lister=lambda: ["gemini-novo-tts"])
    assert "não está na lista" not in ok
    none = voices.voices_report(cfg, models=True, lister=lambda: [])
    assert "nenhum" in none


def test_sample_voices():
    cfg = Config(tts_voice="Kore")
    assert voices.sample_voices(cfg, False) == ["Kore"]
    four = voices.sample_voices(cfg, True)
    assert four[0] == "Kore" and len(four) == 4 and len(set(four)) == 4
    pool = cfg.model_copy(update={"tts_voices_pool": ["Puck", "Puck", "Charon"]})
    assert voices.sample_voices(pool, True) == ["Puck", "Charon"]


@needs_ffmpeg
def test_say_makes_one_sample_per_voice(tmp_path):
    cfg = Config(tts_voice="Kore", output_dir=tmp_path)
    fake = FakeBackend()
    text = voices.voices_report(cfg, "testando", all_voices=True, backend=fake)
    assert fake.voices == voices.sample_voices(cfg, True) and fake.said == ["testando"] * 4
    assert all((tmp_path / "voices" / f"{v}.wav").exists() for v in fake.voices)
    assert "Kore:" in text
