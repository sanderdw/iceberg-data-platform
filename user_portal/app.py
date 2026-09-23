"""Standalone user gateway with session-authenticated HTTP and WebSocket proxying."""

import asyncio
import logging
import os
import re
import secrets
import time
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

import httpx
from docker.errors import DockerException
from fastapi import FastAPI, Query, Request, WebSocket
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from server.mcp_auth import is_mcp_path, mount_mcp
from server.models import (
    ENVIRONMENTS,
    DatabaseRename,
    Environment,
    Name,
    ServiceError,
    ShareInput,
    ShareUpdate,
)
from server.polaris import MAX_SHARES
from server.validation import validation_message

from .directory import UserDirectory, validate_namespace
from .mcp_server import create_mcp
from .preview import run_preview
from .runtime import NotebookRuntime, filespace_key

PUBLIC = Path(__file__).parent / "public"
CONNECT_SCRIPT = Path(__file__).parent / "client" / "iceberg_connect.py"
COOKIE = "iceberg_user_session"
LOG = logging.getLogger(__name__)


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=48)
    secret: str = Field(min_length=1, max_length=1024, repr=False)


class TeamInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    team: str = Field(min_length=1, max_length=100)


class NotebookInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    database: str = Field(min_length=1, max_length=256)
    namespace: list[str] = Field(default_factory=list, max_length=100)
    table: str | None = Field(default=None, max_length=256)


class EnvironmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    environment: Environment


class DatabaseCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    description: str = Field(default="", max_length=280)


class DatabaseDeleteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_name: str = Field(min_length=1, max_length=48)


class PreviewInput(NotebookInput):
    table: str = Field(min_length=1, max_length=256)
    snapshot_id: str | None = Field(default=None, pattern=r"^[0-9]{1,19}$")
    limit: int = Field(default=100, ge=1, le=100)


def same_origin(origin, url):
    return (
        bool(origin)
        and urlsplit(origin).netloc == url.netloc
        and urlsplit(origin).scheme in ("http", "https")
    )


