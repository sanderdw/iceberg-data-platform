"""Extension Bridge v1: the stable interface between the core platform and extensions.

Extensions such as Conversational BI evolve and release on their own. They reach the core only
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
from fastapi import Body, Depends, FastAPI, Header, Request, Response
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from joserfc import jwt
from joserfc.errors import InvalidKeyIdError
from joserfc.jwk import KeySet
from joserfc.jws import JWSRegistry
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import extensions as registry
from . import listings
from .models import ENVIRONMENTS, AutomationInput, Environment, ServiceError, TokenRequest
from .polaris import AUTOMATION_ROLES, PolarisProvider, enc
from .storage import RustFSStorage
from .validation import validation_message

CONTRACT_VERSION = "0.1.2"
CAPABILITIES = ["user-context", "automation-principals", "automation-tokens", "shared-data", "listings"]
BRIDGE_AUDIENCE = "iceberg-bridge"
CONTRACT = Path(__file__).resolve().parent.parent / "contracts" / "bridge" / "v1" / "openapi.yaml"
ADMIN_ROLES = ("admin", "bucket-admin")
CODES = {400: "invalid_request", 401: "unauthorized", 403: "forbidden", 404: "not_found",
         409: "conflict", 422: "invalid_request", 502: "provider_error", 503: "unavailable"}
LOG = logging.getLogger(__name__)
AUDIT = logging.getLogger("iceberg.bridge.audit")


def actor(c):
    """Who acted: the platform user's ID and sign-in name; nothing when the extension acted itself."""
    if not c.principal:
        return {}
    return {"user.id": c.principal["name"], "user.name": c.principal.get("properties", {}).get("portal.name", "")}


def audit(event, fields, message, *args):
    """One audit record: a readable line on stderr, and an event with these attributes over OpenTelemetry."""
    AUDIT.info(message, *args, extra={"otel.event.name": "iceberg.bridge." + event, **fields})


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
    # Where the aliases resolve: Docker network aliases, or Kubernetes Services in `namespace`.
    platform: Literal["docker", "kubernetes"] = "docker"
    namespace: str | None = None


class Extension(BaseModel):
    id: str
    origin: str
    # Capability `listings`: this extension publishes listed data shares to a data catalog.
    listing: bool = False


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


class SharedObject(BaseModel):
    kind: Literal["table", "view", "semantic-model"]
    namespace: list[str]
    name: str


class SharedDatabase(Database):
    sharedWithTeam: str
    ownerTeamName: str = ""
    # Capability `shared-data`: every recipient team of the user, and what they can read.
    recipientTeams: list[str] = []
    sharedObjects: list[SharedObject] = []


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


class ScopedSharedDatabase(Database):
    sharedObjects: list[SharedObject]


class AutomationScope(AutomationPrincipal):
    databases: list[ScopedDatabase]
    # Capability `shared-data`: team shares received in this environment, readable with a read token.
    sharedDatabases: list[ScopedSharedDatabase] = []


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


# Capability `listings`: data shares their owners publish to a data catalog. Metadata only.

class ListingSummary(BaseModel):
    id: str
    version: str
    databaseId: str
    environment: Environment


class ListingList(BaseModel):
    listings: list[ListingSummary]


class ListingOwner(BaseModel):
    team: str
    teamName: str


class ListingDatabase(BaseModel):
    id: str
    name: str
    environment: Environment


class Column(BaseModel):
    id: int
    name: str
    type: str
    required: bool
    doc: str
    children: list[Column] = []


class PartitionField(BaseModel):
    name: str
    sourceColumn: str
    transform: str


class LineageInput(BaseModel):
    database: str
    namespace: list[str]
    name: str


class Quality(BaseModel):
    status: str
    summary: str | None = None
    checkedAt: str | None = None


class Producer(BaseModel):
    name: str
    version: str | None = None
    extension: str | None = None
    url: str | None = None
    runId: str | None = None
    runAt: str | None = None
    revision: str | None = None
    node: str | None = None
    inputs: list[LineageInput] = []
    inputsTruncated: bool = False
    quality: Quality | None = None


class ListedTable(BaseModel):
    kind: Literal["table"]
    namespace: list[str]
    name: str
    uuid: str | None = None
    comment: str
    columns: list[Column]
    partitionFields: list[PartitionField]
    rowCount: int | None = None
    updatedAt: int | None = None
    producer: Producer | None = None


