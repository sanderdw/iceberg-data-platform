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

EnvironmentName = Literal["development", "acceptance", "production"]
SourceType = Literal["postgres"]
ReplicationId = Annotated[str, PathParam(pattern=r"^rp-[a-f0-9]{24}$")]
RunId = Annotated[str, PathParam(pattern=r"^run-[a-f0-9]{24}$")]
TeamId = Annotated[str, PathParam(pattern=r"^team-[a-f0-9]{32}$")]
SOURCE_HELP = ("The source connection for the source type, as list_templates shows it: host, port, database, "
               "username, password, tls (verify-full, verify-ca, require, disable), optional caCertificate (PEM, "
               "for a source whose certificate no public CA signed), and slot and publication for change data "
               "capture. Secret values never go "
               "here: refer to them as \"$secret:<name>\" and set them with set_replication_secrets.")
WAIT_HELP = "Seconds to wait for the run to finish. The answer is the run as it is then, final or not."
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
    environment: EnvironmentName | None = None
    database: str | None = Field(default=None, max_length=100)
    namespace: str | None = Field(default=None, max_length=64)


class ReplicationInput(Definition):
    team: str = Field(pattern=r"^team-[a-f0-9]{32}$")
    environment: EnvironmentName = "development"
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
    expectedRevision: int | None = Field(default=None,
                                         description="Fail if the replication changed since this revision.")


class SecretsInput(Input):
    secrets: dict[str, str | None] = Field(description="Secret name → value, or null to remove it. Write-only.")


class RunInput(Input):
    fullRefresh: bool = Field(default=False, description="Copy everything again: start without the saved change "
                                                         "log position. Rows are upserted on their key.")
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
    code: str = Field(description="A stable code for programs, such as unknown_replication.")
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
    allowedSourceNetworks: list[str] = Field(description="Private networks the platform allows as a source.")


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
    tables: dict[str, Any]
    options: dict[str, Any]
    secrets: list[str]
    setupSql: str
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
    replication: str
    environment: EnvironmentName
    status: RunStatus = Field(description="incomplete: the first copy stopped at the time limit and the next run "
                                          "continues it. Runs an earlier version stored show that as partial.")
    triggeredBy: str = Field(description="The person's name, or \"schedule\".")
    agent: str | None = Field(description="The MCP client when an agent started the run.")
    scheduled: bool
    kind: Literal["sync", "check"] = Field(description="sync replicates; check only connects to the source.")
    revision: int = Field(description="The replication's revision the run used.")
    fullRefresh: bool
    createdAt: Timestamp
    startedAt: Timestamp | None
    finishedAt: Timestamp | None
    exitCode: int | None
    summary: dict[str, Any] = Field(description="The run's totals: records read and written, how far it read the "
                                                "change log, whether the first copy is complete, and the table "
                                                "properties it published.")
    error: str | None
    hint: str | None = None


class Run(RunItem):
    results: list[dict[str, Any]] = Field(description="Per table: name, table, records written, rows replaced or "
                                                      "deleted, and delete files. Empty until the run has results.")


class RunList(BaseModel):
    runs: list[RunItem]


class RunLogs(BaseModel):
    run: str
    status: RunStatus
    lines: list[str]


class CancelResult(BaseModel):
    run: str
    cancelling: bool


class SourceCheck(BaseModel):
    """A source check. While it is still running, only run, status and hint."""
    run: str
    status: RunStatus
    checkedAt: Timestamp | None = None
    connected: bool | None = None
    ready: bool | None = None
    message: str | None = None
    checks: list[dict[str, Any]] = Field(default=[], description="OLake's prerequisite checks.")
    tables: list[dict[str, Any]] = Field(default=[], description="The tables OLake can replicate, with primary "
                                                                 "keys, cursor columns and modes.")
    hint: str | None = None


class Replication(BaseModel):
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
    tables: dict[str, Any]
    options: dict[str, Any]
    secretsSet: list[str]
    secretsMissing: list[str]
    revision: int
    createdAt: Timestamp
    createdBy: str
    updatedAt: Timestamp
    updatedBy: str
    role: str | None = Field(description="Your role in the replication's team.")
    url: str
    schedule: Schedule | None
    lastRun: RunItem | None = Field(description="The last run that replicated; source checks do not count.")
    lastCheck: SourceCheck | None
    setupSql: str = Field(description="What the database owner runs once so the replication can follow changes.")
    warnings: list[str]
    hint: str | None = None


class ReplicationDetail(Replication):
    recentRuns: list[RunItem]
    note: str


class ReplicationList(BaseModel):
    replications: list[Replication]


class Validation(BaseModel):
    valid: bool
    problems: list[str]
    source: dict[str, Any]
    tables: dict[str, Any]
    options: dict[str, Any]
    secretsNeeded: list[str]
    setupSql: str
    warnings: list[str]
    note: str


