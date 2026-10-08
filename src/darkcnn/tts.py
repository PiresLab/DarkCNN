"""TTS: transforma as linhas do roteiro em áudio e monta a trilha de voz com as pausas.

O backend é plugável (`Backend`). O primeiro é o GPT-SoVITS, que roda como um servidor local
(`python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml`) e sintetiza com a voz de
um áudio de referência. A síntese é POR LINHA e fica em cache pelo conteúdo: mexer numa linha do roteiro
não re-sintetiza as outras (importante, porque em CPU isso é lento).
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .config import Config, VoiceCfg
from .media import probe_duration, run

log = logging.getLogger(__name__)

TRACK_SR = 44100  # tudo é normalizado para isso antes de juntar


class TTSError(RuntimeError):
    pass


class Backend(Protocol):
    def say(self, text: str, voice: VoiceCfg, dest: Path) -> None: ...


@dataclass
class Spoken:
    """Uma linha do roteiro já falada e posicionada na trilha final."""
    index: int
    text: str
    wav: Path
    start: float
    end: float
    pause_after: float


# ---------------------------------------------------------------- GPT-SoVITS
class GPTSoVITSBackend:
    def __init__(self, url: str, timeout_s: float = 600.0, split_method: str = "cut5"):
        self.url = url.rstrip("/")
        self.timeout_s = timeout_s
        self.split_method = split_method
        self._weights: tuple[str | None, str | None] = (None, None)

    def _post(self, path: str, **kw):
        import requests

        try:
            return requests.post(f"{self.url}{path}", timeout=self.timeout_s, **kw)
        except Exception as e:  # servidor fora do ar, porta errada, timeout…
            raise TTSError(
                f"não consegui falar com o GPT-SoVITS em {self.url} ({type(e).__name__}). Ele está rodando? "
                "Inicie com: python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml"
            ) from e

    def _load_weights(self, voice: VoiceCfg) -> None:
        """Troca os modelos quando a voz tem os seus (só uma vez por voz)."""
        want = (str(voice.gpt_weights) if voice.gpt_weights else None,
                str(voice.sovits_weights) if voice.sovits_weights else None)
        if want == self._weights or want == (None, None):
            return
        import requests

        for kind, value in (("gpt", want[0]), ("sovits", want[1])):
            if not value:
                continue
            try:
                r = requests.get(f"{self.url}/set_{kind}_weights", params={"weights_path": value},
                                 timeout=self.timeout_s)
            except Exception as e:
                raise TTSError(f"falha ao carregar os modelos {kind} de {value}: {e}") from e
            if r.status_code != 200:
                raise TTSError(f"o GPT-SoVITS recusou os modelos {kind} ({value}): {r.text[:300]}")
        self._weights = want

    def say(self, text: str, voice: VoiceCfg, dest: Path) -> None:
        self._load_weights(voice)
        payload = {
            "text": text, "text_lang": voice.lang,
            "ref_audio_path": str(voice.ref_audio), "prompt_text": voice.prompt_text,
            "prompt_lang": voice.prompt_lang or voice.lang, "text_split_method": self.split_method,
            "media_type": "wav", "streaming_mode": False, "speed_factor": voice.speed,
        }
        r = self._post("/tts", json=payload)
        if r.status_code != 200:  # a API devolve JSON de erro com 400
            raise TTSError(f"o GPT-SoVITS recusou a síntese ({r.status_code}): {r.text[:400]}")
        data = r.content
        if not data[:4] == b"RIFF":
            raise TTSError("o GPT-SoVITS respondeu 200 mas não mandou um WAV "
                           f"(começo: {data[:40]!r}). Confira a versão do api_v2.py.")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)


# ---------------------------------------------------------------- Chatterbox
class ChatterboxBackend:
    """Fala com `tools/chatterbox_server.py` (roda no ambiente do Chatterbox, Python 3.11)."""

    def __init__(self, url: str, timeout_s: float = 600.0):
        self.url = url.rstrip("/")
        self.timeout_s = timeout_s

    def say(self, text: str, voice: VoiceCfg, dest: Path) -> None:
        import requests

        payload = {"text": text, "language_id": voice.lang, "audio_prompt_path": str(voice.ref_audio),
                   "exaggeration": voice.exaggeration, "cfg_weight": voice.cfg_weight}
        try:
            r = requests.post(f"{self.url}/tts", json=payload, timeout=self.timeout_s)
        except Exception as e:
            raise TTSError(
                f"não consegui falar com o servidor do Chatterbox em {self.url} ({type(e).__name__}). "
                "Ele está rodando? Inicie com: python tools/chatterbox_server.py --port 9881") from e
        if r.status_code != 200:
            raise TTSError(f"o Chatterbox recusou a síntese ({r.status_code}): {r.text[:400]}")
        if r.content[:4] != b"RIFF":
            raise TTSError(f"o servidor do Chatterbox não mandou um WAV (começo: {r.content[:40]!r})")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)


def make_backend(cfg: Config) -> Backend:
    if cfg.tts_backend == "chatterbox":
        return ChatterboxBackend(cfg.tts_url, cfg.tts_timeout_s)
    return GPTSoVITSBackend(cfg.tts_url, cfg.tts_timeout_s, cfg.tts_split_method)


# ---------------------------------------------------------------- síntese + trilha
def get_voice(cfg: Config) -> VoiceCfg:
    voice = cfg.voices.get(cfg.voice)
    if voice is None:
        known = ", ".join(sorted(cfg.voices)) or "(nenhuma)"
        raise TTSError(f'voz "{cfg.voice}" não está no config.yaml. Vozes configuradas: {known}')
    return voice


def line_key(text: str, voice_name: str, voice: VoiceCfg, cfg: Config) -> str:
    """Nome do arquivo em cache: depende do texto e da voz, não da posição no roteiro."""
    blob = f"{text}|{voice_name}|{voice.model_dump_json()}|{cfg.tts_backend}|{cfg.tts_split_method}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def synthesize(texts: list[str], cfg: Config, backend: Backend, cache_dir: Path) -> tuple[list[Path], int]:
    """Fala cada texto (com cache por conteúdo). Devolve (wavs, quantos foram realmente sintetizados)."""
    voice = get_voice(cfg)
    cache_dir.mkdir(parents=True, exist_ok=True)
    wavs: list[Path] = []
    made = 0
    for i, text in enumerate(texts, 1):
        wav = cache_dir / f"{line_key(text, cfg.voice, voice, cfg)}.wav"
        if not wav.exists():
            log.info("  voz %d/%d: sintetizando…", i, len(texts))
            backend.say(text, voice, wav)
            if not wav.exists() or wav.stat().st_size < 64:
                raise TTSError(f"a síntese não gerou áudio para: {text[:60]!r}")
            made += 1
        wavs.append(wav)
    return wavs, made


def place(wavs: list[Path], pauses: list[float]) -> list[Spoken]:
    """Posiciona cada linha na linha do tempo da trilha (fala + pausa depois dela)."""
    out: list[Spoken] = []
    t = 0.0
    for i, (wav, pause) in enumerate(zip(wavs, pauses)):
        dur = probe_duration(wav)
        out.append(Spoken(index=i, text="", wav=wav, start=round(t, 3), end=round(t + dur, 3),
                          pause_after=pause))
        t += dur + pause
    return out


def build_track(spokens: list[Spoken], dest: Path) -> float:
    """Junta as falas com os silêncios num único WAV. Devolve a duração total."""
    if not spokens:
        raise TTSError("roteiro sem nenhuma linha falada")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    labels, n = [], 0
    chain = []
    for sp in spokens:
        cmd += ["-i", str(Path(sp.wav).resolve())]
        chain.append(f"[{n}:a]aformat=sample_rates={TRACK_SR}:channel_layouts=mono[s{n}]")
        labels.append(f"[s{n}]")
        n += 1
        if sp.pause_after > 0:
            cmd += ["-f", "lavfi", "-t", f"{sp.pause_after:.3f}",
                    "-i", f"anullsrc=channel_layout=mono:sample_rate={TRACK_SR}"]
            chain.append(f"[{n}:a]aformat=sample_rates={TRACK_SR}:channel_layouts=mono[s{n}]")
            labels.append(f"[s{n}]")
            n += 1
    fc = ";".join(chain) + ";" + "".join(labels) + f"concat=n={len(labels)}:v=0:a=1[out]"
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd += ["-filter_complex", fc, "-map", "[out]", "-c:a", "pcm_s16le", "-ar", str(TRACK_SR),
            "-ac", "1", str(dest)]
    run(cmd)
    return probe_duration(dest)
