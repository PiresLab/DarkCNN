import functools
import http.server
import json
import threading

import pytest

from conftest import needs_ffmpeg
from darkcnn import tts
from darkcnn.config import Config, VoiceCfg
from darkcnn.media import probe_duration, run


def make_wav(path, dur=0.5, freq=440):
    path.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", f"sine=frequency={freq}:duration={dur}", "-ac", "1", "-ar", "32000", str(path)])
    return path


def cfg_with_voice(tmp_path, **kw):
    base = dict(voice="gumball", voices={"gumball": VoiceCfg(ref_audio=tmp_path / "ref.wav",
                                                             prompt_text="olá", lang="pt")})
    base.update(kw)
    return Config(**base)


def test_get_voice_errors_point_to_the_config(tmp_path):
    with pytest.raises(tts.TTSError, match="não está no config.yaml"):
        tts.get_voice(Config(voice="mordecai"))
    cfg = cfg_with_voice(tmp_path)
    assert tts.get_voice(cfg).prompt_text == "olá"
    with pytest.raises(tts.TTSError, match="gumball, mordecai|mordecai"):
        tts.get_voice(cfg.model_copy(update={"voice": "nao_existe",
                                             "voices": {**cfg.voices, "mordecai": cfg.voices["gumball"]}}))


def test_line_key_depends_on_text_and_voice_not_on_position(tmp_path):
    cfg = cfg_with_voice(tmp_path)
    v = tts.get_voice(cfg)
    k = tts.line_key("olá mundo", "gumball", v, cfg)
    assert k == tts.line_key("olá mundo", "gumball", v, cfg)
    assert k != tts.line_key("outro texto", "gumball", v, cfg)
    assert k != tts.line_key("olá mundo", "mordecai", v, cfg)
    assert k != tts.line_key("olá mundo", "gumball", v.model_copy(update={"speed": 1.2}), cfg)


class FakeBackend:
    def __init__(self, dur_per_word=0.4):
        self.said: list[str] = []
        self.dur_per_word = dur_per_word

    def say(self, text, voice, dest):
        self.said.append(text)
        make_wav(dest, dur=max(0.3, len(text.split()) * self.dur_per_word))


@needs_ffmpeg
def test_synthesize_caches_by_content(tmp_path):
    cfg = cfg_with_voice(tmp_path)
    b = FakeBackend()
    wavs, made = tts.synthesize(["uma frase", "outra frase"], cfg, b, tmp_path / "voice")
    assert made == 2 and len(wavs) == 2 and all(w.exists() for w in wavs)

    wavs2, made2 = tts.synthesize(["uma frase", "MUDOU aqui"], cfg, b, tmp_path / "voice")
    assert made2 == 1 and wavs2[0] == wavs[0]  # só a linha alterada foi sintetizada
    assert b.said == ["uma frase", "outra frase", "MUDOU aqui"]
    # a mesma frase em duas posições usa o mesmo arquivo
    rep, made3 = tts.synthesize(["uma frase", "uma frase"], cfg, b, tmp_path / "voice")
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


# ---------------------------------------------------------------- servidor que imita o GPT-SoVITS
class FakeServer:
    """Mínimo da API do GPT-SoVITS: POST /tts devolve WAV; modos de erro para os testes."""

    def __init__(self, wav: bytes, mode="ok"):
        self.wav, self.mode, self.payloads, self.weights = wav, mode, [], []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.weights.append(self.path)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"message":"ok"}')

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                outer.payloads.append(json.loads(body))
                if outer.mode == "erro":
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write('{"message":"ref_audio_path não existe"}'.encode())
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"NAO E WAV" if outer.mode == "lixo" else outer.wav)

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.srv.server_port}"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.srv.shutdown()


@needs_ffmpeg
def test_gptsovits_backend_sends_the_right_payload_and_saves_the_wav(tmp_path, monkeypatch):
    for v in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy", "NO_PROXY"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    wav = make_wav(tmp_path / "ref.wav", dur=0.6).read_bytes()
    with FakeServer(wav) as srv:
        cfg = cfg_with_voice(tmp_path, tts_url=srv.url, tts_split_method="cut3")
        b = tts.GPTSoVITSBackend(cfg.tts_url, 30, cfg.tts_split_method)
        dest = tmp_path / "out" / "fala.wav"
        b.say("bom dia", tts.get_voice(cfg), dest)
        assert dest.exists() and probe_duration(dest) == pytest.approx(0.6, abs=0.05)
        p = srv.payloads[0]
        assert p["text"] == "bom dia" and p["text_lang"] == "pt" and p["prompt_lang"] == "pt"
        assert p["prompt_text"] == "olá" and p["ref_audio_path"].endswith("ref.wav")
        assert p["media_type"] == "wav" and p["streaming_mode"] is False and p["text_split_method"] == "cut3"
        assert p["speed_factor"] == 1.0


@needs_ffmpeg
def test_gptsovits_backend_loads_custom_weights_once(tmp_path, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    wav = make_wav(tmp_path / "ref.wav", dur=0.3).read_bytes()
    with FakeServer(wav) as srv:
        voice = VoiceCfg(ref_audio=tmp_path / "ref.wav", gpt_weights="g.ckpt", sovits_weights="s.pth")
        b = tts.GPTSoVITSBackend(srv.url, 30)
        b.say("um", voice, tmp_path / "a.wav")
        b.say("dois", voice, tmp_path / "b.wav")
        assert len(srv.weights) == 2 and "set_gpt_weights" in srv.weights[0]  # carregado uma vez só
        assert "set_sovits_weights" in srv.weights[1]


def test_backend_errors_are_readable(tmp_path, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    voice = VoiceCfg(ref_audio=tmp_path / "ref.wav")
    with FakeServer(b"", mode="erro") as srv:
        with pytest.raises(tts.TTSError, match="recusou a síntese.*ref_audio_path"):
            tts.GPTSoVITSBackend(srv.url, 10).say("oi", voice, tmp_path / "x.wav")
    with FakeServer(b"", mode="lixo") as srv:
        with pytest.raises(tts.TTSError, match="não mandou um WAV"):
            tts.GPTSoVITSBackend(srv.url, 10).say("oi", voice, tmp_path / "x.wav")
    # servidor fora do ar: a mensagem ensina como subir
    with pytest.raises(tts.TTSError, match="Ele está rodando.*api_v2.py"):
        tts.GPTSoVITSBackend("http://127.0.0.1:1", 2).say("oi", voice, tmp_path / "x.wav")
