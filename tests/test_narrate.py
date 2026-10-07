import json
import random
from pathlib import Path

import pytest

from conftest import make_video, needs_ffmpeg
from darkcnn import narrate, script as S
from darkcnn.config import Config, VoiceCfg
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
            t += span + cfg.pause_s + (cfg.countdown_s if ln.kind == "escolha" else 0.0)
        return words
    return transcribe


def narrate_cfg(tmp_path, **kw):
    base = dict(
        voice="gumball", voices={"gumball": VoiceCfg(ref_audio=tmp_path / "ref.wav", prompt_text="oi")},
        gameplay_dir=gameplay_dir(tmp_path), preset="ultrafast", seed=7, target_s=20,
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
    # duração = falas + pausa + contagem (a última linha não tem pausa depois)
    esperado = sum(max(0.3, len(l.text.split()) * DUR_PER_WORD) for l in lines) + 0.3 + (0.3 + 3.0)
    assert info["duration"] == pytest.approx(esperado, abs=0.5)
    assert backend.calls == 1 and len(tts_backend.said) == 3

    data = json.loads((out / "scripts.json").read_text(encoding="utf-8"))[0]
    assert data["title"] == "Um título de teste" and data["voice"] == "gumball"
    assert [l["kind"] for l in data["lines"]] == ["fala", "escolha", "fala"]
    assert data["match"] == 1.0  # o Whisper falso confirma o roteiro inteiro

    ass = (cfg.workspace_dir / "narrate" / narrate.run_id(cfg) / "01" / "render" / "subs.ass").read_text("utf-8")
    styles = [l.split(",")[3] for l in ass.splitlines() if l.startswith("Dialogue")]
    assert "Default" in styles and "OptA" in styles and styles.count("Timer") == 3
    assert "SEM DOR" in ass and "SEM MEDO" in ass

    text = review.read_text(encoding="utf-8")
    assert "voz clonada de personagem" in text and "Um título de teste" in text
    assert "[Sem dor × Sem medo]" in text and "game" in text

    # 2ª execução: nada de Gemini, nada de TTS, nada de render
    mtime = mp4s[0].stat().st_mtime_ns
    narrate.run_narrate(cfg, client_factory=factory, backend=tts_backend,
                        transcriber=fake_transcriber(lines, cfg))
    assert backend.calls == 1 and len(tts_backend.said) == 3
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


def test_narrate_requires_gameplay_dir_and_known_voice(tmp_path):
    with pytest.raises(ValueError, match="--gameplay-dir"):
        narrate.run_narrate(Config(workspace_dir=tmp_path / "ws"))
    cfg = narrate_cfg(tmp_path, voice="nao_existe")
    from darkcnn.tts import TTSError
    with pytest.raises(TTSError, match="não está no config.yaml"):
        narrate.run_narrate(cfg, client_factory=lambda: None, backend=FakeTTS())
