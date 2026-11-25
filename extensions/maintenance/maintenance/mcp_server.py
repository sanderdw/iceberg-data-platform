"""MCP server: the Maintenance extension for AI agents, with the same capabilities and rules as the REST API.

Agents sign in through Keycloak with the public `ext-<id>-mcp` client (PKCE, loopback redirect),
so every tool runs with the signed-in user's team roles; runs execute as the team's automation
principal. Destructive tools are annotated so clients ask the user first.
"""

import logging
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from fastapi.responses import JSONResponse
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from .auth import Caller
from .errors import MaintenanceError

LOG = logging.getLogger(__name__)
MAX_BODY = 512 * 1024
READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False,
                              open_world_hint=False)
INSTRUCTIONS = """Maintenance (beta) on the Iceberg Data Platform: keeps a team's Iceberg tables tidy. It expires old
snapshots, compacts small data files (applying their deletes), removes dangling delete files, merges
manifests and removes orphan files, per database, on a schedule or on demand.

Start with whoami (your teams, roles, databases, the policy types and defaults, and where
Maintenance is enabled), then list_databases and list_table_health (attention=true for tables that
need work). Table health comes from the last run that saw a table: inspect_database refreshes it and
changes nothing. Runs only happen in environments a team administrator enabled: enable_environment
turns one on (ask the user first: runs act for the whole team). Policies are read and written as
that team's service account too, so they also need the environment enabled.

Policies are Apache Polaris policies, one per type: snapshotExpiry (system.snapshot-expiry),
dataCompaction (system.data-compaction, also dangling deletes), metadataCompaction
(system.metadata-compaction, manifests) and orphanFileRemoval (system.orphan-file-removal). A
database's policies are attached to its catalog (get_policy; update_policy for team administrators).
Exclusions switch types off for a namespace or a table: a policy on a table overrides its namespace's,
which overrides the database's. Runs ask Polaris which policies apply to each table, so policies other
tools attached count too; a type without a policy is not maintained. Before maintaining,
inspect_database with mode dry-run shows what the policies would do; run_maintenance applies them.
set_schedule maintains a database on a cron schedule (UTC, at most once an hour; needs a policy) and
delete_schedule removes it.

A run starts in the background: pass wait (up to 600 seconds) to get it back finished, or read it
with get_run, which also takes wait. Its status is queued, running, success, partial (some tables
failed, the others were maintained), failed, error, cancelled or incomplete. Times are RFC 3339 in
UTC.

Expired snapshots can no longer be used for time travel. Branch and tag heads, each ref's own
retention and the table properties history.expire.* are always respected. Files that expired
snapshots leave behind are deleted by orphan cleanup in a later run, never in the same run, and orphan
files younger than a day are never deleted. Tables with gc.enabled=false keep their history.

Ask the user before maintaining in acceptance or production, before changing a policy or a schedule,
and before any tool marked destructive."""

Environment = Literal["development", "acceptance", "production"]
DatabaseId = Annotated[str, Field(pattern=r"^db-[a-f0-9]{32}$", description="Database id from list_databases.")]
TableName = Annotated[str, Field(min_length=3, max_length=300,
                                 description="namespace.table, as list_table_health shows.")]
RunId = Annotated[str, Field(pattern=r"^run-[a-f0-9]{24}$", description="Run id.")]
TeamId = Annotated[str, Field(pattern=r"^team-[a-f0-9]{32}$", description="Team id from whoami.")]
PolicyType = Literal["snapshotExpiry", "dataCompaction", "metadataCompaction", "orphanFileRemoval"]
Settings = Annotated[dict[str, dict[str, Any]], Field(description=(
    "Per policy type, the settings to change: snapshotExpiry {enabled, maxSnapshotAgeDays >= 1, "
    "minSnapshotsToKeep}, dataCompaction {enabled, targetFileSizeBytes (16 to 512 MiB), minInputFiles, "
    "deleteRatioThreshold, removeDanglingDeletes, maxInputBytesPerRun}, metadataCompaction {enabled, "
    "minCountToMerge}, orphanFileRemoval {enabled, maxOrphanFileAgeInDays >= 1}."))]


class Exclusion(BaseModel):
    target: str = Field(min_length=1, max_length=300, description="A namespace, or a table as namespace.table.")
    level: Literal["namespace", "table"]
    types: list[PolicyType] | None = Field(default=None, min_length=1, description=(
        "The policy types switched off there; all when left out."))
Tables = Annotated[list[str] | None, Field(max_length=200, description=(
    "Only these tables (namespace.table); all when empty."))]
