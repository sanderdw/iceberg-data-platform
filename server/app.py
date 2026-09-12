import asyncio
import hmac
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse

from .models import DatabaseInput, DatabaseMove, Login, Memberships, ServiceError, TeamInput, UserInput
from .polaris import PolarisProvider
from .storage import RustFSStorage

PUBLIC = Path(__file__).resolve().parent.parent / "public"
FILES = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/favicon.svg": "favicon.svg"}
FILES.update({f"/fonts/{name}.ttf": f"fonts/{name}.ttf" for name in ("doto", "space-grotesk", "space-mono")})


def create_app(provider=None, password=None, secure_cookie=None):
    password = password if password is not None else os.environ.get("PORTAL_PASSWORD", "")
    if len(password) < 16:
        raise RuntimeError(
            "Set PORTAL_PASSWORD (at least 16 characters). Run uv run python -m scripts.setup."
        )
    secure_cookie = secure_cookie if secure_cookie is not None else os.environ.get("COOKIE_SECURE") == "true"
    owned = provider is None
    if provider is None:
        if os.environ.get("PROVIDER", "polaris") != "polaris":
            raise RuntimeError("Unknown PROVIDER.")
        provider = PolarisProvider(os.environ, RustFSStorage(os.environ))

    @asynccontextmanager
    async def lifespan(app):
        yield
        if owned:
            provider.close()

    app = FastAPI(title="Iceberg Workspace API", version="0.2.0", lifespan=lifespan, redoc_url=None)
    sessions, attempts = {}, {}
    # This local control plane intentionally runs one worker. Serializing reads and
    # mutations also prevents orphan memberships during concurrent team deletion.
    mutation_lock = asyncio.Lock()

    @app.exception_handler(ServiceError)
    async def service_error(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        fields = sorted({str(e["loc"][-1]) for e in exc.errors()})
        message = "Check the input: " + ", ".join(fields) + "."
        if "teams" in fields:
            message += " Select at least one existing team, without duplicates."
        return JSONResponse({"error": message}, status_code=422)

    @app.middleware("http")
    async def security(request: Request, call_next):
        path, now = request.url.path, time.monotonic()
        for key, expiry in list(sessions.items()):
            if expiry <= now:
                sessions.pop(key, None)
        for key, (_, expiry) in list(attempts.items()):
            if expiry <= now:
                attempts.pop(key, None)
        request.state.authenticated = sessions.get(request.cookies.get("portal_session"), 0) > now
        try:
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
            if protected and not request.state.authenticated:
                raise ServiceError(401, "Sign in to continue.")
            if protected:
                async with mutation_lock:
                    response = await call_next(request)
            else:
                response = await call_next(request)
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

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/session")
    async def session(request: Request):
        return {"authenticated": request.state.authenticated}

    @app.post("/api/session")
    async def login(data: Login, request: Request):
        key = request.client.host if request.client else "local"
        count, until = attempts.get(key, (0, time.monotonic() + 60))
        if count >= 10:
            raise ServiceError(429, "Too many attempts. Try again in one minute.")
        attempts[key] = (count + 1, until)
        if not hmac.compare_digest(data.password.encode(), password.encode()):
            raise ServiceError(401, "Incorrect password.")
        attempts.pop(key, None)
        sessions.pop(request.cookies.get("portal_session"), None)
        id = secrets.token_urlsafe(32)
        sessions[id] = time.monotonic() + 28800
        response = JSONResponse({"authenticated": True})
        response.set_cookie(
            "portal_session", id, max_age=28800, httponly=True, samesite="strict", secure=secure_cookie
        )
        return response

    @app.delete("/api/session")
    async def logout(request: Request):
        sessions.pop(request.cookies.get("portal_session"), None)
        response = JSONResponse({"authenticated": False})
        response.delete_cookie("portal_session", httponly=True, samesite="strict", secure=secure_cookie)
        return response

    @app.get("/api/overview")
    def overview():
        health = provider.health()
        if health["status"] != "online":
            return {"health": health, "teams": [], "databases": [], "users": []}
        return {
            "health": health,
            "teams": provider.list_teams(),
            "databases": provider.list_databases(),
            "users": provider.list_users(),
        }

    async def require_portal_admin(request: Request):
        # A portal password session is the only admin identity. Never accept a
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

    @app.delete("/api/databases/{id}")
    def delete_database(id: str):
        provider.delete_database(id)
        return {"deleted": True}

    @app.get("/api/databases/{id}/connection")
    def connection(id: str):
        return provider.connection(id)

    @app.get("/api/users")
    def users():
        return provider.list_users()

    @app.post("/api/users", status_code=201)
    def create_user(data: UserInput):
        return provider.create_user(data)

    @app.patch("/api/users/{id}")
    def update_user(id: str, data: Memberships):
        return provider.update_memberships(id, data.teams)

    @app.delete("/api/users/{id}")
    def delete_user(id: str):
        provider.delete_user(id)
        return {"deleted": True}

    @app.get("/{path:path}", include_in_schema=False)
    def static(path: str):
        file = FILES.get("/" + path)
        if not file:
            raise ServiceError(404, "Not found.")
        return FileResponse(PUBLIC / file)

    return app
