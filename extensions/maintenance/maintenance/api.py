"""REST API v1. Every operation has an MCP tool of the same capability (OPERATIONS), so agents
and scripts can do everything; the page uses the same API with the browser session.
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi import Path as PathParam
from pydantic import BaseModel, ConfigDict, Field

from .errors import MaintenanceError

# operationId → MCP tool name. tests/test_api.py keeps both sides complete. Starting a run is one operation with
# two tools, so that only the one that changes tables is marked destructive.
OPERATIONS = {
    "whoami": "whoami", "listEnvironments": "list_environments", "enableEnvironment": "enable_environment",
    "disableEnvironment": "disable_environment", "listDatabases": "list_databases",
    "listTableHealth": "list_table_health", "getTableHealth": "get_table_health", "getPolicy": "get_policy",
    "updatePolicy": "update_policy", "deletePolicy": "delete_policy", "setSchedule": "set_schedule",
    "deleteSchedule": "delete_schedule", "runMaintenance": ("inspect_database", "run_maintenance"),
    "listRuns": "list_runs", "getRun": "get_run", "getRunLogs": "get_run_logs", "cancelRun": "cancel_run",
}

EnvironmentName = Literal["development", "acceptance", "production"]
Mode = Literal["inspect", "dry-run", "maintain"]
RunStatus = Literal["queued", "running", "success", "incomplete", "partial", "failed", "error", "cancelled"]
DATABASE = r"^db-[a-f0-9]{32}$"
TABLE = r"^[A-Za-z0-9_$-][A-Za-z0-9_.$-]{1,298}[A-Za-z0-9_$-]$"
DatabaseId = Annotated[str, PathParam(pattern=DATABASE)]
TableName = Annotated[str, PathParam(pattern=TABLE, description="namespace.table")]
RunId = Annotated[str, PathParam(pattern=r"^run-[a-f0-9]{24}$")]
TeamId = Annotated[str, PathParam(pattern=r"^team-[a-f0-9]{32}$")]
Timestamp = Annotated[str, Field(description="RFC 3339 in UTC with milliseconds.",
                                 examples=["2026-11-24T09:15:02.123Z"])]
SETTINGS_HELP = ("Per operation (expireSnapshots, compaction, orphanFiles, rewriteManifests, danglingDeletes), the "
                 "settings to change; the rest keeps its value. whoami lists the defaults.")
MODE_HELP = ("inspect reads every table's health; dry-run plans the policy without changing anything; maintain "
             "applies it (needs a policy).")
WAIT_HELP = "Seconds to wait for the run to finish. The run is returned as it is then, finished or not."
CRON_HELP = "Five-field cron expression in UTC, at most once an hour."


# Requests


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PolicyInput(Input):
    settings: dict[str, dict[str, Any]] | None = Field(default=None, description=SETTINGS_HELP)
    exclude: list[str] | None = Field(default=None, max_length=500,
                                      description="Tables (namespace.table) that maintenance leaves alone; "
                                                  "replaces the list.")
    expectedRevision: int | None = Field(default=None, description="Fail if the policy changed since this revision "
                                                                   "(0: only if there is none yet).")


class ScheduleInput(Input):
    cron: str = Field(min_length=1, max_length=100, description=CRON_HELP)
    enabled: bool = Field(default=True, description="False keeps the schedule and pauses it.")


class RunInput(Input):
    mode: Mode = Field(default="inspect", description=MODE_HELP)
    tables: list[str] | None = Field(default=None, max_length=200,
                                     description="Only these tables (namespace.table); all when empty.")
    wait: int = Field(default=0, ge=0, le=600, description=WAIT_HELP)


# Responses. The services build them (services.public_*); these models describe them in the contract.


class Error(BaseModel):
    error: str = Field(description="What went wrong, for a person.")
    code: str = Field(description="A stable code, for a program.")
    detail: dict[str, Any] | None = Field(default=None, description="Only present when there is more to tell.")


class User(BaseModel):
    id: str
    name: str


class Membership(BaseModel):
    team: str
    teamName: str
    role: str


class MemberDatabase(BaseModel):
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


class ExtensionApp(BaseModel):
    id: str
    title: str
    purpose: str
    origin: str


class Apps(BaseModel):
    workspace: list[WorkspacePage]
    extensions: list[ExtensionApp]


class Me(BaseModel):
    user: User
    memberships: list[Membership]
    databases: list[MemberDatabase]
    extension: str
    origin: str
    environments: list[EnvironmentName]
    enabledEnvironments: list[EnabledEnvironments]
    userPortalUrl: str
    apps: Apps
    operations: dict[str, str] = Field(description="The maintenance operations and their names.")
    defaults: dict[str, dict[str, Any]] = Field(description="What a policy starts with, per operation.")


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


class Schedule(BaseModel):
    cron: str = Field(description="Five fields, UTC.")
    enabled: bool
    nextRunAt: Timestamp | None = Field(description="Null while the schedule is paused.")
    lastRun: str | None = Field(description="The id of the last run this database's schedule started.")


class RunSummary(BaseModel):
    """A run as a list shows it."""

    id: str
    database: str
    team: str
    environment: str
    mode: Mode
    tables: list[str] = Field(description="The tables the run was limited to; empty for all.")
    status: RunStatus
    triggeredBy: str = Field(description="The person's name, or schedule.")
    agent: str | None = Field(description="The MCP client when an agent started it.")
    scheduled: bool
    createdAt: Timestamp
    startedAt: Timestamp | None
    finishedAt: Timestamp | None
    exitCode: int | None
    summary: dict[str, Any] = Field(description="The runner's totals and versions.")
    error: str | None


class Run(RunSummary):
    """A run as get and start return it."""

    databaseName: str
    role: str = Field(description="Your role in the database's team.")
    results: list[dict[str, Any]] = Field(description="Per table: name, status, error and actions. Empty until "
                                                      "the run has results.")


class RunList(BaseModel):
    runs: list[RunSummary]


class RunLogs(BaseModel):
    run: str
    status: RunStatus
    lines: list[str]


class CancelResult(BaseModel):
    run: str
    cancelling: bool


class Deleted(BaseModel):
    deleted: bool
    database: str


class DeletedPolicy(Deleted):
    note: str


class Policy(BaseModel):
    database: str
    databaseName: str
    team: str
    environment: str
    exists: bool = Field(description="False: the database has no policy, and settings are the defaults.")
    settings: dict[str, dict[str, Any]]
    exclude: list[str]
    schedule: Schedule | None
    revision: int
    createdAt: Timestamp | None
    createdBy: str | None
    updatedAt: Timestamp | None
    updatedBy: str | None
    role: str = Field(description="Your role in the database's team.")
    note: str


class Database(BaseModel):
    id: str
    name: str
    team: str
    teamName: str
    environment: str
    role: str
    policy: bool = Field(description="Whether the database has a policy.")
    schedule: Schedule | None
    tables: int = Field(description="Tables seen by the last runs.")
    attention: int = Field(description="Tables that need attention or failed.")
    inspected: bool
    lastRun: RunSummary | None


class DatabaseList(BaseModel):
    databases: list[Database]


class TableHealth(BaseModel):
    database: str
    databaseName: str
    team: str
    environment: str
    name: str = Field(description="namespace.table")
    state: Literal["healthy", "attention", "failed", "no-policy", "excluded"]
    reasons: list[str]
    health: dict[str, Any] = Field(description="What the runner read: snapshots, files, deletes, manifests.")
    inspectedAt: Timestamp
    inspectedBy: str
    lastRun: str | None
    lastStatus: str
    lastError: str | None
    lastActions: list[dict[str, Any]]
    lastMaintainedAt: Timestamp | None


class TableHealthList(BaseModel):
    tables: list[TableHealth]
    total: int = Field(description="How many tables match, before the limit.")


class TableHealthDetail(TableHealth):
    role: str
    policy: Policy
    recentRuns: list[RunSummary]


ERRORS = {status: {"model": Error, "description": text} for status, text in {
    401: "No, invalid or expired token or session", 403: "Refused: not a member, or the role is too low",
    404: "No such resource in your teams", 409: "Not possible in the current state",
    422: "The request is not valid", 500: "Unexpected error", 502: "The platform refused or failed",
    503: "Temporarily unavailable"}.items()}


def router(services, caller_dependency):
    api = APIRouter(prefix="/api/v1", responses=ERRORS)
    Caller = Annotated[object, Depends(caller_dependency)]
    Team = Annotated[str | None, Query(pattern=r"^team-[a-f0-9]{32}$")]

    @api.get("/me", operation_id="whoami", response_model=Me,
             summary="Your teams, roles, databases, the policy defaults and this extension")
    async def whoami(caller: Caller):
        return await services.whoami(caller)

    @api.get("/teams/{team}/environments", operation_id="listEnvironments", response_model=EnvironmentList,
             summary="Where Maintenance is enabled for a team, with each environment's databases")
    async def environments(team: TeamId, caller: Caller):
        return await services.environments(caller, team)

    @api.post("/teams/{team}/environments/{environment}", operation_id="enableEnvironment",
              response_model=Environment, summary="Enable Maintenance for a team environment (team administrators)")
    async def enable(team: TeamId, environment: EnvironmentName, caller: Caller):
        return await services.enable_environment(caller, team, environment)

    @api.delete("/teams/{team}/environments/{environment}", operation_id="disableEnvironment",
                response_model=Environment,
                summary="Disable Maintenance for a team environment (team administrators)")
    async def disable(team: TeamId, environment: EnvironmentName, caller: Caller):
        return await services.disable_environment(caller, team, environment)

    @api.get("/databases", operation_id="listDatabases", response_model=DatabaseList,
             summary="Your teams' databases with their policy, schedule, last run and tables that need attention")
    async def databases(caller: Caller, team: Team = None, environment: EnvironmentName | None = None):
        return await services.list_databases(caller, team, environment)

    @api.get("/tables", operation_id="listTableHealth", response_model=TableHealthList,
             summary="Table health from the last runs, with what needs attention")
    async def tables(caller: Caller, database: Annotated[str | None, Query(pattern=DATABASE)] = None,
                     team: Team = None, environment: EnvironmentName | None = None, attention: bool = False,
                     limit: Annotated[int, Query(ge=1, le=5000)] = 500):
        return await services.list_table_health(caller, database, team, environment, attention, limit)

    @api.get("/databases/{database}/tables/{table}", operation_id="getTableHealth",
             response_model=TableHealthDetail,
             summary="One table's health, its last actions, effective policy and recent runs")
    async def table(database: DatabaseId, table: TableName, caller: Caller):
        return await services.get_table_health(caller, database, table)

    @api.get("/databases/{database}/policy", operation_id="getPolicy", response_model=Policy,
             summary="A database's maintenance policy and schedule, or the defaults when it has none")
    async def get_policy(database: DatabaseId, caller: Caller):
        return await services.get_policy(caller, database)

    @api.patch("/databases/{database}/policy", operation_id="updatePolicy", response_model=Policy,
               summary="Change a database's policy, or create it from the defaults (team administrators)")
    async def update_policy(database: DatabaseId, data: Annotated[PolicyInput, Body()], caller: Caller):
        return await services.update_policy(caller, database, data.model_dump(exclude_unset=True))

    @api.delete("/databases/{database}/policy", operation_id="deletePolicy", response_model=DeletedPolicy,
                summary="Remove a database's policy and schedule (team administrators)")
    async def delete_policy(database: DatabaseId, caller: Caller):
        return await services.delete_policy(caller, database)

    @api.put("/databases/{database}/schedule", operation_id="setSchedule", response_model=Schedule,
             summary="Maintain a database on a cron schedule in UTC; needs a policy (team administrators)")
    async def set_schedule(database: DatabaseId, data: Annotated[ScheduleInput, Body()], caller: Caller):
        return await services.set_schedule(caller, database, data.cron, data.enabled)

    @api.delete("/databases/{database}/schedule", operation_id="deleteSchedule", response_model=Deleted,
                summary="Remove a database's schedule; its policy stays (team administrators)")
    async def delete_schedule(database: DatabaseId, caller: Caller):
        return await services.delete_schedule(caller, database)

    @api.post("/databases/{database}/runs", operation_id="runMaintenance", status_code=202, response_model=Run,
              summary="Inspect, dry-run or maintain a database now (writers)")
    async def run(database: DatabaseId, caller: Caller, data: Annotated[RunInput | None, Body()] = None):
        data = data or RunInput()
        return await services.run_maintenance(caller, database, data.mode, data.tables, data.wait)

    @api.get("/databases/{database}/runs", operation_id="listRuns", response_model=RunList,
             summary="A database's runs, newest first")
    async def runs(database: DatabaseId, caller: Caller, limit: Annotated[int, Query(ge=1, le=200)] = 20):
        return await services.list_runs(caller, database, limit)

    @api.get("/runs/{run}", operation_id="getRun", response_model=Run,
             summary="A run with its totals and each table's actions")
    async def get_run(run: RunId, caller: Caller,
                      wait: Annotated[int, Query(ge=0, le=600, description=WAIT_HELP)] = 0):
        return await services.get_run(caller, run, wait)

    @api.get("/runs/{run}/logs", operation_id="getRunLogs", response_model=RunLogs,
             summary="The last lines of a run's log")
    async def logs(run: RunId, caller: Caller, limit: Annotated[int, Query(ge=1, le=5000)] = 500):
        return await services.get_run_logs(caller, run, limit)

    @api.post("/runs/{run}/cancel", operation_id="cancelRun", response_model=CancelResult,
              summary="Stop a run (writers)")
    async def cancel(run: RunId, caller: Caller):
        return await services.cancel_run(caller, run)

    return api


def require_json_write(request: Request):
    """Cookie-authenticated writes need the API header: a browser form cannot set it."""
    if request.method not in ("GET", "HEAD") and not request.headers.get("authorization"):
        if request.headers.get("x-iceberg-maintenance") != "1":
            raise MaintenanceError(403, "Send the X-Iceberg-Maintenance: 1 header with browser requests.", "csrf")