class ListedView(BaseModel):
    kind: Literal["view"]
    namespace: list[str]
    name: str
    uuid: str | None = None
    comment: str
    columns: list[Column]
    updatedAt: int | None = None


class Expression(BaseModel):
    dialect: str
    expression: str


class ModelTable(BaseModel):
    namespace: list[str]
    name: str


class ContextRow(BaseModel):
    label: str
    value: str


class ModelField(BaseModel):
    name: str
    label: str
    description: str
    datatype: str
    dimension: bool
    timeDimension: bool
    synonyms: list[str]
    # AI context other than synonyms, such as instructions and examples.
    aiContext: list[ContextRow]
    expressions: list[Expression]


class ModelDataset(BaseModel):
    name: str
    description: str
    source: str
    table: ModelTable | None = None
    primaryKey: list[str]
    aiContext: list[ContextRow]
    fields: list[ModelField]


class ModelRelationship(BaseModel):
    model_config = {"populate_by_name": True}

    name: str
    from_: str = Field(alias="from")
    to: str
    fromColumns: list[str]
    toColumns: list[str]


class ModelMetric(BaseModel):
    name: str
    description: str
    datatype: str
    synonyms: list[str]
    aiContext: list[ContextRow]
    expressions: list[Expression]


class SemanticModel(BaseModel):
    name: str
    description: str
    aiContext: list[ContextRow]
    datasets: list[ModelDataset]
    relationships: list[ModelRelationship]
    metrics: list[ModelMetric]


class ListedSemanticModel(BaseModel):
    kind: Literal["semantic-model"]
    namespace: list[str]
    name: str
    specVersion: str
    entityVersion: str
    models: list[SemanticModel]


