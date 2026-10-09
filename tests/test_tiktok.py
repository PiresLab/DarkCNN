import json
import time
from pathlib import Path

import pytest
import yaml

pytest.importorskip("fastapi")
pytest.importorskip("autotok")
from fastapi.testclient import TestClient  # noqa: E402

import autotok  # noqa: E402
from darkcnn import presets  # noqa: E402
from darkcnn.config import Config  # noqa: E402
from darkcnn.db import Database  # noqa: E402
from darkcnn.tiktok import autopost, captions  # noqa: E402
from darkcnn.tiktok.login import LoginManager  # noqa: E402
from darkcnn.tiktok.post import PostError, PostOptions, normalize_schedule, post_video  # noqa: E402
from darkcnn.web.api import AppState, create_app  # noqa: E402
from darkcnn.web.jobs import JobStore  # noqa: E402


def wait_for(fn, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.03)
    return False


class FakeResult:
    def __init__(self, scheduled=None):
        self.video_id, self.scheduled_for = "7123", scheduled


class FakeUploader:
    """Mesma interface do autotok.Client."""
    calls: list = []
    fail_with: list = []

    def __init__(self, name):
        self.name = name

    def upload(self, video, caption, **kw):
        FakeUploader.calls.append((self.name, Path(video).name, caption, kw))
        if FakeUploader.fail_with:
            raise FakeUploader.fail_with.pop(0)
        return FakeResult()

    def check_session(self):
        return self.name != "velha"


@pytest.fixture(autouse=True)
def fake_client(monkeypatch):
    FakeUploader.calls, FakeUploader.fail_with = [], []
    monkeypatch.setattr(autotok.Client, "from_account", classmethod(lambda cls, name, **kw: FakeUploader(name)))


class FakeCaptionClient:
    def __init__(self, *plans):
        self.plans, self.prompts = list(plans), []

    def generate_json(self, prompt, schema, temperature=0.2, media=None):
        self.prompts.append(prompt)
        return self.plans.pop(0)


def app(tmp_path, runners=None):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump({"workspace_dir": str(tmp_path / "ws"), "output_dir": str(tmp_path / "out"),
                                   "gameplay_dir": str(tmp_path / "games")}), encoding="utf-8")
    st = AppState(cfg, runners=runners, concurrency=2)
    return TestClient(create_app(st)), st


def output(tmp_path, kind="narration", n=2):
    folder = tmp_path / "out" / "narrate" / "abc"
    folder.mkdir(parents=True, exist_ok=True)
    items = []
    for i in range(1, n + 1):
        (folder / f"{i:02d}_v.mp4").write_bytes(b"\x00")
        it = {"rank": i, "title": f"Título {i}", "file": f"{i:02d}_v.mp4"}
        if kind == "narration":
            it["lines"] = [{"text": "Fato curioso.", "kind": "fala"}]
        else:
            it.update(hook_text="gancho", context="ctx")
        items.append(it)
    (folder / ("scripts.json" if kind == "narration" else "selection.json")).write_text(json.dumps(items))
    (folder / "review.md").write_text("# r")
    return folder


# ---------------------------------------------------------------- contas
def test_import_list_check_and_remove_account(tmp_path):
    c, _ = app(tmp_path)
    assert c.get("/api/tiktok").json()["available"] is True
    r = c.post("/api/tiktok/import", json={"name": "Meu Canal!", "sessionid": "x" * 32, "datacenter": "useast2a"})
    assert r.status_code == 200 and r.json()["name"] == "Meu-Canal"
    accs = c.get("/api/tiktok").json()["accounts"]
    assert [a["name"] for a in accs] == ["Meu-Canal"] and accs[0]["connected"] is True
    assert c.post("/api/tiktok/accounts/Meu-Canal/check").json() == {"ok": True}
    assert c.post("/api/tiktok/accounts/velha/check").json() == {"ok": False}
    assert c.delete("/api/tiktok/accounts/Meu-Canal").status_code == 200
    assert c.delete("/api/tiktok/accounts/Meu-Canal").status_code == 404
    assert c.post("/api/tiktok/import", json={"name": "x", "sessionid": " "}).status_code == 400


