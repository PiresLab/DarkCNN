import json
import logging
import threading
import time
from pathlib import Path

import pytest
import yaml


fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from darkcnn.web.api import AppState, create_app, safe_under  # noqa: E402

log = logging.getLogger("darkcnn.test")


def state_with(tmp_path, runners=None, config=None):
    cfg_path = tmp_path / "config.yaml"
    base = {"workspace_dir": str(tmp_path / "ws"), "output_dir": str(tmp_path / "out")}
    cfg_path.write_text(yaml.safe_dump({**base, **(config or {})}), encoding="utf-8")
    return AppState(cfg_path, runners=runners, concurrency=2)


def client_with(tmp_path, runners=None, config=None):
    st = state_with(tmp_path, runners, config)
    return TestClient(create_app(st)), st


def wait_for(fn, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.03)
    return False


# ---------------------------------------------------------------- ambiente e config
def test_env_reports_ffmpeg_and_budget(tmp_path):
    c, _ = client_with(tmp_path, config={"daily_request_budget": 25})
    e = c.get("/api/env").json()
    assert e["daily_budget"] == 25 and e["requests_today"] == 0
    assert e["config_exists"] is True and e["whisper_model"]


def test_env_counts_requests_from_usage_json(tmp_path):
    c, st = client_with(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    today = time.strftime("%Y-%m-%d")
    (ws / "usage.json").write_text(json.dumps({today: {"requests": 7, "prompt_tokens": 10, "output_tokens": 5}}))
    e = c.get("/api/env").json()
    assert e["requests_today"] == 7 and e["tokens_today"] == 15


def test_config_roundtrip_and_unknown_key_is_rejected(tmp_path):
    c, st = client_with(tmp_path)
    r = c.put("/api/config", json={"values": {"clips_per_video": 3, "layout": "crop"}})
    assert r.status_code == 200
    assert r.json()["effective"]["clips_per_video"] == 3
    assert yaml.safe_load((tmp_path / "config.yaml").read_text())["layout"] == "crop"

    bad = c.put("/api/config", json={"values": {"voice": "gumball"}})
    assert bad.status_code == 400 and "voice" in bad.json()["detail"]
    # o arquivo não foi corrompido pela tentativa inválida
    assert yaml.safe_load((tmp_path / "config.yaml").read_text())["clips_per_video"] == 3


def test_config_is_read_fresh_on_every_request(tmp_path):
    c, _ = client_with(tmp_path)
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(
        {"workspace_dir": str(tmp_path / "ws"), "output_dir": str(tmp_path / "out"), "crf": 18}))
    assert c.get("/api/config").json()["effective"]["crf"] == 18


def test_broken_config_is_a_clear_400(tmp_path):
    c, _ = client_with(tmp_path)
    (tmp_path / "config.yaml").write_text("layout: [isto\n  nao: fecha")
    r = c.get("/api/config")
    assert r.status_code == 400 and "config.yaml" in r.json()["detail"]


# ---------------------------------------------------------------- execuções
def test_job_runs_captures_log_and_remembers_the_source(tmp_path):
    def runner(source, cfg, force):
        log.info("começando %s (force=%s)", source, force)
        out = Path(cfg.output_dir) / "video-1"
        out.mkdir(parents=True, exist_ok=True)
        (out / "selection.json").write_text("[]")
        (out / "review.md").write_text("# ok")
        log.info("pronto")
        return out / "review.md"

    c, st = client_with(tmp_path, runners={"run": runner})
    job = c.post("/api/jobs", json={"mode": "run", "source": "https://x/y", "overrides": {"clips_per_video": 2}}).json()
    assert wait_for(lambda: st.jobs.get(job["id"])["status"] == "done")

    d = c.get(f"/api/jobs/{job['id']}").json()
    assert d["status"] == "done" and d["review"].endswith("review.md")
    assert any("começando https://x/y" in l for l in d["log"]) and any("pronto" in l for l in d["log"])
    assert st.jobs.remembered(tmp_path / "out" / "video-1")["source"] == "https://x/y"
    # o painel aplicou o override na config do job, sem gravar no arquivo
    assert yaml.safe_load((tmp_path / "config.yaml").read_text()).get("clips_per_video") is None


