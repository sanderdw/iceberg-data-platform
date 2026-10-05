"""The Conversational BI gateway: sign-in, REST API and MCP, the chat UI, and its path to the agent.

Browser → gateway (session cookie) → CopilotKit runtime (internal network, holds no credentials)
→ agent listener (run ticket) → Bridge, Polaris and the query worker (the team's read token).
"""

import asyncio
import json
import logging
import re
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from . import api
from .agent.agui import create_agent_app
from .agent.models import resolve_model
from .auth import COOKIE, BrowserLogin, TokenVerifier
from .bridge_client import BridgeClient
from .catalog import Catalog
from .config import VERSION, Settings
from .errors import CbiError
from .mcp_server import create_mcp, mount
from .results import ResultStore
from .semantic.executor import Executor
from .services import Services
from .tickets import RunTickets, ThreadOwners

PUBLIC = Path(__file__).parent / "public"
LOG = logging.getLogger(__name__)
# CopilotKit and the charts set inline style attributes; scripts and style sheets come only from here.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; "
       "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
       "base-uri 'none'; form-action 'self'")
RUNTIME_ROUTES = re.compile(r"^(info|agent/analyst/(run|connect)|agent/analyst/stop/[A-Za-z0-9_-]{1,128})$")
THREAD = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
MAX_BODY = 2 * 1024 * 1024
ASSET = re.compile(r"^[A-Za-z0-9_.-]+\.(js|css|svg|woff2|ttf|png)$")


def validation_text(errors):
    first = errors[0] if errors else {}
    field = ".".join(str(p) for p in first.get("loc", [])[1:]) or "request"
    return f"Check {field}: {first.get('msg', 'invalid input')}."


def create_app(settings=None, *, bridge=None, verifier=None, catalog=None, executor=None, agent=None, runtime=None):
    settings = settings or Settings.from_env()
    bridge = bridge or BridgeClient(settings)
    verifier = verifier or TokenVerifier(settings)
    catalog = catalog or Catalog(bridge)
    executor = executor or Executor(timeout=settings.query_timeout, concurrency=settings.max_queries)
    services = Services(settings, bridge, catalog, executor, ResultStore())
    login = BrowserLogin(settings, verifier)
    tickets, threads = RunTickets(), ThreadOwners()
    runtime = runtime or httpx.AsyncClient(base_url=settings.runtime_url, timeout=httpx.Timeout(30, read=300),
                                           trust_env=False)
    mcp = create_mcp(settings, services, verifier)
    llm = None if agent else resolve_model(settings)
    state = {"bridge": "unknown"}

    async def check_bridge():
        for attempt in range(60):
            try:
                await bridge.discovery(refresh=True)
                state["bridge"] = "connected"
                return
            except CbiError as exc:
                state["bridge"] = exc.code
                if exc.code == "incompatible_contract":
                    LOG.error("%s", exc)
                    return
            await asyncio.sleep(min(30, 2 + attempt))

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(check_bridge())
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(mcp_running())
            try:
                yield
            finally:
                task.cancel()
                await runtime.aclose()
                await catalog.close()
                await bridge.close()

    app = FastAPI(title="Conversational BI · Iceberg Data Platform", version=VERSION, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url="/api/v1/openapi.json",
                  description="Governed questions over the semantic models of your teams and of the data shares they "
                              "received. Authenticate with a Keycloak bearer token for the iceberg-bridge audience "
                              "(client ext-conversationalbi-mcp), or sign in with the browser. Every operation is "
                              "also an MCP tool at /mcp.")
    app.state.services, app.state.login, app.state.tickets = services, login, tickets
    app.state.agent_app = create_agent_app(settings, services, login, tickets, agent, llm and llm.model)
    mcp_running = mount(app, mcp, settings)

    @app.exception_handler(CbiError)
    async def cbi_error(request, exc):
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
        return {"status": "ok", "bridge": state["bridge"], "llm": llm.status if llm else "ready", "version": VERSION}

    @app.get("/api/session", include_in_schema=False)
    async def session(request: Request):
        try:
            caller_ = await login.caller(request.cookies.get(COOKIE))
        except CbiError:
            return {"authenticated": False, "loginUrl": "/auth/login"}
        return {"authenticated": True, "user": caller_.name}

    @app.api_route("/api/copilotkit/{path:path}", methods=["GET", "POST"], include_in_schema=False)
    async def copilotkit(path: str, request: Request):
        """The chat UI's only way to the agent: signed-in, per-thread ownership, a fresh run ticket."""
        if not RUNTIME_ROUTES.match(path):
            raise CbiError(404, "Not found.", "not_found")
        if request.method == "POST":
            api.require_json_write(request)
        session_id = request.cookies.get(COOKIE)
        person = await login.caller(session_id)
        body = await request.body()
        if len(body) > MAX_BODY:
            raise CbiError(413, "The conversation is too long. Start a new chat.", "too_large")
        thread = path.rsplit("/", 1)[-1] if "/stop/" in path else None
        if path.endswith(("/run", "/connect")):
            try:
                thread = json.loads(body or b"{}").get("threadId")
            except (ValueError, AttributeError):
                thread = None
            if not isinstance(thread, str) or not THREAD.match(thread):
                raise CbiError(422, "The request needs a thread id.", "invalid_request")
        if thread and not threads.claim(thread, person.subject):
            raise CbiError(403, "This conversation belongs to someone else.", "forbidden")
        upstream = runtime.build_request(
            request.method, "/api/copilotkit/" + path, content=body or None,
            headers={"content-type": request.headers.get("content-type", "application/json"),
                     "accept": request.headers.get("accept", "*/*"),
                     "x-cbi-run-ticket": tickets.issue(session_id, person.subject)})
        try:
            response = await runtime.send(upstream, stream=True)
        except httpx.HTTPError:
            raise CbiError(503, "The chat runtime is unavailable. Try again shortly.", "runtime_unavailable") from None
        return StreamingResponse(
            response.aiter_raw(), status_code=response.status_code, background=BackgroundTask(response.aclose),
            headers={"content-type": response.headers.get("content-type", "application/json"),
                     "cache-control": "no-cache, no-store", "x-accel-buffering": "no"})

    @app.get("/auth/login", include_in_schema=False)
    def auth_login(return_to: str = "/"):
        return RedirectResponse(login.start(return_to), status_code=303)

    @app.get("/auth/callback", include_in_schema=False)
    async def auth_callback(state: str = "", code: str = ""):
        try:
            session_id, target = await login.finish(state, code)
        except CbiError:
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
        if not (PUBLIC / "index.html").is_file():
            return Response("The chat UI is not built. Run `npm run build` in web/, or use the API and MCP server.",
                            media_type="text/plain", status_code=503)
        return FileResponse(PUBLIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/favicon.svg", include_in_schema=False)
    def favicon():
        if not (PUBLIC / "favicon.svg").is_file():
            raise CbiError(404, "Not found.", "not_found")
        return FileResponse(PUBLIC / "favicon.svg", headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/{folder}/{name}", include_in_schema=False)
    def static(folder: str, name: str):
        target = PUBLIC / folder / name
        if folder not in ("assets", "fonts") or not ASSET.match(name) or not target.is_file():
            raise CbiError(404, "Not found.", "not_found")
        return FileResponse(target, headers={"Cache-Control": "public, max-age=31536000, immutable"
                                             if folder == "assets" else "public, max-age=86400"})

    return app
