"""The Ingestion extension's web service: REST API and MCP first, plus the pipelines page."""

import asyncio
import logging
import re
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from . import api
from .auth import COOKIE, BrowserLogin, TokenVerifier
from .bridge_client import BridgeClient
from .config import VERSION, Settings
from .errors import IngestionError
from .mcp_server import create_mcp, mount
from .runs import RunManager
from .scheduler import Scheduler
from .secrets import Secrets
from .services import Services
from .store import Store

PUBLIC = Path(__file__).parent / "public"
LOG = logging.getLogger(__name__)
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


def validation_text(errors):
    first = errors[0] if errors else {}
    field = ".".join(str(p) for p in first.get("loc", [])[1:]) or "request"
    return f"Check {field}: {first.get('msg', 'invalid input')}."


def create_app(settings=None, *, bridge=None, verifier=None, runs=None, store=None):
    settings = settings or Settings.from_env()
    store = store or Store(settings.state_dir / "state.sqlite3")
    bridge = bridge or BridgeClient(settings)
    verifier = verifier or TokenVerifier(settings)
    secrets = Secrets(settings.secret_key)
    runs = runs or RunManager(settings, store, bridge, secrets)
    services = Services(settings, store, bridge, runs, secrets)
    login = BrowserLogin(settings, verifier)
    scheduler = Scheduler(services)
    mcp = create_mcp(settings, services, verifier)
    state = {"bridge": "unknown"}

    async def check_bridge():
        for attempt in range(60):
            try:
                await bridge.discovery(refresh=True)
                state["bridge"] = "connected"
                return
            except IngestionError as exc:
                state["bridge"] = exc.code
                if exc.code == "incompatible_contract":
                    LOG.error("%s", exc)
                    return
            await asyncio.sleep(min(30, 2 + attempt))

    @asynccontextmanager
    async def lifespan(app):
        runs.recover()
        tasks = [asyncio.create_task(scheduler.loop()), asyncio.create_task(check_bridge())]
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(mcp_running())
            try:
                yield
            finally:
                for task in tasks:
                    task.cancel()
                await runs.close()
                await bridge.close()
                store.close()

    app = FastAPI(title="Ingestion (dlt) · Iceberg Data Platform", version=VERSION, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url="/api/v1/openapi.json",
                  description="Declarative dlt pipelines that load REST APIs, databases and files into your teams' "
                              "Iceberg databases, with runs and schedules. Authenticate with a Keycloak bearer token "
                              "for the iceberg-bridge audience (client ext-ingestion-mcp), or sign in with the "
                              "browser. Every operation is also an MCP tool at /mcp.")
    app.state.services = services
    mcp_running = mount(app, mcp, settings)

    @app.exception_handler(IngestionError)
    async def ingestion_error(request, exc):
        return JSONResponse(exc.body(), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return JSONResponse({"error": validation_text(exc.errors()), "code": "invalid_request"}, status_code=422)

    @app.middleware("http")
    async def headers(request: Request, call_next):
        try:
            response = await call_next(request)
        except Exception:
            LOG.exception("Request failed")
            response = JSONResponse({"error": "Something went wrong.", "code": "error"}, status_code=500)
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith(("/api/", "/auth/")):
            response.headers["Cache-Control"] = "no-store"
        else:
            # The page's own files: revalidate every time (ETag/Last-Modified), so an update shows at once.
            response.headers.setdefault("Cache-Control", "no-cache")
        return response

    async def caller(request: Request):
        header = request.headers.get("authorization", "")
        if header[:7].lower() == "bearer ":
            return await verifier.verify(header[7:].strip())
        api.require_json_write(request)
        return await login.caller(request.cookies.get(COOKIE))

    app.include_router(api.router(services, caller))

    @app.get("/api/health", include_in_schema=False)
    def health():
        return {"status": "ok", "bridge": state["bridge"], "version": VERSION}

    @app.get("/api/session", include_in_schema=False)
    async def session(request: Request):
        try:
            caller_ = await login.caller(request.cookies.get(COOKIE))
        except IngestionError:
            return {"authenticated": False, "loginUrl": "/auth/login"}
        return {"authenticated": True, "user": caller_.name}

    @app.get("/auth/login", include_in_schema=False)
    def auth_login(return_to: str = "/"):
        return RedirectResponse(login.start(return_to), status_code=303)

    @app.get("/auth/callback", include_in_schema=False)
    async def auth_callback(state: str = "", code: str = ""):
        try:
            session_id, target = await login.finish(state, code)
        except IngestionError:
            return RedirectResponse("/?signin=failed", status_code=303)
        response = RedirectResponse(target, status_code=303)
        response.set_cookie(COOKIE, session_id, max_age=28800, httponly=True, secure=settings.secure, samesite="lax")
        return response

    @app.post("/auth/logout", include_in_schema=False)
    def auth_logout(request: Request):
        api.require_json_write(request)
        target = login.logout(request.cookies.get(COOKIE))
        response = JSONResponse({"logoutUrl": target})
        response.delete_cookie(COOKIE, httponly=True, secure=settings.secure, samesite="lax")
        return response

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(PUBLIC / "index.html")

    @app.get("/fonts/{name}", include_in_schema=False)
    def fonts(name: str):
        if not re.match(r"^[a-z0-9-]+\.ttf$", name) or not (PUBLIC / "fonts" / name).is_file():
            raise IngestionError(404, "Not found.", "not_found")
        return FileResponse(PUBLIC / "fonts" / name, headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/{name}", include_in_schema=False)
    def static(name: str):
        if not re.match(r"^[a-z0-9-]+\.(js|css|svg)$", name) or not (PUBLIC / name).is_file():
            raise IngestionError(404, "Not found.", "not_found")
        return FileResponse(PUBLIC / name)

    return app