def create_app(directory=None, runtime=None, *, oidc=None, session_cookie=COOKIE):
    directory = directory or UserDirectory(os.environ)
    runtime = runtime or NotebookRuntime(os.environ)
    sessions, attempts = {}, {}
    lock = asyncio.Lock()
    previews = asyncio.Semaphore(2)
    secure = oidc.secure if oidc else os.environ.get("USER_COOKIE_SECURE") == "true"

    def session_for(cookies):
        session = sessions.get(cookies.get(session_cookie))
        if not session or session.expires <= time.monotonic():
            raise ServiceError(401, "Sign in to open your workspace.")
        return session

    async def renew_session(session):
        if not oidc or not session.oidc_session:
            return
        grant = session.oidc_session
        try:
            await oidc.renew(grant)
            if grant.claims.get("polaris", {}).get("principal_name") != session.user_id:
                raise ServiceError(401, "Your account link changed. Sign in again.")
        except ServiceError as exc:
            if exc.status == 401:
                sessions.pop(session.id, None)
                await run_in_threadpool(runtime.stop_session, session.id)
            raise
        session.token = grant.access_token
        session.token_until = grant.access_until

    def workspace_state(session):
        profile = directory.profile(session)
        if session.team not in {t["id"] for t in profile["teams"]}:
            runtime.stop_session(session.id)
            session.team = profile["teams"][0]["id"]
        team_databases = [d for d in profile["databases"] if d["team"] == session.team]
        team_databases += profile.get("sharedDatabases", [])
        available = [d for d in team_databases if d["environment"] == session.environment]
        for workspace in list(runtime.workspaces.values()):
            if workspace.session_id == session.id and workspace.database not in {d["id"] for d in available}:
                runtime.stop(workspace.id)
        return {
            **profile,
            "databases": available,
            "deletingDatabases": [
                d for d in profile["deletingDatabases"]
                if d["team"] == session.team and d["environment"] == session.environment
            ],
            "activeTeam": session.team,
            "activeRole": next(t["role"] for t in profile["teams"] if t["id"] == session.team),
            "activeEnvironment": session.environment,
            "environments": list(ENVIRONMENTS),
            "filespaces": [
                {"id": filespace_key(session.team, env), "team": session.team, "environment": env}
                for env in ENVIRONMENTS
                if any(d["environment"] == env for d in team_databases)
            ],
            "notebooks": [w.public() for w in runtime.workspaces.values() if w.session_id == session.id],
        }

    async def sweep():
        while True:
            await asyncio.sleep(30)
            async with lock:
                try:
                    # Expiry is a real revocation in Polaris, not a hidden row.
                    await run_in_threadpool(directory.metadata.expire_shares)
                except ServiceError as exc:
                    LOG.error("Data share expiry failed; retrying on next sweep: %s", exc.status)
                now = time.monotonic()
                for key, (_, until) in list(attempts.items()):
                    if until < now:
                        attempts.pop(key, None)
                for id, session in list(sessions.items()):
                    try:
                        if session.expires <= now:
                            raise ServiceError(401, "Session expired.")
                        if any(w.session_id == id for w in runtime.workspaces.values()):
                            await renew_session(session)
                        await run_in_threadpool(workspace_state, session)
                    except ServiceError as exc:
                        # Fail closed on metadata outages as well: notebooks must
                        # not keep running when their authorization can't be checked.
                        LOG.info("Closing user session after authorization check: %s", exc.status)
                        sessions.pop(id, None)
                        try:
                            await run_in_threadpool(runtime.stop_session, id)
                        except DockerException:
                            LOG.error("Notebook cleanup failed; retrying on next sweep")
                    except DockerException:
                        LOG.error("Notebook cleanup failed; retrying on next sweep")
                for workspace in list(runtime.workspaces.values()):
                    if workspace.session_id not in sessions:
                        try:
                            await run_in_threadpool(runtime.stop, workspace.id)
                        except DockerException:
                            LOG.error("Orphan notebook cleanup failed")

    mcp_running = None

    @asynccontextmanager
    async def lifespan(app):
        await run_in_threadpool(runtime.recover)
        async with AsyncExitStack() as stack:
            if mcp_running:
                # A sub-application's lifespan never runs; the MCP transport is started here.
                await stack.enter_async_context(mcp_running())
            proxy_client = await stack.enter_async_context(httpx.AsyncClient(timeout=60, trust_env=False))
            app.state.proxy_client = proxy_client
            task = asyncio.create_task(sweep())
            try:
                yield
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                try:
                    await run_in_threadpool(runtime.close)
                finally:
                    directory.close()

    app = FastAPI(
        title="Iceberg User Workspace", version="0.5.1", docs_url=None, redoc_url=None, lifespan=lifespan,
        description=(
            "Sign in to the user portal first, then open /docs to call these APIs with your session. "
            "Your team roles and selected team/environment apply to every operation. "
            "Writes require X-Portal-Request: 1 and Content-Type: application/json. "
            "Try it out supplies these headers automatically."
        ),
    )

    def user_openapi():
        if app.openapi_schema is None:
            schema = get_openapi(title=app.title, version=app.version,
                                 description=app.description, routes=app.routes)
            schema.setdefault("components", {}).setdefault("securitySchemes", {})["UserSession"] = {
                "type": "apiKey", "in": "cookie", "name": session_cookie,
                "description": "Sign in through the user portal. The browser sends the session cookie automatically.",
            }
            for path, operations in schema["paths"].items():
                for method, operation in operations.items():
                    if path not in ("/api/session", "/api/health"):
                        operation["security"] = [{"UserSession": []}]
                    if method not in ("get", "head"):
                        operation.setdefault("parameters", []).append({
                            "name": "X-Portal-Request", "in": "header", "required": True,
                            "schema": {"type": "string", "enum": ["1"], "default": "1"},
                        })
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = user_openapi

    @app.get("/docs", include_in_schema=False, response_class=HTMLResponse)
    def api_docs():
        return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Iceberg User Workspace · API</title><link rel="icon" href="/favicon.svg">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js" defer></script>
