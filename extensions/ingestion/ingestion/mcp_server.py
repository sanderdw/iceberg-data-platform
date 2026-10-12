"""MCP server: the Ingestion extension for AI agents, with the same capabilities and rules as the REST API.

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
from .errors import IngestionError

LOG = logging.getLogger(__name__)
MAX_BODY = 512 * 1024
READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
# Runs reach outside the platform: the source.
RUN = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False,
                              open_world_hint=False)
INSTRUCTIONS = """Ingestion (dlt) on the Iceberg Data Platform: declarative dlt pipelines that load REST APIs,
PostgreSQL and MySQL/MariaDB tables, and CSV, JSONL or Parquet files into a team's Iceberg databases.

Start with whoami (your teams, roles, databases and where Ingestion is enabled) and
list_pipelines. A pipeline belongs to one team and environment and loads into one database and
namespace (such as raw_crm). Runs only happen in environments a team administrator enabled:
enable_environment turns one on (ask the user first: runs act for the whole team).

To add a pipeline: list_templates, adapt one, check it with validate_pipeline, then
create_pipeline (team administrators). Pipelines are configuration, never code. Secret values
(API keys, tokens, database passwords) never go in the definition: refer to them as
"$secret:<name>" and set them with set_pipeline_secrets. They are write-only. Prefer that the
user enters secrets themselves in the Ingestion page; set one through this tool only when the user
gives it to you for that purpose. Changing where a pipeline connects removes its secrets.

Each resource (endpoint, table or file) has a writeDisposition: append, replace (keeps the table
and its identity, replaces its rows) or merge (upserts on primaryKey). Incremental cursors carry
over between runs; run_pipeline with full_refresh loads everything again. dlt adds _dlt_load_id
and _dlt_id columns, and child tables <table>__<field> for nested lists. Sources on private
addresses are refused unless the platform allows them.

run_pipeline (writers) starts a run; pass wait to block until it finishes, then read get_run
(tables and row counts) and get_run_logs. Loaded tables show "Produced by dlt" in the platform
catalog. Read them with the user portal's MCP server (describe_table, preview_rows), then model
them with the dbt extension and describe marts with a semantic model.

