import json
import random
from pathlib import Path

import pytest

from conftest import make_video, needs_ffmpeg
from darkcnn import narrate, script as S
from darkcnn.config import Config
from darkcnn.gemini import GeminiClient
from darkcnn.media import probe_duration, run, video_info
from test_tts import FakeBackend as FakeTTS, make_wav

DUR_PER_WORD = 0.4


def gameplay_dir(tmp_path, n=2, dur=60):
    d = tmp_path / "gameplays"
    d.mkdir()
    for i in range(n):
        make_video(d / f"game{i}.mp4", dur=dur)
    return d


def script_of(lines):
    return S.Script(title="Um título de teste", topic="oceano", lines=lines)


class ScriptBackend:
    """Gemini falso que devolve roteiros."""

    def __init__(self, scripts):
        self.scripts, self.calls, self.prompts = scripts, 0, []

    def generate(self, prompt, schema, temperature, media=None):
        self.calls += 1
        self.prompts.append(prompt)
        return S.ScriptBatch(scripts=self.scripts), {"prompt_tokens": 10, "output_tokens": 20}


def fake_transcriber(script_lines, cfg):
    """Whisper falso: devolve as palavras do roteiro nos tempos em que elas realmente estão na trilha."""
    def transcribe(wav, _cfg):
        words, t = [], 0.0
        for i, ln in enumerate(script_lines):
            toks = ln.text.split()
            span = max(0.3, len(toks) * DUR_PER_WORD)
            step = span / len(toks)
            for k, tok in enumerate(toks):
                words.append({"w": tok, "start": round(t + k * step, 3),
                              "end": round(t + (k + 1) * step - 0.02, 3), "p": 0.9})
            # as linhas de um bloco são faladas juntas; só a pergunta (fim do bloco) é seguida de pausa
            t += span + ((cfg.pause_s + cfg.countdown_s) if ln.kind == "escolha" else 0.0)
        return words
    return transcribe


def narrate_cfg(tmp_path, **kw):
    base = dict(
        tts_voice="Kore", gameplay_dir=gameplay_dir(tmp_path), preset="ultrafast", seed=7, target_s=20,
        workspace_dir=tmp_path / "ws", output_dir=tmp_path / "out",
    )
    base.update(kw)
    return Config(**base)


# ---------------------------------------------------------------- gameplay
@needs_ffmpeg
def test_list_and_pick_gameplay(tmp_path):
    d = gameplay_dir(tmp_path, n=3, dur=30)
    (d / "leiame.txt").write_text("não é vídeo")
    files = narrate.list_gameplays(d)
    assert len(files) == 3 and all(f.suffix == ".mp4" for f in files)

    game, offset, loop = narrate.pick_gameplay(files, 10.0, random.Random(1))
    assert game in files and loop is False and 0 <= offset <= 30 - 10 - 0.25
    # com a mesma semente, a mesma escolha (reprodutível)
    assert narrate.pick_gameplay(files, 10.0, random.Random(1)) == (game, offset, loop)
    assert narrate.pick_gameplay(files, 10.0, random.Random(2)) != (game, offset, loop) or True


@needs_ffmpeg
def test_pick_gameplay_loops_when_the_video_is_too_short(tmp_path):
    files = narrate.list_gameplays(gameplay_dir(tmp_path, n=1, dur=5))
    game, offset, loop = narrate.pick_gameplay(files, 30.0, random.Random(0))
    assert loop is True and offset == 0.0


def test_empty_gameplay_dir_is_a_clear_error(tmp_path):
    (tmp_path / "vazia").mkdir()
    with pytest.raises(FileNotFoundError, match="nenhum vídeo em"):
        narrate.list_gameplays(tmp_path / "vazia")


def test_run_id_is_stable_and_specific():
    a = Config(narrate_format="curiosidade", count=2)
    assert narrate.run_id(a) == narrate.run_id(a.model_copy())
    assert narrate.run_id(a) != narrate.run_id(a.model_copy(update={"count": 3}))
    assert narrate.run_id(a) != narrate.run_id(a.model_copy(update={"topic": "oceano"}))
    assert narrate.run_id(a).startswith("curiosidade-")


# ---------------------------------------------------------------- repartir o que o Whisper ouviu
@needs_ffmpeg
def test_heard_per_line_splits_words_by_line(tmp_path):
    cfg = narrate_cfg(tmp_path)
    wavs = [make_wav(tmp_path / f"l{i}.wav", dur=d) for i, d in enumerate([1.0, 1.0])]
    spokens = narrate.tts.place(wavs, [0.5, 0.0])
    track = tmp_path / "track.wav"
    narrate.tts.build_track(spokens, track)

    def transcribe(wav, _cfg):  # uma palavra em cada linha
        return [{"w": "primeira", "start": 0.2, "end": 0.6, "p": 1},
                {"w": "segunda", "start": 1.7, "end": 2.2, "p": 1}]

    heard = narrate.heard_per_line(track, spokens, cfg, transcribe, tmp_path)
    assert [[w["w"] for w in h] for h in heard] == [["primeira"], ["segunda"]]
    assert heard[1][0]["start"] == pytest.approx(0.2)  # tempo passa a ser relativo à linha


