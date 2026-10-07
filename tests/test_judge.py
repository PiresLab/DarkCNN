import pytest

from conftest import FakeBackend, make_cut_video, needs_ffmpeg
from darkcnn import judge as J
from darkcnn.config import Config
from darkcnn.gemini import BudgetExceeded, GeminiClient, GeminiError, TransientError
from darkcnn.media import probe_duration


def clip(start, end, score, title, transcript=None):
    return {"start": start, "end": end, "score": score, "title": title, "reason": f"razão de {title}",
            **({"transcript": transcript} if transcript else {})}


def item(label, score, keep=True, strength="forte", weakness="fraco"):
    return J.JudgeItem(label=label, final_score=score, keep=keep, strength=strength, weakness=weakness)


class JudgeBackend(FakeBackend):
    """Devolve um veredito fixo e confere que recebeu o vídeo com os candidatos."""

    def __init__(self, ranking, script=None):
        super().__init__(script=script)
        self.ranking = ranking
        self.reel_info = None

    def generate(self, prompt, schema, temperature, media=None):
        assert schema is J.JudgeVerdict
        self.calls += 1
        self.prompts.append(prompt)
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, Exception):
                raise item
        if media is not None:
            self.reel_info = (media.exists(), probe_duration(media))
        return J.JudgeVerdict(ranking=self.ranking), {"prompt_tokens": 1, "output_tokens": 1}


def client(tmp_path, backend):
    return GeminiClient(backend, tmp_path / "u.json", 40, retries=0, sleep=lambda s: None)


def prompt_for(clips, theme=None):
    spans = [(f"A{i}", i * 10.0, i * 10.0 + 9) for i, _ in enumerate(clips, 1)]
    return J.build_prompt(clips, spans, Config(), theme)


def test_prompt_lists_every_candidate_with_context_and_theme():
    clips = [clip(10, 20, 8, "Primeiro", transcript="olha {isso} aqui"), clip(30, 40, 6, "Segundo")]
    p = prompt_for(clips)
    assert "SEQUÊNCIA de 2" in p and "A1 —" in p and "A2 —" in p and "olha {isso} aqui" in p and "TEMA" not in p
    t = prompt_for(clips, theme="top 5 finalizações")
    assert 'TEMA DO COMPILADO: "top 5 finalizações"' in t and "foge do tema" in t
    assert "{candidates}" not in p and "{theme_block}" not in p


@needs_ffmpeg
def test_reel_has_every_candidate_in_order(tmp_path):
    video = make_cut_video(tmp_path / "v.mp4")
    clips = [clip(5, 15, 8, "a"), clip(22, 30, 7, "b"), clip(41, 49, 6, "c")]  # 10 + 8 + 8 s
    spans = J.make_reel(video, clips, tmp_path / "reel" / "reel.mp4", Config(proxy_height=144), tmp_path / "parts")
    assert [s[0] for s in spans] == ["A1", "A2", "A3"]
    assert spans[0][1] == 0 and spans[-1][2] == pytest.approx(26, abs=0.8)
    assert probe_duration(tmp_path / "reel" / "reel.mp4") == pytest.approx(spans[-1][2], abs=0.8)
    assert all(a[2] == pytest.approx(b[1], abs=1e-6) for a, b in zip(spans, spans[1:]))  # contíguos


