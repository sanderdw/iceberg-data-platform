"""REST API v1. Every operation is also an MCP tool (OPERATIONS), so scripts and AI agents outside the
chat get the same governed queries; the chat UI loads query results through `getResult`.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi import Path as PathParam

from .errors import CbiError
from .semantic.compiler import ModelRef, QueryInput, QuerySpec

# operationId → MCP tool name. tests/test_api.py keeps both sides complete.
OPERATIONS = {
    "whoami": "whoami", "listEnvironments": "list_environments", "enableEnvironment": "enable_environment",
    "disableEnvironment": "disable_environment", "listModels": "list_models", "describeModel": "describe_model",
    "runQuery": "query", "getResult": "get_result",
}

Environment = Literal["development", "acceptance", "production"]
TeamId = Annotated[str, PathParam(pattern=r"^team-[a-f0-9]{32}$")]
ResultId = Annotated[str, PathParam(pattern=r"^res-[a-f0-9]{24}$")]
CSRF_HEADER = "x-iceberg-cbi"


def router(services, caller_dependency):
    api = APIRouter(prefix="/api/v1")
    Caller = Annotated[object, Depends(caller_dependency)]

    @api.get("/me", operation_id="whoami", summary="Your teams, roles, databases, received shares and this extension")
    async def whoami(caller: Caller):
        return await services.whoami(caller)

    @api.get("/teams/{team}/environments", operation_id="listEnvironments",
             summary="Where Conversational BI is enabled for a team")
    async def environments(team: TeamId, caller: Caller):
        return await services.environments(caller, team)

    @api.post("/teams/{team}/environments/{environment}", operation_id="enableEnvironment",
              summary="Enable Conversational BI for a team environment (team administrators)")
    async def enable(team: TeamId, environment: Environment, caller: Caller):
        return await services.enable_environment(caller, team, environment)

    @api.delete("/teams/{team}/environments/{environment}", operation_id="disableEnvironment",
                summary="Disable Conversational BI for a team environment (team administrators)")
    async def disable(team: TeamId, environment: Environment, caller: Caller):
        return await services.disable_environment(caller, team, environment)

    @api.get("/models", operation_id="listModels",
             summary="Semantic models of your teams and of data shares your teams received")
    async def models(caller: Caller):
        return await services.list_models(caller)

    @api.get("/models/describe", operation_id="describeModel",
             summary="A model's datasets, fields, metrics and the dimensions each metric can be split by")
    async def describe(caller: Caller, database: Annotated[str, Query(pattern=r"^db-[a-f0-9]{32}$")],
                       namespace: Annotated[str, Query(max_length=2000, description="Namespace levels joined by '.'")],
                       name: Annotated[str, Query(pattern=r"^[A-Za-z0-9_-]{1,256}$")]):
        ref = ModelRef(database=database, namespace=namespace.split("."), name=name)
        return await services.describe_model(caller, ref)

    @api.post("/queries", operation_id="runQuery",
              summary="Run a governed query: metrics by dimensions, compiled from the semantic model")
    async def query(data: Annotated[QueryInput, Body()], caller: Caller):
        spec = QuerySpec.model_validate(data.model_dump(exclude={"model"}, exclude_unset=True))
        return await services.run_query(caller, data.model, spec)

    @api.get("/results/{result}", operation_id="getResult", summary="A query result you asked for, by id")
    async def result(result: ResultId, caller: Caller,
                     offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=50000)] = 50000):
        found = await services.get_result(caller, result)
        return {**found, "rows": found["rows"][offset:offset + limit]}

    return api


def require_json_write(request: Request):
    """Cookie-authenticated writes need the API header: a browser form cannot set it."""
    if request.method not in ("GET", "HEAD") and not request.headers.get("authorization"):
        if request.headers.get(CSRF_HEADER) != "1":
            raise CbiError(403, "Send the X-Iceberg-Cbi: 1 header with browser requests.", "csrf")