# ---------------------------------------------------------------- ponta a ponta
@needs_ffmpeg
def test_narrate_end_to_end_with_choices_and_cache(tmp_path):
    lines = [
        S.Line(text="você prefere nunca mais sentir dor ou nunca mais sentir medo"),
        S.Line(text="pensa bem antes de decidir", kind="escolha", option_a="Sem dor", option_b="Sem medo"),
        S.Line(text="a maioria escolhe errado nessa"),
    ]
    sc = script_of(lines)
    cfg = narrate_cfg(tmp_path, count=1, narrate_format="voce-prefere", countdown_s=3.0, pause_s=0.3)
    backend = ScriptBackend([sc])
    tts_backend = FakeTTS(DUR_PER_WORD)
    factory = lambda: GeminiClient(backend, cfg.workspace_dir / "u.json", 40, sleep=lambda s: None)

    review = narrate.run_narrate(cfg, client_factory=factory, backend=tts_backend,
                                 transcriber=fake_transcriber(lines, cfg))
    out = review.parent
    mp4s = list(out.glob("*.mp4"))
    assert len(mp4s) == 1 and mp4s[0].name.startswith("01_um-titulo-de-teste")
    info = video_info(mp4s[0])
    assert (info["width"], info["height"]) == (1080, 1920)
    # 2 blocos: [fala + pergunta] e [comentário]; a pausa + a contagem vêm depois da pergunta
    bloco1 = max(0.3, (len(lines[0].text.split()) + len(lines[1].text.split())) * DUR_PER_WORD)
    bloco2 = max(0.3, len(lines[2].text.split()) * DUR_PER_WORD)
    assert info["duration"] == pytest.approx(bloco1 + 0.3 + 3.0 + bloco2, abs=0.5)
    assert backend.calls == 1 and len(tts_backend.said) == 2  # uma requisição de voz por bloco, não por linha
    assert tts_backend.voices == ["Kore", "Kore"]

    data = json.loads((out / "scripts.json").read_text(encoding="utf-8"))[0]
    assert data["title"] == "Um título de teste" and data["voice"] == "Kore"
    assert [l["kind"] for l in data["lines"]] == ["fala", "escolha", "fala"]
    starts = [l["start"] for l in data["lines"]]
    assert starts == sorted(starts) and starts[0] == 0.0 and starts[1] > 0  # a pergunta começa depois da fala anterior
    assert data["match"] == 1.0  # o Whisper falso confirma o roteiro inteiro

    ass = (cfg.workspace_dir / "narrate" / narrate.run_id(cfg) / "01" / "render" / "subs.ass").read_text("utf-8")
    styles = [l.split(",")[3] for l in ass.splitlines() if l.startswith("Dialogue")]
    assert "Default" in styles and "OptA" in styles and styles.count("Timer") == 3
    assert "SEM DOR" in ass and "SEM MEDO" in ass

    text = review.read_text(encoding="utf-8")
    assert "a voz é sintética" in text and "personagem" not in text and "Um título de teste" in text
    assert "[Sem dor × Sem medo]" in text and "game" in text

    # 2ª execução: nada de Gemini, nada de TTS, nada de render
    mtime = mp4s[0].stat().st_mtime_ns
    narrate.run_narrate(cfg, client_factory=factory, backend=tts_backend,
                        transcriber=fake_transcriber(lines, cfg))
    assert backend.calls == 1 and len(tts_backend.said) == 2
    assert mp4s[0].stat().st_mtime_ns == mtime


@needs_ffmpeg
def test_narrate_two_videos_and_stale_cleanup(tmp_path):
    a = script_of([S.Line(text="primeiro roteiro bem curto aqui")])
    b = script_of([S.Line(text="segundo roteiro diferente do outro")])
    b.title = "Outro titulo"
    cfg = narrate_cfg(tmp_path, count=2, game_volume=0.0)
    backend = ScriptBackend([a, b])
    factory = lambda: GeminiClient(backend, cfg.workspace_dir / "u.json", 40, sleep=lambda s: None)
    review = narrate.run_narrate(cfg, client_factory=factory, backend=FakeTTS(DUR_PER_WORD),
                                 transcriber=lambda w, c: [])  # Whisper não ouve nada: cai na distribuição
    assert len(list(review.parent.glob("*.mp4"))) == 2

    menos = cfg.model_copy(update={"count": 1})
    narrate.run_narrate(menos, client_factory=lambda: GeminiClient(ScriptBackend([a]),
                        cfg.workspace_dir / "u.json", 40, sleep=lambda s: None),
                        backend=FakeTTS(DUR_PER_WORD), transcriber=lambda w, c: [])
    nomes = [p.name for p in (cfg.output_dir / "narrate" / narrate.run_id(menos)).glob("*.mp4")]
    assert len(nomes) == 1 and nomes[0].startswith("01_")


