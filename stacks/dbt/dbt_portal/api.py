"""REST API v1. Every operation has an MCP tool of the same capability (OPERATIONS), so agents
and scripts can do everything; the pipeline viewer uses the same API read-only.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi import Path as PathParam
from pydantic import BaseModel, ConfigDict, Field

from .errors import DbtError

# operationId → MCP tool name. tests/test_api.py keeps both sides complete.
OPERATIONS = {
    "whoami": "whoami", "listEnvironments": "list_environments", "enableEnvironment": "enable_environment",
    "disableEnvironment": "disable_environment", "listProjects": "list_projects", "createProject": "create_project",
    "getProject": "get_project", "deleteProject": "delete_project", "listFiles": "list_files",
    "readFile": "read_file", "commitFiles": "write_files", "listBranches": "list_branches",
    "createBranch": "create_branch", "deleteBranch": "delete_branch", "getHistory": "get_history",
    "getDiff": "diff", "mergeToMain": "merge_to_main", "compile": "compile", "previewModel": "preview_model",
    "startRun": "run", "listRuns": "list_runs", "getRun": "get_run", "getRunLogs": "get_run_logs",
    "cancelRun": "cancel_run", "listSchedules": "list_schedules", "createSchedule": "set_schedule",
    "updateSchedule": "set_schedule", "deleteSchedule": "delete_schedule", "getPipeline": "get_pipeline",
    "getModel": "get_model", "getLineage": "get_lineage",
}

Environment = Literal["development", "acceptance", "production"]
ProjectId = Annotated[str, PathParam(pattern=r"^prj-[a-f0-9]{24}$")]
RunId = Annotated[str, PathParam(pattern=r"^run-[a-f0-9]{24}$")]
ScheduleId = Annotated[str, PathParam(pattern=r"^sch-[a-f0-9]{24}$")]
TeamId = Annotated[str, PathParam(pattern=r"^team-[a-f0-9]{32}$")]
Ref = Annotated[str, Query(max_length=100, description="Branch name or revision.")]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectInput(Input):
    team: str = Field(pattern=r"^team-[a-f0-9]{32}$")
    name: str = Field(description="3–48 lowercase letters, digits or '_', starting with a letter.")
    description: str = Field(default="", max_length=280)
    default_database: str = Field(default="", max_length=48,
                                  description="Display name of the team database models land in by default.")


class DeleteInput(Input):
    confirm_name: str = Field(max_length=48)


class CommitInput(Input):
    branch: str = Field(max_length=100)
    files: dict[str, str | None] = Field(description="Path → new content, or null to delete the file.")
    message: str = Field(max_length=2000)
    expected_revision: str | None = Field(default=None, max_length=40,
                                          description="Fail if the branch moved since this revision.")


class BranchInput(Input):
    name: str = Field(max_length=100)
    source: str = Field(default="main", max_length=100)


class MergeInput(Input):
    branch: str = Field(max_length=100)
    message: str = Field(default="", max_length=2000)


class CompileInput(Input):
    environment: Environment = "development"
    ref: str = Field(default="main", max_length=100)
    select: str = Field(default="", max_length=500)
    sql: str = Field(default="", max_length=20000, description="Inline SQL to compile instead of selected models.")


class PreviewInput(Input):
    model: str = Field(max_length=200)
    environment: Environment = "development"
    ref: str = Field(default="main", max_length=100)
    limit: int = Field(default=20, ge=1, le=100)


class RunInput(Input):
    command: Literal["build", "run", "seed", "test", "compile", "parse", "docs"] = "build"
    environment: Environment = "development"
    ref: str = Field(default="main", max_length=100)
    select: str = Field(default="", max_length=500)
    full_refresh: bool = Field(default=False)
    wait: int = Field(default=0, ge=0, le=600, description="Seconds to wait for the run to finish.")


class ScheduleInput(Input):
    environment: Environment
    cron: str = Field(max_length=100, description="Five-field cron expression in UTC.")
    command: Literal["build", "run", "seed", "test"] = "build"
    select: str = Field(default="", max_length=500)
    ref: str = Field(default="main", max_length=100)
    enabled: bool = True


def router(services, caller_dependency):
    api = APIRouter(prefix="/api/v1")
    Caller = Annotated[object, Depends(caller_dependency)]

    @api.get("/me", operation_id="whoami", summary="Your teams, roles, databases and this extension")
    async def whoami(caller: Caller):
        return await services.whoami(caller)

    @api.get("/teams/{team}/environments", operation_id="listEnvironments",
             summary="Where dbt is enabled for a team, with each environment's databases")
    async def environments(team: TeamId, caller: Caller):
        return await services.environments(caller, team)

    @api.post("/teams/{team}/environments/{environment}", operation_id="enableEnvironment",
              summary="Enable dbt for a team environment (team administrators)")
    async def enable(team: TeamId, environment: Environment, caller: Caller):
        return await services.enable_environment(caller, team, environment)

    @api.delete("/teams/{team}/environments/{environment}", operation_id="disableEnvironment",
                summary="Disable dbt for a team environment (team administrators)")
    async def disable(team: TeamId, environment: Environment, caller: Caller):
        return await services.disable_environment(caller, team, environment)

    @api.get("/projects", operation_id="listProjects", summary="Projects of your teams")
    async def projects(caller: Caller):
        return await services.list_projects(caller)

    @api.post("/projects", operation_id="createProject", status_code=201,
              summary="Create a project from the starter template (writers)")
    async def create_project(data: Annotated[ProjectInput, Body()], caller: Caller):
        return await services.create_project(caller, data.team, data.name, data.description, data.default_database)

    @api.get("/projects/{project}", operation_id="getProject",
             summary="A project with its branches, environments, latest runs and schedules")
    async def project(project: ProjectId, caller: Caller):
        return await services.get_project(caller, project)

    @api.delete("/projects/{project}", operation_id="deleteProject",
                summary="Delete a project and its history (team administrators); tables stay")
    async def delete_project(project: ProjectId, data: Annotated[DeleteInput, Body()], caller: Caller):
        return await services.delete_project(caller, project, data.confirm_name)

    @api.get("/projects/{project}/files", operation_id="listFiles", summary="Files at a branch or revision")
    async def files(project: ProjectId, caller: Caller, ref: Ref = "main"):
        return await services.list_files(caller, project, ref)

    @api.get("/projects/{project}/files/{path:path}", operation_id="readFile", summary="One file's content")
    async def read_file(project: ProjectId, path: str, caller: Caller, ref: Ref = "main"):
        return await services.read_file(caller, project, path, ref)

    @api.post("/projects/{project}/commits", operation_id="commitFiles", status_code=201,
              summary="Write, change or delete files as one commit on a branch (writers; never main)")
    async def commit(project: ProjectId, data: Annotated[CommitInput, Body()], caller: Caller):
        return await services.write_files(caller, project, data.branch, data.files, data.message,
                                          data.expected_revision)

    @api.get("/projects/{project}/branches", operation_id="listBranches", summary="Branches with their heads")
    async def branches(project: ProjectId, caller: Caller):
        return await services.branches(caller, project)

    @api.post("/projects/{project}/branches", operation_id="createBranch", status_code=201,
              summary="Create a branch (writers)")
    async def create_branch(project: ProjectId, data: Annotated[BranchInput, Body()], caller: Caller):
        return await services.create_branch(caller, project, data.name, data.source)

    @api.delete("/projects/{project}/branches/{name:path}", operation_id="deleteBranch",
                summary="Delete a branch (writers; never main)")
    async def delete_branch(project: ProjectId, name: str, caller: Caller):
        return await services.delete_branch(caller, project, name)

    @api.get("/projects/{project}/history", operation_id="getHistory", summary="Commits of a branch")
    async def history(project: ProjectId, caller: Caller, ref: Ref = "main",
                      limit: Annotated[int, Query(ge=1, le=200)] = 30):
        return await services.history(caller, project, ref, limit)

    @api.get("/projects/{project}/diff", operation_id="getDiff", summary="Changes between two branches or revisions")
    async def diff(project: ProjectId, caller: Caller, head: Ref, base: Ref = "main"):
        return await services.diff(caller, project, base, head)

    @api.post("/projects/{project}/merge", operation_id="mergeToMain",
              summary="Merge a branch into main (team administrators)")
    async def merge(project: ProjectId, data: Annotated[MergeInput, Body()], caller: Caller):
        return await services.merge_to_main(caller, project, data.branch, data.message)

    @api.post("/projects/{project}/compile", operation_id="compile",
              summary="Compile selected models or inline SQL with a read-only token")
    async def compile(project: ProjectId, data: Annotated[CompileInput, Body()], caller: Caller):
        return await services.compile(caller, project, data.environment, data.ref, data.select, data.sql)

    @api.post("/projects/{project}/preview", operation_id="previewModel",
              summary="Up to 100 rows of a model's query with a read-only token")
    async def preview(project: ProjectId, data: Annotated[PreviewInput, Body()], caller: Caller):
        return await services.preview(caller, project, data.model, data.environment, data.ref, data.limit)

    @api.post("/projects/{project}/runs", operation_id="startRun", status_code=202,
              summary="Run dbt as the team's automation principal")
    async def start_run(project: ProjectId, data: Annotated[RunInput, Body()], caller: Caller):
        return await services.run(caller, project, data.command, data.environment, data.ref, data.select,
                                  data.full_refresh, data.wait)

    @api.get("/projects/{project}/runs", operation_id="listRuns", summary="Recent runs")
    async def runs(project: ProjectId, caller: Caller, environment: Environment | None = None,
                   limit: Annotated[int, Query(ge=1, le=200)] = 30):
        return await services.list_runs(caller, project, environment, limit)

    @api.get("/runs/{run}", operation_id="getRun", summary="A run with its node results; optionally wait for it")
    async def get_run(run: RunId, caller: Caller, wait: Annotated[int, Query(ge=0, le=600)] = 0):
        return await services.get_run(caller, run, wait)

    @api.get("/runs/{run}/logs", operation_id="getRunLogs", summary="The last lines of a run's dbt log")
    async def logs(run: RunId, caller: Caller, tail: Annotated[int, Query(ge=1, le=5000)] = 400):
        return await services.run_logs(caller, run, tail)

    @api.post("/runs/{run}/cancel", operation_id="cancelRun", summary="Stop a running run (writers)")
    async def cancel(run: RunId, caller: Caller):
        return await services.cancel_run(caller, run)

    @api.get("/projects/{project}/schedules", operation_id="listSchedules", summary="Schedules of a project")
    async def schedules(project: ProjectId, caller: Caller):
        return await services.list_schedules(caller, project)

    @api.post("/projects/{project}/schedules", operation_id="createSchedule", status_code=201,
              summary="Schedule runs (writers in development, administrators elsewhere)")
    async def create_schedule(project: ProjectId, data: Annotated[ScheduleInput, Body()], caller: Caller):
        return await services.set_schedule(caller, project, data.environment, data.cron, data.command, data.select,
                                           data.ref, data.enabled)

    @api.put("/projects/{project}/schedules/{schedule}", operation_id="updateSchedule", summary="Change a schedule")
    async def update_schedule(project: ProjectId, schedule: ScheduleId, data: Annotated[ScheduleInput, Body()],
                              caller: Caller):
        return await services.set_schedule(caller, project, data.environment, data.cron, data.command, data.select,
                                           data.ref, data.enabled, schedule)

    @api.delete("/schedules/{schedule}", operation_id="deleteSchedule", summary="Delete a schedule")
    async def delete_schedule(schedule: ScheduleId, caller: Caller):
        return await services.delete_schedule(caller, schedule)

    @api.get("/projects/{project}/pipeline", operation_id="getPipeline",
             summary="The pipeline graph with each node's latest status and tests")
    async def pipeline(project: ProjectId, caller: Caller, environment: Environment = "development"):
        return await services.pipeline(caller, project, environment)

    @api.get("/projects/{project}/nodes/{node}", operation_id="getModel",
             summary="One node: description, columns, SQL, compiled SQL, tests and latest result")
    async def node(project: ProjectId, node: str, caller: Caller, environment: Environment = "development"):
        return await services.model(caller, project, node, environment)

    @api.get("/projects/{project}/lineage/{node}", operation_id="getLineage",
             summary="Upstream and downstream nodes of a node")
    async def node_lineage(project: ProjectId, node: str, caller: Caller, environment: Environment = "development",
                           depth: Annotated[int, Query(ge=1, le=20)] = 3):
        return await services.lineage(caller, project, node, environment, depth)

    return api


def require_json_write(request: Request):
    """Cookie-authenticated writes need the API header: a browser form cannot set it."""
    if request.method not in ("GET", "HEAD") and not request.headers.get("authorization"):
        if request.headers.get("x-iceberg-dbt") != "1":
            raise DbtError(403, "Send the X-Iceberg-Dbt: 1 header with browser requests.", "csrf")