@needs_ffmpeg
def test_judge_reorders_drops_and_caches(tmp_path):
    video = make_cut_video(tmp_path / "v.mp4")
    cfg = Config(proxy_height=144, workspace_dir=tmp_path / "ws")
    ws = tmp_path / "ws" / "vid"
    clips = [clip(0, 9, 9, "A"), clip(12, 21, 8, "B"), clip(25, 34, 7, "C"), clip(41, 50, 6, "D")]
    backend = JudgeBackend([item("A3", 95), item("A1", 80), item("A4", 40, keep=False, weakness="começa devagar"),
                            item("A2", 60), item("A9", 99)])  # A9 não existe: ignorado
    kept, dropped, st = J.run_judge(video, "vid", ws, clips, cfg, lambda: client(tmp_path, backend))
    assert [c["title"] for c in kept] == ["C", "A", "B"]  # ordem do juiz, não da 1ª passada
    assert [c["judge_score"] for c in kept] == [95, 80, 60] and st["reordered"] is True
    assert dropped[0]["title"] == "D" and "juiz: começa devagar" in dropped[0]["reason_rejected"]
    assert backend.reel_info[0] and backend.reel_info[1] == pytest.approx(36, abs=1.2)  # 4 x 9 s
    assert not (ws / "judge_tmp").exists()  # reel apagado

    again = [clip(0, 9, 9, "A"), clip(12, 21, 8, "B"), clip(25, 34, 7, "C"), clip(41, 50, 6, "D")]
    kept2, _, _ = J.run_judge(video, "vid", ws, again, cfg, lambda: client(tmp_path, backend))
    assert backend.calls == 1 and [c["title"] for c in kept2] == ["C", "A", "B"]  # cache: sem nova chamada
    J.run_judge(video, "vid", ws, [clip(0, 9, 9, "A"), clip(12, 21, 8, "B"), clip(25, 34, 7, "C"), clip(41, 50, 6, "D")],
                cfg, lambda: client(tmp_path, backend), force=True)
    assert backend.calls == 2
    J.run_judge(video, "vid", ws, [clip(0, 9, 9, "A"), clip(12, 21, 8, "B")], cfg, lambda: client(tmp_path, backend))
    assert backend.calls == 3  # candidatos diferentes: nova chave


@needs_ffmpeg
def test_candidates_the_judge_forgot_stay_at_the_end_not_dropped(tmp_path):
    video = make_cut_video(tmp_path / "v.mp4")
    cfg = Config(proxy_height=144, workspace_dir=tmp_path / "ws")
    clips = [clip(0, 9, 9, "A"), clip(12, 21, 8, "B"), clip(25, 34, 7, "C")]
    backend = JudgeBackend([item("A2", 90), item("A3", 70)])  # esqueceu A1
    kept, dropped, _ = J.run_judge(video, "vid", tmp_path / "ws" / "vid", clips, cfg, lambda: client(tmp_path, backend))
    assert [c["title"] for c in kept] == ["B", "C", "A"] and not dropped


@needs_ffmpeg
@pytest.mark.parametrize("error", [TransientError("503 UNAVAILABLE"), GeminiError("400 INVALID"), BudgetExceeded("acabou")])
def test_judge_failure_keeps_first_pass_order(tmp_path, error):
    video = make_cut_video(tmp_path / "v.mp4")
    cfg = Config(proxy_height=144, workspace_dir=tmp_path / "ws")
    clips = [clip(0, 9, 9, "A"), clip(12, 21, 8, "B")]
    backend = JudgeBackend([], script=[error])
    kept, dropped, st = J.run_judge(video, "vid", tmp_path / "ws" / "vid", clips, cfg, lambda: client(tmp_path, backend))
    assert kept == clips and dropped == [] and st["judged"] is False
    assert "judge_score" not in kept[0]


@needs_ffmpeg
def test_single_candidate_and_overflow(tmp_path):
    video = make_cut_video(tmp_path / "v.mp4")
    cfg = Config(proxy_height=144, judge_max_candidates=2, workspace_dir=tmp_path / "ws")
    backend = JudgeBackend([item("A2", 90), item("A1", 80)])
    one = [clip(0, 9, 9, "A")]
    assert J.run_judge(video, "vid", tmp_path / "ws" / "vid", one, cfg, lambda: client(tmp_path, backend)) == (one, [], {"judged": False})
    assert backend.calls == 0  # com 1 candidato não há o que comparar
    clips = [clip(0, 9, 9, "A"), clip(12, 21, 8, "B"), clip(25, 34, 7, "C")]
    kept, dropped, _ = J.run_judge(video, "vid", tmp_path / "ws" / "vid", clips, cfg, lambda: client(tmp_path, backend))
    assert [c["title"] for c in kept] == ["B", "A"]
    assert dropped[0]["title"] == "C" and "fora do limite" in dropped[0]["reason_rejected"]
