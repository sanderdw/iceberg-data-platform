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
from pydantic import Field

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

Start with whoami (your teams, roles, databases, the policy defaults and where Maintenance is
enabled), then list_databases and list_tables (attention=true for tables that need work). Table
health comes from the last run that saw a table: run_maintenance with mode inspect refreshes it.
Runs only happen in environments a team administrator enabled: enable_environment turns one on
(ask the user first: runs act for the whole team).

A policy belongs to one database (get_policy, set_policy for team administrators). Without one,
the defaults apply to inspections and dry runs, and scheduled maintenance is off. Before maintaining,
run_maintenance with mode dry-run shows what the policy would do; mode maintain applies it.

Expired snapshots can no longer be used for time travel. Branch and tag heads, each ref's own
retention and the table properties history.expire.* are always respected. Files that expired
snapshots leave behind are deleted by orphan cleanup in a later run, never in the same run, and orphan
files younger than a day are never deleted. Tables with gc.enabled=false keep their history.

Ask the user before maintaining in acceptance or production, before changing a policy, and before any
tool marked destructive."""

Environment = Literal["development", "acceptance", "production"]
Mode = Literal["inspect", "dry-run", "maintain"]
DatabaseId = Annotated[str, Field(pattern=r"^db-[a-f0-9]{32}$", description="Database id from list_databases.")]
TableName = Annotated[str, Field(min_length=3, max_length=300, description="namespace.table, as list_tables shows.")]
RunId = Annotated[str, Field(pattern=r"^run-[a-f0-9]{24}$", description="Run id.")]
TeamId = Annotated[str, Field(pattern=r"^team-[a-f0-9]{32}$", description="Team id from whoami.")]
Settings = Annotated[dict[str, dict[str, Any]], Field(description=(
    "Per operation, the settings to change: expireSnapshots {enabled, olderThanHours, retainLast}, compaction "
    "{enabled, targetFileSizeMb, minInputFiles, deleteRatio, maxInputMbPerRun}, orphanFiles {enabled, "
    "olderThanHours >= 24}, rewriteManifests {enabled, minManifests}, danglingDeletes {enabled}."))]


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
        policy defaults and this extension."""
        return await call(services.whoami)

    @mcp.tool(annotations=READ)
    async def list_environments(team: TeamId) -> dict[str, Any]:
        """Where Maintenance is enabled for a team, and the databases of each environment."""
        return {"environments": await call(services.environments, team)}

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
        return {"databases": await call(services.list_databases, team, environment)}

    @mcp.tool(annotations=READ)
    async def list_tables(database: DatabaseId | None = None, team: TeamId | None = None,
                          environment: Environment | None = None, attention: bool = False) -> dict[str, Any]:
        """Table health from the last runs: snapshots, data and small files, delete files, manifests, and why a
        table needs attention. attention=true lists only those."""
        return {"tables": await call(services.list_tables, database, team, environment, attention)}

    @mcp.tool(annotations=READ)
    async def get_table(database: DatabaseId, table: TableName) -> dict[str, Any]:
        """One table's health, the actions of its last run, the effective policy and recent runs."""
        return await call(services.get_table, database, table)

    @mcp.tool(annotations=READ)
    async def get_policy(database: DatabaseId) -> dict[str, Any]:
        """A database's maintenance policy and schedule, or the defaults when it has none."""
        return await call(services.get_policy, database)

    @mcp.tool(annotations=WRITE)
    async def set_policy(database: DatabaseId, settings: Settings | None = None,
                         exclude: Annotated[list[str] | None, Field(max_length=500, description=(
                             "Tables (namespace.table) maintenance leaves alone; replaces the list."))] = None,
                         cron: Annotated[str | None, Field(max_length=100, description=(
                             "Five-field cron in UTC, at most hourly; empty removes the schedule."))] = None,
                         schedule_enabled: bool | None = None,
                         expected_revision: int | None = None) -> dict[str, Any]:
        """Create or change a database's policy and schedule (team administrators). Give only what changes. Ask the
        user first: scheduled runs expire snapshots and rewrite files."""
        data = {"settings": settings, "exclude": exclude, "scheduleEnabled": schedule_enabled,
                "expected_revision": expected_revision}
        data = {k: v for k, v in data.items() if v is not None}
        if cron is not None:
            data["cron"] = cron
        return await call(services.set_policy, database, data)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_policy(database: DatabaseId) -> dict[str, Any]:
        """Remove a database's policy and schedule (team administrators). Table health and past runs stay."""
        return await call(services.delete_policy, database)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def run_maintenance(database: DatabaseId, mode: Mode = "inspect",
                              tables: Annotated[list[str] | None, Field(max_length=200, description=(
                                  "Only these tables (namespace.table); all when empty."))] = None,
                              wait: Annotated[int, Field(ge=0, le=600)] = 0) -> dict[str, Any]:
        """Run on a database now (writers). inspect refreshes table health and dry-run plans the policy: neither
        changes anything. maintain applies the policy (needs one): ask the user first. wait blocks up to that many
        seconds."""
        return await call(services.run_maintenance, database, mode, tables, wait)

    @mcp.tool(annotations=READ)
    async def list_runs(database: DatabaseId, limit: Annotated[int, Field(ge=1, le=200)] = 20) -> dict[str, Any]:
        """A database's runs, newest first."""
        return {"runs": await call(services.list_runs, database, limit)}

    @mcp.tool(annotations=READ)
    async def get_run(run: RunId) -> dict[str, Any]:
        """A run's status, totals and each table's actions."""
        return await call(services.get_run, run)

    @mcp.tool(annotations=READ)
    async def get_run_logs(run: RunId, tail: Annotated[int, Field(ge=1, le=5000)] = 500) -> dict[str, Any]:
        """The last lines of a run's log."""
        return await call(services.get_run_logs, run, tail)

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
            await JSONResponse({"error": "The MCP transport is not running."}, status_code=503)(scope, receive, send)
            return
        body, more = bytearray(), True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                break
            body.extend(message.get("body", b""))
            more = message.get("more_body", False)
            if len(body) > MAX_BODY:
                await JSONResponse({"error": "Request is too large."}, status_code=413)(scope, receive, send)
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
