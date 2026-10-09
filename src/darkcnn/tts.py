"""TTS: transforma o roteiro em voz (TTS do Gemini) e monta a trilha com as pausas.

A síntese é por BLOCO de roteiro (uma chamada fala várias linhas seguidas), porque o TTS do Gemini tem limite
de requisições por minuto/dia. Cada bloco fica em cache pelo conteúdo: refazer um roteiro só sintetiza o que mudou.
O backend é plugável (`Backend`) para os testes usarem um falso.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from .config import Config
from .media import probe_duration, run

log = logging.getLogger(__name__)

TRACK_SR = 44100  # tudo é normalizado para isso antes de juntar
PCM_SR = 24000  # o TTS do Gemini devolve PCM 16 bits mono a 24 kHz
TRANSIENT_CODES = (429, 500, 502, 503, 504)

# Vozes prebuilt documentadas pelo Gemini (confira a lista atual na documentação; a API recusa nome inexistente)
KNOWN_VOICES = [
    "Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede", "Callirrhoe", "Autonoe",
    "Enceladus", "Iapetus", "Umbriel", "Algieba", "Despina", "Erinome", "Algenib", "Rasalgethi",
    "Laomedeia", "Achernar", "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi",
    "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat",
]


class TTSError(RuntimeError):
    pass


class Backend(Protocol):
    def say(self, text: str, voice: str, dest: Path) -> None: ...


@dataclass
class Spoken:
    """Um bloco do roteiro já falado e posicionado na trilha final."""
    index: int
    text: str
    wav: Path
    start: float
    end: float
    pause_after: float


# ---------------------------------------------------------------- Gemini TTS
def pcm_to_wav(pcm: bytes, dest: Path, rate: int = PCM_SR) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dest), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm)


class GeminiTTSBackend:
    """`client.models.generate_content` com `response_modalities=["AUDIO"]` e uma voz prebuilt."""

    def __init__(self, api_key: str | None, model: str, style: str = "", speed: float = 1.0,
                 retries: int = 3, backoff_s: float = 10.0, client: Any = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.api_key, self.model, self.style, self.speed = api_key, model, style.strip(), speed
        self.retries, self.backoff_s = retries, backoff_s
        self._client, self._sleep = client, sleep

    def _get_client(self):
        if self._client is None:
            if not self.api_key:
                raise TTSError("GEMINI_API_KEY não definida (crie o .env a partir do .env.example)")
            from google import genai

            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def _request(self, text: str, voice: str) -> tuple[bytes, int]:
        from google.genai import types

        prompt = f"{self.style}\n\n{text}" if self.style else text
        cfg = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice))),
        )
        r = self._get_client().models.generate_content(model=self.model, contents=prompt, config=cfg)
        for cand in (r.candidates or []):
            for part in (getattr(cand.content, "parts", None) or []):
                inline = getattr(part, "inline_data", None)
                if inline is not None and inline.data:
                    m = re.search(r"rate=(\d+)", inline.mime_type or "")
                    return inline.data, int(m.group(1)) if m else PCM_SR
        why = getattr(r.candidates[0], "finish_reason", None) if r.candidates else "sem candidatos"
        raise _NoAudio(f"o modelo não devolveu áudio (motivo: {why})")

    def say(self, text: str, voice: str, dest: Path) -> None:
        last: Exception | None = None
        pcm, rate = b"", PCM_SR
        for attempt in range(self.retries + 1):
            try:
                pcm, rate = self._request(text, voice)
                break
            except Exception as e:
                code = getattr(e, "code", None)
                if isinstance(code, int) and code not in TRANSIENT_CODES:
                    msg = (getattr(e, "message", None) or str(e))[:400]
                    hint = (" (confira o ID do modelo com `darkcnn voices --models` e o nome da voz)"
                            if code in (400, 404) else "")
                    raise TTSError(f"o TTS do Gemini recusou o pedido ({code}): {msg}{hint}") from e
                last = e
                if attempt < self.retries:
                    wait = self.backoff_s * 2**attempt
                    log.warning("TTS indisponível (%s); nova tentativa em %.0fs", str(e)[:120], wait)
                    self._sleep(wait)
        if not pcm:
            raise TTSError(f"o TTS do Gemini falhou após {self.retries + 1} tentativas: {last}")
        pcm_to_wav(pcm, dest, rate)
        if abs(self.speed - 1.0) > 0.01:
            tmp = dest.with_name(dest.stem + "_fast.wav")
            run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(dest.resolve()),
                 "-filter:a", f"atempo={self.speed:.3f}", str(tmp.resolve())])
            tmp.replace(dest)


class _NoAudio(Exception):
    """Resposta sem áudio (acontece: o modelo às vezes devolve texto). Tratada como falha transitória."""


def make_backend(cfg: Config) -> Backend:
    return GeminiTTSBackend(os.environ.get("GEMINI_API_KEY"), cfg.tts_model, cfg.tts_style, cfg.tts_speed,
                            cfg.tts_retries)


def list_tts_models() -> list[str]:
    """Modelos com 'tts' no nome que a chave do usuário enxerga (o ID certo depende do projeto)."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise TTSError("GEMINI_API_KEY não definida (crie o .env a partir do .env.example)")
    from google import genai

    client = genai.Client(api_key=key)  # precisa continuar referenciado: a lista é paginada e carrega aos poucos
    names = [(m.name or "").removeprefix("models/") for m in client.models.list()]
    return sorted(n for n in names if "tts" in n.lower())


