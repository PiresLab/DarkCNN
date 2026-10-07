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


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # --- Gemini ---
    gemini_model: str = "gemini-3.1-flash-lite"  # NUNCA fixar no código: IDs mudam/são descontinuados
    prompt_version: str = "select_v1"
    daily_request_budget: int = Field(40, ge=1)  # conservador até confirmar a cota do projeto
    gemini_retries: int = Field(3, ge=0)
    gemini_backoff_s: float = 5.0

    # --- seleção dos cortes ---
    min_clip_s: float = Field(30.0, gt=0)
    max_clip_s: float = Field(60.0, gt=0)
    clips_per_video: int = Field(5, ge=1)
    pad_start_s: float = 0.15
    pad_end_s: float = 0.30
    max_single_window_min: float = 40.0  # acima disso avisa (chunking só na Fase 3)

    # --- transcrição ---
    language: str = "pt"
    whisper_model: str = "small"
    whisper_beam: int = 1
    whisper_compute_type: str = "int8"
    min_word_s: float = 0.08  # Whisper às vezes devolve palavras com duração 0

    # --- vídeo/texto na tela ---
    text_mode: Literal["captions", "titled", "none"] = "captions"
    layout: Literal["crop", "blur"] = "crop"
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
