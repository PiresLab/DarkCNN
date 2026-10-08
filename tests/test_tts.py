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


# ---------------------------------------------------------------- tic-tac da contagem
CH = [{"a": "A", "b": "B", "start": 1.0, "end": 3.0, "countdown": 3.0}]


def _energy(x, a, b, sr=tts.TRACK_SR):
    import numpy as np
    return float(np.sum(np.abs(x[int(a * sr):int(b * sr)])))


def test_tick_audio_only_sounds_inside_the_countdown():
    x = tts.tick_audio(CH, 8.0, 0.5)
    assert x.size == 8 * tts.TRACK_SR
    assert _energy(x, 0, 3.0) == 0 and _energy(x, 6.2, 8.0) == 0  # antes e depois: silêncio
    # 3 s de contagem = 6 estalos (tic, tac, tic, tac, tic, tac), um a cada meio segundo
    for k in range(6):
        t = 3.0 + k * 0.5
        assert _energy(x, t, t + 0.09) > 0, f"faltou o estalo {k} em {t}s"
        assert _energy(x, t + 0.15, t + 0.5) == 0  # entre um estalo e outro, silêncio


def test_tick_audio_volume_scales_the_peak_and_zero_is_silent():
    import numpy as np
    assert np.max(np.abs(tts.tick_audio(CH, 8.0, 1.0))) == pytest.approx(0.8, abs=0.01)
    assert np.max(np.abs(tts.tick_audio(CH, 8.0, 0.25))) == pytest.approx(0.2, abs=0.01)
    assert not np.any(tts.tick_audio(CH, 8.0, 0.0))


def test_tic_and_tac_have_different_pitch():
    import numpy as np
    x = tts.tick_audio(CH, 8.0, 1.0)
    sr = tts.TRACK_SR

    def peak_hz(t):
        seg = x[int(t * sr):int((t + 0.09) * sr)]
        spec = np.abs(np.fft.rfft(seg * np.hanning(seg.size)))
        return float(np.fft.rfftfreq(seg.size, 1 / sr)[int(np.argmax(spec))])
    tic, tac = peak_hz(3.0), peak_hz(3.5)
    assert 2000 < tic < 2600 and 1200 < tac < 1800 and tic > tac * 1.3  # tic agudo, tac grave


@needs_ffmpeg
def test_mix_ticks_keeps_the_voice_and_the_duration(tmp_path):
    import numpy as np
    import wave
    voice = tmp_path / "voice.wav"
    spokens = tts.place([make_wav(tmp_path / "a.wav", dur=3.0), make_wav(tmp_path / "b.wav", dur=2.0)], [3.35, 0.0])
    tts.build_track(spokens, voice)
    out = tts.mix_ticks(voice, CH, 0.5, tmp_path / "mixed.wav")
    assert out != voice and probe_duration(out) == pytest.approx(probe_duration(voice), abs=0.02)

    def pcm(p):
        with wave.open(str(p), "rb") as wf:
            return np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2").astype(float)
    a, b = pcm(voice), pcm(out)
    sr = tts.TRACK_SR
    assert np.array_equal(a[: 3 * sr], b[: 3 * sr])  # a fala antes da contagem não muda
    assert not np.array_equal(a[3 * sr:int(4.5 * sr)], b[3 * sr:int(4.5 * sr)])  # a contagem ganhou o tic-tac
    assert np.max(np.abs(b)) <= 32767


def test_mix_ticks_is_a_no_op_without_countdown_or_volume(tmp_path):
    track = tmp_path / "voice.wav"
    track.write_bytes(b"x")
    assert tts.mix_ticks(track, CH, 0.0, tmp_path / "o.wav") == track
    assert tts.mix_ticks(track, [], 0.5, tmp_path / "o.wav") == track
    assert tts.mix_ticks(track, [{**CH[0], "countdown": 0.0}], 0.5, tmp_path / "o.wav") == track