Wait = Annotated[int, Field(ge=0, le=600, description=(
    "Seconds to wait for the run to finish; it is returned as it is then, finished or not."))]


class Verifier:
    def __init__(self, verifier):
        self.verifier = verifier

    async def verify_token(self, token):
        try:
            caller = await self.verifier.verify(token)
        except MaintenanceError:
            return None
        return AccessToken(token=token, client_id=caller.client, scopes=[], expires_at=int(caller.expires_at),
                           subject=caller.subject, claims={"name": caller.name, "azp": caller.client})


def current():
    access = get_access_token()
    if access is None:
        raise ToolError("Sign in through Keycloak to use these tools.")
    return Caller(access.token, access.subject, access.claims.get("name", ""), access.client_id,
                  float(access.expires_at or 0))


def create_mcp(settings, services, verifier):
    mcp = MCPServer(
        "iceberg-maintenance", title="Maintenance", instructions=INSTRUCTIONS, token_verifier=Verifier(verifier),
        auth=AuthSettings(issuer_url=settings.issuer, resource_server_url=settings.origin + "/mcp",
                          validate_token_resource=False),
        log_level="WARNING",
    )

    async def call(fn, *args, **kwargs):
        try:
            return await fn(current(), *args, **kwargs)
        except MaintenanceError as exc:
            detail = f" Detail: {exc.detail}" if exc.detail else ""
            raise ToolError(f"{exc} [{exc.code}]{detail}") from None

    @mcp.tool(annotations=READ)
    async def whoami() -> dict[str, Any]:
        """Your teams with your role in each, their databases per environment, where Maintenance is enabled, the
        policy types with their defaults, and this extension."""
        return await call(services.whoami)

    @mcp.tool(annotations=READ)
    async def list_environments(team: TeamId) -> dict[str, Any]:
        """Where Maintenance is enabled for a team, and the databases of each environment."""
        return await call(services.environments, team)

    @mcp.tool(annotations=WRITE)
    async def enable_environment(team: TeamId, environment: Environment) -> dict[str, Any]:
        """Enable Maintenance for a team environment (team administrators). Runs there act as the team's service
        account. Ask the user first."""
        return await call(services.enable_environment, team, environment)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def disable_environment(team: TeamId, environment: Environment) -> dict[str, Any]:
        """Disable Maintenance for a team environment (team administrators): its schedules stop running."""
        return await call(services.disable_environment, team, environment)

    @mcp.tool(annotations=READ)
    async def list_databases(team: TeamId | None = None, environment: Environment | None = None) -> dict[str, Any]:
        """Your teams' databases with their policy, schedule, last run and how many tables need attention."""
        return await call(services.list_databases, team, environment)

    @mcp.tool(annotations=READ)
    async def list_table_health(database: DatabaseId | None = None, team: TeamId | None = None,
                                environment: Environment | None = None, attention: bool = False,
                                limit: Annotated[int, Field(ge=1, le=5000)] = 500) -> dict[str, Any]:
        """Table health from the last runs: snapshots, data and small files, delete files, manifests, and why a
        table needs attention. attention=true lists only those. total counts the tables that match; narrow by
        database when it is more than limit."""
        return await call(services.list_table_health, database, team, environment, attention, limit)

    @mcp.tool(annotations=READ)
    async def get_table_health(database: DatabaseId, table: TableName) -> dict[str, Any]:
        """One table's health, the actions of its last run, the policies Polaris resolved for it then (and where
        each is attached) and recent runs."""
        return await call(services.get_table_health, database, table)

    @mcp.tool(annotations=READ)
    async def get_policy(database: DatabaseId) -> dict[str, Any]:
        """A database's maintenance policies (the Polaris policies attached to its catalog, with their versions),
        its exclusions and its schedule; the defaults for a type without a policy."""
        return await call(services.get_policy, database)

    @mcp.tool(annotations=WRITE)
    async def update_policy(database: DatabaseId, settings: Settings | None = None,
                            exclusions: Annotated[list[Exclusion] | None, Field(max_length=500, description=(
                                "Namespaces and tables maintenance leaves alone, per policy type; replaces the "
                                "list. [] removes every exclusion."))] = None,
                            expected_versions: Annotated[dict[PolicyType, int | None] | None, Field(description=(
                                "Per policy type, the version get_policy showed (null: none). A type that "
                                "changed since is refused."))] = None) -> dict[str, Any]:
        """Change a database's Polaris policies (team administrators): give only what changes, the rest keeps its
        value. A type without a policy is created from the defaults and attached to the database's catalog.
        Ask the user first: maintenance runs expire snapshots and rewrite files by it. The schedule is set with
        set_schedule."""
        data = {"settings": settings, "expectedVersions": expected_versions,
                "exclusions": [e.model_dump() for e in exclusions] if exclusions is not None else None}
        return await call(services.update_policy, database, {k: v for k, v in data.items() if v is not None})

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_policy(database: DatabaseId) -> dict[str, Any]:
        """Remove a database's policies (team administrators): detach them from its catalog, delete the ones
        Maintenance created, its exclusions and its schedule. Table health and past runs stay."""
        return await call(services.delete_policy, database)

    @mcp.tool(annotations=WRITE)
    async def set_schedule(database: DatabaseId, cron: Annotated[str, Field(min_length=1, max_length=100)],
                           enabled: bool = True) -> dict[str, Any]:
        """Maintain a database on a five-field cron schedule in UTC, at most once an hour (team administrators).
        The database needs a policy. enabled=false keeps the schedule and pauses it. A due run is skipped while
        another run of the database is in progress. Ask the user first: scheduled runs expire snapshots and
        rewrite files."""
        return await call(services.set_schedule, database, cron, enabled)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_schedule(database: DatabaseId) -> dict[str, Any]:
        """Remove a database's schedule (team administrators). Its policy stays."""
        return await call(services.delete_schedule, database)

    @mcp.tool(annotations=WRITE)
    async def inspect_database(database: DatabaseId, mode: Literal["inspect", "dry-run"] = "inspect",
                               tables: Tables = None, wait: Wait = 0) -> dict[str, Any]:
        """Read a database now, without changing a table (writers). inspect refreshes every table's health;
        dry-run plans the policies that apply to each table and returns per table what maintenance would do."""
        return await call(services.run_maintenance, database, mode, tables, wait)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def run_maintenance(database: DatabaseId, tables: Tables = None, wait: Wait = 0) -> dict[str, Any]:
        """Maintain a database now (writers): apply the policies that apply to each table; the database needs
        its own. This expires snapshots, which ends time travel to them, and rewrites and deletes files. Ask the
        user first, and show them a dry run (inspect_database with mode dry-run)."""
        return await call(services.run_maintenance, database, "maintain", tables, wait)

    @mcp.tool(annotations=READ)
    async def list_runs(database: DatabaseId, limit: Annotated[int, Field(ge=1, le=200)] = 20) -> dict[str, Any]:
        """A database's runs, newest first, without each table's results."""
        return await call(services.list_runs, database, limit)

    @mcp.tool(annotations=READ)
    async def get_run(run: RunId, wait: Wait = 0) -> dict[str, Any]:
        """A run's status, totals and each table's actions (results)."""
        return await call(services.get_run, run, wait)

    @mcp.tool(annotations=READ)
    async def get_run_logs(run: RunId, limit: Annotated[int, Field(ge=1, le=5000)] = 500) -> dict[str, Any]:
        """The last limit lines of a run's log."""
        return await call(services.get_run_logs, run, limit)

    @mcp.tool(annotations=WRITE)
    async def cancel_run(run: RunId) -> dict[str, Any]:
        """Stop a queued or running run (writers). Tables it already maintained keep the changes."""
        return await call(services.cancel_run, run)

    return mcp


