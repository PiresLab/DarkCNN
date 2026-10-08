import json
from pathlib import Path

import yaml
from sqlalchemy import select

from conftest import make_video, needs_ffmpeg
from darkcnn import legacy
from darkcnn.config import Config
from darkcnn.db import Database, Job


def old_project(root: Path) -> Path:
    """Projeto no formato antigo: config.yaml + output/ + workspace/web_runs.json + gameplays/."""
    root.mkdir(parents=True)
    (root / "config.yaml").write_text(yaml.safe_dump({"crf": 18}), encoding="utf-8")
    folder = root / "output" / "video-1"
    folder.mkdir(parents=True)
    (folder / "selection.json").write_text(json.dumps([{"rank": 1, "title": "Um"}]), encoding="utf-8")
    (folder / "01_um.mp4").write_bytes(b"\x00")
    narr = root / "output" / "narrate" / "curiosidade-abc"
    narr.mkdir(parents=True)
    (narr / "scripts.json").write_text("[]", encoding="utf-8")
    ws = root / "workspace"
    ws.mkdir()
    key_cuts = str(Path("output") / "video-1")
    key_gone = str(Path("output") / "sumiu")
    (ws / "web_runs.json").write_text(json.dumps({
        key_cuts: {"mode": "run", "source": "https://x/y", "params": {}, "label": "Meu vídeo", "at": 1_700_000_000.0},
        key_gone: {"mode": "run", "source": "z", "label": "?", "at": 1.0},
    }), encoding="utf-8")
    (ws / "usage.json").write_text("{}", encoding="utf-8")
    (ws / "words-cache").mkdir()
    return root


def target(tmp_path: Path):
    new = tmp_path / "data"
    cfg = Config(workspace_dir=new / "workspace", output_dir=new / "outputs", gameplay_dir=new / "gameplays")
    db = Database(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    db.init()
    return cfg, db, new / "config.yaml"


def test_migrates_outputs_config_and_history_and_is_idempotent(tmp_path):
    origin = old_project(tmp_path / "old")
    cfg, db, cfg_dest = target(tmp_path)
    rep = legacy.migrate(origin, cfg, db, config_dest=cfg_dest)

    assert rep.outputs == 2 and rep.config == "copiado" and rep.runs == 1
    assert (cfg.output_dir / "video-1" / "01_um.mp4").is_file()
    assert (cfg.output_dir / "narrate" / "curiosidade-abc" / "scripts.json").is_file()
    assert yaml.safe_load(cfg_dest.read_text())["crf"] == 18
    assert any("sumiu" in n for n in rep.notes)  # histórico de saída inexistente é avisado, não quebra

    with db.session() as s:
        job = s.scalars(select(Job)).one()
    assert job.status == "done" and job.source == "https://x/y" and job.output_dir == str(cfg.output_dir / "video-1")

    again = legacy.migrate(origin, cfg, db, config_dest=cfg_dest)
    assert again.outputs == 0 and again.runs == 0 and again.config.startswith("já existe")
    assert (origin / "output" / "video-1" / "01_um.mp4").is_file()  # a origem não é tocada


def test_dry_run_changes_nothing(tmp_path):
    origin = old_project(tmp_path / "old")
    cfg, db, cfg_dest = target(tmp_path)
    rep = legacy.migrate(origin, cfg, db, dry_run=True, config_dest=cfg_dest)
    assert rep.outputs == 2
    assert not cfg.output_dir.exists() and not cfg_dest.exists()
    with db.session() as s:
        assert s.scalars(select(Job)).first() is None


def test_workspace_cache_is_copied_only_on_request(tmp_path):
    origin = old_project(tmp_path / "old")
    cfg, db, cfg_dest = target(tmp_path)
    legacy.migrate(origin, cfg, db, config_dest=cfg_dest)
    assert not (cfg.workspace_dir / "words-cache").exists()
    rep = legacy.migrate(origin, cfg, db, with_workspace=True, config_dest=cfg_dest)
    assert rep.workspace >= 1 and (cfg.workspace_dir / "words-cache").is_dir()
    assert (cfg.workspace_dir / "usage.json").is_file()


@needs_ffmpeg
def test_gameplays_are_copied_and_registered(tmp_path):
    origin = old_project(tmp_path / "old")
    (origin / "gameplays").mkdir()
    make_video(origin / "gameplays" / "mine.mp4", dur=6, size="320x180")
    cfg, db, cfg_dest = target(tmp_path)
    rep = legacy.migrate(origin, cfg, db, config_dest=cfg_dest)
    assert rep.gameplays == 1 and (cfg.gameplay_dir / "mine.mp4").is_file()
    from darkcnn import gameplays as gp
    assert [g.filename for g in gp.list_all(db)] == ["mine.mp4"]
