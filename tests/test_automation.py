import time
from pathlib import Path

import pytest
import yaml

from conftest import make_video, needs_ffmpeg

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from darkcnn import autopilot, presets  # noqa: E402
from darkcnn.config import Config  # noqa: E402
from darkcnn.db import Database  # noqa: E402
from darkcnn.scheduler import Scheduler, is_due, validate_cron  # noqa: E402
from darkcnn.web.api import AppState, create_app  # noqa: E402
from darkcnn.web.jobs import JobStore  # noqa: E402


def wait_for(fn, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.03)
    return False


def client(tmp_path, runners=None, config=None):
    cfg_path = tmp_path / "config.yaml"
    base = {"workspace_dir": str(tmp_path / "ws"), "output_dir": str(tmp_path / "out"),
            "gameplay_dir": str(tmp_path / "games")}
    cfg_path.write_text(yaml.safe_dump({**base, **(config or {})}), encoding="utf-8")
    st = AppState(cfg_path, runners=runners, concurrency=2)
    return TestClient(create_app(st)), st


class FakeClient:
    """Mesma interface do GeminiClient; devolve as respostas na ordem."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts: list[str] = []

    def generate_json(self, prompt, schema, temperature=0.2, media=None):
        self.prompts.append(prompt)
        return self.answers.pop(0)


class FakeYdl:
    def __init__(self, entries):
        self.entries = entries
        self.queries: list[str] = []

    def __call__(self, opts):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, q, download=False):
        self.queries.append(q)
        return {"entries": self.entries}


# ---------------------------------------------------------------- presets
def test_preset_validation_and_crud(tmp_path):
    c, _ = client(tmp_path)
    bad = c.post("/api/presets", json={"name": "x", "spec": {"mode": "run", "auto_source": False}})
    assert bad.status_code == 400 and "link" in bad.json()["detail"]
    assert c.post("/api/presets", json={"name": "x", "spec": {"inexistente": 1}}).status_code == 400

    p = c.post("/api/presets", json={"name": "Curiosidades", "spec": {"niche": "espaço", "count": 2}}).json()
    assert p["spec"]["auto_topic"] is True and p["spec"]["count"] == 2
    upd = c.put(f"/api/presets/{p['id']}", json={"name": "Espaço", "spec": {"count": 3}}).json()
    assert upd["name"] == "Espaço" and upd["spec"]["count"] == 3
    assert [x["name"] for x in c.get("/api/presets").json()["presets"]] == ["Espaço"]
    assert c.delete(f"/api/presets/{p['id']}").status_code == 200
    assert c.delete(f"/api/presets/{p['id']}").status_code == 404


def test_schedule_validation(tmp_path):
    c, _ = client(tmp_path)
    p = c.post("/api/presets", json={"name": "a", "spec": {}}).json()
    assert c.put(f"/api/presets/{p['id']}/schedule", json={"cron": "amanhã"}).status_code == 400
    ok = c.put(f"/api/presets/{p['id']}/schedule", json={"cron": "0 18 * * *"})
    assert ok.status_code == 200 and ok.json()["enabled"] is True
    assert c.get("/api/presets").json()["presets"][0]["schedule"]["cron"] == "0 18 * * *"
    assert c.delete(f"/api/presets/{p['id']}/schedule").status_code == 200
    assert c.get("/api/presets").json()["presets"][0]["schedule"] is None


def test_run_preset_applies_spec_to_the_job_config(tmp_path):
    seen = {}

    def runner(source, cfg, force):
        seen.update(source=source, fmt=cfg.narrate_format, count=cfg.count, voice=cfg.tts_voice)
        out = Path(cfg.output_dir) / "n"
        out.mkdir(parents=True, exist_ok=True)
        return out / "review.md"

    c, st = client(tmp_path, runners={"auto": runner})
    p = c.post("/api/presets", json={"name": "Dilemas", "spec": {
        "narrate_format": "voce-prefere", "count": 3, "tts_voice": "Puck"}}).json()
    job = c.post(f"/api/presets/{p['id']}/run").json()
    assert job["mode"] == "auto" and job["preset_id"] == p["id"] and job["label"] == "Dilemas"
    assert wait_for(lambda: st.jobs.get(job["id"])["status"] == "done")
    assert seen == {"source": p["id"], "fmt": "voce-prefere", "count": 3, "voice": "Puck"}


# ---------------------------------------------------------------- agendamento
def test_cron_helpers():
    validate_cron("*/5 * * * *")
    with pytest.raises(ValueError):
        validate_cron("* * *")
    base = 1_700_000_000.0
    assert not is_due("0 0 1 1 *", None, base, base + 60)
    assert is_due("* * * * *", base, base, base + 120)


def test_scheduler_fires_due_presets_once_and_skips_busy(tmp_path):
    db = Database(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    db.init()
    store = JobStore(db)
    p = presets.create(db, "a", presets.PresetSpec())
    presets.set_schedule(db, p.id, "* * * * *", True)
    clock = [time.time() + 3600]
    sch = Scheduler(db, lambda pid: store.enqueue_preset(pid, {"workspace_dir": str(tmp_path / "ws"),
                                                              "output_dir": str(tmp_path / "out")}),
                    now=lambda: clock[0])
    assert sch.tick() == [p.id]
    assert sch.tick() == []  # mesmo minuto: não dispara de novo
    clock[0] += 120
    assert sch.tick() == []  # o job anterior ainda está na fila: pula
    assert len([j for j in store.list() if j["preset_id"] == p.id]) == 1
    store.claim()
    store.finish(store.list()[0]["id"], "done")
    clock[0] += 120
    assert sch.tick() == [p.id]


# ---------------------------------------------------------------- autopilot
def plan(topic="Buracos negros", queries=("buraco negro documentário",)):
    return autopilot.TopicPlan(topic=topic, angle="a", queries=list(queries))


def test_auto_narrate_injects_the_proposed_topic_and_avoids_history():
    seen = {}
    fake = FakeClient(plan())
    spec = presets.PresetSpec(mode="narrate", niche="espaço")
    runners = {"narrate": lambda src, cfg, force: seen.update(topic=cfg.topic) or Path("r.md")}
    out = autopilot.run_auto("p", Config(), False, spec=spec, history=["Vulcões"], used_sources=set(),
                             client_factory=lambda: fake, runners=runners)
    assert out == Path("r.md") and seen["topic"] == "Buracos negros"
    assert "Vulcões" in fake.prompts[0] and "espaço" in fake.prompts[0]


def test_auto_run_searches_filters_and_picks_a_video():
    entries = [
        {"id": "a1", "title": "Curto", "duration": 60, "channel": "X"},
        {"id": "b2", "title": "Longo demais", "duration": 99999, "channel": "X"},
        {"id": "c3", "title": "Bom", "duration": 1200, "channel": "Y"},
        {"id": "d4", "title": "Já usado", "duration": 1200, "channel": "Y"},
    ]
    ydl = FakeYdl(entries)
    fake = FakeClient(plan(queries=("q1", "q2")), autopilot.VideoPick(index=0, reason="ok"))
    seen = {}
    spec = presets.PresetSpec(mode="run")
    runners = {"run": lambda src, cfg, force: seen.update(src=src) or Path("r.md")}
    autopilot.run_auto("p", Config(), False, spec=spec, history=[],
                       used_sources={"https://www.youtube.com/watch?v=d4"},
                       client_factory=lambda: fake, runners=runners, ydl_cls=ydl)
    assert ydl.queries == ["ytsearch8:q1", "ytsearch8:q2"]
    assert seen["src"] == "https://www.youtube.com/watch?v=c3"  # único que passou nos filtros
    assert "Bom" in fake.prompts[1] and "Curto" not in fake.prompts[1] and "Já usado" not in fake.prompts[1]


def test_auto_run_fails_clearly_when_nothing_suitable():
    fake = FakeClient(plan(), autopilot.VideoPick(index=-1))
    ydl = FakeYdl([{"id": "c3", "title": "Ruim", "duration": 1200}])
    with pytest.raises(RuntimeError, match="nenhum vídeo adequado"):
        autopilot.run_auto("p", Config(), False, spec=presets.PresetSpec(mode="compile"), history=[],
                           used_sources=set(), client_factory=lambda: fake, runners={}, ydl_cls=ydl)


def test_auto_with_fixed_topic_never_calls_the_model_for_narration():
    spec = presets.PresetSpec(mode="narrate", auto_topic=False, topic="Pirâmides")
    cfg = presets.build_config({}, spec, None, None)
    seen = {}
    autopilot.run_auto("p", cfg, False, spec=spec, history=[], used_sources=set(),
                       client_factory=lambda: (_ for _ in ()).throw(AssertionError("não devia chamar")),
                       runners={"narrate": lambda s, c, f: seen.update(t=c.topic) or Path("r.md")})
    assert seen["t"] == "Pirâmides"


# ---------------------------------------------------------------- gameplays
@needs_ffmpeg
def test_gameplay_upload_list_thumb_delete_and_preset_subset(tmp_path):
    src = make_video(tmp_path / "clip.mp4", dur=8, size="320x180")
    c, st = client(tmp_path)
    with src.open("rb") as f:
        g = c.post("/api/gameplays", files={"file": ("Minha Gameplay.mp4", f, "video/mp4")}).json()
    assert g["duration"] >= 7 and g["has_thumb"] is True
    assert (tmp_path / "games" / g["filename"]).is_file()
    assert c.get(f"/api/gameplays/{g['id']}/thumb").status_code == 200
    assert c.get(f"/api/gameplays/{g['id']}/file").status_code == 200
    assert [x["id"] for x in c.get("/api/gameplays").json()["gameplays"]] == [g["id"]]

    spec = presets.PresetSpec(gameplay_ids=[g["id"]])
    cfg = presets.build_config(st.saved(), spec, tmp_path / "games", st.db)
    assert [Path(f).name for f in cfg.gameplay_files] == [g["filename"]]
    with pytest.raises(ValueError):  # ids que não existem: erro claro em vez de sortear qualquer gameplay
        presets.build_config(st.saved(), presets.PresetSpec(gameplay_ids=["nope"]), tmp_path / "games", st.db)

    assert c.delete(f"/api/gameplays/{g['id']}").status_code == 200
    assert not (tmp_path / "games" / g["filename"]).exists()
    with pytest.raises(ValueError):
        presets.build_config(st.saved(), spec, tmp_path / "games", st.db)


def test_gameplay_upload_rejects_non_video(tmp_path):
    c, _ = client(tmp_path)
    assert c.post("/api/gameplays", files={"file": ("a.txt", b"oi", "text/plain")}).status_code == 400
    r = c.post("/api/gameplays", files={"file": ("a.mp4", b"isto nao e video", "video/mp4")})
    assert r.status_code == 400 and "válido" in r.json()["detail"]
    assert list((tmp_path / "games").glob("*.part")) == []


@needs_ffmpeg
def test_folder_files_are_registered_on_listing(tmp_path):
    games = tmp_path / "games"
    games.mkdir()
    make_video(games / "copiada.mp4", dur=6, size="320x180")
    c, _ = client(tmp_path)
    data = c.get("/api/gameplays").json()["gameplays"]
    assert [g["filename"] for g in data] == ["copiada.mp4"]
    assert len(c.get("/api/gameplays").json()["gameplays"]) == 1  # não duplica


def test_adhoc_run_carries_its_spec_in_the_job(tmp_path):
    seen = {}

    def runner(source, cfg, force):
        seen.update(source=source, fmt=cfg.narrate_format)
        out = Path(cfg.output_dir) / "n"
        out.mkdir(parents=True, exist_ok=True)
        return out / "review.md"

    c, st = client(tmp_path, runners={"auto": runner})
    assert c.post("/api/runs", json={"spec": {"mode": "run", "auto_source": False}}).status_code == 400
    job = c.post("/api/runs", json={"name": "Teste", "spec": {"narrate_format": "e-se"}}).json()
    assert job["preset_id"] is None and job["params"]["spec"]["narrate_format"] == "e-se"
    assert wait_for(lambda: st.jobs.get(job["id"])["status"] == "done")
    assert seen == {"source": "adhoc", "fmt": "e-se"}


def test_clips_field_sets_clips_per_video_for_cuts_and_compile_only():
    base = {"clips_per_video": 7}
    run = presets.build_config(base, presets.PresetSpec(mode="run", clips=3), None, None)
    top = presets.build_config(base, presets.PresetSpec(mode="compile", clips=4), None, None)
    assert run.clips_per_video == 3 and top.clips_per_video == 4
    # sem valor no preset vale o de Configurações; narração nunca mexe nos cortes
    assert presets.build_config(base, presets.PresetSpec(mode="run"), None, None).clips_per_video == 7
    nar = presets.build_config(base, presets.PresetSpec(mode="narrate", count=2, clips=9), None, None)
    assert nar.clips_per_video == 7 and nar.count == 2
    # `count` (vídeos narrados) deixou de ser usado como "quantos cortes"
    assert presets.build_config(base, presets.PresetSpec(mode="run", count=9), None, None).clips_per_video == 7
