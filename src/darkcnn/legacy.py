"""Migra o uso antigo (pastas ao lado do projeto + config.yaml + web_runs.json) para o novo layout.

Copia, não move: a pasta de origem fica intacta, então dá para conferir antes de apagar qualquer coisa.
Pode rodar mais de uma vez: o que já foi migrado é pulado.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from sqlalchemy import select

from . import gameplays as gp, storage
from .config import Config
from .db import Database, Job
from .storage import VIDEO_EXTS

log = logging.getLogger(__name__)


@dataclass
class Report:
    config: str = "nada a fazer"
    outputs: int = 0
    gameplays: int = 0
    runs: int = 0
    workspace: int = 0
    notes: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        out = [f"config.yaml:   {self.config}",
               f"saídas:        {self.outputs} pasta(s) copiada(s)",
               f"gameplays:     {self.gameplays} arquivo(s) copiado(s)",
               f"histórico:     {self.runs} execução(ões) importada(s)"]
        if self.workspace:
            out.append(f"workspace:     {self.workspace} pasta(s) copiada(s)")
        return out + [f"aviso: {n}" for n in self.notes]


def _same(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def _copy_tree(src: Path, dst: Path, dry: bool) -> bool:
    """Copia a pasta se o destino ainda não existe. Devolve True se copiou (ou copiaria)."""
    if not src.is_dir() or dst.exists() or _same(src, dst):
        return False
    if not dry:
        shutil.copytree(src, dst)
    return True


def _old_dirs(origin: Path) -> tuple[Path, Path, Path]:
    """Pastas do uso antigo, respeitando o config.yaml de origem."""
    data: dict = {}
    cfg = origin / "config.yaml"
    if cfg.is_file():
        try:
            data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            data = {}

    def pick(key: str, default: str) -> Path:
        p = Path(data.get(key) or default)
        return p if p.is_absolute() else origin / p

    return pick("output_dir", "output"), pick("workspace_dir", "workspace"), pick("gameplay_dir", "gameplays")


def migrate(origin: Path, cfg: Config, db: Database, *, dry_run: bool = False,
            with_workspace: bool = False, config_dest: Path | None = None) -> Report:
    rep = Report()
    old_out, old_ws, old_games = _old_dirs(origin)
    new_out, new_ws = Path(cfg.output_dir), Path(cfg.workspace_dir)
    new_games = Path(cfg.gameplay_dir) if cfg.gameplay_dir else origin / "gameplays"

    # --- config.yaml
    src_cfg, dst_cfg = origin / "config.yaml", config_dest or storage.default_config_path()
    if src_cfg.is_file() and not _same(src_cfg, dst_cfg):
        if dst_cfg.exists():
            rep.config = "já existe no destino (mantido)"
        else:
            rep.config = "copiado"
            if not dry_run:
                dst_cfg.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_cfg, dst_cfg)

    # --- saídas: cada pasta (ex.: output/<vídeo>, output/narrate/<id>) é copiada inteira
    mapping: dict[str, Path] = {}  # pasta antiga (como o web_runs.json a guardava) -> pasta nova
    if old_out.is_dir():
        for sel in sorted({p.parent for name in ("selection.json", "scripts.json") for p in old_out.rglob(name)}):
            rel = sel.relative_to(old_out)
            dst = new_out / rel
            if _copy_tree(sel, dst, dry_run):
                rep.outputs += 1
            mapping[str(sel)] = dst
            mapping[str(Path("output") / rel)] = dst

    # --- gameplays
    if old_games.is_dir() and not _same(old_games, new_games):
        for f in sorted(old_games.rglob("*")):
            if f.is_file() and f.suffix.lower() in VIDEO_EXTS:
                dst = new_games / f.relative_to(old_games)
                if not dst.exists():
                    rep.gameplays += 1
                    if not dry_run:
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(f, dst)
    if not dry_run:
        gp.sync_folder(db, new_games)

    # --- histórico: web_runs.json vira linhas em `jobs`
    runs_file = old_ws / "web_runs.json"
    if runs_file.is_file():
        try:
            runs = json.loads(runs_file.read_text(encoding="utf-8"))
        except ValueError:
            runs = {}
            rep.notes.append("web_runs.json ilegível: histórico não importado")
        with db.session() as s:
            known = set(s.scalars(select(Job.output_dir).where(Job.output_dir.is_not(None))).all())
        for old_key, info in runs.items():
            dst = mapping.get(old_key) or mapping.get(str(Path(old_key)))
            if dst is None:
                rep.notes.append(f"saída {old_key} não encontrada na pasta antiga; histórico pulado")
                continue
            if str(dst) in known:
                continue
            rep.runs += 1
            if dry_run:
                continue
            at = float(info.get("at") or 0) or None
            with db.session() as s:
                s.add(Job(id="legacy-" + hashlib.sha1(old_key.encode()).hexdigest()[:8], mode=info.get("mode") or "run",
                          label=info.get("label") or dst.name, source=info.get("source"),
                          params=info.get("params") or {}, config_snapshot={}, status="done",
                          review=str(dst / "review.md"), output_dir=str(dst),
                          created=at or 0.0, started=at, ended=at))

    # --- cache do workspace (opcional: evita refazer Whisper/downloads)
    if with_workspace and old_ws.is_dir() and not _same(old_ws, new_ws):
        for child in sorted(old_ws.iterdir()):
            if child.is_dir() and _copy_tree(child, new_ws / child.name, dry_run):
                rep.workspace += 1
        usage = old_ws / "usage.json"
        if usage.is_file() and not (new_ws / "usage.json").exists() and not dry_run:
            new_ws.mkdir(parents=True, exist_ok=True)
            shutil.copy2(usage, new_ws / "usage.json")
    return rep