# ---------------------------------------------------------------- login remoto
class FakeDriver:
    def __init__(self, login_after=3, fail_open=None):
        self.n, self.login_after, self.fail_open = 0, login_after, fail_open
        self.clicks, self.typed, self.keys, self.closed = [], [], [], False

    def open(self, proxy):
        if self.fail_open:
            raise RuntimeError(self.fail_open)

    def screenshot(self):
        self.n += 1
        return b"\xff\xd8fakejpeg"

    def click(self, x, y):
        self.clicks.append((x, y))

    def type(self, text):
        self.typed.append(text)

    def press(self, key):
        self.keys.append(key)

    def cookies(self):
        if self.n < self.login_after:
            return [{"name": "other", "value": "1", "domain": ".tiktok.com"}]
        return [{"name": "sessionid", "value": "abc" * 10, "domain": ".tiktok.com"},
                {"name": "tt-target-idc", "value": "useast2a", "domain": ".tiktok.com"},
                {"name": "foreign", "value": "1", "domain": ".example.com"}]

    def user_agent(self):
        return "UA/1.0"

    def wait(self, ms):
        time.sleep(0.01)

    def close(self):
        self.closed = True


def test_login_session_streams_frames_forwards_input_and_saves_the_account(tmp_path):
    drv = FakeDriver(login_after=40)
    mgr = LoginManager(lambda: drv)
    s = mgr.start(tmp_path / "tk", "Meu Canal", None)
    assert wait_for(lambda: s.frame is not None)
    s.command("click", (10, 20))
    s.command("type", "oi")
    s.command("key", "Enter")
    s.command("key", "F12")  # tecla fora da lista: ignorada
    assert wait_for(lambda: s.status == "connected")
    assert drv.clicks == [(10, 20)] and drv.typed == ["oi"] and drv.keys == ["Enter"] and drv.closed
    assert s.view()["image"].startswith("data:image/jpeg;base64,")
    from darkcnn.tiktok import accounts
    acc = accounts.store(tmp_path / "tk").load("Meu-Canal")
    assert acc.session_id and acc.datacenter == "useast2a" and acc.user_agent == "UA/1.0"
    assert all("tiktok" in c["domain"] for c in acc.cookies)  # cookies de outros sites não são guardados


def test_login_can_be_cancelled_and_reports_driver_errors(tmp_path):
    mgr = LoginManager(lambda: FakeDriver(login_after=10**9))
    s = mgr.start(tmp_path / "tk", "a", None)
    assert wait_for(lambda: s.status == "waiting")
    s.cancel()
    assert wait_for(lambda: s.status == "cancelled")

    bad = LoginManager(lambda: FakeDriver(fail_open="Executable doesn't exist at /x"))
    e = bad.start(tmp_path / "tk", "b", None)
    assert wait_for(lambda: e.status == "error")
    assert "Chromium" in e.error and "playwright install" in e.error


def test_login_routes_and_limits(tmp_path):
    mgr = LoginManager(lambda: FakeDriver(login_after=10**9))
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump({"workspace_dir": str(tmp_path / "ws"), "output_dir": str(tmp_path / "out")}))
    st = AppState(cfg)
    from fastapi import FastAPI
    from darkcnn.web import routes_tiktok
    fa = FastAPI()
    routes_tiktok.register(fa, st, mgr)
    c = TestClient(fa)
    a = c.post("/api/tiktok/login", json={"name": "um"}).json()
    c.post("/api/tiktok/login", json={"name": "dois"})
    third = c.post("/api/tiktok/login", json={"name": "tres"})
    assert third.status_code == 400 and "em andamento" in third.json()["detail"]
    assert c.post(f"/api/tiktok/login/{a['id']}/click", json={"x": 5000, "y": -4}).status_code == 200
    assert c.get("/api/tiktok/login/nao-existe").status_code == 404
    assert c.post("/api/tiktok/login", json={"name": "x", "proxy": "isso nao e proxy"}).status_code in (400,)
    c.delete(f"/api/tiktok/login/{a['id']}")


# ---------------------------------------------------------------- postar
def test_schedule_rules():
    assert normalize_schedule(None) is None and normalize_schedule(0) is None
    assert normalize_schedule(60) == 900 and normalize_schedule(7200) == 7200


