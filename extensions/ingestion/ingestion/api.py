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

Environment = Literal["development", "acceptance", "production"]
SourceType = Literal["rest_api", "sql_database", "filesystem"]
PipelineId = Annotated[str, PathParam(pattern=r"^pl-[a-f0-9]{24}$")]
RunId = Annotated[str, PathParam(pattern=r"^run-[a-f0-9]{24}$")]
TeamId = Annotated[str, PathParam(pattern=r"^team-[a-f0-9]{32}$")]
SOURCE_HELP = ("The source definition for the source type, as list_templates shows it. Secret values never go here: "
               "refer to them as \"$secret:<name>\" and set them with set_pipeline_secrets.")
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
    environment: Environment | None = None
    database: str | None = Field(default=None, max_length=100)
    namespace: str | None = Field(default=None, max_length=64)


class PipelineInput(Definition):
    team: str = Field(pattern=r"^team-[a-f0-9]{32}$")
    environment: Environment = "development"
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
    expected_revision: int | None = Field(default=None, description="Fail if the pipeline changed since this revision.")


class DeleteInput(Input):
    confirm_name: str = Field(max_length=64)


class SecretsInput(Input):
    secrets: dict[str, str | None] = Field(description="Secret name → value, or null to remove it. Write-only.")


class RunInput(Input):
    full_refresh: bool = Field(default=False, description="Load everything again: ignore incremental state and "
                                                          "replace the tables' rows.")
    wait: int = Field(default=0, ge=0, le=600, description="Seconds to wait for the run to finish.")


class ScheduleInput(Input):
    cron: str = Field(max_length=100, description="Five-field cron expression in UTC, at most every 5 minutes.")
    enabled: bool = True


def router(services, caller_dependency):
    api = APIRouter(prefix="/api/v1")
    Caller = Annotated[object, Depends(caller_dependency)]

    @api.get("/me", operation_id="whoami", summary="Your teams, roles, databases and this extension")
    async def whoami(caller: Caller):
        return await services.whoami(caller)

    @api.get("/teams/{team}/environments", operation_id="listEnvironments",
             summary="Where Ingestion is enabled for a team, with each environment's databases")
    async def environments(team: TeamId, caller: Caller):
        return await services.environments(caller, team)

    @api.post("/teams/{team}/environments/{environment}", operation_id="enableEnvironment",
              summary="Enable Ingestion for a team environment (team administrators)")
    async def enable(team: TeamId, environment: Environment, caller: Caller):
        return await services.enable_environment(caller, team, environment)

    @api.delete("/teams/{team}/environments/{environment}", operation_id="disableEnvironment",
                summary="Disable Ingestion for a team environment (team administrators)")
    async def disable(team: TeamId, environment: Environment, caller: Caller):
        return await services.disable_environment(caller, team, environment)

    @api.get("/templates", operation_id="listTemplates", summary="Pipeline templates to start from")
    async def list_templates(caller: Caller):
        return await services.list_templates(caller)

    @api.get("/pipelines", operation_id="listPipelines", summary="Pipelines of your teams, with their last run")
    async def pipelines(caller: Caller, team: Annotated[str | None, Query(pattern=r"^team-[a-f0-9]{32}$")] = None,
                        environment: Environment | None = None):
        return await services.list_pipelines(caller, team, environment)

    @api.post("/pipelines/validate", operation_id="validatePipeline",
              summary="Check a pipeline definition without saving it")
    async def validate_pipeline(data: Annotated[ValidateInput, Body()], caller: Caller):
        return await services.validate_pipeline(caller, data.model_dump(exclude_none=True))

    @api.post("/pipelines", operation_id="createPipeline", status_code=201,
              summary="Create a pipeline (team administrators)")
    async def create_pipeline(data: Annotated[PipelineInput, Body()], caller: Caller):
        return await services.create_pipeline(caller, data.model_dump())

    @api.get("/pipelines/{pipeline}", operation_id="getPipeline",
             summary="A pipeline with its definition, secrets set, schedule and recent runs")
    async def get_pipeline(pipeline: PipelineId, caller: Caller):
        return await services.get_pipeline(caller, pipeline)

    @api.patch("/pipelines/{pipeline}", operation_id="updatePipeline",
               summary="Change a pipeline (team administrators)")
    async def update_pipeline(pipeline: PipelineId, data: Annotated[PipelineChange, Body()], caller: Caller):
        change = data.model_dump(exclude_none=True)
        return await services.update_pipeline(caller, pipeline, change, change.pop("expected_revision", None))

    @api.post("/pipelines/{pipeline}/delete", operation_id="deletePipeline",
              summary="Delete a pipeline, its runs and schedule; its tables stay (team administrators)")
    async def delete_pipeline(pipeline: PipelineId, data: Annotated[DeleteInput, Body()], caller: Caller):
        return await services.delete_pipeline(caller, pipeline, data.confirm_name)

    @api.put("/pipelines/{pipeline}/secrets", operation_id="setPipelineSecrets",
             summary="Set or remove a pipeline's secrets; they are never returned (team administrators)")
    async def set_secrets(pipeline: PipelineId, data: Annotated[SecretsInput, Body()], caller: Caller):
        return await services.set_pipeline_secrets(caller, pipeline, data.secrets)

    @api.post("/pipelines/{pipeline}/runs", operation_id="runPipeline", status_code=202,
              summary="Run a pipeline now (writers)")
    async def run_pipeline(pipeline: PipelineId, caller: Caller, data: Annotated[RunInput | None, Body()] = None):
        data = data or RunInput()
        return await services.run_pipeline(caller, pipeline, data.full_refresh, data.wait)

    @api.get("/pipelines/{pipeline}/runs", operation_id="listRuns", summary="A pipeline's runs, newest first")
    async def runs(pipeline: PipelineId, caller: Caller, limit: Annotated[int, Query(ge=1, le=200)] = 20):
        return await services.list_runs(caller, pipeline, limit)

    @api.get("/runs/{run}", operation_id="getRun", summary="A run with its tables and row counts")
    async def run(run: RunId, caller: Caller):
        return await services.get_run(caller, run)

    @api.get("/runs/{run}/logs", operation_id="getRunLogs", summary="A run's log, with secrets masked")
    async def logs(run: RunId, caller: Caller, tail: Annotated[int, Query(ge=1, le=5000)] = 500):
        return await services.get_run_logs(caller, run, tail)

    @api.post("/runs/{run}/cancel", operation_id="cancelRun", summary="Stop a run (writers)")
    async def cancel(run: RunId, caller: Caller):
        return await services.cancel_run(caller, run)

    @api.put("/pipelines/{pipeline}/schedule", operation_id="setSchedule",
             summary="Run a pipeline on a cron schedule in UTC (team administrators)")
    async def set_schedule(pipeline: PipelineId, data: Annotated[ScheduleInput, Body()], caller: Caller):
        return await services.set_schedule(caller, pipeline, data.cron, data.enabled)

    @api.delete("/pipelines/{pipeline}/schedule", operation_id="deleteSchedule",
                summary="Remove a pipeline's schedule (team administrators)")
    async def delete_schedule(pipeline: PipelineId, caller: Caller):
        return await services.delete_schedule(caller, pipeline)

    return api


def require_json_write(request: Request):
    """Cookie-authenticated writes need the API header: a browser form cannot set it."""
    if request.method not in ("GET", "HEAD") and not request.headers.get("authorization"):
        if request.headers.get("x-iceberg-ingestion") != "1":
            raise IngestionError(403, "Send the X-Iceberg-Ingestion: 1 header with browser requests.", "csrf")
