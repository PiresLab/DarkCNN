"""Configuração: config.yaml + variáveis de ambiente (.env) + flags da CLI (nessa ordem de prioridade inversa)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class WatermarkCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: Path | None = None  # PNG com alpha; None = sem marca d'água
    opacity: float = Field(0.6, ge=0.0, le=1.0)
    # expressões do filtro overlay do FFmpeg (W/H = vídeo, w/h = marca d'água)
    x: str = "W-w-48"
    y: str = "120"  # acima do título (layout 'titled'), fora da zona de UI das plataformas


class VoiceCfg(BaseModel):
    """Uma voz do GPT-SoVITS: áudio de referência (3-10 s) + a transcrição exata dele."""
    model_config = ConfigDict(extra="forbid")

    ref_audio: Path  # WAV de referência; o caminho é lido pelo SERVIDOR do GPT-SoVITS
    prompt_text: str = ""  # o que é falado no áudio de referência (melhora muito a imitação)
    lang: str = "pt"  # idioma do texto a falar (o que o servidor aceita depende da versão do GPT-SoVITS)
    prompt_lang: str = ""  # idioma do áudio de referência; vazio = o mesmo de `lang`
    speed: float = Field(1.0, gt=0.1, le=3.0)
    gpt_weights: Path | None = None  # modelos próprios dessa voz (opcional)
    sovits_weights: Path | None = None
    exaggeration: float = Field(0.5, ge=0.0, le=2.0)  # só Chatterbox: expressividade
    cfg_weight: float = Field(0.5, ge=0.0, le=1.0)  # só Chatterbox: 0 reduz o sotaque da referência


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # --- Gemini ---
    gemini_model: str = "gemini-3.1-flash-lite"  # NUNCA fixar no código: IDs mudam/são descontinuados
    prompt_version: str = "select_v2"  # perfil de fala
    visual_prompt_version: str = "select_visual_v2"
    theme_prompt_version: str = "select_theme_v1"
    daily_request_budget: int = Field(40, ge=1)  # conservador até confirmar a cota do projeto
    gemini_retries: int = Field(3, ge=0)
    gemini_backoff_s: float = 5.0
    # raciocínio do Gemini ("thinking"): off = não envia; se o modelo não suportar, desliga sozinho com aviso
    thinking_level: Literal["off", "low", "medium", "high"] = "medium"

    # --- juiz: 2ª passada que assiste aos candidatos e os compara entre si ---
    judge: bool = True
    judge_model: str | None = None  # None = o mesmo gemini_model
    judge_factor: int = Field(3, ge=1)  # candidatos pedidos = clips_per_video x judge_factor
    judge_max_candidates: int = Field(12, ge=2)  # teto de candidatos no vídeo que o juiz assiste
    judge_prompt_version: str = "judge_v1"
    theme: str | None = None  # modo compilado ("top 5 finalizações")

    # --- narração sobre gameplay (comando `narrate`) ---
    script_prompt_version: str = "script_v1"
    narrate_format: str = "curiosidade"  # curiosidade | voce-prefere | e-se | texto livre
    topic: str | None = None  # None = a IA escolhe o tema
    count: int = Field(1, ge=1, le=10)  # quantos vídeos por execução
    target_s: float = Field(45.0, gt=5)  # duração alvo da narração
    voice: str = "default"  # nome de uma voz em `voices`
    voices: dict[str, VoiceCfg] = {}
    tts_backend: Literal["gptsovits", "chatterbox"] = "gptsovits"
    tts_url: str = "http://127.0.0.1:9880"
    tts_timeout_s: float = 600.0  # síntese em CPU é lenta
    tts_split_method: str = "cut5"  # como o GPT-SoVITS quebra o texto (ver text_segmentation_method.py)
    gameplay_dir: Path | None = None  # pasta com as gameplays de fundo
    game_volume: float = Field(0.06, ge=0.0, le=1.0)  # volume do áudio da gameplay (0 = mudo)
    pause_s: float = Field(0.35, ge=0.0)  # silêncio entre as linhas do roteiro
    countdown_s: float = Field(3.0, ge=0.0)  # tempo de decisão após uma pergunta "você prefere"
    seed: int | None = None  # fixa a escolha da gameplay (reprodutível)
    theme_min_fit: int = Field(5, ge=1, le=10)  # abaixo disso o momento não combina com o tema e é descartado

    # --- perfil: talk = vídeo com fala (transcrição); visual = sem fala (planos + vídeo para o Gemini) ---
    profile: Literal["talk", "visual"] = "talk"
    scene_threshold: float = Field(0.30, gt=0, lt=1)  # sensibilidade do detector de corte de cena
    min_shot_s: float = 1.5  # planos menores que isso são fundidos ao vizinho
    max_shot_s: float = 12.0  # plano contínuo maior que isso é dividido (senão não haveria onde cortar)
    visual_window_min: float = 10.0  # minutos de vídeo por chamada ao Gemini
    visual_fps: int = 2  # quadros/s do vídeo enviado (o Gemini amostra ~1 fps)
    proxy_height: int = 360  # altura do vídeo enviado ao Gemini
    visual_pause_s: float = 15.0  # espera entre janelas (o limite de tokens/min do free tier)

    # --- seleção dos cortes ---
    min_clip_s: float = Field(30.0, gt=0)
    max_clip_s: float = Field(60.0, gt=0)
    clips_per_video: int = Field(5, ge=1)
    pad_start_s: float = 0.15
    pad_end_s: float = 0.30
    max_single_window_min: float = 40.0  # acima disso avisa (chunking só na Fase 3)
    max_download_min: float = 180.0  # recusa baixar vídeos maiores que isso

    # --- transcrição ---
    language: str = "pt"
    whisper_model: str = "small"
    whisper_beam: int = 1
    whisper_compute_type: str = "int8"
    min_word_s: float = 0.08  # Whisper às vezes devolve palavras com duração 0

    # --- vídeo/texto na tela ---
    # captions = legenda por palavra | titled = título no topo + gancho embaixo | both = contexto no topo + legenda
    # | ranked = compilado "Top N" | none. Padrão (se não escolhido): both no perfil talk, titled no visual.
    text_mode: Literal["captions", "titled", "both", "ranked", "none"] = "captions"
    layout: Literal["crop", "blur"] = "blur"
    font: str = "Arial" if os.name == "nt" else "DejaVu Sans"
    fonts_dir: Path | None = None
    preset: str = "medium"
    crf: int = 20
    watermark: WatermarkCfg = WatermarkCfg()

    # --- procedência (vai para o review.md) ---
    source: str | None = None  # URL ou descrição da fonte
    license: str | None = None  # ex.: "CC-BY 4.0", "autorizado por <canal> em <data>"

    # --- pastas ---
    workspace_dir: Path = Path("workspace")
    output_dir: Path = Path("output")

    @model_validator(mode="after")
    def _check(self) -> "Config":
        if self.max_clip_s < self.min_clip_s:
            raise ValueError("max_clip_s deve ser >= min_clip_s")
        return self


def load_dotenv(path: Path = Path(".env")) -> None:
    """Lê KEY=VALUE do .env sem dependências. Variáveis já definidas no ambiente têm prioridade."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_config(path: Path | None = None, overrides: dict[str, Any] | None = None) -> Config:
    """config.yaml (se existir) + overrides da CLI (valores None são ignorados)."""
    data: dict[str, Any] = {}
    p = path or Path("config.yaml")
    if p.exists():
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    elif path is not None:
        raise FileNotFoundError(f"config não encontrado: {path}")
    for k, v in (overrides or {}).items():
        if v is None:
            continue
        if k.startswith("watermark_"):
            data.setdefault("watermark", {})[k.removeprefix("watermark_")] = v
        else:
            data[k] = v
    return Config(**data)
