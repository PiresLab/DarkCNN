import functools
import http.server
import threading

import pytest

from conftest import YT_INFO, make_fake_ydl, make_video, needs_ffmpeg
from darkcnn.config import Config
from darkcnn.ingest import IngestError, apply_meta, is_cc, is_url, resolve_input
from darkcnn.review import license_warnings

URL = "https://youtu.be/IALW8WPhUQ4?si=abc"


def test_is_url_and_is_cc():
    assert is_url("https://youtu.be/x") and is_url("HTTP://a.b") and not is_url("video.mp4")
    assert not is_url("C:\\videos\\a.mp4")
    assert is_cc("Creative Commons Attribution license (reuse allowed)") and not is_cc(None) and not is_cc("")


def test_local_file_missing_and_other_schemes(tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")
    ing = resolve_input(str(f), Config())
    assert ing.path == f.resolve() and ing.meta == {}
    with pytest.raises(IngestError, match="não encontrado"):
        resolve_input(str(tmp_path / "nao.mp4"), Config())
    with pytest.raises(IngestError, match="só links http"):
        resolve_input("ftp://servidor/x.mp4", Config())
    with pytest.raises(IngestError, match="só links http"):
        resolve_input("file:///etc/passwd", Config())


@needs_ffmpeg
def test_url_downloads_once_then_reuses_without_network(tmp_path):
    cfg = Config(workspace_dir=tmp_path / "ws")
    ydl = make_fake_ydl({**YT_INFO, "license": "Creative Commons Attribution license (reuse allowed)"}, video_dur=3)
    a = resolve_input(URL, cfg, ydl)
    assert a.path.exists() and a.path.parent.parent == cfg.workspace_dir / "downloads"
    assert a.meta["title"] == "Papo sobre a vida" and a.meta["channel"] == "Canal Teste"
    assert is_cc(a.meta["license"]) and ydl.downloads == 1
    b = resolve_input(URL, cfg, ydl)
    assert b.path == a.path and b.meta == a.meta
    assert ydl.instances == 1 and ydl.downloads == 1  # 2ª vez: nenhuma instância nova, nada baixado


@needs_ffmpeg
def test_ydl_options(tmp_path):
    captured = {}
    base = make_fake_ydl(YT_INFO, video_dur=2)

    class Spy(base):
        def __init__(self, opts):
            super().__init__(opts)
            captured.update(opts)

    resolve_input(URL, Config(workspace_dir=tmp_path / "ws"), Spy)
    assert captured["noplaylist"] is True and captured["merge_output_format"] == "mp4"
    assert "height<=?1080" in captured["format"]  # `<=?`: não descarta formato sem altura and captured["outtmpl"].endswith("video.%(ext)s")


@pytest.mark.parametrize("info,msg", [
    ({"_type": "playlist", "entries": [1, 2]}, "playlist"),
    ({**YT_INFO, "is_live": True}, "ao vivo"),
    ({**YT_INFO, "live_status": "is_upcoming"}, "ao vivo"),
    ({**YT_INFO, "duration": 4 * 3600}, "limite"),
    (None, "não devolveu"),
])
def test_rejected_inputs_never_download(tmp_path, info, msg):
    ydl = make_fake_ydl(info)
    with pytest.raises(IngestError, match=msg):
        resolve_input(URL, Config(workspace_dir=tmp_path / "ws"), ydl)
    assert ydl.downloads == 0


def test_download_failure_has_actionable_message(tmp_path):
    class Boom(make_fake_ydl(YT_INFO)):
        def extract_info(self, url, download=False):
            raise RuntimeError("Private video. Sign in if you've been granted access")

    with pytest.raises(IngestError, match=r"Private video.*pip install -U yt-dlp"):
        resolve_input(URL, Config(workspace_dir=tmp_path / "ws"), Boom)


def test_download_without_output_file_is_an_error(tmp_path):
    class NoFile(make_fake_ydl(YT_INFO)):
        def download(self, urls):
            pass

    with pytest.raises(IngestError, match="não achei o arquivo"):
        resolve_input(URL, Config(workspace_dir=tmp_path / "ws"), NoFile)


def test_apply_meta_keeps_user_values_and_flags_standard_license():
    cfg = Config()
    assert apply_meta(cfg, {}) is cfg  # arquivo local: nada muda
    std = apply_meta(cfg, {"webpage_url": "https://y/watch?v=1", "license": None})
    assert std.source == "https://y/watch?v=1" and "padrão do YouTube" in std.license
    cc = apply_meta(cfg, {"webpage_url": "u", "license": "Creative Commons Attribution license (reuse allowed)"})
    assert is_cc(cc.license)
    mine = apply_meta(Config(source="meu rótulo", license="autorizado por X"), {"webpage_url": "u", "license": None})
    assert mine.source == "meu rótulo" and mine.license == "autorizado por X"


def test_license_warnings():
    assert license_warnings("Licença padrão do YouTube (não é Creative Commons)")[0].startswith("licença padrão")
    assert any("ND" in w for w in license_warnings("CC-BY-ND 4.0"))
    assert any("NC" in w for w in license_warnings("CC BY-NC-SA"))
    assert any("NC" in w for w in license_warnings("Creative Commons Non-Commercial"))
    assert license_warnings("Creative Commons Attribution license (reuse allowed)") == []
    assert license_warnings("CC-BY 4.0") == [] and license_warnings(None) == []


@needs_ffmpeg
def test_real_ytdlp_downloads_from_local_http_server(tmp_path, monkeypatch):
    """yt-dlp de verdade (extrator genérico) baixando um mp4 de um servidor local: valida opções,
    merge/nome do arquivo e o caminho final, sem depender do YouTube."""
    pytest.importorskip("yt_dlp")
    for v in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(v, raising=False)
    site = tmp_path / "site"
    site.mkdir()
    make_video(site / "clip.mp4", dur=3)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(site))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        cfg = Config(workspace_dir=tmp_path / "ws")
        ing = resolve_input(f"http://127.0.0.1:{srv.server_port}/clip.mp4", cfg)
    finally:
        srv.shutdown()
    assert ing.path.exists() and ing.path.stat().st_size > 1000 and ing.path.name.startswith("video.")
    assert ing.meta["id"] and ing.meta["webpage_url"].endswith("clip.mp4")
    from darkcnn.media import video_info
    assert video_info(ing.path)["duration"] == pytest.approx(3, abs=0.3)