def test_job_error_is_reported_not_raised(tmp_path):
    def runner(source, cfg, force):
        raise RuntimeError("licença não informada")

    c, st = client_with(tmp_path, runners={"run": runner})
    job = c.post("/api/jobs", json={"mode": "run", "source": "x"}).json()
    assert wait_for(lambda: st.jobs.get(job["id"])["ended"] is not None)
    d = c.get(f"/api/jobs/{job['id']}").json()
    assert d["status"] == "error" and "licença não informada" in d["error"]
    assert c.get("/api/jobs").json()["jobs"][0]["id"] == job["id"]


def test_two_jobs_do_not_mix_their_logs(tmp_path):
    started = threading.Barrier(2, timeout=10)

    def runner(source, cfg, force):
        started.wait()
        for i in range(3):
            log.info("%s linha %d", source, i)
        out = Path(cfg.output_dir) / source
        out.mkdir(parents=True, exist_ok=True)
        return out / "review.md"

    c, st = client_with(tmp_path, runners={"run": runner})
    a = c.post("/api/jobs", json={"mode": "run", "source": "alfa"}).json()
    b = c.post("/api/jobs", json={"mode": "run", "source": "beta"}).json()
    assert wait_for(lambda: all(st.jobs.get(j["id"])["ended"] for j in (a, b)))
    la = c.get(f"/api/jobs/{a['id']}").json()["log"]
    lb = c.get(f"/api/jobs/{b['id']}").json()["log"]
    assert la and lb
    assert all("alfa" in x for x in la) and all("beta" in x for x in lb)


def test_cancel_marks_the_job_as_interrupted(tmp_path):
    stop = threading.Event()

    def runner(source, cfg, force):
        log.info("rodando")
        stop.wait(10)
        raise KeyboardInterrupt("morto")

    c, st = client_with(tmp_path, runners={"run": runner})
    job = c.post("/api/jobs", json={"mode": "run", "source": "x"}).json()
    assert wait_for(lambda: any("rodando" in l for l in st.jobs.logs(job["id"])[0]))
    assert c.post(f"/api/jobs/{job['id']}/cancel").json()["ok"] is True
    stop.set()
    assert wait_for(lambda: st.jobs.get(job["id"])["status"] == "cancelled")
    assert c.post(f"/api/jobs/{job['id']}/cancel").status_code == 409


def test_run_without_source_is_refused_but_narrate_is_not(tmp_path):
    def runner(source, cfg, force):
        out = Path(cfg.output_dir) / "narrate"
        out.mkdir(parents=True, exist_ok=True)
        return out / "review.md"

    c, st = client_with(tmp_path, runners={"run": runner, "narrate": runner})
    assert c.post("/api/jobs", json={"mode": "run"}).status_code == 400
    assert c.post("/api/jobs", json={"mode": "narrate"}).status_code == 200
    assert c.post("/api/jobs", json={"mode": "inexistente", "source": "x"}).status_code == 400


def test_job_stream_sends_the_lines_and_closes(tmp_path):
    def runner(source, cfg, force):
        log.info("primeira")
        log.info("segunda")
        out = Path(cfg.output_dir) / "x"
        out.mkdir(parents=True, exist_ok=True)
        return out / "review.md"

    c, st = client_with(tmp_path, runners={"run": runner})
    job = c.post("/api/jobs", json={"mode": "run", "source": "x"}).json()
    assert wait_for(lambda: st.jobs.get(job["id"])["ended"] is not None)
    with c.stream("GET", f"/api/jobs/{job['id']}/stream") as r:
        body = "".join(chunk for chunk in r.iter_text())
    assert "event: line" in body and "primeira" in body and "segunda" in body
    assert "event: end\ndata: done" in body


# ---------------------------------------------------------------- revisão
def selection(out, name="video-1", items=None):
    folder = out / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "selection.json").write_text(json.dumps(items if items is not None else [
        {"rank": 1, "title": "Um", "start": 10.0, "end": 40.0, "duration": 30.0, "score": 9,
         "file": "01_um.mp4", "hook": 9, "standalone": 8, "emotion": 7, "payoff": 8},
        {"rank": 2, "title": "Dois", "start": 60.0, "end": 95.0, "duration": 35.0, "score": 7,
         "file": "02_dois.mp4"}]), encoding="utf-8")
    (folder / "review.md").write_text("# Revisão", encoding="utf-8")
    return folder