<script src="/docs.js" defer></script></head>
<body><div id="swagger-ui"></div></body></html>"""

    if oidc:
        async def accept_oidc(request, claims, token, lifetime, grant):
            async with lock:
                session = await run_in_threadpool(
                    directory.login_oidc, claims, token, lifetime, secrets.token_urlsafe(32)
                )
                session.oidc_session = grant
                session.expires = grant.expires
                previous = sessions.pop(request.cookies.get(session_cookie), None)
                if previous:
                    await run_in_threadpool(runtime.stop_session, previous.id)
                sessions[session.id] = session
                response = oidc.redirect(request.state.oidc_return_to)
                response.set_cookie(session_cookie, session.id, max_age=int(grant.expires - time.monotonic()), httponly=True,
                                    secure=secure, samesite="strict")
                return response

        oidc.install(app, accept_oidc)

    @app.exception_handler(ServiceError)
    async def service_error(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({"error": validation_message(exc.errors(), request.url.path)}, status_code=422)

    @app.middleware("http")
    async def security(request, call_next):
        path = request.url.path
        try:
            if is_mcp_path(path):
                # Bearer-token API for MCP clients: no cookies, CSRF checks or session lock. The
                # endpoint enforces its own body cap.
                response = await call_next(request)
            else:
                response = await guarded(request, call_next, path)
        except ServiceError as exc:
            response = JSONResponse({"error": str(exc)}, status_code=exc.status)
        except Exception as exc:
            LOG.error("User gateway request failed: %s", type(exc).__name__)
            response = JSONResponse({"error": "The action failed. Please try again."}, status_code=502)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            }
        )
        if path == "/docs":
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data:; "
                "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
            )
        elif not path.startswith("/workspaces/"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
            )
        else:
            response.headers["Content-Security-Policy"] = "frame-ancestors 'self'"
        return response

    async def guarded(request, call_next, path):
        protected = path.startswith("/workspaces/") or (
            path.startswith("/api/") and path not in ("/api/session", "/api/health")
        ) or path in ("/docs", "/openapi.json")
        if protected:
            request.state.session = session_for(request.cookies)
        if request.method not in ("GET", "HEAD"):
            origin = request.headers.get("origin")
            if path.startswith("/api/"):
                if (
                    request.headers.get("x-portal-request") != "1"
                    or request.headers.get("content-type", "").split(";")[0] != "application/json"
                    or (origin and not same_origin(origin, request.url))
                ):
                    raise ServiceError(403, "Invalid request origin.")
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 8192:
                        raise ServiceError(413, "Request is too large.")
                request._body = bytes(body)
            elif path.startswith("/workspaces/") and not same_origin(origin, request.url):
                raise ServiceError(403, "Invalid request origin.")
        if path.startswith("/api/") and path not in ("/api/health", "/api/preview"):
            async with lock:
                if protected:
                    await renew_session(session_for(request.cookies))
                response = await call_next(request)
        else:
            if protected:
                async with lock:
                    await renew_session(session_for(request.cookies))
            response = await call_next(request)
        return response

    if oidc:
        mcp = create_mcp(directory, oidc, lock=lock, previews=previews)
        mcp_running = mount_mcp(app, mcp, oidc, "Iceberg Workspaces")

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/session")
    async def session(request: Request):
        try:
            await renew_session(session_for(request.cookies))
        except ServiceError as exc:
            if exc.status != 401:
                raise
            return {"authenticated": False, **({"loginUrl": "/auth/login"} if oidc else {})}
        return {"authenticated": True, **({"loginUrl": "/auth/login"} if oidc else {})}

    @app.post("/api/session")
    def login(data: LoginInput, request: Request):
        if oidc:
            raise ServiceError(403, "Use Keycloak to sign in.")
        key = request.client.host if request.client else "local"
        now = time.monotonic()
        count, until = attempts.get(key, (0, now + 60))
        if until <= now:
            count, until = 0, now + 60
        if count >= 10:
            raise ServiceError(429, "Too many attempts. Try again in one minute.")
        attempts[key] = (count + 1, until)
        session = directory.login(data.username, data.secret, secrets.token_urlsafe(32))
        previous = sessions.pop(request.cookies.get(session_cookie), None)
        if previous:
            runtime.stop_session(previous.id)
        sessions[session.id] = session
        attempts.pop(key, None)
        response = JSONResponse({"authenticated": True})
        response.set_cookie(
            session_cookie, session.id, httponly=True, secure=secure, samesite="strict", max_age=28800
        )
        return response

    @app.delete("/api/session")
    def logout(request: Request):
        session = sessions.pop(request.cookies.get(session_cookie), None)
        if session:
            runtime.stop_session(session.id)
        response = JSONResponse({"authenticated": False, **({"logoutUrl": oidc.logout_url} if oidc else {})})
        response.delete_cookie(session_cookie, httponly=True, secure=secure, samesite="strict")
        return response

    @app.get("/api/workspace")
    def workspace(request: Request):
        return workspace_state(request.state.session)

    @app.patch("/api/team")
    def switch_team(data: TeamInput, request: Request):
        session = request.state.session
        profile = directory.profile(session)
        if data.team not in {t["id"] for t in profile["teams"]}:
            raise ServiceError(403, "You are not a member of this team.")
        if session.team != data.team:
            runtime.stop_session(session.id)
            session.team = data.team
        return workspace_state(session)

    @app.get("/api/team")
    def team_overview(request: Request):
        return directory.team_members(request.state.session)

    @app.post("/api/databases", status_code=201)
    def create_database(data: DatabaseCreateInput, request: Request):
        return directory.create_database(request.state.session, data.name, data.description)

    database_id = Annotated[str, PathParam(pattern=r"^db-[a-f0-9]{32}$")]

    @app.patch("/api/databases/{id}")
    def rename_database(id: database_id, data: DatabaseRename, request: Request):
        return directory.rename_database(request.state.session, id, data.name)

    @app.delete("/api/databases/{id}")
    def delete_database(id: database_id, data: DatabaseDeleteInput, request: Request):
        return directory.delete_database(request.state.session, id, data.confirm_name)

    @app.patch("/api/environment")
    def switch_environment(data: EnvironmentInput, request: Request):
        session = request.state.session
        if session.environment != data.environment:
            runtime.stop_session(session.id)
            session.environment = data.environment
        return workspace_state(session)

    @app.get("/api/contents")
    def contents(
        request: Request,
        database: Annotated[str, Query(min_length=1, max_length=256)],
        namespace: Annotated[list[str] | None, Query()] = None,
    ):
        validate_namespace(namespace or [])
        return directory.contents(request.state.session, database, namespace or [])

    @app.get("/api/details")
    def details(
        request: Request,
        database: Annotated[str, Query(min_length=1, max_length=256)],
        kind: Literal["database", "namespace", "table", "view"],
        namespace: Annotated[list[str] | None, Query()] = None,
        name: Annotated[str | None, Query(min_length=1, max_length=256)] = None,
    ):
        parts = namespace or []
        validate_namespace(parts)
        if kind != "database" and not parts:
            raise ServiceError(422, "Select a namespace.")
        if kind in ("table", "view"):
            validate_namespace([name or ""])
        return directory.details(request.state.session, database, parts, kind, name)

    @app.post("/api/preview")
    async def preview(data: PreviewInput, request: Request):
        validate_namespace(data.namespace)
        validate_namespace([data.table])
        if not data.namespace:
            raise ServiceError(422, "Select a namespace.")
        if previews.locked():
            raise ServiceError(429, "Preview slots are busy. Try again shortly.")
        async with previews:
            async with lock:
                session = session_for(request.cookies)
                context = (session.team, session.environment)
                prepared = await run_in_threadpool(
                    directory.preview_request,
                    session,
                    data.database,
                    data.namespace,
                    data.table,
                    data.snapshot_id,
                    data.limit,
                )
            result = await run_in_threadpool(run_preview, prepared)
            async with lock:
                # Sign-out, context switches and revocation while reading discard the result.
                session = session_for(request.cookies)
                if context != (session.team, session.environment):
                    raise ServiceError(403, "Your workspace context changed. Preview again.")
                await run_in_threadpool(
                    directory.details, session, data.database, data.namespace, "table", data.table
                )
            return result

    @app.post("/api/notebooks", status_code=201)
    def start_notebook(data: NotebookInput, request: Request):
        session = request.state.session
        database = directory.database(session, data.database)
        validate_namespace(data.namespace)
        if data.namespace or data.table:
            content = directory.contents(session, data.database, data.namespace)
            if data.table and data.table not in {t["name"] for t in content["tables"]}:
                raise ServiceError(404, "Table not found in this namespace.")
        return runtime.start(
            session, data.database, data.namespace, data.table, database_name=database["name"],
            **({"shared_objects": database["sharedObjects"]} if database.get("shared") else {}),
        ).public()

    @app.delete("/api/notebooks/{id}")
    def stop_notebook(id: str, request: Request):
        runtime.get(id, request.state.session)
        runtime.stop(id)
        return {"stopped": True}

    def authorize_workspace(id, session):
        workspace = runtime.get(id, session)
        directory.database(session, workspace.database)
        return workspace

    @app.get("/internal/notebooks/{id}/token", include_in_schema=False)
    async def notebook_token(id: str, request: Request):
        # A runtime may retrieve only its owner's short-lived token. Portal cookies
        # never authorize this endpoint, and refresh tokens never leave the gateway.
        async with lock:
            workspace = runtime.workspaces.get(id)
            supplied = request.headers.get("authorization", "")
            if not workspace or not secrets.compare_digest(supplied, f"Bearer {workspace.token}"):
                raise ServiceError(401, "Notebook authorization required.")
            session = sessions.get(workspace.session_id)
            if not session or session.expires <= time.monotonic() or not session.oidc_session:
                raise ServiceError(401, "Notebook session expired.")
            await renew_session(session)
            await run_in_threadpool(authorize_workspace, id, session)
            return {"access_token": session.token}

    def proxy_headers(headers, workspace):
        # Never give notebook code either portal's cookies, user bearer headers,
        # Docker credentials or platform-admin credentials.
        excluded = {
            "host",
            "cookie",
            "authorization",
            "origin",
            "connection",
            "content-length",
            "transfer-encoding",
            "upgrade",
            "accept-encoding",
        }
        result = {
            k: v
            for k, v in headers.items()
            if k.lower() not in excluded and not k.lower().startswith(("sec-websocket-", "x-forwarded-"))
        }
        result["authorization"] = f"Bearer {workspace.token}"
        result["origin"] = workspace.upstream
        return result

    @app.api_route("/workspaces/{id}/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    async def notebook_proxy(id: str, path: str, request: Request):
        async with lock:
            workspace = await run_in_threadpool(authorize_workspace, id, request.state.session)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 32 * 1024 * 1024:
                raise ServiceError(413, "File is too large (maximum 32 MB).")
        # Reuse the raw, encoded path; do not decode and re-encode notebook filenames.
        url = workspace.upstream + request.scope["raw_path"].decode("ascii")
        if request.url.query:
            url += "?" + request.url.query
        try:
            result = await app.state.proxy_client.request(
                request.method, url, content=bytes(body), headers=proxy_headers(request.headers, workspace)
            )
        except httpx.HTTPError as exc:
            raise ServiceError(503, "This notebook has stopped or is unavailable. Open it again.") from exc
        headers = {
            k: v
            for k, v in result.headers.items()
            if k.lower()
            not in {
                "set-cookie",
                "content-length",
                "content-encoding",
                "transfer-encoding",
                "connection",
                "x-frame-options",
            }
        }
        return Response(result.content, status_code=result.status_code, headers=headers)

    @app.websocket("/workspaces/{id}/{path:path}")
    async def notebook_socket(websocket: WebSocket, id: str, path: str):
        try:
            if not same_origin(websocket.headers.get("origin"), websocket.url):
                raise ServiceError(403, "Invalid request origin.")
            async with lock:
                session = session_for(websocket.cookies)
                await renew_session(session)
                workspace = await run_in_threadpool(authorize_workspace, id, session)
            url = workspace.upstream.replace("http://", "ws://", 1) + websocket.scope["raw_path"].decode(
                "ascii"
            )
            if websocket.url.query:
                url += "?" + websocket.url.query
            protocols = websocket.scope.get("subprotocols") or None
            async with connect(
                url,
                additional_headers=proxy_headers(websocket.headers, workspace),
                subprotocols=protocols,
                max_size=32 * 1024 * 1024,
                proxy=None,
            ) as upstream:
                await websocket.accept(subprotocol=upstream.subprotocol)

                async def to_notebook():
                    while True:
                        message = await websocket.receive()
                        if message["type"] == "websocket.disconnect":
                            return
                        await upstream.send(
                            message.get("text") if message.get("text") is not None else message["bytes"]
                        )

                async def to_browser():
                    async for message in upstream:
                        if isinstance(message, bytes):
                            await websocket.send_bytes(message)
                        else:
                            await websocket.send_text(message)

                tasks = [asyncio.create_task(to_notebook()), asyncio.create_task(to_browser())]
                try:
                    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        except ServiceError, ConnectionClosed, InvalidHandshake, WebSocketDisconnect, OSError:
            pass
        finally:
            with suppress(RuntimeError, WebSocketDisconnect):
                await websocket.close(code=1008)

    share_id = Annotated[str, PathParam(pattern=r"^share-[a-f0-9]{32}$")]

    @app.get("/api/share-teams")
    def share_teams(request: Request):
        directory.manage_team(request.state.session)
        return [{"id": t["id"], "name": t["name"]} for t in directory.metadata.list_teams()
                if t["id"] != request.state.session.team]

    @app.get("/api/received-shares")
    def received_shares(request: Request):
        session = request.state.session
        profile = directory.profile(session)
        if session.team not in {t["id"] for t in profile["teams"]}:
            raise ServiceError(403, "You are not a member of this team.")
        databases = {d["id"]: d for d in directory.metadata.list_databases()
                     if d["environment"] == session.environment and d["status"] == "ready"}
        team_names = {t["id"]: t["name"] for t in directory.metadata.list_teams()}
        return [{"id": s["id"], "name": s["name"], "description": s["description"],
                 "database": s["database"], "databaseName": databases[s["database"]]["name"],
                 "ownerTeam": databases[s["database"]]["team"],
                 "ownerTeamName": team_names.get(databases[s["database"]]["team"], databases[s["database"]]["team"]),
                 "objects": s["objects"], "expiresAt": s["expiresAt"]}
                for s in directory.metadata.list_shares(drift=True)
                if s.get("recipientTeam") == session.team and s["database"] in databases]

    @app.get("/api/shares")
    def shares(request: Request, database: Annotated[str, Query(pattern=r"^db-[a-f0-9]{32}$")]):
        if directory.database(request.state.session, database).get("shared"):
            raise ServiceError(403, "Only the owning team can list outgoing shares.")
        return {
            "shares": directory.metadata.list_shares(database, drift=True),
            "limits": {"shares": MAX_SHARES, "objects": 50},
        }

    @app.post("/api/shares", status_code=201)
    def create_share(data: ShareInput, request: Request):
        database, user = directory.share_database(request.state.session, data.database)
        return directory.metadata.create_share(data, user, expected_team=database["team"])

    @app.patch("/api/shares/{id}")
    def update_share(id: share_id, data: ShareUpdate, request: Request):
        directory.share(request.state.session, id)
        return {"share": directory.metadata.update_share(id, data)}

    @app.post("/api/shares/{id}/rotate")
    def rotate_share(id: share_id, request: Request):
        directory.share(request.state.session, id)
        return directory.metadata.rotate_share(id)

    @app.delete("/api/shares/{id}")
    def delete_share(id: share_id, request: Request):
        directory.share(request.state.session, id)
        directory.metadata.delete_share(id)
        return {"deleted": True}

    @app.get("/iceberg_connect.py", include_in_schema=False)
    def connect_script():
        # Tools on the user's computer sign in with Keycloak's device login. The
        # script holds no secret, only where to sign in and which catalog to use.
        if not oidc:
            raise ServiceError(404, "Not found.")
        values = {"ISSUER": oidc.issuer, "CATALOG_URI": directory.metadata.public_url + "/api/catalog"}
        source = re.sub(r"^(ISSUER|CATALOG_URI) = .*$", lambda m: f"{m[1]} = {values[m[1]]!r}",
                        CONNECT_SCRIPT.read_text(), flags=re.MULTILINE)
        return Response(source, media_type="text/x-python", headers={
            "Content-Disposition": 'attachment; filename="iceberg_connect.py"'})

    files = {
        "": "index.html",
        "app.js": "app.js",
        "docs.js": "docs.js",
        "catalog.js": "catalog.js",
        "shares.js": "shares.js",
        "guide.js": "guide.js",
        "style.css": "style.css",
        "favicon.svg": "favicon.svg",
    }
    files.update(
        {f"fonts/{font}.ttf": f"fonts/{font}.ttf" for font in ("doto", "space-grotesk", "space-mono")}
    )

    @app.get("/{path:path}", include_in_schema=False)
    def static(path: str):
        if path not in files:
            raise ServiceError(404, "Not found.")
        asset = PUBLIC / files[path]
        if path.startswith("fonts/") or path == "favicon.svg":
            shared = Path(__file__).parent.parent / "public" / files[path]
            if shared.is_file():
                asset = shared
        return FileResponse(asset)

    return app
