"""Login no TikTok pelo painel: o servidor abre o navegador e o usuário vê/controla a página pelo próprio navegador.

O Chromium roda no servidor (sem janela). A cada ~1 s sai um quadro JPEG da página (o QR code aparece nele); cliques
e texto do usuário são repassados à página, para escolher "QR code", resolver captcha ou entrar com e-mail. Quando
o TikTok grava o cookie `sessionid`, a sessão é salva e o navegador fecha. O QR code é o caminho preferido: nada
digitado passa pelo servidor. Se o usuário digitar e-mail/senha na página remota, as teclas passam pelo servidor
(não são gravadas nem registradas em log).
"""
from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Protocol

from . import accounts, require

log = logging.getLogger(__name__)

LOGIN_URL = "https://www.tiktok.com/login/qrcode"
VIEWPORT = {"width": 1000, "height": 680}
TIMEOUT_S = 600
MAX_ACTIVE = 2
KEEP_FINISHED_S = 120


class Driver(Protocol):
    def open(self, proxy: str | None) -> None: ...
    def screenshot(self) -> bytes: ...
    def click(self, x: float, y: float) -> None: ...
    def type(self, text: str) -> None: ...
    def press(self, key: str) -> None: ...
    def cookies(self) -> list[dict]: ...
    def user_agent(self) -> str: ...
    def wait(self, ms: int) -> None: ...
    def close(self) -> None: ...


class PlaywrightDriver:
    """Chromium sem janela, via as mesmas funções de navegador do autotok (proxy, WebRTC, anti-detecção)."""

    def __init__(self) -> None:
        self._cm: Any = None
        self._handle: Any = None
        self.page: Any = None
        self.ctx: Any = None

    def open(self, proxy: str | None) -> None:
        require()
        from autotok import settings
        from autotok.browsers import open_browser, sync_playwright
        from autotok.proxy import parse_proxy

        self._cm = sync_playwright()
        pw = self._cm.__enter__()
        self._handle = open_browser(pw, headless=True, proxy=parse_proxy(proxy), timeout=TIMEOUT_S + 300)
        if self._handle.is_remote:  # navegador na nuvem: o contexto já vem pronto
            self.ctx = self._handle.context()
            self.page = self._handle.page(self.ctx)
        else:
            self.ctx = self._handle.context(viewport=VIEWPORT, user_agent=settings.DEFAULT_USER_AGENT,
                                            locale="pt-BR")
            self.page = self.ctx.new_page()
        self.page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)

    def screenshot(self) -> bytes:
        return self.page.screenshot(type="jpeg", quality=70)

    def click(self, x: float, y: float) -> None:
        self.page.mouse.click(x, y)

    def type(self, text: str) -> None:
        self.page.keyboard.type(text, delay=20)

    def press(self, key: str) -> None:
        self.page.keyboard.press(key)

    def cookies(self) -> list[dict]:
        return self.ctx.cookies()

    def user_agent(self) -> str:
        return self.page.evaluate("() => navigator.userAgent")

    def wait(self, ms: int) -> None:
        self.page.wait_for_timeout(ms)  # mantém o laço de eventos do Playwright rodando

    def close(self) -> None:
        try:
            if self._handle:
                self._handle.close()
        finally:
            if self._cm:
                try:
                    self._cm.__exit__(None, None, None)
                except Exception:  # noqa: BLE001
                    pass


ALLOWED_KEYS = {"Enter", "Backspace", "Tab", "Escape", "ArrowDown", "ArrowUp", "Delete"}


