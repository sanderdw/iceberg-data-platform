"""MCP server: the Replication extension for AI agents, with the same capabilities and rules as the REST API.

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
from .errors import ReplicationError

LOG = logging.getLogger(__name__)
MAX_BODY = 512 * 1024
READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
# Runs reach outside the platform: the source.
RUN = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False,
                              open_world_hint=False)
INSTRUCTIONS = """Replication (OLake, beta) on the Iceberg Data Platform: keeps operational databases in step with a
team's Iceberg databases. Each run reads the changes since the last one (change data capture from
PostgreSQL's write-ahead log, or an incremental cursor), writes them as the team's service account
and stops. For REST APIs, files and one-time database loads, use the Ingestion (dlt) extension.

Start with whoami (your teams, roles, databases and where Replication is enabled) and
list_replications. A replication belongs to one team and environment and writes into one database
and namespace (such as raw_crm). Runs only happen in environments a team administrator enabled:
enable_environment turns one on (ask the user first: runs act for the whole team).

To add one: list_templates, adapt one, check it with validate_replication, then
create_replication (team administrators; tables may stay empty at first). Secret values (the
database password) never go in the definition: refer to them as "$secret:<name>" and set them with
set_replication_secrets. They are write-only. Prefer that the user enters secrets in the
Replication page; set one through this tool only when the user gives it to you for that purpose.
Then check_source: OLake's prerequisite checks (logical WAL, replication privilege, slot,
publication) and the tables it can replicate, with their primary keys and cursor columns. If a
check fails, give the database owner the replication's setupSql. Choose tables with
update_replication: mode cdc (needs slot and publication), incremental (needs cursor) or
full_refresh.

Updates and deletes are written as positional deletes (deleteMode pos, the default) or deletion
vectors (dv, Iceberg v3). Equality deletes are never used: PyIceberg and Polars cannot read them.
OLake adds _olake_id, _olake_timestamp, _op_type, _cdc_lsn and _cdc_timestamp. A row deleted in the
source stays with _op_type = 'd': filter on _op_type <> 'd' for the current rows.

run_replication (writers) starts a run; pass wait to block until it finishes, then read get_run
(changes per table) and get_run_logs. A large first copy may take several runs (status partial);
each continues where the last stopped. Schedule frequent runs with set_schedule. Read the tables
with the user portal's MCP server.

