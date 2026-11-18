"""REST API v1. Every operation has an MCP tool of the same capability (OPERATIONS), so agents
and scripts can do everything; the page uses the same API with the browser session.
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi import Path as PathParam
from pydantic import BaseModel, ConfigDict, Field

from .errors import MaintenanceError

# operationId → MCP tool name. tests/test_api.py keeps both sides complete.
OPERATIONS = {
    "whoami": "whoami", "listEnvironments": "list_environments", "enableEnvironment": "enable_environment",
    "disableEnvironment": "disable_environment", "listDatabases": "list_databases", "listTables": "list_tables",
    "getTable": "get_table", "getPolicy": "get_policy", "setPolicy": "set_policy", "deletePolicy": "delete_policy",
    "runMaintenance": "run_maintenance", "listRuns": "list_runs", "getRun": "get_run", "getRunLogs": "get_run_logs",
    "cancelRun": "cancel_run",
}

Environment = Literal["development", "acceptance", "production"]
Mode = Literal["inspect", "dry-run", "maintain"]
DATABASE = r"^db-[a-f0-9]{32}$"
TABLE = r"^[A-Za-z0-9_$-][A-Za-z0-9_.$-]{1,298}[A-Za-z0-9_$-]$"
DatabaseId = Annotated[str, PathParam(pattern=DATABASE)]
TableName = Annotated[str, PathParam(pattern=TABLE, description="namespace.table")]
RunId = Annotated[str, PathParam(pattern=r"^run-[a-f0-9]{24}$")]
TeamId = Annotated[str, PathParam(pattern=r"^team-[a-f0-9]{32}$")]
SETTINGS_HELP = ("Per operation (expireSnapshots, compaction, orphanFiles, rewriteManifests, danglingDeletes), the "
                 "settings to change; the rest keeps its value. whoami lists the defaults.")
MODE_HELP = ("inspect reads every table's health; dry-run plans the policy without changing anything; maintain "
             "applies it (needs a policy).")


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PolicyInput(Input):
    settings: dict[str, dict[str, Any]] | None = Field(default=None, description=SETTINGS_HELP)
    exclude: list[str] | None = Field(default=None, max_length=500,
                                      description="Tables (namespace.table) that maintenance leaves alone.")
    cron: str | None = Field(default=None, max_length=100, description="Five-field cron expression in UTC, at most "
                                                                       "once an hour; empty removes the schedule.")
    scheduleEnabled: bool | None = Field(default=None, description="Pause or resume the schedule.")
    expected_revision: int | None = Field(default=None, description="Fail if the policy changed since this revision "
                                                                    "(0: only if there is none yet).")


class RunInput(Input):
    mode: Mode = Field(default="inspect", description=MODE_HELP)
    tables: list[str] | None = Field(default=None, max_length=200,
                                     description="Only these tables (namespace.table); all when empty.")
    wait: int = Field(default=0, ge=0, le=600, description="Seconds to wait for the run to finish.")


def router(services, caller_dependency):
    api = APIRouter(prefix="/api/v1")
    Caller = Annotated[object, Depends(caller_dependency)]

    @api.get("/me", operation_id="whoami", summary="Your teams, roles, databases, the policy defaults and this "
                                                   "extension")
    async def whoami(caller: Caller):
        return await services.whoami(caller)

    @api.get("/teams/{team}/environments", operation_id="listEnvironments",
             summary="Where Maintenance is enabled for a team, with each environment's databases")
    async def environments(team: TeamId, caller: Caller):
        return await services.environments(caller, team)

    @api.post("/teams/{team}/environments/{environment}", operation_id="enableEnvironment",
              summary="Enable Maintenance for a team environment (team administrators)")
    async def enable(team: TeamId, environment: Environment, caller: Caller):
        return await services.enable_environment(caller, team, environment)

    @api.delete("/teams/{team}/environments/{environment}", operation_id="disableEnvironment",
                summary="Disable Maintenance for a team environment (team administrators)")
    async def disable(team: TeamId, environment: Environment, caller: Caller):
        return await services.disable_environment(caller, team, environment)

    @api.get("/databases", operation_id="listDatabases",
             summary="Your teams' databases with their policy, schedule, last run and tables that need attention")
    async def databases(caller: Caller, team: Annotated[str | None, Query(pattern=r"^team-[a-f0-9]{32}$")] = None,
                        environment: Environment | None = None):
        return await services.list_databases(caller, team, environment)

    @api.get("/tables", operation_id="listTables", summary="Table health from the last runs, with what needs "
                                                           "attention")
    async def tables(caller: Caller, database: Annotated[str | None, Query(pattern=DATABASE)] = None,
                     team: Annotated[str | None, Query(pattern=r"^team-[a-f0-9]{32}$")] = None,
                     environment: Environment | None = None, attention: bool = False):
        return await services.list_tables(caller, database, team, environment, attention)

    @api.get("/databases/{database}/tables/{table}", operation_id="getTable",
             summary="One table's health, its last actions, effective policy and recent runs")
    async def table(database: DatabaseId, table: TableName, caller: Caller):
        return await services.get_table(caller, database, table)

    @api.get("/databases/{database}/policy", operation_id="getPolicy",
             summary="A database's maintenance policy and schedule, or the defaults when it has none")
    async def get_policy(database: DatabaseId, caller: Caller):
        return await services.get_policy(caller, database)

    @api.put("/databases/{database}/policy", operation_id="setPolicy",
             summary="Create or change a database's policy and schedule (team administrators)")
    async def set_policy(database: DatabaseId, data: Annotated[PolicyInput, Body()], caller: Caller):
        return await services.set_policy(caller, database, data.model_dump(exclude_unset=True))

    @api.delete("/databases/{database}/policy", operation_id="deletePolicy",
                summary="Remove a database's policy and schedule (team administrators)")
    async def delete_policy(database: DatabaseId, caller: Caller):
        return await services.delete_policy(caller, database)

    @api.post("/databases/{database}/runs", operation_id="runMaintenance", status_code=202,
              summary="Inspect, dry-run or maintain a database now (writers)")
    async def run(database: DatabaseId, caller: Caller, data: Annotated[RunInput | None, Body()] = None):
        data = data or RunInput()
        return await services.run_maintenance(caller, database, data.mode, data.tables, data.wait)

    @api.get("/databases/{database}/runs", operation_id="listRuns", summary="A database's runs, newest first")
    async def runs(database: DatabaseId, caller: Caller, limit: Annotated[int, Query(ge=1, le=200)] = 20):
        return await services.list_runs(caller, database, limit)

    @api.get("/runs/{run}", operation_id="getRun", summary="A run with its totals and each table's actions")
    async def get_run(run: RunId, caller: Caller):
        return await services.get_run(caller, run)

    @api.get("/runs/{run}/logs", operation_id="getRunLogs", summary="A run's log")
    async def logs(run: RunId, caller: Caller, tail: Annotated[int, Query(ge=1, le=5000)] = 500):
        return await services.get_run_logs(caller, run, tail)

    @api.post("/runs/{run}/cancel", operation_id="cancelRun", summary="Stop a run (writers)")
    async def cancel(run: RunId, caller: Caller):
        return await services.cancel_run(caller, run)

    return api


def require_json_write(request: Request):
    """Cookie-authenticated writes need the API header: a browser form cannot set it."""
    if request.method not in ("GET", "HEAD") and not request.headers.get("authorization"):
        if request.headers.get("x-iceberg-maintenance") != "1":
            raise MaintenanceError(403, "Send the X-Iceberg-Maintenance: 1 header with browser requests.", "csrf")
