"""Extension Bridge v1: the stable interface between the core platform and extension stacks.

Extensions such as the dbt stack evolve and release on their own. They reach the core only
through this API, Iceberg REST and S3, as described in `contracts/bridge/`. Two kinds of
callers authenticate with Keycloak access tokens for the `iceberg-bridge` audience:

- a user, through the extension's own clients (`ext-<id>`, `ext-<id>-mcp`); the platform
  user is resolved and re-checked on every request, like a portal session;
- the extension itself, with the service account of its confidential client `ext-<id>`.

Neither token is accepted by Polaris. Data access happens only through automation
principals, whose secrets stay in this process: an extension receives short-lived tokens.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastapi import Body, Depends, FastAPI, Request
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from joserfc import jwt
from joserfc.errors import InvalidKeyIdError
from joserfc.jwk import KeySet
from joserfc.jws import JWSRegistry
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from . import extensions as registry
from .models import ENVIRONMENTS, AutomationInput, Environment, ServiceError, TokenRequest
from .polaris import AUTOMATION_ROLES, PolarisProvider, enc
from .storage import RustFSStorage
from .validation import validation_message

CONTRACT_VERSION = "1.0.0"
CAPABILITIES = ["user-context", "automation-principals", "automation-tokens"]
BRIDGE_AUDIENCE = "iceberg-bridge"
CONTRACT = Path(__file__).resolve().parent.parent / "contracts" / "bridge" / "v1" / "openapi.yaml"
ADMIN_ROLES = ("admin", "bucket-admin")
CODES = {400: "invalid_request", 401: "unauthorized", 403: "forbidden", 404: "not_found",
         409: "conflict", 422: "invalid_request", 502: "provider_error", 503: "unavailable"}
LOG = logging.getLogger(__name__)
AUDIT = logging.getLogger("iceberg.bridge.audit")


class BridgeError(ServiceError):
    def __init__(self, status, message, code=None):
        super().__init__(status, message)
        self.code = code or CODES.get(status, "error")


# Response schemas: the contract. Changing them is a contract change (contracts/bridge/COMPATIBILITY.md).

class Error(BaseModel):
    error: str
    code: str


class CatalogEndpoints(BaseModel):
    uri: str
    internalUri: str
    tokenUri: str


class StorageEndpoints(BaseModel):
    endpoint: str
    internalEndpoint: str
    region: str
    pathStyle: bool


class OIDCEndpoints(BaseModel):
    issuer: str
    internalIssuer: str
    bridgeAudience: str


class Network(BaseModel):
    name: str
    aliases: dict[str, str]
    roleLabel: str
    extensionLabel: str


class Extension(BaseModel):
    id: str
    origin: str


class Discovery(BaseModel):
    contractVersion: str
    capabilities: list[str]
    catalog: CatalogEndpoints
    storage: StorageEndpoints
    oidc: OIDCEndpoints
    environments: list[str]
    network: Network
    userPortalUrl: str
    extensions: list[Extension]


class User(BaseModel):
    id: str
    name: str


class Membership(BaseModel):
    team: str
    teamName: str
    role: Literal["reader", "writer", "admin", "bucket-admin"]


class Database(BaseModel):
    id: str
    name: str
    team: str
    environment: Environment
    status: str


class SharedDatabase(Database):
    sharedWithTeam: str


class UserContext(BaseModel):
    user: User
    memberships: list[Membership]
    databases: list[Database]
    sharedDatabases: list[SharedDatabase]


class AutomationPrincipal(BaseModel):
    id: str
    name: str
    team: str
    environment: Environment
    extension: str
    status: Literal["active", "revoking"]
    createdAt: int | None = None
    createdBy: str


class ScopedDatabase(Database):
    storageLocation: str


class AutomationScope(AutomationPrincipal):
    databases: list[ScopedDatabase]


class AutomationList(BaseModel):
    automationPrincipals: list[AutomationPrincipal]


class TokenCatalog(BaseModel):
    internalUri: str


class TokenResponse(BaseModel):
    accessToken: str
    tokenType: Literal["bearer"]
    expiresIn: int
    access: Literal["read", "write"]
    principal: str
    catalog: TokenCatalog


class Revoked(BaseModel):
    revoked: bool


@dataclass
class Caller:
    kind: Literal["user", "service"]
    extension: str
    claims: dict = field(repr=False)
    principal: dict | None = field(default=None, repr=False)


class TokenVerifier:
    """Keycloak access tokens for the bridge audience: signature, issuer, audience, subject, expiry."""

    def __init__(self, issuer, internal_issuer, audience=BRIDGE_AUDIENCE):
        self.issuer = issuer.rstrip("/")
        self.jwks_uri = internal_issuer.rstrip("/") + "/protocol/openid-connect/certs"
        self.audience = audience
        self.keys = None

    async def fetch_keys(self):
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            response = await client.get(self.jwks_uri)
            response.raise_for_status()
            self.keys = KeySet.import_key_set(response.json())

    async def claims(self, token):
        if self.keys is None:
            await self.fetch_keys()
        registry_ = JWSRegistry(algorithms=["RS256"])
        try:
            decoded = jwt.decode(token, self.keys, registry=registry_)
        except InvalidKeyIdError:
            # One refresh handles signing-key rotation; verification stays enabled.
            await self.fetch_keys()
            decoded = jwt.decode(token, self.keys, registry=registry_)
        jwt.JWTClaimsRegistry(
            iss={"essential": True, "value": self.issuer},
            aud={"essential": True, "value": self.audience},
            sub={"essential": True}, exp={"essential": True}, azp={"essential": True},
        ).validate(decoded.claims)
        return decoded.claims


def user_context(provider, principal):
    user = provider.user(principal)
    roles = {m["team"]: m["role"] for m in user["memberships"]}
    teams = [t for t in provider.list_teams() if t["id"] in roles]
    if not teams:
        raise BridgeError(403, "You no longer have any available teams.", "no_teams")
    ready = {d["id"]: d for d in provider.list_databases() if d["status"] == "ready"}
    fields = ("id", "name", "team", "environment", "status")
    shared = {}
    for share in provider.list_shares():
        team = share.get("recipientTeam")
        database = ready.get(share["database"])
        if team in roles and database and database["team"] not in roles:
            shared.setdefault(database["id"], {**{k: database[k] for k in fields}, "sharedWithTeam": team})
    return {
        "user": {"id": user["id"], "name": user["name"]},
        "memberships": [{"team": t["id"], "teamName": t["name"], "role": roles[t["id"]]} for t in teams],
        "databases": sorted(
            ({k: d[k] for k in fields} for d in ready.values() if d["team"] in roles),
            key=lambda d: (d["environment"], d["name"]),
        ),
        "sharedDatabases": sorted(shared.values(), key=lambda d: (d["environment"], d["name"])),
    }


def create_bridge_app(provider=None, verifier=None, env=None):
    env = os.environ if env is None else env
    provider = provider or PolarisProvider(env, RustFSStorage(env))
    verifier = verifier or TokenVerifier(env["OIDC_ISSUER"], env.get("OIDC_INTERNAL_ISSUER", env["OIDC_ISSUER"]))
    known = registry.extensions(env)
    by_client = {registry.client_id(id): id for id in known} | {registry.mcp_client_id(id): id for id in known}
    catalog_internal = env.get("BRIDGE_CATALOG_URI", "http://polaris-control-plane:8181/api/catalog").rstrip("/")
    mutations = asyncio.Lock()
    principal_locks = {}
    # Automation secrets live only in this process. A restart resets them on first use;
    # tokens issued before a reset stay valid until they expire.
    credentials = {}

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            await run_in_threadpool(provider.close)

    app = FastAPI(
        title="Iceberg Platform Bridge", version=CONTRACT_VERSION, docs_url=None, redoc_url=None,
        openapi_url=None, lifespan=lifespan,
        description=(
            "The stable interface for extension stacks. Authenticate with a Keycloak access token for the "
            f"`{BRIDGE_AUDIENCE}` audience: a user token issued to the extension's clients, or the service-account "
            "token of the extension's confidential client. See contracts/bridge for the compatibility policy."
        ),
    )

    def error(status, message, code):
        return JSONResponse({"error": message, "code": code}, status_code=status,
                            headers={"WWW-Authenticate": "Bearer"} if status == 401 else None)

    @app.exception_handler(ServiceError)
    async def service_error(request, exc):
        return error(exc.status, str(exc), getattr(exc, "code", None) or CODES.get(exc.status, "error"))

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return error(422, validation_message(exc.errors(), request.url.path), "invalid_request")

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    async def caller(request: Request) -> Caller:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise BridgeError(401, "A bearer token is required.", "unauthorized")
        try:
            claims = await verifier.claims(token)
        except Exception as exc:  # noqa: BLE001 - Token and provider errors are never disclosed.
            LOG.info("Bridge token rejected: %s", type(exc).__name__)
            raise BridgeError(401, "The token is invalid or expired.", "unauthorized") from None
        extension = by_client.get(claims.get("azp"))
        if not extension:
            raise BridgeError(403, "This client is not a registered extension.", "unknown_extension")
        mapping = claims.get("polaris") or {}
        name = mapping.get("principal_name")
        service_user = "service-account-" + registry.client_id(extension)
        if claims["azp"] == registry.client_id(extension) and not name and (
            claims.get("preferred_username") == service_user or claims.get("client_id") == claims["azp"]
        ):
            return Caller("service", extension, claims)
        if not isinstance(name, str) or not name.startswith("portal-") or mapping.get("principal_id") != 0:
            raise BridgeError(403, "Your identity is not linked to a platform user.", "not_linked")
        try:
            principal = await run_in_threadpool(provider.require_user, f"/principals/{enc(name)}")
        except ServiceError as exc:
            if exc.status == 404:
                raise BridgeError(403, "Your platform user no longer exists.", "not_linked") from None
            raise
        p = principal["properties"]
        # The same checks as a portal sign-in: the link is exact and still active.
        if (p.get("portal.oidc-subject") != claims["sub"] or p.get("portal.oidc-issuer") != claims["iss"]
                or p.get("portal.identity-status", "linked") != "linked"):
            raise BridgeError(403, "Your identity is no longer linked to this user.", "not_linked")
        return Caller("user", extension, claims, principal)

    AnyCaller = Annotated[Caller, Depends(caller)]

    def as_user(c: AnyCaller) -> Caller:
        if c.kind != "user":
            raise BridgeError(403, "This operation acts for a signed-in user.", "user_required")
        return c

    def as_service(c: AnyCaller) -> Caller:
        if c.kind != "service":
            raise BridgeError(403, "This operation is reserved for the extension's service account.",
                              "service_required")
        return c

    UserCaller = Annotated[Caller, Depends(as_user)]
    ServiceCaller = Annotated[Caller, Depends(as_service)]

    def team_role(c, team):
        roles = {m["team"]: m["role"] for m in provider.user(c.principal)["memberships"]}
        if team not in roles or team not in {t["id"] for t in provider.list_teams()}:
            raise BridgeError(403, "You are not a member of this team.", "not_a_member")
        return roles[team]

    errors = {status: {"model": Error} for status in (401, 403, 404, 409, 422, 502, 503)}
    team_id = Annotated[str, PathParam(pattern=r"^team-[a-f0-9]{32}$")]
    automation_id = Annotated[str, PathParam(pattern=r"^svc-[a-f0-9]{32}$")]

    @app.get("/bridge/v1", response_model=Discovery, summary="Discover the platform")
    def discovery():
        public_catalog = env.get("POLARIS_PUBLIC_URL", "http://localhost:8181").rstrip("/") + "/api/catalog"
        return {
            "contractVersion": CONTRACT_VERSION,
            "capabilities": CAPABILITIES,
            "catalog": {"uri": public_catalog, "internalUri": catalog_internal,
                        "tokenUri": catalog_internal + "/v1/oauth/tokens"},
            "storage": {"endpoint": env.get("S3_ENDPOINT", "http://localhost:9000"),
                        "internalEndpoint": env.get("S3_INTERNAL_ENDPOINT", "http://rustfs:9000"),
                        "region": env.get("AWS_REGION", "us-east-1"), "pathStyle": True},
            "oidc": {"issuer": env["OIDC_ISSUER"],
                     "internalIssuer": env.get("OIDC_INTERNAL_ISSUER", env["OIDC_ISSUER"]),
                     "bridgeAudience": BRIDGE_AUDIENCE},
            "environments": list(ENVIRONMENTS),
            "network": {
                "name": env.get("PLATFORM_NETWORK", "iceberg-platform_default"),
                "aliases": {"bridge": "bridge", "catalog": "polaris-control-plane",
                            "storage": "rustfs", "oidc": "keycloak"},
                "roleLabel": "io.iceberg-platform.role",
                "extensionLabel": "io.iceberg-platform.extension",
            },
            "userPortalUrl": env.get("USER_ORIGIN", "http://localhost:3002"),
            "extensions": registry.public(env),
        }

    @app.get("/bridge/v1/openapi.yaml", include_in_schema=False)
    def contract():
        return FileResponse(CONTRACT, media_type="application/yaml")

    @app.get("/bridge/v1/health", include_in_schema=False)
    def health():
        return {"status": "ok"}

    @app.get("/bridge/v1/me", response_model=UserContext, responses=errors,
             summary="The signed-in user's teams, roles and databases")
    async def me(c: UserCaller):
        return await run_in_threadpool(user_context, provider, c.principal)

    @app.get("/bridge/v1/teams/{teamId}/automation-principals", response_model=AutomationList,
             responses=errors, summary="This extension's automation principals of a team")
    async def team_automation(teamId: team_id, c: UserCaller):
        def run():
            team_role(c, teamId)
            return {"automationPrincipals": provider.list_automation(team=teamId, extension=c.extension)}
        return await run_in_threadpool(run)

    @app.post("/bridge/v1/teams/{teamId}/automation-principals", response_model=AutomationPrincipal,
              status_code=201, responses={**errors, 200: {"model": AutomationPrincipal}},
              summary="Enable this extension for a team environment (team administrators)")
    async def enable(teamId: team_id, data: Annotated[AutomationInput, Body()], c: UserCaller):
        def run():
            if team_role(c, teamId) not in ADMIN_ROLES:
                raise BridgeError(403, "Only team administrators can enable an extension.", "forbidden_role")
            user = provider.user(c.principal)
            return provider.create_automation(teamId, data.environment, c.extension,
                                              {"id": user["id"], "name": user["name"]})
        async with mutations:
            item, created = await run_in_threadpool(run)
        AUDIT.info("automation principal %s: %s extension=%s team=%s environment=%s by=%s",
                   "created" if created else "exists", item["id"], c.extension, teamId, data.environment,
                   c.principal["name"])
        return JSONResponse(item, status_code=201 if created else 200)

    @app.delete("/bridge/v1/automation-principals/{id}", response_model=Revoked, responses=errors,
                summary="Revoke an automation principal (team administrators or the extension)")
    async def revoke(id: automation_id, c: AnyCaller):
        def run():
            item = provider.automation(provider.require_automation(id, extension=c.extension))
            if c.kind == "user" and team_role(c, item["team"]) not in ADMIN_ROLES:
                raise BridgeError(403, "Only team administrators can revoke an extension.", "forbidden_role")
            provider.delete_automation(id)
            credentials.pop(id, None)
            return item
        async with mutations:
            item = await run_in_threadpool(run)
        AUDIT.info("automation principal revoked: %s extension=%s team=%s by=%s", id, c.extension,
                   item["team"], c.principal["name"] if c.principal else "extension")
        return {"revoked": True}

    @app.get("/bridge/v1/automation-principals", response_model=AutomationList, responses=errors,
             summary="All automation principals of this extension")
    async def all_automation(c: ServiceCaller):
        items = await run_in_threadpool(lambda: provider.list_automation(extension=c.extension))
        return {"automationPrincipals": items}

    @app.get("/bridge/v1/automation-principals/{id}", response_model=AutomationScope, responses=errors,
             summary="An automation principal and the databases it can reach")
    async def scope(id: automation_id, c: ServiceCaller):
        return await run_in_threadpool(lambda: provider.automation_scope(id, extension=c.extension))

    @app.post("/bridge/v1/automation-principals/{id}/tokens", response_model=TokenResponse, responses=errors,
              summary="A short-lived catalog token for an automation principal")
    async def token(id: automation_id, data: Annotated[TokenRequest, Body()], c: ServiceCaller):
        lock = principal_locks.setdefault(id, asyncio.Lock())

        def issue():
            provider.require_automation(id, extension=c.extension)
            # Heals databases created concurrently with the principal.
            provider.reconcile_automation(id)
            role = id + AUTOMATION_ROLES[data.access][0]
            for attempt in (1, 2):
                if id not in credentials:
                    credentials[id] = provider.reset_automation_secret(id)
                secret = credentials[id]
                try:
                    return provider.principal_token(secret["clientId"], secret["clientSecret"], role)
                except ServiceError as exc:
                    # Another bridge process, or a restore, reset the secret: reset once more.
                    credentials.pop(id, None)
                    if exc.status != 401 or attempt == 2:
                        raise
        async with lock:
            issued = await run_in_threadpool(issue)
        AUDIT.info("automation token issued: %s extension=%s access=%s purpose=%r", id, c.extension,
                   data.access, data.purpose)
        return {
            "accessToken": issued["access_token"], "tokenType": "bearer",
            "expiresIn": int(issued.get("expires_in", 0)), "access": data.access, "principal": id,
            "catalog": {"internalUri": catalog_internal},
        }

    def schema():
        if app.openapi_schema is None:
            from fastapi.openapi.utils import get_openapi
            schema = get_openapi(title=app.title, version=app.version, description=app.description,
                                 routes=app.routes)
            schema["components"].setdefault("securitySchemes", {})["KeycloakBearer"] = {
                "type": "http", "scheme": "bearer", "bearerFormat": "JWT",
                "description": f"Keycloak access token with audience {BRIDGE_AUDIENCE}.",
            }
            for path, operations in schema["paths"].items():
                if path != "/bridge/v1":
                    for operation in operations.values():
                        operation["security"] = [{"KeycloakBearer": []}]
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = schema
    return app
