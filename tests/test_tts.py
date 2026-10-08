import random
from types import SimpleNamespace

import pytest

from conftest import needs_ffmpeg
from darkcnn import tts
from darkcnn.config import Config
from darkcnn.media import probe_duration, run


def make_wav(path, dur=0.5, freq=440):
    path.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", f"sine=frequency={freq}:duration={dur}", "-ac", "1", "-ar", "32000", str(path)])
    return path


def test_pick_voice_uses_the_pool_or_the_default():
    assert tts.pick_voice(Config(tts_voice="Kore"), random.Random(1)) == "Kore"
    pool = Config(tts_voices_pool=["Puck", "Charon", "Fenrir"])
    seen = {tts.pick_voice(pool, random.Random(i)) for i in range(30)}
    assert seen == {"Puck", "Charon", "Fenrir"}
    assert tts.pick_voice(pool, random.Random(5)) == tts.pick_voice(pool, random.Random(5))  # reprodutível


def test_line_key_depends_on_text_voice_style_and_speed_not_on_position():
    cfg = Config()
    k = tts.line_key("olá mundo", "Kore", cfg)
    assert k == tts.line_key("olá mundo", "Kore", cfg)
    assert k != tts.line_key("outro texto", "Kore", cfg)
    assert k != tts.line_key("olá mundo", "Puck", cfg)
    assert k != tts.line_key("olá mundo", "Kore", cfg.model_copy(update={"tts_speed": 1.2}))
    assert k != tts.line_key("olá mundo", "Kore", cfg.model_copy(update={"tts_style": "grite"}))
    assert k != tts.line_key("olá mundo", "Kore", cfg.model_copy(update={"tts_model": "outro-modelo"}))


class FakeBackend:
    def __init__(self, dur_per_word=0.4):
        self.said: list[str] = []
        self.voices: list[str] = []
        self.dur_per_word = dur_per_word

    def say(self, text, voice, dest):
        self.said.append(text)
        self.voices.append(voice)
        make_wav(dest, dur=max(0.3, len(text.split()) * self.dur_per_word))


@needs_ffmpeg
def test_synthesize_caches_by_content(tmp_path):
    cfg = Config()
    b = FakeBackend()
    wavs, made = tts.synthesize(["uma frase", "outra frase"], "Kore", cfg, b, tmp_path / "voice")
    assert made == 2 and len(wavs) == 2 and all(w.exists() for w in wavs)

    wavs2, made2 = tts.synthesize(["uma frase", "MUDOU aqui"], "Kore", cfg, b, tmp_path / "voice")
    assert made2 == 1 and wavs2[0] == wavs[0]  # só a linha alterada foi sintetizada
    assert b.said == ["uma frase", "outra frase", "MUDOU aqui"]
    # a mesma frase em duas posições usa o mesmo arquivo
    rep, made3 = tts.synthesize(["uma frase", "uma frase"], "Kore", cfg, b, tmp_path / "voice")
    assert made3 == 0 and rep[0] == rep[1]


@needs_ffmpeg
def test_place_and_build_track_insert_the_pauses(tmp_path):
    wavs = [make_wav(tmp_path / f"l{i}.wav", dur=d) for i, d in enumerate([1.0, 0.5, 0.8])]
    spokens = tts.place(wavs, [0.4, 3.0, 0.0])
    assert [(s.start, s.end) for s in spokens] == [(0.0, 1.0), (1.4, 1.9), (4.9, 5.7)]
    dur = tts.build_track(spokens, tmp_path / "track.wav")
    assert dur == pytest.approx(5.7, abs=0.08)  # falas + pausas
    assert probe_duration(tmp_path / "track.wav") == pytest.approx(dur)


def test_build_track_without_lines(tmp_path):
    with pytest.raises(tts.TTSError, match="sem nenhuma linha"):
        tts.build_track([], tmp_path / "x.wav")


# ---------------------------------------------------------------- Gemini TTS (cliente falso)
class FakeGenaiClient:
    """Imita `client.models.generate_content` para o TTS: devolve PCM, ou erros programados."""

    def __init__(self, script=None, pcm_seconds=1.0, mime="audio/L16;codec=pcm;rate=24000"):
        self.script = list(script or [])
        self.calls: list[dict] = []
        self.pcm = b"\x00\x01" * int(24000 * pcm_seconds)
        self.mime = mime
        self.models = SimpleNamespace(generate_content=self._gen)

    def _gen(self, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        step = self.script.pop(0) if self.script else "ok"
        if isinstance(step, Exception):
            raise step
        if step == "sem_audio":
            part = SimpleNamespace(inline_data=None, text="desculpe")
            return SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]),
                                                              finish_reason="OTHER")])
        part = SimpleNamespace(inline_data=SimpleNamespace(data=self.pcm, mime_type=self.mime))
        return SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))])


