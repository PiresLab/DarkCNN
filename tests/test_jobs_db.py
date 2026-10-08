import time
from pathlib import Path

from darkcnn.config import Config
from darkcnn.db import Database
from darkcnn.web.jobs import JobStore, Worker


def make(tmp_path):
    db = Database(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    db.init()
    return db, JobStore(db)


def cfg(tmp_path):
    return Config(workspace_dir=tmp_path / "ws", output_dir=tmp_path / "out")


def wait_for(fn, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.03)
    return False


def test_jobs_survive_a_restart_and_orphans_become_errors(tmp_path):
    db, store = make(tmp_path)
    a = store.enqueue("narrate", cfg(tmp_path), None, "a", {})
    claimed = store.claim()
    assert claimed.id == a["id"] and store.get(a["id"])["status"] == "running"

    db2 = Database(db.url)  # "novo processo"
    store2 = JobStore(db2)
    assert store2.recover_orphans() == 1
    got = store2.get(a["id"])
    assert got["status"] == "error" and "reiniciou" in got["error"]


def test_claim_is_exclusive_and_ordered(tmp_path):
    _, store = make(tmp_path)
    first = store.enqueue("narrate", cfg(tmp_path), None, "1", {})
    second = store.enqueue("narrate", cfg(tmp_path), None, "2", {})
    assert store.claim().id == first["id"]
    assert store.claim().id == second["id"]
    assert store.claim() is None


def test_cancel_queued_job_never_runs(tmp_path):
    _, store = make(tmp_path)
    a = store.enqueue("narrate", cfg(tmp_path), None, "a", {})
    assert store.cancel(a["id"]) is True
    assert store.get(a["id"])["status"] == "cancelled" and store.claim() is None
    assert store.cancel(a["id"]) is False


def test_worker_runs_snapshot_config_and_stores_log_and_output(tmp_path):
    _, store = make(tmp_path)

    def runner(source, c, force):
        assert c.clips_per_video == 2 and source == "x"
        out = Path(c.output_dir) / "v"
        out.mkdir(parents=True, exist_ok=True)
        return out / "review.md"

    c = Config(workspace_dir=tmp_path / "ws", output_dir=tmp_path / "out", clips_per_video=2)
    job = store.enqueue("run", c, "x", "x", {})
    w = Worker(store, {"run": runner}, concurrency=1, poll_s=0.02)
    w.start()
    try:
        assert wait_for(lambda: store.get(job["id"])["status"] == "done")
    finally:
        w.stop()
    assert store.remembered(tmp_path / "out" / "v")["source"] == "x"