def test_post_maps_errors_and_retries_only_when_safe(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00")
    opts = PostOptions(account="a", caption="oi #x")

    FakeUploader.fail_with = [autotok.UploadError("rede"), autotok.SigningError("assinatura")]
    waits = []
    res = post_video(tmp_path, video, opts, sleep=waits.append)
    assert res.video_id == "7123" and len(FakeUploader.calls) == 3 and len(waits) == 2

    FakeUploader.fail_with = [autotok.PublishUncertainError("caiu")]
    with pytest.raises(PostError) as e:
        post_video(tmp_path, video, opts, sleep=waits.append)
    assert e.value.uncertain and len(FakeUploader.calls) == 4  # incerto: nunca repete sozinho

    FakeUploader.fail_with = [autotok.PublishError("x", status_msg="viola diretrizes")]
    with pytest.raises(PostError, match="viola diretrizes"):
        post_video(tmp_path, video, opts)
    FakeUploader.fail_with = [autotok.NotLoggedInError("exp")]
    with pytest.raises(PostError, match="reconecte"):
        post_video(tmp_path, video, opts)
    with pytest.raises(PostError, match="privados"):
        post_video(tmp_path, video, PostOptions(account="a", caption="x", visibility="private", schedule_s=3600))


def test_manual_post_runs_through_the_queue_and_shows_in_posts(tmp_path):
    c, st = app(tmp_path)
    output(tmp_path)
    c.post("/api/tiktok/import", json={"name": "canal", "sessionid": "x" * 30})
    ok = c.post("/api/tiktok/post", json={"review_id": "narrate/abc", "rank": 2, "options": {
        "account": "canal", "caption": "Olha isso #curiosidades", "ai_label": True, "schedule_s": 3600}})
    assert ok.status_code == 200 and ok.json()["mode"] == "post"
    assert wait_for(lambda: st.jobs.get(ok.json()["id"])["status"] == "done")
    name, file, caption, kw = FakeUploader.calls[0]
    assert (name, file, caption) == ("canal", "02_v.mp4", "Olha isso #curiosidades")
    assert kw["ai_label"] is True and kw["schedule"] == 3600 and kw["visibility"] == "public"
    post = c.get("/api/tiktok/posts").json()["posts"][0]
    assert post["review_id"] == "narrate/abc" and post["file"] == "02_v.mp4" and post["video_id"] == "7123"
    # o histórico de re-renderização da saída não é contaminado pela postagem
    assert st.jobs.remembered(tmp_path / "out" / "narrate" / "abc") == {}

    bad = c.post("/api/tiktok/post", json={"review_id": "narrate/abc", "rank": 1, "options": {"account": "outra", "caption": "x"}})
    assert bad.status_code == 400 and "não conectada" in bad.json()["detail"]
    assert c.post("/api/tiktok/post", json={"review_id": "narrate/abc", "rank": 9, "options": {"account": "canal", "caption": "x"}}).status_code == 404
    assert c.post("/api/tiktok/post", json={"review_id": "..%2F..", "rank": 1, "options": {"account": "canal", "caption": "x"}}).status_code in (400, 404)


def test_failed_post_is_reported_in_the_job(tmp_path):
    c, st = app(tmp_path)
    output(tmp_path)
    c.post("/api/tiktok/import", json={"name": "canal", "sessionid": "x" * 30})
    FakeUploader.fail_with = [autotok.PublishUncertainError("caiu")]
    job = c.post("/api/tiktok/post", json={"review_id": "narrate/abc", "rank": 1, "options": {"account": "canal", "caption": "x"}}).json()
    assert wait_for(lambda: st.jobs.get(job["id"])["status"] == "error")
    post = c.get("/api/tiktok/posts").json()["posts"][0]
    assert post["uncertain"] is True and "pode ou não" in post["error"]


# ---------------------------------------------------------------- legenda por IA
def test_caption_generation_and_fallbacks(tmp_path):
    plan = captions.CaptionPlan(caption="Você nunca vai adivinhar isso", hashtags=["#Ciência", "curiosidades", "ciencia", "x y"])
    out = captions.compose(plan.caption, plan.hashtags)
    assert out == "Você nunca vai adivinhar isso #Ciencia #curiosidades #xy"  # sem acento, sem duplicata
    item = {"title": "Buracos negros", "lines": [{"text": "Fato."}]}
    client = FakeCaptionClient(plan)
    assert captions.generate(client, item, "narration", "ciência").startswith("Você nunca")
    assert "Buracos negros" in client.prompts[0] and "ciência" in client.prompts[0]

    class Boom:
        def generate_json(self, *a, **k):
            raise RuntimeError("cota")
    assert captions.generate(Boom(), item, "narration") == "Buracos negros #curiosidades #voceSabia"

    c, st = app(tmp_path)
    output(tmp_path)
    st.caption_client = lambda: FakeCaptionClient(plan)
    r = c.post("/api/tiktok/caption", json={"review_id": "narrate/abc", "rank": 1}).json()
    assert r["ai"] is True and r["caption"].startswith("Você nunca")


# ---------------------------------------------------------------- automação
def test_automation_enqueues_staggered_posts(tmp_path):
    db = Database(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    db.init()
    store = JobStore(db)
    folder = output(tmp_path, n=3)
    cfg = Config(workspace_dir=tmp_path / "ws", output_dir=tmp_path / "out", tiktok_base_tags=[])
    spec = presets.PresetSpec(tiktok=presets.TikTokSpec(enabled=True, account="canal", delay_min=30, stagger_min=45))
    plans = [captions.CaptionPlan(caption=f"Legenda {i}", hashtags=["curiosidades"]) for i in range(3)]
    jobs = autopost.enqueue_posts(store, cfg, spec, str(folder / "review.md"), "p1",
                                  client_factory=lambda: FakeCaptionClient(*plans))
    assert [j["mode"] for j in jobs] == ["post"] * 3
    posts = [store.get(j["id"])["params"] for j in jobs]
    assert [p["post"]["schedule_s"] for p in posts] == [1800, 1800 + 2700, 1800 + 5400]
    assert posts[0]["post"]["caption"] == "Legenda 0 #curiosidades" and posts[0]["review_id"] == "narrate/abc"
    assert all(p["post"]["ai_label"] is True for p in posts)

    now = presets.TikTokSpec(enabled=True, account="canal", visibility="private", caption_ai=False)
    j = autopost.enqueue_posts(store, cfg, presets.PresetSpec(tiktok=now), str(folder / "review.md"), None)
    p = store.get(j[1]["id"])["params"]["post"]
    assert p["schedule_s"] is None and p["caption"].startswith("Título 2")  # privado não agenda; sem IA usa o título

    assert autopost.enqueue_posts(store, cfg, presets.PresetSpec(), str(folder / "review.md"), None) == []


def test_cuts_are_not_labeled_as_ai(tmp_path):
    db = Database(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    db.init()
    folder = output(tmp_path, kind="cuts", n=1)
    cfg = Config(workspace_dir=tmp_path / "ws", output_dir=tmp_path / "out")
    spec = presets.PresetSpec(mode="run", tiktok=presets.TikTokSpec(enabled=True, account="c", caption_ai=False))
    jobs = autopost.enqueue_posts(JobStore(db), cfg, spec, str(folder / "review.md"), None)
    assert JobStore(db).get(jobs[0]["id"])["params"]["post"]["ai_label"] is False


def test_finished_automation_queues_the_tiktok_posts(tmp_path):
    posted = []

    def auto(source, cfg, force):
        return output(tmp_path) / "review.md"

    def post(source, cfg, force):
        posted.append(Path(source).name)
        return Path(source)

    c, st = app(tmp_path, runners={"auto": auto, "post": post})
    spec = {"narrate_format": "curiosidade", "tiktok": {"enabled": True, "account": "canal", "caption_ai": False}}
    assert c.post("/api/runs", json={"spec": {"tiktok": {"enabled": True}}}).status_code == 400  # sem conta
    job = c.post("/api/runs", json={"name": "t", "spec": spec}).json()
    assert wait_for(lambda: st.jobs.get(job["id"])["status"] == "done")
    assert wait_for(lambda: sorted(posted) == ["01_v.mp4", "02_v.mp4"])
    posts = c.get("/api/tiktok/posts").json()["posts"]
    assert {p["file"] for p in posts} == {"01_v.mp4", "02_v.mp4"} and all(p["account"] == "canal" for p in posts)


# ---------------------------------------------------------------- navegador real (pula se não houver Chromium/Chrome)
@pytest.fixture
def real_driver(monkeypatch):
    """PlaywrightDriver de verdade: usa o Chromium do Playwright ou, na falta dele, o Chrome instalado."""
    pytest.importorskip("playwright")
    from darkcnn.tiktok.login import PlaywrightDriver

    last = None
    for channel in (None, "chrome"):
        if channel:
            monkeypatch.setenv("AUTOTOK_BROWSER_CHANNEL", channel)
        d = PlaywrightDriver()
        try:
            d.launch(None)
        except Exception as e:  # noqa: BLE001
            last = e
            d.close()
            continue
        yield d
        d.close()
        return
    pytest.skip(f"sem navegador para o Playwright: {str(last)[:80]}")


PAGE = """<body style='margin:0'>
<input id=a style='position:absolute;left:300px;top:200px;width:200px;height:40px'>
<iframe srcdoc="<input id=b style='width:180px;height:36px'>" style='position:absolute;left:300px;top:400px;width:260px;height:80px'></iframe>
</body>"""


def test_real_click_then_type_reaches_main_page_and_iframe_fields(real_driver):
    d = real_driver
    d.page.set_content(PAGE)
    d.click(400, 220)
    d.type("123456")
    assert d.page.evaluate("document.getElementById('a').value") == "123456"
    d.click(400, 428)
    d.type("654321")
    assert d.page.frames[1].evaluate("document.getElementById('b').value") == "654321"


def test_real_type_without_click_focuses_the_first_visible_field(real_driver):
    d = real_driver
    d.page.set_content("<body><iframe srcdoc=\"<input id=b>\" style='width:260px;height:80px'></iframe></body>")
    d.wait(300)
    d.type("987654")  # nada em foco: o driver acha o campo, inclusive dentro do iframe
    assert d.page.frames[1].evaluate("document.getElementById('b').value") == "987654"


def test_real_new_window_becomes_the_page_shown_and_closing_returns(real_driver):
    d = real_driver
    d.page.set_content("<a id=l href='about:blank' target=_blank>x</a><button id=o onclick=\"window.open('about:blank','v')\">abrir</button>")
    first = d.page
    d.page.click("#o")
    d.wait(800)
    assert d.page is not first
    d.page.close()
    d.wait(300)
    assert d.page is first


# ---------------------------------------------------------------- recusa do TikTok
def invalid_params():
    return autotok.PublishError("TikTok rejected the post: Invalid parameters (status_code=5)", status_code=5,
                                status_msg="Invalid parameters", response={"status_code": 5})


def test_invalid_parameters_is_reported_with_code_and_response_in_the_log(tmp_path, caplog):
    import logging
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00")
    FakeUploader.fail_with = [invalid_params()]
    with caplog.at_level(logging.WARNING, logger="darkcnn.tiktok.post"), pytest.raises(PostError) as e:
        post_video(tmp_path, video, PostOptions(account="a", caption="oi", ai_label=True))
    assert "código 5" in str(e.value) and len(FakeUploader.calls) == 1  # rejeição não é repetida às cegas
    assert FakeUploader.calls[0][3]["ai_label"] is True  # o rótulo de IA nunca é removido
    assert any("status_code=5" in r.message and "resposta=" in r.message for r in caplog.records)


def test_post_logs_a_summary_of_what_is_sent(tmp_path, caplog):
    import logging
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00" * 1000)
    with caplog.at_level(logging.INFO, logger="darkcnn.tiktok.post"):
        post_video(tmp_path, video, PostOptions(account="canal", caption="oi #a #b", schedule_s=3600))
    msg = next(r.message for r in caplog.records if "enviando" in r.message)
    assert "conta=canal" in msg and "agendado=sim" in msg and "2 hashtags" in msg and "rótulo_IA=True" in msg


# ---------------------------------------------------------------- corpo de publicação do Studio
def test_studio_client_sends_the_body_tiktok_studio_sends_today():
    from darkcnn.tiktok.client import StudioClient
    c = StudioClient(autotok.Account(name="a", cookies=[{"name": "sessionid", "value": "x", "domain": ".tiktok.com"}]))
    c.video_meta = {"width": 1080, "height": 1920}
    c.allow_content_reuse, c.allow_ai_remix = True, True
    d = c._payload("cid", "vid", "oi", "oi", [], 0, True, False, False, True)
    priv = d["feature_common_info_list"][0]["privacy_setting_info"]
    assert priv == {"visibility_type": 0, "allow_duet": 0, "allow_stitch": 0, "allow_comment": 1,
                    "allow_content_reuse": 1, "allow_ai_remix": 1}
    assert d["feature_common_info_list"][0]["aigc_info"] == {"aigc_label_type": 1}
    post = d["single_post_req_list"][0]["single_post_feature_info"]
    assert (post["cloud_edit_video_width"], post["cloud_edit_video_height"]) == (1080, 1920)
    assert post["has_original_audio"] == 1 and post["is_upload_audio_track"] is False
    assert post["cloud_edit_is_use_video_canvas"] is False

    c.allow_content_reuse, c.allow_ai_remix = False, False
    d = c._payload("cid", "vid", "oi", "oi", [], 0, True, False, False, False)
    assert d["feature_common_info_list"][0]["privacy_setting_info"]["allow_ai_remix"] == 2  # 2 = não permitir
    assert d["feature_common_info_list"][0]["privacy_setting_info"]["allow_content_reuse"] == 0
    assert "aigc_info" not in d["feature_common_info_list"][0]


def test_post_video_configures_the_client_with_the_options(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00")
    seen = []

    class Configurable(FakeUploader):
        def configure(self, *, allow_content_reuse, allow_ai_remix, video):
            seen.append((allow_content_reuse, allow_ai_remix, Path(video).name))

    post_video(tmp_path, video, PostOptions(account="a", caption="oi", allow_ai_remix=False),
               client_factory=lambda name: Configurable(name))
    assert seen == [(True, False, "v.mp4")]


def test_post_options_default_to_studio_defaults_and_flow_from_automation():
    o = PostOptions(account="a", caption="x")
    assert o.allow_content_reuse is True and o.allow_ai_remix is True
    t = presets.TikTokSpec(enabled=True, account="a", allow_ai_remix=False)
    opts = autopost.post_params({"title": "t"}, "narration", presets.PresetSpec(tiktok=t), 0, "oi")
    assert opts.allow_ai_remix is False and opts.allow_content_reuse is True


# ---------------------------------------------------------------- hashtags de alcance + do assunto
def test_compose_puts_reach_tags_first_then_the_subject_tags_without_duplicates():
    import random
    base = ["fy", "fyp", "foryou", "parati", "viral"]
    out = captions.compose("Você sabia?", ["#Ciência", "fyp", "internet", "tecnologia", "extra"], base, random.Random(1))
    tags = out.split(" ", 2)[2].split()  # depois de "Você sabia?"
    assert len(tags) == 6 and len(set(t.lower() for t in tags)) == 6  # teto de 6, sem repetir
    assert {t[1:] for t in tags[:3]} <= set(base)  # as 3 primeiras são de alcance
    assert "#Ciencia" in tags[3:] and len([t for t in tags if t.lower() == "#fyp"]) <= 1  # "fyp" da IA não repete a base


def test_compose_with_short_or_empty_base_list():
    import random
    assert captions.compose("oi", ["a", "b", "c"], [], random.Random(0)) == "oi #a #b #c"
    out = captions.compose("oi", ["a"], ["fy", "#fyy"], random.Random(0))
    assert sorted(out.split()[1:3]) == ["#fy", "#fyy"] and out.endswith("#a")  # base curta entra inteira


def test_compose_keeps_emoji_in_the_caption():
    assert captions.compose("Final chocante \U0001F631", ["x"]) == "Final chocante \U0001F631 #x"


def test_generate_and_fallback_use_the_configured_reach_tags():
    import random
    plan = captions.CaptionPlan(caption="Olha só", hashtags=["internet", "apocalipse", "tecnologia"])
    item = {"title": "Internet fora", "lines": [{"text": "Fato."}]}
    out = captions.generate(FakeCaptionClient(plan), item, "narration", None, ["fy", "fyp", "viral"], random.Random(3))
    tags = out.split()[2:]
    assert sorted(tags[:3]) == ["#fy", "#fyp", "#viral"] and tags[3:] == ["#internet", "#apocalipse", "#tecnologia"]
    fb = captions.fallback(item, ["fy", "fyp", "viral"], random.Random(3))
    assert fb.startswith("Internet fora #") and "#curiosidades" in fb and "#fy" in fb


def test_prompt_asks_for_three_subject_hashtags_and_no_reach_tags():
    from darkcnn.analyze import PROMPTS_DIR
    prompt = (PROMPTS_DIR / "caption_v1.md").read_text(encoding="utf-8")
    assert "exatamente 3 hashtags" in prompt and "NÃO inclua hashtags de alcance" in prompt


def test_base_tags_are_saved_through_the_config_api(tmp_path):
    c, st = app(tmp_path)
    assert c.get("/api/config").json()["effective"]["tiktok_base_tags"] == ["fy", "fyp", "foryou", "parati", "viral"]
    assert c.put("/api/config", json={"values": {"tiktok_base_tags": ["fy", "fyy", "xyzbca"]}}).status_code == 200
    assert st.cfg().tiktok_base_tags == ["fy", "fyy", "xyzbca"]
    # a legenda sugerida no modal usa a lista salva
    output(tmp_path)
    plan = captions.CaptionPlan(caption="Olha", hashtags=["internet"])
    st.caption_client = lambda: FakeCaptionClient(plan)
    cap = c.post("/api/tiktok/caption", json={"review_id": "narrate/abc", "rank": 1}).json()["caption"]
    assert {"#fy", "#fyy", "#xyzbca"} <= set(cap.split()) and cap.endswith("#internet")