# ---------------------------------------------------------------- síntese + trilha
def pick_voice(cfg: Config, rng) -> str:
    """A voz de um vídeo: sorteada do pool (se houver) ou a padrão."""
    return rng.choice(cfg.tts_voices_pool) if cfg.tts_voices_pool else cfg.tts_voice


def line_key(text: str, voice: str, cfg: Config) -> str:
    """Nome do arquivo em cache: depende do texto, da voz e do estilo, não da posição no roteiro."""
    blob = f"{text}|{voice}|{cfg.tts_model}|{cfg.tts_style}|{cfg.tts_speed}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def synthesize(texts: list[str], voice: str, cfg: Config, backend: Backend,
               cache_dir: Path) -> tuple[list[Path], int]:
    """Fala cada texto (com cache por conteúdo). Devolve (wavs, quantos foram realmente sintetizados)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    wavs: list[Path] = []
    made = 0
    for i, text in enumerate(texts, 1):
        wav = cache_dir / f"{line_key(text, voice, cfg)}.wav"
        if not wav.exists():
            log.info("  voz %d/%d (%s): sintetizando…", i, len(texts), voice)
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


# ---------------------------------------------------------------- tic-tac da contagem
TICK_STEP_S = 0.5  # um tic e um tac por segundo, como um relógio de parede


def _click(freq: float, decay: float, sr: int, noise: float = 0.35, length_s: float = 0.09):
    """Um estalo curto: senoide que morre rápido + um pouco de ruído para soar 'madeira', não 'bipe'."""
    import numpy as np

    t = np.arange(int(sr * length_s)) / sr
    rng = np.random.default_rng(7)  # semente fixa: o mesmo som em toda execução (e o render faz cache)
    body = (1 - noise) * np.sin(2 * np.pi * freq * t) + noise * (rng.random(t.size) * 2 - 1)
    return body * np.exp(-t * decay)


def tick_audio(choices: list[dict], total_s: float, volume: float, sr: int = TRACK_SR):
    """Trilha de tic-tac, do tamanho do vídeo, com sons só dentro da contagem de cada pergunta.
    `choices`: {end, countdown}. Devolve um array float em [-1, 1]."""
    import numpy as np

    out = np.zeros(int(round(total_s * sr)) + sr, dtype=np.float64)
    tic, tac = _click(2300, 75, sr), _click(1500, 65, sr)
    for c in choices:
        n = int(round(c["countdown"] / TICK_STEP_S))
        for k in range(n):
            snd = tic if k % 2 == 0 else tac
            i = int(round((c["end"] + k * TICK_STEP_S) * sr))
            if i >= out.size:
                break
            seg = snd[: out.size - i]
            out[i:i + seg.size] += seg
    peak = float(np.max(np.abs(out))) if out.size else 0.0
    if peak > 0:
        out *= min(1.0, max(0.0, volume)) * 0.8 / peak
    return out[: int(round(total_s * sr))]


def mix_ticks(track: Path, choices: list[dict], volume: float, dest: Path) -> Path:
    """Soma o tic-tac à trilha de voz (WAV PCM 16 bits mono) e grava em `dest`. Sem escolhas ou volume 0,
    devolve a própria trilha (nada a fazer)."""
    if volume <= 0 or not any(c.get("countdown", 0) > 0 for c in choices):
        return track
    import numpy as np

    with wave.open(str(track), "rb") as wf:
        sr, width, ch = wf.getframerate(), wf.getsampwidth(), wf.getnchannels()
        raw = wf.readframes(wf.getnframes())
    if width != 2 or ch != 1:
        raise TTSError("a trilha de voz precisa ser PCM 16 bits mono para receber o tic-tac")
    voice = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0  # mesma escala na ida e na volta
    ticks = tick_audio(choices, voice.size / sr, volume, sr)
    n = min(voice.size, ticks.size)
    mixed = voice.copy()
    mixed[:n] += ticks[:n]
    pcm = np.clip(np.rint(mixed * 32768.0), -32768, 32767).astype("<i2")  # trechos sem tic-tac voltam idênticos
    dest.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dest), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    return dest