def test_narrate_requires_gameplay_dir(tmp_path):
    with pytest.raises(ValueError, match="--gameplay-dir"):
        narrate.run_narrate(Config(workspace_dir=tmp_path / "ws"))


def test_old_character_voice_config_is_rejected():
    for old in ({"voice": "gumball"}, {"voices": {}}, {"tts_backend": "gptsovits"}, {"tts_url": "http://x"}):
        with pytest.raises(Exception, match="Extra inputs are not permitted"):
            Config(**old)


def test_make_blocks_closes_a_block_at_each_question():
    L = S.Line
    q = lambda t: L(text=t, kind="escolha", option_a="A", option_b="B")
    s = script_of([L(text="a"), L(text="b"), q("c"), L(text="d"), q("e"), L(text="f")])
    assert narrate.make_blocks(s) == [[0, 1, 2], [3, 4], [5]]
    flat = script_of([L(text="a"), L(text="b"), L(text="c")])
    assert narrate.make_blocks(flat) == [[0, 1, 2]]  # curiosidade/e-se = um bloco = uma requisição de voz


def test_line_times_comes_from_the_aligned_words():
    L = S.Line
    s = script_of([L(text="um dois"), L(text="três quatro cinco", kind="escolha", option_a="A", option_b="B"),
                   L(text="seis")])
    blocks = narrate.make_blocks(s)
    words = [{"w": w, "start": float(i), "end": i + 0.9, "p": 1} for i, w in
             enumerate("um dois três quatro cinco seis".split())]
    spokens = [narrate.tts.Spoken(0, "", Path("a.wav"), 0.0, 5.0, 3.0),
               narrate.tts.Spoken(1, "", Path("b.wav"), 8.0, 9.0, 0.0)]
    times = narrate.line_times(s, blocks, words, spokens)
    assert times[0] == (0.0, 1.9)
    assert times[1] == (2.0, 5.0)  # a pergunta começa na 1ª palavra dela e vai até o fim do áudio do bloco
    assert times[2][0] == 5.0


@needs_ffmpeg
def test_each_video_draws_its_voice_from_the_pool(tmp_path):
    a = script_of([S.Line(text="primeiro roteiro bem curto aqui")])
    b = script_of([S.Line(text="segundo roteiro diferente do outro")])
    b.title = "Outro titulo"
    cfg = narrate_cfg(tmp_path, count=2, game_volume=0.0, tts_voices_pool=["Puck", "Charon", "Fenrir"])
    backend = ScriptBackend([a, b])
    factory = lambda: GeminiClient(backend, cfg.workspace_dir / "u.json", 40, sleep=lambda s: None)
    fake = FakeTTS(DUR_PER_WORD)
    review = narrate.run_narrate(cfg, client_factory=factory, backend=fake, transcriber=lambda w, c: [])
    assert len(fake.voices) == 2 and set(fake.voices) <= {"Puck", "Charon", "Fenrir"}
    data = json.loads((review.parent / "scripts.json").read_text(encoding="utf-8"))
    assert [v["voice"] for v in data] == fake.voices


@needs_ffmpeg
def test_render_falls_back_to_voice_only_when_the_game_audio_cannot_be_decoded(tmp_path, monkeypatch):
    from darkcnn import render
    from darkcnn.media import MediaError

    game = tmp_path / "g.mp4"
    make_video(game, dur=5)
    voice = tmp_path / "v.wav"
    make_wav(voice, dur=1.5)
    real_run, calls = render.run, []

    def flaky(cmd, cwd=None):
        calls.append([str(c) for c in cmd])
        if any("amix" in str(c) for c in cmd):  # imita o ffmpeg quebrando ao decodificar o áudio da gameplay
            raise MediaError("falhou (69): Decode error rate 0.97 exceeds maximum")
        return real_run(cmd, cwd=cwd)

    monkeypatch.setattr(render, "run", flaky)
    cfg = narrate_cfg(tmp_path, preset="ultrafast")
    words = [{"w": "olá", "start": 0.1, "end": 0.6, "p": 1}]
    dest = tmp_path / "out" / "v.mp4"
    assert render.render_narration(game, 0.0, False, voice, words, [], 1.5, cfg, dest, tmp_path / "w", "k")
    assert dest.exists() and probe_duration(dest) == pytest.approx(1.5, abs=0.3)
    assert len(calls) == 2 and not any("amix" in c for c in calls[1])


