"""REST API v1. Every operation has an MCP tool of the same capability (OPERATIONS), so agents
and scripts can do everything; the replications page uses the same API with the browser session.
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi import Path as PathParam
from pydantic import BaseModel, ConfigDict, Field

from .errors import ReplicationError

# operationId → MCP tool name. tests/test_api.py keeps both sides complete.
OPERATIONS = {
    "whoami": "whoami", "listEnvironments": "list_environments", "enableEnvironment": "enable_environment",
    "disableEnvironment": "disable_environment", "listTemplates": "list_templates",
    "listReplications": "list_replications", "getReplication": "get_replication",
    "validateReplication": "validate_replication", "createReplication": "create_replication",
    "updateReplication": "update_replication", "deleteReplication": "delete_replication",
    "setReplicationSecrets": "set_replication_secrets", "checkSource": "check_source",
    "runReplication": "run_replication", "listRuns": "list_runs",
    "getRun": "get_run", "getRunLogs": "get_run_logs", "cancelRun": "cancel_run", "setSchedule": "set_schedule",
    "deleteSchedule": "delete_schedule",
}

Environment = Literal["development", "acceptance", "production"]
SourceType = Literal["postgres"]
ReplicationId = Annotated[str, PathParam(pattern=r"^rp-[a-f0-9]{24}$")]
RunId = Annotated[str, PathParam(pattern=r"^run-[a-f0-9]{24}$")]
TeamId = Annotated[str, PathParam(pattern=r"^team-[a-f0-9]{32}$")]
SOURCE_HELP = ("The source connection for the source type, as list_templates shows it: host, port, database, "
               "username, password, tls, and slot and publication for change data capture. Secret values never go "
               "here: refer to them as \"$secret:<name>\" and set them with set_replication_secrets.")
TABLES_HELP = ("Source tables keyed by schema.table: mode cdc (change data capture), incremental (needs cursor) or "
               "full_refresh; optional table (the Iceberg table name), partition (such as \"/{created_at,month}\") and "
               "appendOnly (for tables without a primary key).")
OPTIONS_HELP = ("deleteMode pos (positional deletes, default) or dv (deletion vectors, Iceberg v3); firstRun copy "
                "(existing rows, then changes; default) or changes (only changes from now on).")


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Definition(Input):
    sourceType: SourceType | None = None
    source: dict[str, Any] | None = Field(default=None, description=SOURCE_HELP)
    tables: dict[str, Any] | None = Field(default=None, description=TABLES_HELP)
    options: dict[str, Any] | None = Field(default=None, description=OPTIONS_HELP)


class ValidateInput(Definition):
    team: str | None = Field(default=None, pattern=r"^team-[a-f0-9]{32}$")
    environment: Environment | None = None
    database: str | None = Field(default=None, max_length=100)
    namespace: str | None = Field(default=None, max_length=64)


class ReplicationInput(Definition):
    team: str = Field(pattern=r"^team-[a-f0-9]{32}$")
    environment: Environment = "development"
    name: str = Field(max_length=64, description="Up to 64 letters, digits, spaces, '.', '_' or '-'.")
    description: str = Field(default="", max_length=500)
    database: str = Field(max_length=100, description="A team database in this environment, by id or name.")
    namespace: str = Field(max_length=64, description="Where tables land, such as raw_crm.")
    sourceType: SourceType
    source: dict[str, Any] = Field(description=SOURCE_HELP)


class ReplicationChange(Definition):
    name: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=500)
    database: str | None = Field(default=None, max_length=100)
    namespace: str | None = Field(default=None, max_length=64)
    expected_revision: int | None = Field(default=None,
                                          description="Fail if the replication changed since this revision.")


class DeleteInput(Input):
    confirm_name: str = Field(max_length=64)


class SecretsInput(Input):
    secrets: dict[str, str | None] = Field(description="Secret name → value, or null to remove it. Write-only.")


class RunInput(Input):
    full_refresh: bool = Field(default=False, description="Copy everything again: start without the saved change "
                                                          "log position. Rows are upserted on their key.")
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
             summary="Where Replication is enabled for a team, with each environment's databases")
    async def environments(team: TeamId, caller: Caller):
        return await services.environments(caller, team)

    @api.post("/teams/{team}/environments/{environment}", operation_id="enableEnvironment",
              summary="Enable Replication for a team environment (team administrators)")
    async def enable(team: TeamId, environment: Environment, caller: Caller):
        return await services.enable_environment(caller, team, environment)

    @api.delete("/teams/{team}/environments/{environment}", operation_id="disableEnvironment",
                summary="Disable Replication for a team environment (team administrators)")
    async def disable(team: TeamId, environment: Environment, caller: Caller):
        return await services.disable_environment(caller, team, environment)

    @api.get("/templates", operation_id="listTemplates",
             summary="Replication templates to start from, with the source setup SQL")
    async def list_templates(caller: Caller):
        return await services.list_templates(caller)

    @api.get("/replications", operation_id="listReplications",
             summary="Replications of your teams, with their last run")
    async def replications(caller: Caller, team: Annotated[str | None, Query(pattern=r"^team-[a-f0-9]{32}$")] = None,
                        environment: Environment | None = None):
        return await services.list_replications(caller, team, environment)

    @api.post("/replications/validate", operation_id="validateReplication",
              summary="Check a replication definition without saving it")
    async def validate_replication(data: Annotated[ValidateInput, Body()], caller: Caller):
        return await services.validate_replication(caller, data.model_dump(exclude_none=True))

    @api.post("/replications", operation_id="createReplication", status_code=201,
              summary="Create a replication (team administrators)")
    async def create_replication(data: Annotated[ReplicationInput, Body()], caller: Caller):
        return await services.create_replication(caller, data.model_dump())

    @api.get("/replications/{replication}", operation_id="getReplication",
             summary="A replication with its definition, secrets set, schedule and recent runs")
    async def get_replication(replication: ReplicationId, caller: Caller):
        return await services.get_replication(caller, replication)

    @api.patch("/replications/{replication}", operation_id="updateReplication",
               summary="Change a replication (team administrators)")
    async def update_replication(replication: ReplicationId, data: Annotated[ReplicationChange, Body()],
                                 caller: Caller):
        change = data.model_dump(exclude_none=True)
        return await services.update_replication(caller, replication, change, change.pop("expected_revision", None))

    @api.post("/replications/{replication}/delete", operation_id="deleteReplication",
              summary="Delete a replication, its runs and schedule; its tables stay (team administrators)")
    async def delete_replication(replication: ReplicationId, data: Annotated[DeleteInput, Body()], caller: Caller):
        return await services.delete_replication(caller, replication, data.confirm_name)

    @api.put("/replications/{replication}/secrets", operation_id="setReplicationSecrets",
             summary="Set or remove a replication's secrets; they are never returned (team administrators)")
    async def set_secrets(replication: ReplicationId, data: Annotated[SecretsInput, Body()], caller: Caller):
        return await services.set_replication_secrets(caller, replication, data.secrets)

    @api.post("/replications/{replication}/check", operation_id="checkSource",
              summary="Connect to the source: OLake's checks and the tables it can replicate (writers)")
    async def check_source(replication: ReplicationId, caller: Caller):
        return await services.check_source(caller, replication)

    @api.post("/replications/{replication}/runs", operation_id="runReplication", status_code=202,
              summary="Run a replication now (writers)")
    async def run_replication(replication: ReplicationId, caller: Caller,
                              data: Annotated[RunInput | None, Body()] = None):
        data = data or RunInput()
        return await services.run_replication(caller, replication, data.full_refresh, data.wait)

    @api.get("/replications/{replication}/runs", operation_id="listRuns", summary="A replication's runs, newest first")
    async def runs(replication: ReplicationId, caller: Caller, limit: Annotated[int, Query(ge=1, le=200)] = 20):
        return await services.list_runs(caller, replication, limit)

    @api.get("/runs/{run}", operation_id="getRun", summary="A run with its tables and change counts")
    async def run(run: RunId, caller: Caller):
        return await services.get_run(caller, run)

    @api.get("/runs/{run}/logs", operation_id="getRunLogs", summary="A run's log, with secrets masked")
    async def logs(run: RunId, caller: Caller, tail: Annotated[int, Query(ge=1, le=5000)] = 500):
        return await services.get_run_logs(caller, run, tail)

    @api.post("/runs/{run}/cancel", operation_id="cancelRun", summary="Stop a run (writers)")
    async def cancel(run: RunId, caller: Caller):
        return await services.cancel_run(caller, run)

    @api.put("/replications/{replication}/schedule", operation_id="setSchedule",
             summary="Run a replication on a cron schedule in UTC (team administrators)")
    async def set_schedule(replication: ReplicationId, data: Annotated[ScheduleInput, Body()], caller: Caller):
        return await services.set_schedule(caller, replication, data.cron, data.enabled)

    @api.delete("/replications/{replication}/schedule", operation_id="deleteSchedule",
                summary="Remove a replication's schedule (team administrators)")
    async def delete_schedule(replication: ReplicationId, caller: Caller):
        return await services.delete_schedule(caller, replication)

    return api


def require_json_write(request: Request):
    """Cookie-authenticated writes need the API header: a browser form cannot set it."""
    if request.method not in ("GET", "HEAD") and not request.headers.get("authorization"):
        if request.headers.get("x-iceberg-replication") != "1":
            raise ReplicationError(403, "Send the X-Iceberg-Replication: 1 header with browser requests.", "csrf")