class ApiErr(Exception):
    def __init__(self, code, message="x"):
        super().__init__(message)
        self.code, self.message = code, message


def gemini_backend(client, **kw):
    sleeps: list[float] = []
    clock = [0.0]
    kw.setdefault("min_interval_s", 0.0)
    b = tts.GeminiTTSBackend("chave", "modelo-tts", style="Narre bem.", client=client,
                             sleep=lambda s: (sleeps.append(s), clock.__setitem__(0, clock[0] + s)),
                             clock=lambda: clock[0], backoff_s=10.0, **kw)
    return b, sleeps


@needs_ffmpeg
def test_gemini_backend_saves_pcm_as_24k_mono_wav_with_voice_and_style(tmp_path):
    client = FakeGenaiClient(pcm_seconds=1.0)
    b, _ = gemini_backend(client)
    dest = tmp_path / "o" / "fala.wav"
    b.say("bom dia pessoal", "Puck", dest)
    assert probe_duration(dest) == pytest.approx(1.0, abs=0.02)
    call = client.calls[0]
    assert call["model"] == "modelo-tts"
    assert call["contents"] == "Narre bem.\n\nbom dia pessoal"
    cfg = call["config"]
    assert list(cfg.response_modalities) == ["AUDIO"]
    assert cfg.speech_config.voice_config.prebuilt_voice_config.voice_name == "Puck"


@needs_ffmpeg
def test_gemini_backend_speed_uses_atempo(tmp_path):
    b, _ = gemini_backend(FakeGenaiClient(pcm_seconds=2.0), speed=1.25)
    dest = tmp_path / "fala.wav"
    b.say("texto", "Kore", dest)
    assert probe_duration(dest) == pytest.approx(1.6, abs=0.05)


@needs_ffmpeg
def test_gemini_backend_reads_the_sample_rate_from_the_mime_type(tmp_path):
    b, _ = gemini_backend(FakeGenaiClient(pcm_seconds=1.0, mime="audio/L16;rate=48000"))
    b.say("texto", "Kore", tmp_path / "fala.wav")
    assert probe_duration(tmp_path / "fala.wav") == pytest.approx(0.5, abs=0.02)  # mesmos bytes, 2x a taxa


@needs_ffmpeg
def test_gemini_backend_retries_transient_errors_and_missing_audio(tmp_path):
    client = FakeGenaiClient(script=[ApiErr(429, "cota"), "sem_audio", "ok"])
    b, sleeps = gemini_backend(client)
    b.say("texto", "Kore", tmp_path / "fala.wav")
    assert len(client.calls) == 3 and sleeps == [10.0, 20.0]  # backoff exponencial
    assert (tmp_path / "fala.wav").exists()


def test_gemini_backend_gives_up_with_a_readable_error(tmp_path):
    client = FakeGenaiClient(script=["sem_audio"] * 5)
    b, _ = gemini_backend(client, retries=2)
    with pytest.raises(tts.TTSError, match="falhou após 3 tentativas.*não devolveu áudio"):
        b.say("texto", "Kore", tmp_path / "fala.wav")
    assert len(client.calls) == 3


def test_gemini_backend_does_not_retry_a_rejected_request(tmp_path):
    client = FakeGenaiClient(script=[ApiErr(404, "model not found")])
    b, sleeps = gemini_backend(client)
    with pytest.raises(tts.TTSError, match="recusou o pedido \\(404\\).*voices --models"):
        b.say("texto", "Kore", tmp_path / "fala.wav")
    assert len(client.calls) == 1 and sleeps == []


@needs_ffmpeg
def test_gemini_backend_spaces_the_calls(tmp_path):
    client = FakeGenaiClient()
    b, sleeps = gemini_backend(client, min_interval_s=7.0)
    b.say("um", "Kore", tmp_path / "a.wav")
    b.say("dois", "Kore", tmp_path / "b.wav")
    assert sleeps == [7.0]  # só a 2ª chamada espera


def test_missing_api_key_is_a_clear_error(tmp_path):
    b = tts.GeminiTTSBackend(None, "m")
    with pytest.raises(tts.TTSError, match="GEMINI_API_KEY"):
        b.say("x", "Kore", tmp_path / "a.wav")


def test_make_backend_uses_the_config(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    b = tts.make_backend(Config(tts_model="m", tts_speed=1.1, tts_min_interval_s=3))
    assert isinstance(b, tts.GeminiTTSBackend) and b.model == "m" and b.speed == 1.1 and b.min_interval_s == 3
