"""Fixtures: vídeo sintético, palavras/frases falsas e um backend Gemini falso (sem rede, sem Whisper)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from darkcnn.analyze import Candidate, Selection
from darkcnn.config import Config


def make_video(path: Path, dur: float = 60, size: str = "640x360") -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=25:duration={dur}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True,
    )
    return path


def make_words(n_sentences: int = 12, sent_s: float = 5.0, words_per: int = 5,
               speech_frac: float = 1.0) -> list[dict]:
    """n frases a cada `sent_s` s; a última palavra de cada frase termina em ponto.
    speech_frac < 1 deixa silêncio entre as frases (1.0 = quase coladas, ~0,1 s)."""
    words = []
    step = sent_s * speech_frac / words_per
    for s in range(n_sentences):
        for k in range(words_per):
            t = s * sent_s + k * step
            w = f"palavra{s}x{k}" + ("." if k == words_per - 1 else "")
            words.append({"w": w, "start": round(t, 3), "end": round(t + step * 0.9, 3), "p": 0.95})
    return words


def cand(start_id, end_id, score=7, title="Título de teste", hook_text="Gancho", **kw) -> Candidate:
    base = dict(start_id=start_id, end_id=end_id, title=title, hook_text=hook_text, reason="porque sim",
                hook=7, standalone=7, emotion=7, payoff=7, score=score)
    base.update(kw)
    return Candidate(**base)


class FakeBackend:
    """Mesma interface do GenaiBackend; devolve o que o teste mandar e conta as chamadas."""

    def __init__(self, candidates=None, script=None):
        self.candidates = candidates or []
        self.script = list(script or [])  # exceções/resultados a devolver na ordem
        self.calls = 0
        self.prompts: list[str] = []
        self.media: list = []

    def generate(self, prompt, schema, temperature, media=None):
        self.calls += 1
        self.prompts.append(prompt)
        self.media.append(media)
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, Exception):
                raise item
        return schema(candidates=self.candidates), {"prompt_tokens": 100, "output_tokens": 50}


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(min_clip_s=8, max_clip_s=20, clips_per_video=3, preset="ultrafast",
                  workspace_dir=tmp_path / "ws", output_dir=tmp_path / "out")


needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg não encontrado")


def make_watermark(path: Path) -> Path:
    """PNG 360x90 com moldura branca opaca e miolo transparente (mesmo truque do spike 01)."""
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "color=c=white:s=360x90,format=rgba,drawbox=x=6:y=6:w=348:h=78:color=black@0:t=fill:replace=1",
         "-frames:v", "1", str(path)],
        check=True,
    )
    return path


def make_fake_ydl(info: dict, video_dur: float = 60):
    """Classe com a interface mínima do yt_dlp.YoutubeDL; `download` grava um vídeo sintético em outtmpl."""

    class FakeYDL:
        instances = 0
        downloads = 0

        def __init__(self, opts):
            type(self).instances += 1
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            return dict(info) if info is not None else None

        def download(self, urls):
            type(self).downloads += 1
            make_video(Path(self.opts["outtmpl"].replace("%(ext)s", "mp4")), dur=video_dur)

    return FakeYDL


YT_INFO = {"id": "IALW8WPhUQ4", "title": "Papo sobre a vida", "channel": "Canal Teste", "duration": 60,
           "webpage_url": "https://www.youtube.com/watch?v=IALW8WPhUQ4", "license": None}


def make_cut_video(path: Path, seg_s: int = 10, audio: bool = True) -> Path:
    """6 planos de seg_s s com visuais bem diferentes (cortes em seg_s*1..5). Com áudio, os planos de índice
    1 e 4 são ALTOS e os demais baixos."""
    srcs = ["testsrc2", "smptebars", "color=c=blue", "mandelbrot", "rgbtestsrc", "yuvtestsrc"]
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for s in srcs:
        sep = ":" if "=" in s else "="
        cmd += ["-f", "lavfi", "-t", str(seg_s), "-i", f"{s}{sep}size=320x180:rate=25"]
    total = seg_s * len(srcs)
    fc = "".join(f"[{i}:v]setsar=1,format=yuv420p[v{i}];" for i in range(len(srcs)))
    fc += "".join(f"[v{i}]" for i in range(len(srcs))) + f"concat=n={len(srcs)}:v=1:a=0[v]"
    cmd += ["-f", "lavfi", "-i",
            f"sine=frequency=440:duration={total},volume='if(between(t,{seg_s},{2 * seg_s})+between(t,{4 * seg_s},{5 * seg_s}),1.0,0.05)':eval=frame"]
    cmd += ["-filter_complex", fc, "-map", "[v]"]
    if audio:
        cmd += ["-map", f"{len(srcs)}:a", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True)
    return path