Ask the user before running in acceptance or production, before setting secrets, and before any
tool marked destructive."""

Environment = Literal["development", "acceptance", "production"]
SourceType = Literal["postgres"]
ReplicationId = Annotated[str, Field(pattern=r"^rp-[a-f0-9]{24}$",
                                     description="Replication id from list_replications.")]
RunId = Annotated[str, Field(pattern=r"^run-[a-f0-9]{24}$", description="Run id.")]
TeamId = Annotated[str, Field(pattern=r"^team-[a-f0-9]{32}$", description="Team id from whoami.")]
Source = Annotated[dict[str, Any], Field(description="The source definition, as list_templates shows it. Refer to "
                                                     "secrets as \"$secret:<name>\". tls verify-full (default) "
                                                     "trusts public CAs; give caCertificate (PEM) for others, "
                                                     "such as Amazon RDS.")]
Tables = Annotated[dict[str, Any], Field(description="Source tables keyed by schema.table: mode (cdc, incremental "
                                                     "or full_refresh), cursor (incremental), table (Iceberg name), "
                                                     "partition (such as \"/{created_at,month}\"), appendOnly.")]
Options = Annotated[dict[str, Any], Field(description="deleteMode pos (default) or dv; firstRun copy (default) or "
                                                     "changes.")]


class Verifier:
    def __init__(self, verifier):
        self.verifier = verifier

    async def verify_token(self, token):
        try:
            caller = await self.verifier.verify(token)
        except ReplicationError:
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
        "iceberg-replication", title="Replication (OLake)", instructions=INSTRUCTIONS,
        token_verifier=Verifier(verifier),
        auth=AuthSettings(issuer_url=settings.issuer, resource_server_url=settings.origin + "/mcp",
                          validate_token_resource=False),
        log_level="WARNING",
    )

    async def call(fn, *args, **kwargs):
        try:
            return await fn(current(), *args, **kwargs)
        except ReplicationError as exc:
            detail = f" Detail: {exc.detail}" if exc.detail else ""
            raise ToolError(f"{exc} [{exc.code}]{detail}") from None

    @mcp.tool(annotations=READ)
    async def whoami() -> dict[str, Any]:
        """Your teams with your role in each, their databases per environment, where Replication is enabled, and
        this extension."""
        return await call(services.whoami)

    @mcp.tool(annotations=READ)
    async def list_environments(team: TeamId) -> dict[str, Any]:
        """Where Replication is enabled for a team, and the databases of each environment."""
        return {"environments": await call(services.environments, team)}

    @mcp.tool(annotations=WRITE)
    async def enable_environment(team: TeamId, environment: Environment) -> dict[str, Any]:
        """Enable Replication for a team environment (team administrators). Runs there act as the team's service
        account. Ask the user first."""
        return await call(services.enable_environment, team, environment)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def disable_environment(team: TeamId, environment: Environment) -> dict[str, Any]:
        """Disable Replication for a team environment (team administrators): its replications stop running."""
        return await call(services.disable_environment, team, environment)

    @mcp.tool(annotations=READ)
    async def list_templates() -> dict[str, Any]:
        """Replication templates: a complete definition per source type to adapt, with the secrets each needs and
        the SQL the database owner runs once."""
        return {"templates": await call(services.list_templates)}

    @mcp.tool(annotations=READ)
    async def list_replications(team: TeamId | None = None, environment: Environment | None = None) -> dict[str, Any]:
        """Replications of your teams with their schedule and last run; optionally of one team or environment."""
        return {"replications": await call(services.list_replications, team, environment)}

    @mcp.tool(annotations=READ)
    async def get_replication(replication: ReplicationId) -> dict[str, Any]:
        """A replication's definition, which secrets are set or missing (never their values), its last source check,
        setup SQL, schedule and recent runs."""
        return await call(services.get_replication, replication)

    @mcp.tool(annotations=READ)
    async def validate_replication(source_type: SourceType, source: Source, tables: Tables | None = None,
                                   options: Options | None = None, team: TeamId | None = None,
                                   environment: Environment | None = None, database: str | None = None,
                                   namespace: str | None = None) -> dict[str, Any]:
        """Check a definition without saving it: allowed keys, addresses, the secrets it needs and the setup SQL."""
        data = {"sourceType": source_type, "source": source, "tables": tables, "options": options, "team": team,
                "environment": environment, "database": database, "namespace": namespace}
        return await call(services.validate_replication, {k: v for k, v in data.items() if v is not None})

    @mcp.tool(annotations=WRITE)
    async def create_replication(team: TeamId, name: Annotated[str, Field(max_length=64)],
                              database: Annotated[str, Field(max_length=100, description="Team database id or name.")],
                              namespace: Annotated[str, Field(max_length=64, description="Such as raw_crm.")],
                              source_type: SourceType, source: Source, tables: Tables | None = None,
                              options: Options | None = None, environment: Environment = "development",
                              description: Annotated[str, Field(max_length=500)] = "") -> dict[str, Any]:
        """Create a replication (team administrators). Then set its secrets, check the source and choose tables."""
        return await call(services.create_replication, {
            "team": team, "environment": environment, "name": name, "description": description,
            "database": database, "namespace": namespace, "sourceType": source_type, "source": source,
            "tables": tables, "options": options})

    @mcp.tool(annotations=WRITE)
    async def update_replication(replication: ReplicationId, source: Source | None = None, tables: Tables | None = None,
                              options: Options | None = None,
                              name: Annotated[str | None, Field(max_length=64)] = None,
                              description: Annotated[str | None, Field(max_length=500)] = None,
                              database: Annotated[str | None, Field(max_length=100)] = None,
                              namespace: Annotated[str | None, Field(max_length=64)] = None,
                              source_type: SourceType | None = None,
                              expected_revision: int | None = None) -> dict[str, Any]:
        """Change a replication (team administrators). Give only what changes; source, tables and options replace
        the old ones whole. A new host removes the secrets; a new source, slot, database or namespace makes the next
        run copy everything again."""
        data = {"source": source, "tables": tables, "options": options, "name": name, "description": description,
                "database": database, "namespace": namespace, "sourceType": source_type}
        return await call(services.update_replication, replication, {k: v for k, v in data.items() if v is not None},
                          expected_revision)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_replication(replication: ReplicationId, confirm_name: Annotated[str, Field(max_length=64)]
                              ) -> dict[str, Any]:
        """Delete a replication with its runs, schedule and secrets (team administrators). The tables it wrote
        stay. The answer has the SQL to drop the replication slot: give it to the database owner, or the source
        keeps its change log. confirm_name must be the replication's name. Ask the user first."""
        return await call(services.delete_replication, replication, confirm_name)

    @mcp.tool(annotations=WRITE)
    async def set_replication_secrets(replication: ReplicationId,
                                   secrets: Annotated[dict[str, str | None], Field(
                                       description="Secret name → value, or null to remove it.")]) -> dict[str, Any]:
        """Set, replace or remove a replication's secrets (team administrators). Write-only: no tool returns them.
        Prefer that the user enters secrets in the Replication page; use this only with a value the user gave you
        for it."""
        return await call(services.set_replication_secrets, replication, secrets)

    @mcp.tool(annotations=RUN)
    async def check_source(replication: ReplicationId) -> dict[str, Any]:
        """Connect to the source with the stored secrets (writers): OLake's prerequisite checks and the tables it can
        replicate, with primary keys, cursor columns and supported modes. Waits up to two minutes."""
        return await call(services.check_source, replication)

    @mcp.tool(annotations=RUN)
    async def run_replication(replication: ReplicationId, full_refresh: bool = False,
                           wait: Annotated[int, Field(ge=0, le=600)] = 0) -> dict[str, Any]:
        """Run a replication now (writers). full_refresh copies everything again. wait blocks up to that many
        seconds for the run to finish."""
        return await call(services.run_replication, replication, full_refresh, wait)

    @mcp.tool(annotations=READ)
    async def list_runs(replication: ReplicationId, limit: Annotated[int, Field(ge=1, le=200)] = 20) -> dict[str, Any]:
        """A replication's runs, newest first."""
        return {"runs": await call(services.list_runs, replication, limit)}

    @mcp.tool(annotations=READ)
    async def get_run(run: RunId) -> dict[str, Any]:
        """A run's status, the changes per table (inserts, updates, deletes), how far it read the change log, and its
        error."""
        return await call(services.get_run, run)

    @mcp.tool(annotations=READ)
    async def get_run_logs(run: RunId, tail: Annotated[int, Field(ge=1, le=5000)] = 500) -> dict[str, Any]:
        """The last lines of a run's log, with secrets masked."""
        return await call(services.get_run_logs, run, tail)

    @mcp.tool(annotations=WRITE)
    async def cancel_run(run: RunId) -> dict[str, Any]:
        """Stop a queued or running run (writers)."""
        return await call(services.cancel_run, run)

    @mcp.tool(annotations=WRITE)
    async def set_schedule(replication: ReplicationId, cron: Annotated[str, Field(max_length=100)],
                           enabled: bool = True) -> dict[str, Any]:
        """Run a replication on a five-field cron schedule in UTC, at most every 5 minutes (team administrators). A
        due run is skipped while the replication is still running."""
        return await call(services.set_schedule, replication, cron, enabled)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_schedule(replication: ReplicationId) -> dict[str, Any]:
        """Remove a replication's schedule (team administrators)."""
        return await call(services.delete_schedule, replication)

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
                "resource_name": "Replication (OLake)"}

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