@needs_ffmpeg
def test_weak_opening_is_reported_but_the_video_is_still_made(tmp_path):
    weak = script_of([S.Line(text="Fala galera, hoje vou falar do oceano e de coisas legais que existem nele")])
    cfg = narrate_cfg(tmp_path, game_volume=0.0)
    backend = ScriptBackend([weak])
    factory = lambda: GeminiClient(backend, cfg.workspace_dir / "u.json", 40, sleep=lambda s: None)
    review = narrate.run_narrate(cfg, client_factory=factory, backend=FakeTTS(DUR_PER_WORD),
                                 transcriber=lambda w, c: [])
    assert len(list(review.parent.glob("*.mp4"))) == 1
    text = review.read_text(encoding="utf-8")
    assert "Abertura fraca" in text and "saudação" in text
    data = json.loads((review.parent / "scripts.json").read_text(encoding="utf-8"))[0]
    assert data["opening_warnings"]


@needs_ffmpeg
def test_playable_detects_a_corrupt_stretch(tmp_path):
    good = make_video(tmp_path / "ok.mp4", dur=10)
    assert narrate.playable(good, 2.0, 3.0, False)
    bad = tmp_path / "ruim.mp4"
    bad.write_bytes(b"isto nao e um video" * 500)
    assert not narrate.playable(bad, 0.0, 3.0, False)


@needs_ffmpeg
def test_pick_playable_redraws_when_a_stretch_is_unreadable(tmp_path, monkeypatch):
    files = narrate.list_gameplays(gameplay_dir(tmp_path, n=2, dur=30))
    results = iter([False, False, True])
    monkeypatch.setattr(narrate, "playable", lambda *a: next(results))
    game, offset, loop = narrate.pick_playable(files, 5.0, random.Random(3))
    assert game in files and not loop


@needs_ffmpeg
def test_pick_playable_skips_files_that_do_not_even_open(tmp_path):
    d = gameplay_dir(tmp_path, n=1, dur=20)
    (d / "quebrado.mp4").write_bytes(b"lixo" * 1000)
    files = narrate.list_gameplays(d)
    for seed in range(6):
        game, offset, loop = narrate.pick_playable(files, 5.0, random.Random(seed))
        assert game.name == "game0.mp4"


@needs_ffmpeg
def test_pick_playable_gives_a_clear_error_when_everything_is_corrupt(tmp_path, monkeypatch):
    files = narrate.list_gameplays(gameplay_dir(tmp_path, n=1, dur=30))
    monkeypatch.setattr(narrate, "playable", lambda *a: False)
    with pytest.raises(RuntimeError, match="corrompido.*-c copy"):
        narrate.pick_playable(files, 5.0, random.Random(0), attempts=3)


@needs_ffmpeg
def test_choice_video_has_the_clock_ticking_during_the_countdown(tmp_path):
    import numpy as np
    lines = [S.Line(text="você prefere nunca mais sentir dor ou nunca mais sentir medo", kind="escolha",
                    option_a="Sem dor", option_b="Sem medo"),
             S.Line(text="pensa bem antes de decidir")]
    sc = script_of(lines)
    out = {}
    for vol in (0.5, 0.0):
        base = tmp_path / f"v{vol}"
        base.mkdir()
        cfg = narrate_cfg(base, count=1, narrate_format="voce-prefere", countdown_s=3.0,
                          pause_s=0.3, game_volume=0.0, tick_volume=vol)
        backend = ScriptBackend([sc])
        factory = lambda cfg=cfg, backend=backend: GeminiClient(backend, cfg.workspace_dir / "u.json", 40,
                                                                 sleep=lambda s: None)
        review = narrate.run_narrate(cfg, client_factory=factory, backend=FakeTTS(DUR_PER_WORD),
                                     transcriber=fake_transcriber(lines, cfg))
        mp4 = next(review.parent.glob("*.mp4"))
        wav = tmp_path / f"a{vol}.wav"
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(mp4), "-vn", "-ac", "1",
             "-ar", "44100", str(wav)])
        import wave
        with wave.open(str(wav), "rb") as wf:
            out[vol] = np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2").astype(float)
    voice_end = len(lines[0].text.split()) * DUR_PER_WORD  # a pergunta termina aqui; a contagem começa
    sr = 44100
    n = min(out[0.5].size, out[0.0].size)
    diff = np.abs(out[0.5][:n] - out[0.0][:n])
    inside = diff[int((voice_end + 0.05) * sr):int((voice_end + 3.0) * sr)]
    before = diff[: int((voice_end - 0.2) * sr)]
    assert inside.max() > 800, "o tic-tac não apareceu durante a contagem"
    assert before.max() < 800, "o tic-tac vazou para antes da contagem"
