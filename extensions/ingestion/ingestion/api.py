"""REST API v1. Every operation has an MCP tool of the same capability (OPERATIONS), so agents
and scripts can do everything; the pipelines page uses the same API with the browser session.
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi import Path as PathParam
from pydantic import BaseModel, ConfigDict, Field

from .errors import IngestionError

# operationId → MCP tool name. tests/test_api.py keeps both sides complete.
OPERATIONS = {
    "whoami": "whoami", "listEnvironments": "list_environments", "enableEnvironment": "enable_environment",
    "disableEnvironment": "disable_environment", "listTemplates": "list_templates",
    "listPipelines": "list_pipelines", "getPipeline": "get_pipeline", "validatePipeline": "validate_pipeline",
    "createPipeline": "create_pipeline", "updatePipeline": "update_pipeline", "deletePipeline": "delete_pipeline",
    "setPipelineSecrets": "set_pipeline_secrets", "runPipeline": "run_pipeline", "listRuns": "list_runs",
    "getRun": "get_run", "getRunLogs": "get_run_logs", "cancelRun": "cancel_run", "setSchedule": "set_schedule",
    "deleteSchedule": "delete_schedule",
}

EnvironmentName = Literal["development", "acceptance", "production"]
SourceType = Literal["rest_api", "sql_database", "filesystem"]
PipelineId = Annotated[str, PathParam(pattern=r"^pl-[a-f0-9]{24}$")]
RunId = Annotated[str, PathParam(pattern=r"^run-[a-f0-9]{24}$")]
TeamId = Annotated[str, PathParam(pattern=r"^team-[a-f0-9]{32}$")]
SOURCE_HELP = ("The source definition for the source type, as list_templates shows it. Secret values never go here: "
               "refer to them as \"$secret:<name>\" and set them with set_pipeline_secrets.")
WAIT_HELP = "Seconds to wait for the run to finish. The answer is the run as it is then, final or not."
RESOURCES_HELP = ("Per resource (endpoint, table or file): writeDisposition append, replace or merge (merge needs "
                  "primaryKey), and partition columns.")


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Definition(Input):
    sourceType: SourceType | None = None
    source: dict[str, Any] | None = Field(default=None, description=SOURCE_HELP)
    resources: dict[str, Any] | None = Field(default=None, description=RESOURCES_HELP)


class ValidateInput(Definition):
    team: str | None = Field(default=None, pattern=r"^team-[a-f0-9]{32}$")
    environment: EnvironmentName | None = None
    database: str | None = Field(default=None, max_length=100)
    namespace: str | None = Field(default=None, max_length=64)


class PipelineInput(Definition):
    team: str = Field(pattern=r"^team-[a-f0-9]{32}$")
    environment: EnvironmentName = "development"
    name: str = Field(max_length=64, description="Up to 64 letters, digits, spaces, '.', '_' or '-'.")
    description: str = Field(default="", max_length=500)
    database: str = Field(max_length=100, description="A team database in this environment, by id or name.")
    namespace: str = Field(max_length=64, description="Where tables land, such as raw_crm (dlt's dataset).")
    sourceType: SourceType
    source: dict[str, Any] = Field(description=SOURCE_HELP)


class PipelineChange(Definition):
    name: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=500)
    database: str | None = Field(default=None, max_length=100)
    namespace: str | None = Field(default=None, max_length=64)
    expectedRevision: int | None = Field(default=None, description="Fail if the pipeline changed since this revision.")


class SecretsInput(Input):
    secrets: dict[str, str | None] = Field(description="Secret name → value, or null to remove it. Write-only.")


class RunInput(Input):
    fullRefresh: bool = Field(default=False, description="Load everything again: ignore incremental state and "
                                                         "replace the tables' rows.")
    wait: int = Field(default=0, ge=0, le=600, description=WAIT_HELP)


class ScheduleInput(Input):
    cron: str = Field(max_length=100, description="Five-field cron expression in UTC, at most every 5 minutes.")
    enabled: bool = True


# Responses. Every key a service returns is declared here; a key that is not always there has a default and is
# left out of the response when the service did not return it (response_model_exclude_unset).

Timestamp = Annotated[str, Field(description="RFC 3339 in UTC with milliseconds, such as 2026-11-24T09:15:02.123Z.")]
RunStatus = Literal["queued", "running", "success", "incomplete", "partial", "failed", "error", "cancelled"]


class Error(BaseModel):
    error: str = Field(description="What went wrong, for a person.")
    code: str = Field(description="A stable code for programs, such as unknown_pipeline.")
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
    sourceTypes: list[SourceType]
    allowPrivateSources: bool


class DatabaseName(BaseModel):
    id: str
    name: str


class Environment(BaseModel):
    team: str
    environment: EnvironmentName
    enabled: bool
    enabledBy: str | None = Field(description="Who enabled it; null when it is not enabled.")
    databases: list[DatabaseName]


class EnvironmentList(BaseModel):
    environments: list[Environment]


class Template(BaseModel):
    id: str
    title: str
    description: str
    sourceType: SourceType
    namespace: str
    source: dict[str, Any]
    resources: dict[str, Any]
    secrets: list[str]
    note: str


class TemplateList(BaseModel):
    templates: list[Template]


class Schedule(BaseModel):
    cron: str
    enabled: bool
    nextRunAt: Timestamp | None = Field(description="Null while the schedule is not enabled.")
    lastRun: str | None = Field(description="The id of the last run the schedule started.")
    createdBy: str
    createdAt: Timestamp


class RunItem(BaseModel):
    id: str
    pipeline: str
    environment: EnvironmentName
    status: RunStatus
    triggeredBy: str = Field(description="The person's name, or \"schedule\".")
    agent: str | None = Field(description="The MCP client when an agent started the run.")
    scheduled: bool
    revision: int = Field(description="The pipeline's revision the run used.")
    fullRefresh: bool
    createdAt: Timestamp
    startedAt: Timestamp | None
    finishedAt: Timestamp | None
    exitCode: int | None
    summary: dict[str, Any] = Field(description="The run's totals: rows, the dlt load id and version, and the "
                                                "table properties it published.")
    error: str | None
    hint: str | None = None


class Run(RunItem):
    results: list[dict[str, Any]] = Field(description="Per table: name and rows loaded. Empty until the run has "
                                                      "results.")


class RunList(BaseModel):
    runs: list[RunItem]


class RunLogs(BaseModel):
    run: str
    status: RunStatus
    lines: list[str]


class CancelResult(BaseModel):
    run: str
    cancelling: bool


class Pipeline(BaseModel):
    id: str
    name: str
    description: str
    team: str
    environment: EnvironmentName
    database: str
    databaseName: str | None
    namespace: str
    sourceType: SourceType
    source: dict[str, Any]
    resources: dict[str, Any]
    secretsSet: list[str]
    secretsMissing: list[str]
    revision: int
    createdAt: Timestamp
    createdBy: str
    updatedAt: Timestamp
    updatedBy: str
    role: str | None = Field(description="Your role in the pipeline's team.")
    url: str
    schedule: Schedule | None
    lastRun: RunItem | None
    hint: str | None = None


class PipelineDetail(Pipeline):
    recentRuns: list[RunItem]
    note: str


class PipelineList(BaseModel):
    pipelines: list[Pipeline]


class Validation(BaseModel):
    valid: bool
    problems: list[str]
    source: dict[str, Any]
    resources: dict[str, Any]
    secretsNeeded: list[str]
    resourceNames: list[str]
    note: str


class Deleted(BaseModel):
    deleted: bool
    pipeline: str
    note: str | None = None


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
             summary="Your teams, roles, databases and this extension")
    async def whoami(caller: Caller):
        return await services.whoami(caller)

    @api.get("/teams/{team}/environments", operation_id="listEnvironments", response_model=EnvironmentList, **shape,
             summary="Where Ingestion is enabled for a team, with each environment's databases")
    async def environments(team: TeamId, caller: Caller):
        return await services.environments(caller, team)

    @api.post("/teams/{team}/environments/{environment}", operation_id="enableEnvironment",
              response_model=Environment, **shape,
              summary="Enable Ingestion for a team environment (team administrators)")
    async def enable(team: TeamId, environment: EnvironmentName, caller: Caller):
        return await services.enable_environment(caller, team, environment)

    @api.delete("/teams/{team}/environments/{environment}", operation_id="disableEnvironment",
                response_model=Environment, **shape,
                summary="Disable Ingestion for a team environment (team administrators)")
    async def disable(team: TeamId, environment: EnvironmentName, caller: Caller):
        return await services.disable_environment(caller, team, environment)

    @api.get("/templates", operation_id="listTemplates", response_model=TemplateList, **shape,
             summary="Pipeline templates to start from")
    async def list_templates(caller: Caller):
        return await services.list_templates(caller)

    @api.get("/pipelines", operation_id="listPipelines", response_model=PipelineList, **shape,
             summary="Pipelines of your teams, with their last run")
    async def pipelines(caller: Caller, team: Annotated[str | None, Query(pattern=r"^team-[a-f0-9]{32}$")] = None,
                        environment: EnvironmentName | None = None):
        return await services.list_pipelines(caller, team, environment)

    @api.post("/pipelines/validate", operation_id="validatePipeline", response_model=Validation, **shape,
              summary="Check a pipeline definition without saving it")
    async def validate_pipeline(data: Annotated[ValidateInput, Body()], caller: Caller):
        return await services.validate_pipeline(caller, data.model_dump(exclude_none=True))

    @api.post("/pipelines", operation_id="createPipeline", status_code=201, response_model=Pipeline, **shape,
              summary="Create a pipeline (team administrators)")
    async def create_pipeline(data: Annotated[PipelineInput, Body()], caller: Caller):
        return await services.create_pipeline(caller, data.model_dump())

    @api.get("/pipelines/{pipeline}", operation_id="getPipeline", response_model=PipelineDetail, **shape,
             summary="A pipeline with its definition, secrets set, schedule and recent runs")
    async def get_pipeline(pipeline: PipelineId, caller: Caller):
        return await services.get_pipeline(caller, pipeline)

    @api.patch("/pipelines/{pipeline}", operation_id="updatePipeline", response_model=Pipeline, **shape,
               summary="Change a pipeline; what is left out stays (team administrators)")
    async def update_pipeline(pipeline: PipelineId, data: Annotated[PipelineChange, Body()], caller: Caller):
        change = data.model_dump(exclude_none=True)
        return await services.update_pipeline(caller, pipeline, change, change.pop("expectedRevision", None))

    @api.delete("/pipelines/{pipeline}", operation_id="deletePipeline", response_model=Deleted, **shape,
                summary="Delete a pipeline, its runs and schedule; its tables stay (team administrators)")
    async def delete_pipeline(pipeline: PipelineId, caller: Caller, confirm_name: Annotated[str, Query(
            alias="confirmName", max_length=64, description="The pipeline's exact name.")]):
        return await services.delete_pipeline(caller, pipeline, confirm_name)

    @api.patch("/pipelines/{pipeline}/secrets", operation_id="setPipelineSecrets", response_model=Pipeline, **shape,
               summary="Set or remove a pipeline's secrets; they are never returned (team administrators)")
    async def set_secrets(pipeline: PipelineId, data: Annotated[SecretsInput, Body()], caller: Caller):
        return await services.set_pipeline_secrets(caller, pipeline, data.secrets)

    @api.post("/pipelines/{pipeline}/runs", operation_id="runPipeline", status_code=202, response_model=Run, **shape,
              summary="Run a pipeline now (writers)")
    async def run_pipeline(pipeline: PipelineId, caller: Caller, data: Annotated[RunInput | None, Body()] = None):
        data = data or RunInput()
        return await services.run_pipeline(caller, pipeline, data.fullRefresh, data.wait)

    @api.get("/pipelines/{pipeline}/runs", operation_id="listRuns", response_model=RunList, **shape,
             summary="A pipeline's runs, newest first")
    async def runs(pipeline: PipelineId, caller: Caller, limit: Annotated[int, Query(ge=1, le=200)] = 20):
        return await services.list_runs(caller, pipeline, limit)

    @api.get("/runs/{run}", operation_id="getRun", response_model=Run, **shape,
             summary="A run with its tables and row counts")
    async def run(run: RunId, caller: Caller,
                  wait: Annotated[int, Query(ge=0, le=600, description=WAIT_HELP)] = 0):
        return await services.get_run(caller, run, wait)

    @api.get("/runs/{run}/logs", operation_id="getRunLogs", response_model=RunLogs, **shape,
             summary="The last lines of a run's log, with secrets masked")
    async def logs(run: RunId, caller: Caller, limit: Annotated[int, Query(ge=1, le=5000)] = 500):
        return await services.get_run_logs(caller, run, limit)

    @api.post("/runs/{run}/cancel", operation_id="cancelRun", response_model=CancelResult, **shape,
              summary="Stop a run (writers)")
    async def cancel(run: RunId, caller: Caller):
        return await services.cancel_run(caller, run)

    @api.put("/pipelines/{pipeline}/schedule", operation_id="setSchedule", response_model=Schedule, **shape,
             summary="Run a pipeline on a cron schedule in UTC (team administrators)")
    async def set_schedule(pipeline: PipelineId, data: Annotated[ScheduleInput, Body()], caller: Caller):
        return await services.set_schedule(caller, pipeline, data.cron, data.enabled)

    @api.delete("/pipelines/{pipeline}/schedule", operation_id="deleteSchedule", response_model=Deleted, **shape,
                summary="Remove a pipeline's schedule (team administrators)")
    async def delete_schedule(pipeline: PipelineId, caller: Caller):
        return await services.delete_schedule(caller, pipeline)

    return api


def require_json_write(request: Request):
    """Cookie-authenticated writes need the API header: a browser form cannot set it."""
    if request.method not in ("GET", "HEAD") and not request.headers.get("authorization"):
        if request.headers.get("x-iceberg-ingestion") != "1":
            raise IngestionError(403, "Send the X-Iceberg-Ingestion: 1 header with browser requests.", "csrf")
