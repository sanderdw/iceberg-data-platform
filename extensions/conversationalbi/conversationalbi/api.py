"""REST API v1. Every operation is also an MCP tool (OPERATIONS), so scripts and AI agents outside the
chat get the same governed queries; the chat UI loads query results through `getResult`.
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi import Path as PathParam
from pydantic import BaseModel, Field

from .errors import CbiError
from .semantic.compiler import ModelRef, QueryInput, QuerySpec

# operationId → MCP tool name. tests/test_api.py keeps both sides complete.
OPERATIONS = {
    "whoami": "whoami", "listEnvironments": "list_environments", "enableEnvironment": "enable_environment",
    "disableEnvironment": "disable_environment", "listSemanticModels": "list_semantic_models",
    "describeSemanticModel": "describe_semantic_model", "querySemanticModel": "query_semantic_model",
    "getResult": "get_result",
}

EnvironmentName = Literal["development", "acceptance", "production"]
TeamId = Annotated[str, PathParam(pattern=r"^team-[a-f0-9]{32}$")]
ResultId = Annotated[str, PathParam(pattern=r"^res-[a-f0-9]{24}$")]
CSRF_HEADER = "x-iceberg-cbi"

# Responses. Every key a service returns is declared here; a key that is not always there has a default and is
# left out of the response when the service did not return it (response_model_exclude_unset).

Timestamp = Annotated[str, Field(description="RFC 3339 in UTC with milliseconds, such as 2026-11-24T09:15:02.123Z.")]


class Error(BaseModel):
    error: str = Field(description="What went wrong, for a person.")
    code: str = Field(description="A stable code for programs, such as unknown_model.")
    detail: dict[str, Any] | None = None


class User(BaseModel):
    id: str
    name: str


class Membership(BaseModel):
    team: str
    teamName: str
    role: str


class Database(BaseModel):
    id: str
    name: str
    team: str
    environment: str
    status: str


class EnabledEnvironments(BaseModel):
    team: str
    environments: list[EnvironmentName]


class WorkspacePage(BaseModel):
    id: str
    title: str
    url: str


class ExtensionLink(BaseModel):
    id: str
    title: str
    purpose: str
    origin: str


class Apps(BaseModel):
    workspace: list[WorkspacePage]
    extensions: list[ExtensionLink]


class SharedDatabase(BaseModel):
    id: str
    name: str
    team: str = Field(description="The team that owns the database and shares from it.")
    ownerTeamName: str
    environment: str
    recipientTeams: list[str] = Field(description="Your teams that received the share.")


class Me(BaseModel):
    user: User
    memberships: list[Membership]
    databases: list[Database]
    extension: str
    origin: str
    environments: list[EnvironmentName]
    enabledEnvironments: list[EnabledEnvironments]
    userPortalUrl: str
    apps: Apps
    sharedData: bool = Field(description="Whether the platform supports asking about shared semantic models.")
    sharedDatabases: list[SharedDatabase]


class DatabaseName(BaseModel):
    id: str
    name: str


class Environment(BaseModel):
    team: str
    environment: EnvironmentName
    enabled: bool
    enabledBy: str | None = Field(description="Who enabled it; null when it is not enabled.")
    databases: list[DatabaseName]
    sharedDatabases: list[DatabaseName] = Field(description="Databases the team received a share of there.")


class EnvironmentList(BaseModel):
    environments: list[Environment]


class SemanticModelRef(BaseModel):
    database: str = Field(description="Database id.")
    namespace: list[str]
    name: str


class SemanticModel(BaseModel):
    model: SemanticModelRef = Field(description="The reference to pass to describe and to a query.")
    key: str
    databaseName: str
    environment: EnvironmentName
    team: str = Field(description="The team that owns the model.")
    teamName: str
    shared: bool = Field(description="True when one of your teams received the model through a data share.")
    readingTeam: str = Field(description="Your team whose access reads the data.")
    readingTeamName: str


class NotEnabled(BaseModel):
    team: str
    teamName: str
    environment: EnvironmentName
    canEnable: bool = Field(description="Whether you administer the team, so you can enable it there.")


class SemanticModelList(BaseModel):
    semanticModels: list[SemanticModel]
    notEnabled: list[NotEnabled] = Field(description="Team environments with models to ask about once Conversational "
                                                     "BI is enabled there.")


ERRORS = {status: {"model": Error, "description": description} for status, description in {
    401: "No, invalid or expired sign-in", 403: "Not allowed", 404: "No such resource in your teams",
    409: "Not possible in the current state", 422: "The request is not valid", 500: "Unexpected error",
    502: "The platform refused or failed", 503: "Temporarily unavailable"}.items()}


def router(services, caller_dependency):
    api = APIRouter(prefix="/api/v1", responses=ERRORS)
    Caller = Annotated[object, Depends(caller_dependency)]
    # A response holds exactly what the service returned: an optional key it left out stays out.
    shape = {"response_model_exclude_unset": True}

    @api.get("/me", operation_id="whoami", response_model=Me, **shape,
             summary="Your teams, roles, databases, received shares and this extension")
    async def whoami(caller: Caller):
        return await services.whoami(caller)

    @api.get("/teams/{team}/environments", operation_id="listEnvironments", response_model=EnvironmentList, **shape,
             summary="Where Conversational BI is enabled for a team, with what it can read in each environment")
    async def environments(team: TeamId, caller: Caller):
        return await services.environments(caller, team)

    @api.post("/teams/{team}/environments/{environment}", operation_id="enableEnvironment",
              response_model=Environment, **shape,
              summary="Enable Conversational BI for a team environment (team administrators)")
    async def enable(team: TeamId, environment: EnvironmentName, caller: Caller):
        return await services.enable_environment(caller, team, environment)

    @api.delete("/teams/{team}/environments/{environment}", operation_id="disableEnvironment",
                response_model=Environment, **shape,
                summary="Disable Conversational BI for a team environment (team administrators)")
    async def disable(team: TeamId, environment: EnvironmentName, caller: Caller):
        return await services.disable_environment(caller, team, environment)

    @api.get("/semantic-models", operation_id="listSemanticModels", response_model=SemanticModelList, **shape,
             summary="Semantic models of your teams and of data shares your teams received")
    async def semantic_models(caller: Caller):
        return await services.list_semantic_models(caller)

    @api.get("/semantic-models/describe", operation_id="describeSemanticModel",
             summary="A model's datasets, fields, metrics and the dimensions each metric can be split by")
    async def describe(caller: Caller, database: Annotated[str, Query(pattern=r"^db-[a-f0-9]{32}$")],
                       namespace: Annotated[list[str], Query(min_length=1, max_length=20, description=(
                           "Namespace levels in order; repeat the parameter for nested levels."))],
                       model: Annotated[str, Query(pattern=r"^[A-Za-z0-9_-]{1,256}$",
                                                   description="The semantic model's name.")]):
        ref = ModelRef(database=database, namespace=namespace, name=model)
        return await services.describe_model(caller, ref)

    @api.post("/queries", operation_id="querySemanticModel",
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
