"""Rotas do TikTok: contas, login remoto (QR), legenda por IA e postagem."""
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from .. import storage, tiktok
from ..tiktok import accounts as tkaccounts, captions as tkcaptions
from ..tiktok.login import VIEWPORT, LoginManager
from ..tiktok.post import PostOptions


class LoginBody(BaseModel):
    name: str
    proxy: str | None = None


class ClickBody(BaseModel):
    x: float  # em pixels do viewport remoto (a interface converte)
    y: float


class TextBody(BaseModel):
    text: str


class KeyBody(BaseModel):
    key: str


class ImportBody(BaseModel):
    name: str
    sessionid: str
    datacenter: str | None = None
    proxy: str | None = None


class CaptionBody(BaseModel):
    review_id: str
    rank: int


class PostBody(BaseModel):
    review_id: str
    rank: int
    options: dict[str, Any]


_browser_ok: dict[str, bool] = {}


def browser_installed() -> bool:
    """O Chromium do Playwright existe nesta máquina? (resultado guardado: a checagem sobe o driver)"""
    if "v" not in _browser_ok:
        try:
            from autotok import settings as tkset
            if tkset.browser_provider() != "local" or tkset.browser_channel() or (tkset.browser_executable() and Path(tkset.browser_executable()).exists()):
                _browser_ok["v"] = True  # usa o Chrome já instalado (AUTOTOK_BROWSER_CHANNEL / _PATH)
                return True
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                _browser_ok["v"] = Path(pw.chromium.executable_path).exists()
        except Exception:  # noqa: BLE001
            _browser_ok["v"] = False
    return _browser_ok["v"]


def register(app: Any, st: Any, login_manager: LoginManager | None = None) -> None:
    from fastapi import HTTPException

    from .api import SELECTION, SCRIPTS, _load, safe_under

    mgr = login_manager or LoginManager()
    app.state.tiktok_login = mgr

    def root() -> Path:
        return storage.tiktok_dir(st.cfg().workspace_dir)

    def need() -> None:
        try:
            tiktok.require()
        except tiktok.TikTokUnavailable as e:
            raise HTTPException(501, str(e)) from e

    @app.get("/api/tiktok")
    def status() -> dict:
        if not tiktok.available():
            return {"available": False, "browser_installed": False, "accounts": []}
        return {"available": True, "browser_installed": browser_installed(), "accounts": tkaccounts.list_accounts(root())}

    # ---------------------------------------------------------------- login remoto
    @app.post("/api/tiktok/login")
    def login_start(body: LoginBody) -> dict:
        need()
        try:
            return mgr.start(root(), body.name, body.proxy).view()
        except (ValueError, RuntimeError) as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:  # noqa: BLE001 - nome/proxy inválidos vêm como erros do autotok
            raise HTTPException(400, str(e)) from e

    def session(sid: str):
        s = mgr.get(sid)
        if s is None:
            raise HTTPException(404, "login não encontrado ou expirado")
        return s

    @app.get("/api/tiktok/login/{sid}")
    def login_view(sid: str) -> dict:
        return session(sid).view()

    @app.post("/api/tiktok/login/{sid}/click")
    def login_click(sid: str, body: ClickBody) -> dict:
        x = min(max(body.x, 0), VIEWPORT["width"])
        y = min(max(body.y, 0), VIEWPORT["height"])
        session(sid).command("click", (x, y))
        return {"ok": True}

    @app.post("/api/tiktok/login/{sid}/type")
    def login_type(sid: str, body: TextBody) -> dict:
        session(sid).command("type", body.text)
        return {"ok": True}

    @app.post("/api/tiktok/login/{sid}/key")
    def login_key(sid: str, body: KeyBody) -> dict:
        session(sid).command("key", body.key)
        return {"ok": True}

    @app.delete("/api/tiktok/login/{sid}")
    def login_cancel(sid: str) -> dict:
        session(sid).cancel()
        return {"ok": True}

    # ---------------------------------------------------------------- contas
    @app.post("/api/tiktok/import")
    def import_session(body: ImportBody) -> dict:
        need()
        try:
            return tkaccounts.import_session(root(), body.name, body.sessionid, body.datacenter, body.proxy)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, str(e)) from e

    @app.post("/api/tiktok/accounts/{name}/check")
    def check_account(name: str) -> dict:
        need()
        try:
            return {"ok": bool(tkaccounts.check(root(), name))}
        except Exception as e:  # noqa: BLE001 - conta inexistente, rede fora...
            raise HTTPException(400, str(e)) from e

    @app.delete("/api/tiktok/accounts/{name}")
    def delete_account(name: str) -> dict:
        need()
        if not tkaccounts.remove(root(), name):
            raise HTTPException(404, "conta não encontrada")
        return {"ok": True}

    # ---------------------------------------------------------------- postagem
    def find_item(review_id: str, rank: int) -> tuple[Path, str, dict]:
        cfg = st.cfg()
        try:
            folder = safe_under(Path(cfg.output_dir), review_id)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        kind = "narration" if (folder / SCRIPTS).exists() and not (folder / SELECTION).exists() else "cuts"
        items = _load(folder / (SCRIPTS if kind == "narration" else SELECTION))
        for i, it in enumerate(items, 1):
            if int(it.get("rank", i)) == rank:
                return folder, kind, it
        raise HTTPException(404, f"vídeo {rank} não existe nessa saída")

    @app.post("/api/tiktok/caption")
    def make_caption(body: CaptionBody) -> dict:
        _, kind, item = find_item(body.review_id, body.rank)
        factory = getattr(st, "caption_client", None)
        client = None
        try:
            from .. import pipeline
            client = factory() if factory else pipeline.make_client(st.cfg())
        except Exception as e:  # noqa: BLE001 - sem chave: devolve o título
            return {"caption": tkcaptions.fallback(item), "ai": False, "reason": str(e)[:160]}
        return {"caption": tkcaptions.generate(client, item, kind), "ai": True}

    @app.post("/api/tiktok/post")
    def post(body: PostBody) -> dict:
        need()
        folder, kind, item = find_item(body.review_id, body.rank)
        file = item.get("file")
        if not file or not (folder / str(file)).is_file():
            raise HTTPException(404, "esse vídeo ainda não foi renderizado")
        try:
            opts = PostOptions(**body.options)
        except ValidationError as e:
            raise HTTPException(400, "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())) from e
        if opts.account not in [a["name"] for a in tkaccounts.list_accounts(root())]:
            raise HTTPException(400, "conta do TikTok não conectada")
        title = item.get("title") or file
        return st.jobs.enqueue("post", st.cfg(), str(folder / str(file)), f"TikTok: {title}",
                               {"post": opts.model_dump(), "review_id": body.review_id, "file": file,
                                "rank": body.rank})

    @app.get("/api/tiktok/posts")
    def posts() -> dict:
        return {"posts": st.jobs.post_jobs()}