Ask the user before running in acceptance or production, before setting secrets, and before any
tool marked destructive."""

Environment = Literal["development", "acceptance", "production"]
SourceType = Literal["rest_api", "sql_database", "filesystem"]
PipelineId = Annotated[str, Field(pattern=r"^pl-[a-f0-9]{24}$", description="Pipeline id from list_pipelines.")]
RunId = Annotated[str, Field(pattern=r"^run-[a-f0-9]{24}$", description="Run id.")]
TeamId = Annotated[str, Field(pattern=r"^team-[a-f0-9]{32}$", description="Team id from whoami.")]
Source = Annotated[dict[str, Any], Field(description="The source definition, as list_templates shows it. Refer to "
                                                     "secrets as \"$secret:<name>\".")]
Resources = Annotated[dict[str, Any], Field(description="Per resource: writeDisposition (append, replace or merge), "
                                                        "primaryKey (list, needed for merge) and partition (list).")]


class Verifier:
    def __init__(self, verifier):
        self.verifier = verifier

    async def verify_token(self, token):
        try:
            caller = await self.verifier.verify(token)
        except IngestionError:
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
        "iceberg-ingestion", title="Ingestion (dlt)", instructions=INSTRUCTIONS, token_verifier=Verifier(verifier),
        auth=AuthSettings(issuer_url=settings.issuer, resource_server_url=settings.origin + "/mcp",
                          validate_token_resource=False),
        log_level="WARNING",
    )

    async def call(fn, *args, **kwargs):
        try:
            return await fn(current(), *args, **kwargs)
        except IngestionError as exc:
            detail = f" Detail: {exc.detail}" if exc.detail else ""
            raise ToolError(f"{exc} [{exc.code}]{detail}") from None

    @mcp.tool(annotations=READ)
    async def whoami() -> dict[str, Any]:
        """Your teams with your role in each, their databases per environment, where Ingestion is enabled, and
        this extension."""
        return await call(services.whoami)

    @mcp.tool(annotations=READ)
    async def list_environments(team: TeamId) -> dict[str, Any]:
        """Where Ingestion is enabled for a team, and the databases of each environment."""
        return {"environments": await call(services.environments, team)}

    @mcp.tool(annotations=WRITE)
    async def enable_environment(team: TeamId, environment: Environment) -> dict[str, Any]:
        """Enable Ingestion for a team environment (team administrators). Runs there act as the team's service
        account. Ask the user first."""
        return await call(services.enable_environment, team, environment)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def disable_environment(team: TeamId, environment: Environment) -> dict[str, Any]:
        """Disable Ingestion for a team environment (team administrators): its pipelines stop running."""
        return await call(services.disable_environment, team, environment)

    @mcp.tool(annotations=READ)
    async def list_templates() -> dict[str, Any]:
        """Pipeline templates: a complete definition per source type to adapt, with the secrets each needs."""
        return {"templates": await call(services.list_templates)}

    @mcp.tool(annotations=READ)
    async def list_pipelines(team: TeamId | None = None, environment: Environment | None = None) -> dict[str, Any]:
        """Pipelines of your teams with their schedule and last run; optionally of one team or environment."""
        return {"pipelines": await call(services.list_pipelines, team, environment)}

    @mcp.tool(annotations=READ)
    async def get_pipeline(pipeline: PipelineId) -> dict[str, Any]:
        """A pipeline's definition, which secrets are set or missing (never their values), schedule and recent
        runs."""
        return await call(services.get_pipeline, pipeline)

    @mcp.tool(annotations=READ)
    async def validate_pipeline(source_type: SourceType, source: Source, resources: Resources | None = None,
                                team: TeamId | None = None, environment: Environment | None = None,
                                database: str | None = None, namespace: str | None = None) -> dict[str, Any]:
        """Check a definition without saving it: allowed keys, addresses, secrets it needs and resource hints."""
        data = {"sourceType": source_type, "source": source, "resources": resources, "team": team,
                "environment": environment, "database": database, "namespace": namespace}
        return await call(services.validate_pipeline, {k: v for k, v in data.items() if v is not None})

    @mcp.tool(annotations=WRITE)
    async def create_pipeline(team: TeamId, name: Annotated[str, Field(max_length=64)],
                              database: Annotated[str, Field(max_length=100, description="Team database id or name.")],
                              namespace: Annotated[str, Field(max_length=64, description="Such as raw_crm.")],
                              source_type: SourceType, source: Source, resources: Resources | None = None,
                              environment: Environment = "development",
                              description: Annotated[str, Field(max_length=500)] = "") -> dict[str, Any]:
        """Create a pipeline (team administrators). Then set its secrets and run it."""
        return await call(services.create_pipeline, {
            "team": team, "environment": environment, "name": name, "description": description,
            "database": database, "namespace": namespace, "sourceType": source_type, "source": source,
            "resources": resources})

    @mcp.tool(annotations=WRITE)
    async def update_pipeline(pipeline: PipelineId, source: Source | None = None, resources: Resources | None = None,
                              name: Annotated[str | None, Field(max_length=64)] = None,
                              description: Annotated[str | None, Field(max_length=500)] = None,
                              database: Annotated[str | None, Field(max_length=100)] = None,
                              namespace: Annotated[str | None, Field(max_length=64)] = None,
                              source_type: SourceType | None = None,
                              expected_revision: int | None = None) -> dict[str, Any]:
        """Change a pipeline (team administrators). Give only what changes; source and resources replace the old
        ones whole. A new host removes the secrets; a new database or namespace starts loading from scratch."""
        data = {"source": source, "resources": resources, "name": name, "description": description,
                "database": database, "namespace": namespace, "sourceType": source_type}
        return await call(services.update_pipeline, pipeline, {k: v for k, v in data.items() if v is not None},
                          expected_revision)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_pipeline(pipeline: PipelineId, confirm_name: Annotated[str, Field(max_length=64)]
                              ) -> dict[str, Any]:
        """Delete a pipeline with its runs, schedule and secrets (team administrators). The tables it loaded
        stay. confirm_name must be the pipeline's name. Ask the user first."""
        return await call(services.delete_pipeline, pipeline, confirm_name)

    @mcp.tool(annotations=WRITE)
    async def set_pipeline_secrets(pipeline: PipelineId,
                                   secrets: Annotated[dict[str, str | None], Field(
                                       description="Secret name → value, or null to remove it.")]) -> dict[str, Any]:
        """Set, replace or remove a pipeline's secrets (team administrators). Write-only: no tool returns them.
        Prefer that the user enters secrets in the Ingestion page; use this only with a value the user gave you
        for it."""
        return await call(services.set_pipeline_secrets, pipeline, secrets)

    @mcp.tool(annotations=RUN)
    async def run_pipeline(pipeline: PipelineId, full_refresh: bool = False,
                           wait: Annotated[int, Field(ge=0, le=600)] = 0) -> dict[str, Any]:
        """Run a pipeline now (writers). full_refresh loads everything again. wait blocks up to that many
        seconds for the run to finish."""
        return await call(services.run_pipeline, pipeline, full_refresh, wait)

    @mcp.tool(annotations=READ)
    async def list_runs(pipeline: PipelineId, limit: Annotated[int, Field(ge=1, le=200)] = 20) -> dict[str, Any]:
        """A pipeline's runs, newest first."""
        return {"runs": await call(services.list_runs, pipeline, limit)}

    @mcp.tool(annotations=READ)
    async def get_run(run: RunId) -> dict[str, Any]:
        """A run's status, the tables it loaded with their row counts, and its error."""
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
    async def set_schedule(pipeline: PipelineId, cron: Annotated[str, Field(max_length=100)],
                           enabled: bool = True) -> dict[str, Any]:
        """Run a pipeline on a five-field cron schedule in UTC, at most every 5 minutes (team administrators). A
        due run is skipped while the pipeline is still running."""
        return await call(services.set_schedule, pipeline, cron, enabled)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_schedule(pipeline: PipelineId) -> dict[str, Any]:
        """Remove a pipeline's schedule (team administrators)."""
        return await call(services.delete_schedule, pipeline)

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
                "resource_name": "Ingestion (dlt)"}

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