class Endpoint:
    """ASGI endpoint for /mcp that forwards to the transport of the running lifespan, with a body cap."""

    def __init__(self):
        self.app = None

    async def __call__(self, scope, receive, send):
        if self.app is None:
            await JSONResponse({"error": "The MCP transport is not running.", "code": "unavailable"},
                               status_code=503)(scope, receive, send)
            return
        body, more = bytearray(), True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                break
            body.extend(message.get("body", b""))
            more = message.get("more_body", False)
            if len(body) > MAX_BODY:
                await JSONResponse({"error": "Request is too large.", "code": "too_large"},
                                   status_code=413)(scope, receive, send)
                return
        replayed = False

        async def replay():
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send)


def mount(app, mcp, settings):
    endpoint = Endpoint()
    app.add_route("/mcp", endpoint)
    metadata = {"resource": settings.origin + "/mcp", "authorization_servers": [settings.issuer],
                "bearer_methods_supported": ["header"], "scopes_supported": ["openid", "profile"],
                "resource_name": "Maintenance"}

    @app.get("/.well-known/oauth-protected-resource", include_in_schema=False)
    @app.get("/.well-known/oauth-protected-resource/mcp", include_in_schema=False)
    def resource_metadata():
        return metadata

    @asynccontextmanager
    async def running():
        endpoint.app = mcp.streamable_http_app(
            streamable_http_path="/mcp", stateless_http=True, json_response=True,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            endpoint.app = None

    return running