class LoginSession:
    def __init__(self, name: str, proxy: str | None, root: Path, driver_factory: Callable[[], Driver]):
        self.id = uuid.uuid4().hex[:12]
        self.name, self.proxy, self.root = name, proxy, Path(root)
        self.status = "starting"  # starting | waiting | connected | error | cancelled
        self.error: str | None = None
        self.frame: bytes | None = None
        self.finished_at: float | None = None
        self._cmds: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._stop = threading.Event()
        self._driver_factory = driver_factory
        self._thread = threading.Thread(target=self._run, name=f"tiktok-login-{self.id}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def cancel(self) -> None:
        self._stop.set()

    def command(self, kind: str, arg: Any) -> None:
        self._cmds.put((kind, arg))

    @property
    def active(self) -> bool:
        return self.status in ("starting", "waiting")

    def _apply(self, drv: Driver) -> None:
        while True:
            try:
                kind, arg = self._cmds.get_nowait()
            except queue.Empty:
                return
            try:
                if kind == "click":
                    drv.click(arg[0], arg[1])
                elif kind == "type":
                    drv.type(str(arg)[:200])
                elif kind == "key" and arg in ALLOWED_KEYS:
                    drv.press(arg)
            except Exception:  # noqa: BLE001 - um clique perdido não derruba o login
                log.debug("comando %s falhou", kind, exc_info=True)

    def _run(self) -> None:
        drv = self._driver_factory()
        try:
            drv.open(self.proxy)
            self.status = "waiting"
            t0 = time.monotonic()
            while not self._stop.is_set():
                self._apply(drv)
                self.frame = drv.screenshot()
                cookies = drv.cookies()
                names = {c.get("name") for c in cookies if c.get("value")}
                if {"sessionid", "tt-target-idc"} <= names:
                    self._save(cookies, drv.user_agent())
                    self.status = "connected"
                    return
                if time.monotonic() - t0 > TIMEOUT_S:
                    raise TimeoutError("tempo esgotado: o login não foi concluído em 10 minutos")
                drv.wait(700)
            self.status = "cancelled"
        except Exception as e:  # noqa: BLE001
            self.status, self.error = "error", self._friendly(e)
            log.warning("login do TikTok falhou: %s", e)
        finally:
            self.finished_at = time.time()
            try:
                drv.close()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _friendly(e: Exception) -> str:
        msg = str(e)
        if "Executable doesn't exist" in msg or "playwright install" in msg or "not installed" in msg:
            return ("O navegador do servidor (Chromium) não está instalado. No Docker ele já vem na imagem; "
                    "fora dele rode: python -m playwright install chromium")
        return msg[:300]

    def _save(self, cookies: list[dict], user_agent: str) -> None:
        from autotok import Account
        from autotok.proxy import parse_proxy

        p = parse_proxy(self.proxy)
        tiktok = [c for c in cookies if "tiktok" in str(c.get("domain", ""))]
        acc = Account(name=self.name, cookies=tiktok, user_agent=user_agent, proxy=p.url if p else None)
        accounts.store(self.root).save(acc)

    def view(self) -> dict:
        import base64
        return {"id": self.id, "name": self.name, "status": self.status, "error": self.error,
                "image": ("data:image/jpeg;base64," + base64.b64encode(self.frame).decode()) if self.frame else None,
                "viewport": VIEWPORT}


class LoginManager:
    def __init__(self, driver_factory: Callable[[], Driver] = PlaywrightDriver):
        self._factory = driver_factory
        self._sessions: dict[str, LoginSession] = {}
        self._lock = threading.Lock()

    def _gc(self) -> None:
        now = time.time()
        for sid, s in list(self._sessions.items()):
            if not s.active and s.finished_at and now - s.finished_at > KEEP_FINISHED_S:
                del self._sessions[sid]

    def start(self, root: Path, label: str, proxy: str | None) -> LoginSession:
        require()
        from autotok.accounts import validate_account_name
        from autotok.proxy import parse_proxy

        name = accounts.slugify(label)
        validate_account_name(name)
        parse_proxy(proxy or None)  # erro claro se o proxy estiver mal escrito
        with self._lock:
            self._gc()
            if sum(1 for s in self._sessions.values() if s.active) >= MAX_ACTIVE:
                raise RuntimeError("já há logins em andamento; conclua ou cancele um antes de abrir outro")
            s = LoginSession(name, proxy or None, root, self._factory)
            self._sessions[s.id] = s
        s.start()
        return s

    def get(self, sid: str) -> LoginSession | None:
        return self._sessions.get(sid)
