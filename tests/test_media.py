

def test_kill_thread_process_stops_a_running_ffmpeg(tmp_path):
    import threading
    from darkcnn import media

    started = threading.Event()
    result = {}

    def work():
        started.set()
        try:
            media.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                       "-i", "testsrc2=size=320x240:rate=25:duration=120", str(tmp_path / "longo.mp4")])
            result["out"] = "terminou"
        except media.MediaError as e:
            result["out"] = f"morreu: {e}"

    t = threading.Thread(target=work)
    t.start()
    started.wait(5)
    assert wait_until(lambda: media.kill_thread_process(t.ident), 5), "o processo não apareceu no registro"
    t.join(20)
    assert result["out"].startswith("morreu")
    assert media.kill_thread_process(t.ident) is False  # nada mais rodando nessa thread


def wait_until(fn, timeout):
    import time
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.05)
    return False
