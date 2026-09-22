import asyncio
import hmac
import logging
import os
import secrets
import time
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

import httpx
from fastapi import Depends, FastAPI, Query, Request
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse

from .mcp_admin import create_admin_mcp
from .mcp_auth import is_mcp_path, mount_mcp
from .models import (
    DatabaseInput,
    DatabaseMove,
    DatabaseRename,
    Login,
    Memberships,
    ServiceError,
    TeamInput,
    UserInput,
)
from .polaris import PolarisProvider
from .storage import RustFSStorage
from .validation import validation_message

PUBLIC = Path(__file__).resolve().parent.parent / "public"
FILES = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/favicon.svg": "favicon.svg",
         "/infrastructure.js": "infrastructure.js", "/identity.js": "identity.js"}
FILES.update({f"/fonts/{name}.ttf": f"fonts/{name}.ttf" for name in ("doto", "space-grotesk", "space-mono")})


def create_app(provider=None, password=None, secure_cookie=None, *, oidc=None,
               session_cookie="portal_session", user_management=None):
    password = password if password is not None else os.environ.get("PORTAL_PASSWORD", "")
    if oidc is None and len(password) < 16:
        raise RuntimeError(
            "Set PORTAL_PASSWORD (at least 16 characters). Run uv run python -m scripts.setup."
        )
    secure_cookie = secure_cookie if secure_cookie is not None else os.environ.get("COOKIE_SECURE") == "true"
    owned = provider is None
    if provider is None:
        if os.environ.get("PROVIDER", "polaris") != "polaris":
            raise RuntimeError("Unknown PROVIDER.")
        provider = PolarisProvider(os.environ, RustFSStorage(os.environ))

    mcp_running = None

    @asynccontextmanager
    async def lifespan(app):
        async with AsyncExitStack() as stack:
            if mcp_running:
                # A sub-application's lifespan never runs; the MCP transport is started here.
                await stack.enter_async_context(mcp_running())
            try:
                yield
            finally:
                if user_management:
                    user_management.close()
                if owned:
                    provider.close()

    app = FastAPI(title="Iceberg Workspace API", version="0.5.0", lifespan=lifespan, redoc_url=None)
    sessions, attempts = {}, {}
    # This local control plane intentionally runs one worker. Serializing reads and
    # mutations also prevents orphan memberships during concurrent team deletion.
    mutation_lock = asyncio.Lock()

    def list_users():
        return user_management.users(provider) if user_management else provider.list_users()

    def require_editable_user(id):
        if user_management:
            principal = user_management.principal(provider, id)
            if principal["properties"].get("portal.identity-status") in ("pending", "revoking"):
                raise ServiceError(409, "Finish setup or revocation before editing this user.")
            return principal

    if oidc:
        async def accept_oidc(request, claims, token, lifetime, grant):
            roles = claims.get("resource_access", {}).get(oidc.client_id, {}).get("roles", [])
            if "platform-admin" not in roles:
                raise ServiceError(403, "Platform administrator access is required.")
            sessions.pop(request.cookies.get(session_cookie), None)
            session_id = secrets.token_urlsafe(32)
            sessions[session_id] = grant
            response = oidc.redirect(request.state.oidc_return_to)
            response.set_cookie(session_cookie, session_id, max_age=int(grant.expires - time.monotonic()), httponly=True,
                                samesite="strict", secure=secure_cookie)
            return response

        oidc.install(app, accept_oidc)

    @app.exception_handler(ServiceError)
    async def service_error(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({"error": validation_message(exc.errors(), request.url.path)}, status_code=422)

    @app.middleware("http")
    async def security(request: Request, call_next):
        path, now = request.url.path, time.monotonic()
        for key, expiry in list(sessions.items()):
            if (expiry.expires if oidc else expiry) <= now:
                sessions.pop(key, None)
        for key, (_, expiry) in list(attempts.items()):
            if expiry <= now:
                attempts.pop(key, None)
        session_id = request.cookies.get(session_cookie)
        current = sessions.get(session_id)
        request.state.authenticated = bool(current) if oidc else (current or 0) > now
        try:
            if is_mcp_path(path):
                # Bearer-token API for MCP clients: no cookies, CSRF checks, body cap or session lock.
                response = await call_next(request)
            else:
                response = await guarded(request, call_next, path, session_id, current)
        except ServiceError as exc:
            response = JSONResponse({"error": str(exc)}, status_code=exc.status)
        except Exception as exc:
            logging.getLogger(__name__).error("Request failed: %s", type(exc).__name__)
            response = JSONResponse({"error": "Something went wrong. Please try again."}, status_code=500)
        response.headers.update(
            {
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            }
        )
        if path.startswith("/docs"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data:; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
            )
        return response

    async def guarded(request, call_next, path, session_id, current):
        if path.startswith("/api/") and request.method not in ("GET", "HEAD"):
            origin = request.headers.get("origin")
            if (
                request.headers.get("x-portal-request") != "1"
                or request.headers.get("content-type", "").split(";")[0] != "application/json"
                or (origin and urlsplit(origin).netloc != request.headers.get("host"))
            ):
                raise ServiceError(403, "Invalid request origin.")
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 8192:
                    raise ServiceError(413, "Request is too large.")
            request._body = bytes(body)
        protected = (
            path.startswith("/api/") and path not in ("/api/session", "/api/health")
        ) or path in ("/docs", "/openapi.json", "/docs/oauth2-redirect")
        if oidc and current and (protected or (path == "/api/session" and request.method == "GET")):
            try:
                await oidc.renew(current)
                roles = current.claims.get("resource_access", {}).get(oidc.client_id, {}).get("roles", [])
                if "platform-admin" not in roles:
                    raise ServiceError(401, "Administrator access ended. Sign in again.")
                if sessions.get(session_id) is not current:
                    raise ServiceError(401, "Sign in to continue.")
            except ServiceError as exc:
                if exc.status == 401:
                    sessions.pop(session_id, None)
                    request.state.authenticated = False
                if exc.status != 401 or protected:
                    raise
        if protected and not request.state.authenticated:
            raise ServiceError(401, "Sign in to continue.")
        if protected and path != "/api/infrastructure":
            async with mutation_lock:
                return await call_next(request)
        return await call_next(request)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/session")
    async def session(request: Request):
        return {"authenticated": request.state.authenticated, **({"loginUrl": "/auth/login"} if oidc else {}),
                **({"userManagement": "keycloak"} if user_management else {})}

    @app.post("/api/session")
    async def login(data: Login, request: Request):
        if oidc:
            raise ServiceError(403, "Use Keycloak to sign in.")
        key = request.client.host if request.client else "local"
        count, until = attempts.get(key, (0, time.monotonic() + 60))
        if count >= 10:
            raise ServiceError(429, "Too many attempts. Try again in one minute.")
        attempts[key] = (count + 1, until)
        if not hmac.compare_digest(data.password.encode(), password.encode()):
            raise ServiceError(401, "Incorrect password.")
        attempts.pop(key, None)
        sessions.pop(request.cookies.get(session_cookie), None)
        id = secrets.token_urlsafe(32)
        sessions[id] = time.monotonic() + 28800
        response = JSONResponse({"authenticated": True})
        response.set_cookie(
            session_cookie, id, max_age=28800, httponly=True, samesite="strict", secure=secure_cookie
        )
        return response

    @app.delete("/api/session")
    async def logout(request: Request):
        sessions.pop(request.cookies.get(session_cookie), None)
        response = JSONResponse({"authenticated": False, **({"logoutUrl": oidc.logout_url} if oidc else {})})
        response.delete_cookie(session_cookie, httponly=True, samesite="strict", secure=secure_cookie)
        return response

    @app.get("/api/overview")
    def overview():
        health = provider.health()
        if health["status"] != "online":
            return {"health": health, "teams": [], "databases": [], "users": [], "shares": []}
        return {
            "health": health,
            "teams": provider.list_teams(),
            "databases": provider.list_databases(),
            "users": list_users(),
            "shares": provider.list_shares(),
        }

    @app.get("/api/infrastructure")
    async def infrastructure():
        endpoint = os.environ.get("MONITOR_URL", "")
        if not endpoint:
            raise ServiceError(503, "Monitoring is not configured. Start the monitoring service and set MONITOR_URL.")
        try:
            async with httpx.AsyncClient(timeout=4, trust_env=False) as client:
                response = await client.get(endpoint.rstrip("/") + "/snapshot",
                                            headers={"X-Monitor-Token": password})
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError):
            raise ServiceError(503, "The monitoring service is unavailable. Retrying automatically.") from None

    async def require_portal_admin(request: Request):
        # A validated portal session is the only admin identity. Never accept a
        # Polaris bearer token, database role, request header or browser flag.
        if not request.state.authenticated:
            raise ServiceError(401, "Sign in as a portal administrator to browse the catalog.")

    @app.get("/api/admin/explorer/databases", dependencies=[Depends(require_portal_admin)])
    def explorer_databases():
        return {"databases": provider.explorer_databases()}

    @app.get("/api/admin/explorer/contents", dependencies=[Depends(require_portal_admin)])
    def explorer_contents(
        database: Annotated[str, Query(min_length=1, max_length=256)],
        namespace: Annotated[list[str] | None, Query()] = None,
    ):
        parts = namespace or []
        if (
            database in (".", "..")
            or any(c in database for c in "\x00\x1f")
            or len(parts) > 100
            or any(
                not part or len(part) > 256 or part in (".", "..") or any(c in part for c in "\x00\x1f")
                for part in parts
            )
        ):
            raise ServiceError(422, "Invalid database or namespace.")
        return provider.explorer_contents(database, parts)

    @app.get("/api/teams")
    def teams():
        return provider.list_teams()

    @app.post("/api/teams", status_code=201)
    def create_team(data: TeamInput):
        return provider.save_team(data)

    @app.patch("/api/teams/{id}")
    def update_team(id: str, data: TeamInput):
        return provider.save_team(data, id)

    @app.delete("/api/teams/{id}")
    def delete_team(id: str):
        provider.delete_team(id)
        return {"deleted": True}

    @app.get("/api/databases")
    def databases():
        return provider.list_databases()

    @app.post("/api/databases", status_code=201)
    def create_database(data: DatabaseInput):
        return provider.create_database(data)

    @app.patch("/api/databases/{id}")
    def move_database(id: str, data: DatabaseMove):
        return provider.move_database(id, data.team)

    @app.patch("/api/databases/{id}/name")
    def rename_database(id: str, data: DatabaseRename):
        return provider.rename_database(id, data.name)

    @app.delete("/api/databases/{id}")
    def delete_database(id: str):
        provider.delete_database(id)
        return {"deleted": True}

    @app.get("/api/databases/{id}/connection")
    def connection(id: str):
        return provider.connection(id)

    @app.get("/api/users")
    def users():
        return list_users()

    @app.post("/api/users", status_code=201)
    def create_user(data: UserInput):
        if user_management:
            raise ServiceError(409, "Use Create Keycloak account or Link existing account.")
        return provider.create_user(data)

    @app.patch("/api/users/{id}")
    def update_user(id: str, data: Memberships):
        # The MCP update_user_access tool calls this function directly; keep its signature.
        principal = require_editable_user(id)
        if user_management:
            # A linked legacy account keeps the bucket administration it already holds.
            current = provider.user(principal)["memberships"]
            held = {m["team"] for m in current if m["role"] == "bucket-admin"}
            if any(m.role == "bucket-admin" and m.team not in held for m in data.memberships):
                raise ServiceError(422, "Keycloak users access storage through Polaris; direct S3 accounts are not supported.")
        return provider.update_memberships(id, data.roles)

    @app.delete("/api/users/{id}")
    def delete_user(id: str):
        if user_management:
            user_management.revoke(provider, id)
        else:
            provider.delete_user(id)
        return {"deleted": True}

    # Team administrators create and edit shares in the user portal. Here they can only be revoked.
    @app.delete("/api/shares/{id}")
    def delete_share(id: Annotated[str, PathParam(pattern=r"^share-[a-f0-9]{32}$")]):
        provider.delete_share(id)
        return {"deleted": True}

    if user_management:
        user_management.install(app, provider)

    if oidc:
        mcp = create_admin_mcp(provider, oidc, lock=mutation_lock, user_management=user_management,
                               overview=overview, users=list_users, update_user=update_user)
        mcp_running = mount_mcp(app, mcp, oidc, "Iceberg Workspace Administration")

    @app.get("/{path:path}", include_in_schema=False)
    def static(path: str):
        file = FILES.get("/" + path)
        if not file:
            raise ServiceError(404, "Not found.")
        return FileResponse(PUBLIC / file)

    return app
