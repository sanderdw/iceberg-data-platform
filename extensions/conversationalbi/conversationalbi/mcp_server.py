"""MCP server: Conversational BI for AI agents, with the same capabilities and rules as the REST API.

Agents sign in through Keycloak with the public `ext-<id>-mcp` client (PKCE, loopback redirect),
so every tool runs with the signed-in user's teams; queries read data as the team's automation
principal with a read-only token. There are no destructive data tools: this extension only reads.
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
from .errors import CbiError
from .semantic.compiler import Dimension, Filter, ModelRef, Order, QuerySpec

LOG = logging.getLogger(__name__)
MAX_BODY = 256 * 1024
MCP_ROWS = 200
READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True,
                              open_world_hint=False)
INSTRUCTIONS = """Conversational BI on the Iceberg Data Platform: governed questions over semantic models.

Start with list_models: the Apache Ossie semantic models of your teams and of data shares your
teams received. describe_model explains one model: datasets, fields, metrics (with their SQL and
unit) and which dimensions each metric can be split by. Then call query with metric names,
dimensions written as DATASET.field, and optional filters, a time grain, ordering and a limit.
The platform compiles the SQL from the model: you never write SQL, and metric definitions and
joins are always the model owner's. When a dimension can be reached along several relationships
(for example an airport of departure or of destination), the error names the options: pass one in
`via`. A dimension that would repeat rows of the metric's dataset is refused (fan_out).

State the metric definition, unit, time window, filters and join path with every answer, and
follow the model's instructions. Results are also available by resultId with get_result. When a
team has not enabled Conversational BI for an environment, a team administrator calls
enable_environment."""

Environment = Literal["development", "acceptance", "production"]
TeamId = Annotated[str, Field(pattern=r"^team-[a-f0-9]{32}$", description="Team id from whoami.")]
Database = Annotated[str, Field(pattern=r"^db-[a-f0-9]{32}$", description="Database id from list_models.")]
Namespace = Annotated[list[str], Field(min_length=1, max_length=20, description="Namespace levels, from list_models.")]
ModelName = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,256}$", description="Semantic model name.")]


class Verifier:
    def __init__(self, verifier):
        self.verifier = verifier

    async def verify_token(self, token):
        try:
            caller = await self.verifier.verify(token)
        except CbiError:
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
        "iceberg-conversationalbi", title="Conversational BI", instructions=INSTRUCTIONS,
        token_verifier=Verifier(verifier),
        auth=AuthSettings(issuer_url=settings.issuer, resource_server_url=settings.origin + "/mcp",
                          validate_token_resource=False),
        log_level="WARNING",
    )

    async def call(fn, *args, **kwargs):
        try:
            return await fn(current(), *args, **kwargs)
        except CbiError as exc:
            detail = f" Detail: {exc.detail}" if exc.detail else ""
            raise ToolError(f"{exc} [{exc.code}]{detail}") from None

    @mcp.tool(annotations=READ)
    async def whoami() -> dict[str, Any]:
        """Your teams with your role in each, their databases, the data shares they received, and this extension."""
        return await call(services.whoami)

    @mcp.tool(annotations=READ)
    async def list_environments(team: TeamId) -> dict[str, Any]:
        """Where Conversational BI is enabled for a team."""
        return {"environments": await call(services.environments, team)}

    @mcp.tool(annotations=WRITE)
    async def enable_environment(team: TeamId, environment: Environment) -> dict[str, Any]:
        """Let Conversational BI read a team environment, and the shares the team received there, with a read-only
        service account (team administrators)."""
        return await call(services.enable_environment, team, environment)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def disable_environment(team: TeamId, environment: Environment) -> dict[str, Any]:
        """Revoke Conversational BI's service account for a team environment; questions there stop working."""
        return await call(services.disable_environment, team, environment)

    @mcp.tool(annotations=READ)
    async def list_models() -> dict[str, Any]:
        """Semantic models you can ask questions of, own and shared, and environments that still need enabling."""
        return await call(services.list_models)

    @mcp.tool(annotations=READ)
    async def describe_model(database: Database, namespace: Namespace, name: ModelName) -> dict[str, Any]:
        """Datasets, fields, metrics and reachable dimensions of one semantic model, with the owner's instructions."""
        return await call(services.describe_model, ModelRef(database=database, namespace=namespace, name=name))

    @mcp.tool(annotations=READ)
    async def query(database: Database, namespace: Namespace, model: ModelName,
                    metrics: Annotated[list[str], Field(min_length=1, max_length=5)],
                    dimensions: Annotated[list[Dimension], Field(max_length=5)] = [],  # noqa: B006
                    filters: Annotated[list[Filter], Field(max_length=10)] = [],  # noqa: B006
                    order_by: Annotated[list[Order], Field(max_length=5)] = [],  # noqa: B006
                    limit: Annotated[int, Field(ge=1, le=5000)] = 100) -> dict[str, Any]:
        """Run a governed query: metrics split by dimensions (DATASET.field, optional grain and via), with filters.
        Returns at most 200 rows inline; get_result returns the rest by resultId."""
        spec = QuerySpec(metrics=metrics, dimensions=dimensions, filters=filters, order_by=order_by, limit=limit)
        result = await call(services.run_query, ModelRef(database=database, namespace=namespace, name=model), spec)
        return {**services.for_llm(result, MCP_ROWS), "rowsUrl": f"{settings.origin}/api/v1/results/{result['id']}"}

    @mcp.tool(annotations=READ)
    async def get_result(result_id: Annotated[str, Field(pattern=r"^res-[a-f0-9]{24}$")],
                         offset: Annotated[int, Field(ge=0)] = 0,
                         limit: Annotated[int, Field(ge=1, le=MCP_ROWS)] = MCP_ROWS) -> dict[str, Any]:
        """Rows of a result you asked for earlier (results are kept for 30 minutes)."""
        found = await call(services.get_result, result_id)
        return {**services.for_llm(found, 0), "rows": found["rows"][offset:offset + limit], "offset": offset}

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
                "resource_name": "Conversational BI"}

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