class Listing(BaseModel):
    id: str
    name: str
    description: str
    contact: str
    expiresAt: str | None = None
    version: str
    owner: ListingOwner
    database: ListingDatabase
    objects: list[Annotated[ListedTable | ListedView | ListedSemanticModel, Field(discriminator="kind")]]
    # Listed objects that were dropped, or can no longer be read.
    unavailable: list[SharedObject]


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
    all_teams = provider.list_teams()
    names = {t["id"]: t["name"] for t in all_teams}
    teams = [t for t in all_teams if t["id"] in roles]
    if not teams:
        raise BridgeError(403, "You no longer have any available teams.", "no_teams")
    ready = {d["id"]: d for d in provider.list_databases() if d["status"] == "ready"}
    fields = ("id", "name", "team", "environment", "status")
    order = [t["id"] for t in teams]
    shared = {}
    for share in provider.list_shares(drift=True):
        team = share.get("recipientTeam")
        database = ready.get(share["database"])
        if team in roles and database and database["team"] not in roles:
            entry = shared.setdefault(
                database["id"], {**{k: database[k] for k in fields}, "ownerTeamName": names.get(database["team"], ""),
                                 "recipientTeams": [], "sharedObjects": {}}
            )
            if team not in entry["recipientTeams"]:
                entry["recipientTeams"].append(team)
            # Team-share grants sit on each member's own role, so shares of several teams combine.
            for o in provider.received_objects(share):
                entry["sharedObjects"][(o["kind"], tuple(o["namespace"]), o["name"])] = o
    for entry in shared.values():
        entry["recipientTeams"].sort(key=order.index)
        entry["sharedWithTeam"] = entry["recipientTeams"][0]
        entry["sharedObjects"] = sorted(entry["sharedObjects"].values(),
                                        key=lambda o: (o["namespace"], o["name"], o["kind"]))
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
    listing_ids = set(registry.listing_extensions(env))
    by_client = {registry.client_id(id): id for id in known} | {registry.mcp_client_id(id): id for id in known}
    catalog_internal = env.get("BRIDGE_CATALOG_URI", "http://polaris-control-plane:8181/api/catalog").rstrip("/")
    mutations = asyncio.Lock()
    principal_locks = {}
    # Automation secrets live only in this process. A restart resets them on first use;
    # tokens issued before a reset stay valid until they expire.
    credentials = {}

    @asynccontextmanager
    async def lifespan(app):
        if listing_ids:
            # A stopped bridge may have left a temporary listing role behind.
            try:
                await run_in_threadpool(provider.sweep_listing_roles)
            except ServiceError:
                LOG.warning("Leftover listing roles could not be removed; the next start retries.")
        try:
            yield
        finally:
            await run_in_threadpool(provider.close)

    app = FastAPI(
        title="Iceberg Platform Bridge", version=CONTRACT_VERSION, docs_url=None, redoc_url=None,
        openapi_url=None, lifespan=lifespan,
        description=(
            "The stable interface for extensions. Authenticate with a Keycloak access token for the "
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

    def as_listing(c: AnyCaller) -> Caller:
        as_service(c)
        if c.extension not in listing_ids:
            raise BridgeError(403, "This extension is not registered to publish listings.", "listing_not_enabled")
        return c

    UserCaller = Annotated[Caller, Depends(as_user)]
    ServiceCaller = Annotated[Caller, Depends(as_service)]
    ListingCaller = Annotated[Caller, Depends(as_listing)]

    def team_role(c, team):
        roles = {m["team"]: m["role"] for m in provider.user(c.principal)["memberships"]}
        if team not in roles or team not in {t["id"] for t in provider.list_teams()}:
            raise BridgeError(403, "You are not a member of this team.", "not_a_member")
        return roles[team]

    errors = {status: {"model": Error} for status in (401, 403, 404, 409, 422, 502, 503)}
    team_id = Annotated[str, PathParam(pattern=r"^team-[a-f0-9]{32}$")]
    automation_id = Annotated[str, PathParam(pattern=r"^svc-[a-f0-9]{32}$")]
    share_id = Annotated[str, PathParam(pattern=r"^share-[a-f0-9]{32}$")]

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
                "name": env.get("PLATFORM_NETWORK", env.get("BRIDGE_NAMESPACE", "iceberg-platform_default")),
                "platform": env.get("BRIDGE_PLATFORM", "docker"),
                "namespace": env.get("BRIDGE_NAMESPACE"),
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
        state = "created" if created else "exists"
        audit("automation_principal." + state,
              {"iceberg.automation_principal.id": item["id"], "iceberg.extension": c.extension,
               "iceberg.team": teamId, "iceberg.environment": data.environment, **actor(c)},
              "automation principal %s: %s extension=%s team=%s environment=%s by=%s",
              state, item["id"], c.extension, teamId, data.environment, c.principal["name"])
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
        by = c.principal["name"] if c.principal else "extension"
        audit("automation_principal.revoked",
              {"iceberg.automation_principal.id": id, "iceberg.extension": c.extension,
               "iceberg.team": item["team"], **actor(c)},
              "automation principal revoked: %s extension=%s team=%s by=%s",
              id, c.extension, item["team"], by)
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
        audit("automation_token.issued",
              {"iceberg.automation_principal.id": id, "iceberg.extension": c.extension,
               "iceberg.access": data.access, "iceberg.purpose": data.purpose},
              "automation token issued: %s extension=%s access=%s purpose=%r", id, c.extension, data.access,
              data.purpose)
        return {
            "accessToken": issued["access_token"], "tokenType": "bearer",
            "expiresIn": int(issued.get("expires_in", 0)), "access": data.access, "principal": id,
            "catalog": {"internalUri": catalog_internal},
        }

    @app.get("/bridge/v1/listings", response_model=ListingList, responses=errors,
             summary="Data shares listed in a data catalog (listing extensions)")
    async def all_listings(c: ListingCaller):
        return {"listings": await run_in_threadpool(listings.summaries, provider)}

    @app.get("/bridge/v1/listings/{shareId}", response_model=Listing, responses={**errors, 304: {}},
             summary="One listing with the metadata of its objects (listing extensions)")
    async def one_listing(shareId: share_id, c: ListingCaller,
                          if_none_match: Annotated[str | None, Header()] = None):
        body = await run_in_threadpool(listings.listing, provider, shareId, known)
        body = Listing.model_validate(body).model_dump(mode="json", by_alias=True)
        etag = f'"{listings.digest(body)}"'
        audit("listing.read", {"iceberg.share.id": shareId, "iceberg.extension": c.extension},
              "listing read: %s extension=%s", shareId, c.extension)
        if if_none_match == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return JSONResponse(body, headers={"ETag": etag})

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
