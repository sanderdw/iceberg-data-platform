"""MCP server: the dbt stack for AI agents, with the same capabilities and rules as the REST API.

Agents sign in through Keycloak with the public `ext-<id>-mcp` client (PKCE, loopback redirect),
so every tool runs with the signed-in user's team roles; builds run as the team's automation
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
from .errors import DbtError

LOG = logging.getLogger(__name__)
MAX_BODY = 512 * 1024
READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False,
                              open_world_hint=False)
INSTRUCTIONS = """dbt pipelines on the Iceberg Data Platform (dbt v2 OSS with DuckDB on Apache Iceberg).

Start with whoami (your teams, roles and databases) and list_projects. A project belongs to one
team and is a Git repository. main is protected: create_branch, then write_files (one commit per
call; paths map to full file contents, null deletes), compile to check, run with
command=build in development on your branch, read get_run and get_run_logs, and ask a team
administrator to merge_to_main. Acceptance and production only build main.

Project rules: `schema` is the Iceberg namespace, `+catalog_name` picks the team database by its
display name with '-' as '_'. Keep `+materialized: replace_table` (keeps table identity, data
shares and column docs) or use incremental. Iceberg views are not supported. Never add
profiles.yml or catalogs.yml: they are generated for each run. Document models and columns in
YAML: descriptions become table comments and column docs in the platform catalog, and tests
become the table's quality status.