def test_reviews_list_cuts_and_narration(tmp_path):
    c, _ = client_with(tmp_path)
    out = tmp_path / "out"
    selection(out)
    narr = out / "narrate" / "curiosidade-abc"
    narr.mkdir(parents=True, exist_ok=True)
    (narr / "scripts.json").write_text(json.dumps([
        {"rank": 1, "title": "Buraco negro", "duration": 36.0, "voice": "Puck", "file": "01_b.mp4",
         "opening_warnings": ["a abertura começa com saudação"], "lines": [{"text": "oi", "kind": "fala", "start": 0.0}]}]))
    data = c.get("/api/reviews").json()["reviews"]
    kinds = {r["kind"] for r in data}
    assert kinds == {"cuts", "narration"}
    cuts = [r for r in data if r["kind"] == "cuts"][0]
    assert cuts["items"][0]["status"] == "pending" and cuts["has_review_md"] is True
    narration = [r for r in data if r["kind"] == "narration"][0]
    assert narration["items"][0]["opening_warnings"]


def test_patch_item_saves_status_and_recomputes_duration(tmp_path):
    c, _ = client_with(tmp_path)
    folder = selection(tmp_path / "out")
    r = c.patch("/api/reviews/video-1/items/1", json={"status": "approved", "start": 12.0, "end": 30.0,
                                                      "title": "Novo título"})
    assert r.status_code == 200 and r.json()["duration"] == 18.0
    saved = json.loads((folder / "selection.json").read_text(encoding="utf-8"))
    assert saved[0]["status"] == "approved" and saved[0]["title"] == "Novo título"
    assert saved[1]["title"] == "Dois"  # os outros itens não foram tocados
    assert c.patch("/api/reviews/video-1/items/9", json={"status": "approved"}).status_code == 404


def test_review_markdown_and_media_are_served_from_the_output_dir(tmp_path):
    c, _ = client_with(tmp_path)
    folder = selection(tmp_path / "out")
    (folder / "01_um.mp4").write_bytes(b"\x00\x01")
    assert "Revisão" in c.get("/api/reviews/video-1/markdown").text
    assert c.get("/api/media/video-1/01_um.mp4").content == b"\x00\x01"
    assert c.get("/api/media/video-1/nao-existe.mp4").status_code == 404


def test_paths_outside_the_output_dir_are_refused(tmp_path):
    c, _ = client_with(tmp_path)
    selection(tmp_path / "out")
    (tmp_path / "segredo.txt").write_text("nada a ver")
    assert c.get("/api/media/..%2Fsegredo.txt").status_code in (400, 404)
    assert c.patch("/api/reviews/..%2F..%2Fetc/items/1", json={"status": "approved"}).status_code in (400, 404)
    with pytest.raises(ValueError):
        safe_under(tmp_path / "out", "../segredo.txt")
    assert safe_under(tmp_path / "out", "video-1/01_um.mp4").name == "01_um.mp4"


def test_spa_fallback_and_unknown_api_route(tmp_path):
    c, _ = client_with(tmp_path)
    page = c.get("/")
    assert page.status_code == 200 and "DarkCNN" in page.text
    assert c.get("/criar").status_code == 200  # rota do React: devolve o index
    assert c.get("/api/nao-existe").status_code == 404
    assert c.get("/api/health").json() == {"ok": True}


def test_gemini_key_is_saved_from_the_ui(tmp_path, monkeypatch):
    from darkcnn import storage
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    c, _ = client_with(tmp_path)
    assert c.put("/api/secrets/gemini", json={"key": "curta"}).status_code == 400
    assert c.put("/api/secrets/gemini", json={"key": "AIza" + "x" * 30}).status_code == 200
    assert c.get("/api/env").json()["api_key"] is True
    assert storage.secret_file().read_text() == "AIza" + "x" * 30
    c.delete("/api/secrets/gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert c.get("/api/env").json()["api_key"] is False


def test_unset_cli_flags_and_empty_fields_do_not_break_a_job(tmp_path):
    """As flags da CLI chegam como None; o formulário manda campos vazios. Nem um nem outro é config."""
    def runner(source, cfg, force):
        assert cfg.narrate_format == "e-se"
        out = Path(cfg.output_dir) / "n"
        out.mkdir(parents=True, exist_ok=True)
        return out / "review.md"

    st = AppState(tmp_path / "ausente.yaml", overrides={"workspace_dir": None, "output_dir": tmp_path / "out"},
                  runners={"narrate": runner}, database_url=f"sqlite:///{(tmp_path / 'x.db').as_posix()}")
    c = TestClient(create_app(st))
    r = c.post("/api/jobs", json={"mode": "narrate", "overrides": {"narrate_format": "e-se", "topic": None,
                                                                   "gameplay_dir": ""}})
    assert r.status_code == 200, r.text
    assert wait_for(lambda: st.jobs.get(r.json()["id"])["status"] == "done")