class Deleted(BaseModel):
    deleted: bool
    replication: str
    note: str | None = None
    dropSlotSql: str | None = None
    warning: str | None = None


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
             summary="Where Replication is enabled for a team, with each environment's databases")
    async def environments(team: TeamId, caller: Caller):
        return await services.environments(caller, team)

    @api.post("/teams/{team}/environments/{environment}", operation_id="enableEnvironment",
              response_model=Environment, **shape,
              summary="Enable Replication for a team environment (team administrators)")
    async def enable(team: TeamId, environment: EnvironmentName, caller: Caller):
        return await services.enable_environment(caller, team, environment)

    @api.delete("/teams/{team}/environments/{environment}", operation_id="disableEnvironment",
                response_model=Environment, **shape,
                summary="Disable Replication for a team environment (team administrators)")
    async def disable(team: TeamId, environment: EnvironmentName, caller: Caller):
        return await services.disable_environment(caller, team, environment)

    @api.get("/templates", operation_id="listTemplates", response_model=TemplateList, **shape,
             summary="Replication templates to start from, with the source setup SQL")
    async def list_templates(caller: Caller):
        return await services.list_templates(caller)

    @api.get("/replications", operation_id="listReplications", response_model=ReplicationList, **shape,
             summary="Replications of your teams, with their last run")
    async def replications(caller: Caller, team: Annotated[str | None, Query(pattern=r"^team-[a-f0-9]{32}$")] = None,
                           environment: EnvironmentName | None = None):
        return await services.list_replications(caller, team, environment)

    @api.post("/replications/validate", operation_id="validateReplication", response_model=Validation, **shape,
              summary="Check a replication definition without saving it")
    async def validate_replication(data: Annotated[ValidateInput, Body()], caller: Caller):
        return await services.validate_replication(caller, data.model_dump(exclude_none=True))

    @api.post("/replications", operation_id="createReplication", status_code=201, response_model=Replication,
              **shape, summary="Create a replication (team administrators)")
    async def create_replication(data: Annotated[ReplicationInput, Body()], caller: Caller):
        return await services.create_replication(caller, data.model_dump())

    @api.get("/replications/{replication}", operation_id="getReplication", response_model=ReplicationDetail,
             **shape, summary="A replication with its definition, secrets set, schedule and recent runs")
    async def get_replication(replication: ReplicationId, caller: Caller):
        return await services.get_replication(caller, replication)

    @api.patch("/replications/{replication}", operation_id="updateReplication", response_model=Replication,
               **shape, summary="Change a replication; what is left out stays (team administrators)")
    async def update_replication(replication: ReplicationId, data: Annotated[ReplicationChange, Body()],
                                 caller: Caller):
        change = data.model_dump(exclude_none=True)
        return await services.update_replication(caller, replication, change, change.pop("expectedRevision", None))

    @api.delete("/replications/{replication}", operation_id="deleteReplication", response_model=Deleted, **shape,
                summary="Delete a replication, its runs and schedule; its tables stay (team administrators)")
    async def delete_replication(replication: ReplicationId, caller: Caller, confirm_name: Annotated[str, Query(
            alias="confirmName", max_length=64, description="The replication's exact name.")]):
        return await services.delete_replication(caller, replication, confirm_name)

    @api.patch("/replications/{replication}/secrets", operation_id="setReplicationSecrets",
               response_model=Replication, **shape,
               summary="Set or remove a replication's secrets; they are never returned (team administrators)")
    async def set_secrets(replication: ReplicationId, data: Annotated[SecretsInput, Body()], caller: Caller):
        return await services.set_replication_secrets(caller, replication, data.secrets)

    @api.post("/replications/{replication}/check", operation_id="checkSource", response_model=SourceCheck, **shape,
              summary="Connect to the source: OLake's checks and the tables it can replicate (writers)")
    async def check_source(replication: ReplicationId, caller: Caller):
        return await services.check_source(caller, replication)

    @api.post("/replications/{replication}/runs", operation_id="runReplication", status_code=202,
              response_model=Run, **shape, summary="Run a replication now (writers)")
    async def run_replication(replication: ReplicationId, caller: Caller,
                              data: Annotated[RunInput | None, Body()] = None):
        data = data or RunInput()
        return await services.run_replication(caller, replication, data.fullRefresh, data.wait)

    @api.get("/replications/{replication}/runs", operation_id="listRuns", response_model=RunList, **shape,
             summary="A replication's runs, newest first")
    async def runs(replication: ReplicationId, caller: Caller, limit: Annotated[int, Query(ge=1, le=200)] = 20):
        return await services.list_runs(caller, replication, limit)

    @api.get("/runs/{run}", operation_id="getRun", response_model=Run, **shape,
             summary="A run with its tables and change counts")
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

    @api.put("/replications/{replication}/schedule", operation_id="setSchedule", response_model=Schedule, **shape,
             summary="Run a replication on a cron schedule in UTC (team administrators)")
    async def set_schedule(replication: ReplicationId, data: Annotated[ScheduleInput, Body()], caller: Caller):
        return await services.set_schedule(caller, replication, data.cron, data.enabled)

    @api.delete("/replications/{replication}/schedule", operation_id="deleteSchedule", response_model=Deleted,
                **shape, summary="Remove a replication's schedule (team administrators)")
    async def delete_schedule(replication: ReplicationId, caller: Caller):
        return await services.delete_schedule(caller, replication)

    return api


def require_json_write(request: Request):
    """Cookie-authenticated writes need the API header: a browser form cannot set it."""
    if request.method not in ("GET", "HEAD") and not request.headers.get("authorization"):
        if request.headers.get("x-iceberg-replication") != "1":
            raise ReplicationError(403, "Send the X-Iceberg-Replication: 1 header with browser requests.", "csrf")