get_pipeline returns the DAG with each node's latest status; get_model and get_lineage explain
one node. Runs act for the whole team: ask the user before running in acceptance or production,
before merge_to_main, and before any tool marked destructive."""

Environment = Literal["development", "acceptance", "production"]
ProjectId = Annotated[str, Field(pattern=r"^prj-[a-f0-9]{24}$", description="Project id from list_projects.")]
RunId = Annotated[str, Field(pattern=r"^run-[a-f0-9]{24}$", description="Run id.")]
TeamId = Annotated[str, Field(pattern=r"^team-[a-f0-9]{32}$", description="Team id from whoami.")]
Ref = Annotated[str, Field(max_length=100, description="Branch name or revision.")]
Select = Annotated[str, Field(max_length=500, description="dbt node selection, such as 'stg_orders+' or 'tag:daily'.")]
Node = Annotated[str, Field(max_length=300, description="Node id from get_pipeline, such as model.sales.orders.")]


class Verifier:
    def __init__(self, verifier):
        self.verifier = verifier

    async def verify_token(self, token):
        try:
            caller = await self.verifier.verify(token)
        except DbtError:
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
        "iceberg-dbt", title="dbt pipelines", instructions=INSTRUCTIONS, token_verifier=Verifier(verifier),
        auth=AuthSettings(issuer_url=settings.issuer, resource_server_url=settings.origin + "/mcp",
                          validate_token_resource=False),
        log_level="WARNING",
    )

    async def call(fn, *args, **kwargs):
        try:
            return await fn(current(), *args, **kwargs)
        except DbtError as exc:
            detail = f" Detail: {exc.detail}" if exc.detail else ""
            raise ToolError(f"{exc} [{exc.code}]{detail}") from None

    @mcp.tool(annotations=READ)
    async def whoami() -> dict[str, Any]:
        """Your teams with your role in each, their databases per environment, and this extension."""
        return await call(services.whoami)

    @mcp.tool(annotations=READ)
    async def list_environments(team: TeamId) -> dict[str, Any]:
        """Where dbt is enabled for a team, and the databases of each environment."""
        return {"environments": await call(services.environments, team)}

    @mcp.tool(annotations=WRITE)
    async def enable_environment(team: TeamId, environment: Environment) -> dict[str, Any]:
        """Let dbt build in a team environment as the team's service account (team administrators)."""
        return await call(services.enable_environment, team, environment)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def disable_environment(team: TeamId, environment: Environment) -> dict[str, Any]:
        """Revoke dbt's service account for a team environment; its runs and schedules stop working."""
        return await call(services.disable_environment, team, environment)

    @mcp.tool(annotations=READ)
    async def list_projects() -> dict[str, Any]:
        """dbt projects of your teams, with your role in each."""
        return {"projects": await call(services.list_projects)}

    @mcp.tool(annotations=WRITE)
    async def create_project(team: TeamId, name: str, description: str = "",
                             default_database: str = "") -> dict[str, Any]:
        """Create a project from a runnable starter (seed, staging and mart models with tests) on main.

        name: 3–48 lowercase letters, digits or '_'. default_database: display name of the team
        database models land in (defaults to the first).
        """
        return await call(services.create_project, team, name, description, default_database)

    @mcp.tool(annotations=READ)
    async def get_project(project: ProjectId) -> dict[str, Any]:
        """A project with branches, environments, latest run per environment and schedules."""
        return await call(services.get_project, project)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_project(project: ProjectId, confirm_name: str) -> dict[str, Any]:
        """Delete a project and its Git history (team administrators). Tables it built stay."""
        return await call(services.delete_project, project, confirm_name)

    @mcp.tool(annotations=READ)
    async def list_files(project: ProjectId, ref: Ref = "main") -> dict[str, Any]:
        """All files of the project at a branch or revision, with the revision."""
        return await call(services.list_files, project, ref)

    @mcp.tool(annotations=READ)
    async def read_file(project: ProjectId, path: str, ref: Ref = "main") -> dict[str, Any]:
        """One file's content at a branch or revision."""
        return await call(services.read_file, project, path, ref)

    @mcp.tool(annotations=WRITE)
    async def write_files(project: ProjectId, branch: str, files: dict[str, str | None], message: str,
                          expected_revision: str | None = None) -> dict[str, Any]:
        """Commit file changes to a branch in one commit (writers). main is never written directly.

        files maps paths to their full new content, or null to delete. Pass expected_revision from
        list_files to fail instead of overwriting someone else's newer commit.
        """
        return await call(services.write_files, project, branch, files, message, expected_revision)

    @mcp.tool(annotations=READ)
    async def list_branches(project: ProjectId) -> dict[str, Any]:
        """Branches with their latest commit."""
        return {"branches": await call(services.branches, project)}

    @mcp.tool(annotations=WRITE)
    async def create_branch(project: ProjectId, name: str, source: Ref = "main") -> dict[str, Any]:
        """Create a branch from main or another branch (writers)."""
        return await call(services.create_branch, project, name, source)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_branch(project: ProjectId, name: str) -> dict[str, Any]:
        """Delete a branch (writers). main cannot be deleted."""
        return await call(services.delete_branch, project, name)

    @mcp.tool(annotations=READ)
    async def get_history(project: ProjectId, ref: Ref = "main", limit: int = 30) -> dict[str, Any]:
        """Recent commits of a branch."""
        return {"commits": await call(services.history, project, ref, limit)}

    @mcp.tool(annotations=READ)
    async def diff(project: ProjectId, head: Ref, base: Ref = "main") -> dict[str, Any]:
        """Changed files and the patch between two branches or revisions."""
        return await call(services.diff, project, base, head)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def merge_to_main(project: ProjectId, branch: str, message: str = "") -> dict[str, Any]:
        """Merge a branch into main (team administrators). Acceptance and production build main."""
        return await call(services.merge_to_main, project, branch, message)

    @mcp.tool(annotations=READ)
    async def compile(project: ProjectId, environment: Environment = "development", ref: Ref = "main",
                      select: Select = "", sql: str = "") -> dict[str, Any]:
        """Compile selected models (or inline SQL) with a read-only token; returns compiled SQL and output."""
        return await call(services.compile, project, environment, ref, select, sql)

    @mcp.tool(annotations=READ)
    async def preview_model(project: ProjectId, model: str, environment: Environment = "development",
                            ref: Ref = "main", limit: int = 20) -> dict[str, Any]:
        """Run one model's query without writing and return up to 100 rows (read-only token)."""
        return await call(services.preview, project, model, environment, ref, limit)

    @mcp.tool(annotations=WRITE)
    async def run(project: ProjectId,
                  command: Literal["build", "run", "seed", "test", "compile", "parse", "docs"] = "build",
                  environment: Environment = "development", ref: Ref = "main", select: Select = "",
                  full_refresh: bool = False, wait: int = 120) -> dict[str, Any]:
        """Run dbt as the team's service account. build, run and seed write tables (writers).

        Waits up to `wait` seconds (max 600) and returns the run; poll get_run for longer runs.
        full_refresh recreates tables whose columns changed.
        """
        return await call(services.run, project, command, environment, ref, select, full_refresh, min(wait, 600))

    @mcp.tool(annotations=READ)
    async def list_runs(project: ProjectId, environment: Environment | None = None, limit: int = 30) -> dict[str, Any]:
        """Recent runs, newest first."""
        return {"runs": await call(services.list_runs, project, environment, limit)}

    @mcp.tool(annotations=READ)
    async def get_run(run: RunId, wait: int = 0) -> dict[str, Any]:
        """A run with per-node results; wait up to `wait` seconds (max 600) for it to finish."""
        return await call(services.get_run, run, wait)

    @mcp.tool(annotations=READ)
    async def get_run_logs(run: RunId, tail: int = 400) -> dict[str, Any]:
        """The last lines of a run's dbt log, with compiler and database errors."""
        return await call(services.run_logs, run, tail)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def cancel_run(run: RunId) -> dict[str, Any]:
        """Stop a running run (writers)."""
        return await call(services.cancel_run, run)

    @mcp.tool(annotations=READ)
    async def list_schedules(project: ProjectId) -> dict[str, Any]:
        """Schedules of a project."""
        return {"schedules": await call(services.list_schedules, project)}

    @mcp.tool(annotations=WRITE)
    async def set_schedule(project: ProjectId, environment: Environment, cron: str,
                           command: Literal["build", "run", "seed", "test"] = "build", select: Select = "",
                           ref: Ref = "main", enabled: bool = True, schedule: str | None = None) -> dict[str, Any]:
        """Create or change a schedule (cron in UTC). Development needs writers, other environments administrators."""
        return await call(services.set_schedule, project, environment, cron, command, select, ref, enabled, schedule)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_schedule(schedule: str) -> dict[str, Any]:
        """Delete a schedule."""
        return await call(services.delete_schedule, schedule)

    @mcp.tool(annotations=READ)
    async def get_pipeline(project: ProjectId, environment: Environment = "development") -> dict[str, Any]:
        """The pipeline DAG: nodes with type, table, latest status and test counts, and edges."""
        return await call(services.pipeline, project, environment)

    @mcp.tool(annotations=READ)
    async def get_model(project: ProjectId, node: Node, environment: Environment = "development") -> dict[str, Any]:
        """One node: description, columns with docs, SQL and compiled SQL, tests and latest result."""
        return await call(services.model, project, node, environment)

    @mcp.tool(annotations=READ)
    async def get_lineage(project: ProjectId, node: Node, environment: Environment = "development",
                          depth: int = 3) -> dict[str, Any]:
        """Upstream and downstream nodes of a node, up to `depth` steps."""
        return await call(services.lineage, project, node, environment, depth)

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
                "resource_name": "dbt pipelines"}

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
