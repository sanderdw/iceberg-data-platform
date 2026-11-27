"""REST API v1. Every operation has an MCP tool of the same capability (OPERATIONS), so agents
and scripts can do everything; the pipeline viewer uses the same API with the browser session.
"""

from typing import Annotated, Any, Literal

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
    "getDiff": "get_diff", "mergeToMain": "merge_to_main", "compileProject": "compile_project",
    "previewModel": "preview_model", "runProject": "run_project", "listRuns": "list_runs", "getRun": "get_run",
    "getRunLogs": "get_run_logs", "cancelRun": "cancel_run", "listSchedules": "list_schedules",
    "createSchedule": "create_schedule", "updateSchedule": "update_schedule", "deleteSchedule": "delete_schedule",
    "getPipelineGraph": "get_pipeline_graph", "getNode": "get_node", "getLineage": "get_lineage",
}

EnvironmentName = Literal["development", "acceptance", "production"]
RunCommand = Literal["build", "run", "seed", "test", "compile", "parse", "docs"]
ScheduleCommand = Literal["build", "run", "seed", "test"]
ProjectId = Annotated[str, PathParam(pattern=r"^prj-[a-f0-9]{24}$")]
RunId = Annotated[str, PathParam(pattern=r"^run-[a-f0-9]{24}$")]
ScheduleId = Annotated[str, PathParam(pattern=r"^sch-[a-f0-9]{24}$")]
TeamId = Annotated[str, PathParam(pattern=r"^team-[a-f0-9]{32}$")]
Ref = Annotated[str, Query(max_length=100, description="Branch name or revision.")]
RunRef = Annotated[str | None, Query(max_length=100,
                                    description="Only runs of this branch or revision; all when left out.")]
WAIT_HELP = "Seconds to wait for the run to finish. The answer is the run as it is then, final or not."
CRON_HELP = "Five-field cron expression in UTC, at most every 5 minutes."


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectInput(Input):
    team: str = Field(pattern=r"^team-[a-f0-9]{32}$")
    name: str = Field(description="3–48 lowercase letters, digits or '_', starting with a letter.")
    description: str = Field(default="", max_length=280)
    defaultDatabase: str = Field(default="", max_length=48,
                                 description="Display name of the team database models land in by default.")


class CommitInput(Input):
    branch: str = Field(max_length=100)
    files: dict[str, str | None] = Field(description="Path → new content, or null to delete the file.")
    message: str = Field(max_length=2000)
    expectedRevision: str | None = Field(default=None, max_length=40,
                                         description="Fail if the branch moved since this revision.")


class BranchInput(Input):
    name: str = Field(max_length=100)
    source: str = Field(default="main", max_length=100)


class MergeInput(Input):
    branch: str = Field(max_length=100)
    message: str = Field(default="", max_length=2000)
    revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{7,40}$",
                                 description="The branch head you reviewed; the merge is refused if the branch moved.")


class CompileInput(Input):
    environment: EnvironmentName = "development"
    ref: str = Field(default="main", max_length=100)
    select: str = Field(default="", max_length=500)
    sql: str = Field(default="", max_length=20000, description="Inline SQL to compile instead of selected models.")


class PreviewInput(Input):
    model: str = Field(max_length=200)
    environment: EnvironmentName = "development"
    ref: str = Field(default="main", max_length=100)
    limit: int = Field(default=20, ge=1, le=100)


class RunInput(Input):
    command: RunCommand = "build"
    environment: EnvironmentName = "development"
    ref: str = Field(default="main", max_length=100)
    select: str = Field(default="", max_length=500)
    fullRefresh: bool = Field(default=False, description="Recreate tables whose columns changed (build, run, seed).")
    wait: int = Field(default=0, ge=0, le=600, description=WAIT_HELP)


class ScheduleInput(Input):
    environment: EnvironmentName
    cron: str = Field(max_length=100, description=CRON_HELP)
    command: ScheduleCommand = "build"
    select: str = Field(default="", max_length=500)
    ref: str = Field(default="main", max_length=100)
    enabled: bool = True


class ScheduleChange(Input):
    environment: EnvironmentName | None = None
    cron: str | None = Field(default=None, max_length=100, description=CRON_HELP)
    command: ScheduleCommand | None = None
    select: str | None = Field(default=None, max_length=500)
    ref: str | None = Field(default=None, max_length=100)
    enabled: bool | None = None


# Responses. Every key a service returns is declared here; a key that is not always there has a default and is
# left out of the response when the service did not return it (response_model_exclude_unset).

Timestamp = Annotated[str, Field(description="RFC 3339 in UTC with milliseconds, such as 2026-11-24T09:15:02.123Z.")]
RunStatus = Literal["queued", "running", "success", "incomplete", "partial", "failed", "error", "cancelled"]


class Error(BaseModel):
    error: str = Field(description="What went wrong, for a person.")
    code: str = Field(description="A stable code for programs, such as unknown_project.")
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
    id: str
    project: str
    environment: EnvironmentName
    cron: str
    command: ScheduleCommand
    select: str = Field(description="dbt node selection; empty for everything.")
    ref: str
    enabled: bool
    nextRunAt: Timestamp | None = Field(description="Null while the schedule is not enabled.")
    lastRun: str | None = Field(description="The id of the last run the schedule started.")
    createdBy: str
    createdAt: Timestamp


class ScheduleList(BaseModel):
    schedules: list[Schedule]


class RunItem(BaseModel):
    id: str
    project: str
    environment: EnvironmentName
    status: RunStatus
    triggeredBy: str = Field(description="The person's name, or \"schedule\".")
    agent: str | None = Field(description="The MCP client when an agent started the run.")
    scheduled: bool
    schedule: str | None = Field(description="The id of the schedule that started the run.")
    command: str
    select: str = Field(description="dbt node selection; empty for everything.")
    ref: str = Field(description="The branch or revision the run was asked for.")
    revision: str = Field(description="The commit the run used.")
    createdAt: Timestamp
    startedAt: Timestamp | None
    finishedAt: Timestamp | None
    exitCode: int | None
    summary: dict[str, Any] = Field(description="dbt's totals (counts per status, nodes), the catalogs the run used "
                                                "and what it published: table properties and semantic models.")
    error: str | None
    hint: str | None = None


class Run(RunItem):
    results: list[dict[str, Any]] = Field(description="Per node: node, status, executionTime, message and "
                                                      "rowsAffected. Empty until the run has results.")


class RunList(BaseModel):
    runs: list[RunItem]


class RunLogs(BaseModel):
    run: str
    status: RunStatus
    lines: list[str]


class CancelResult(BaseModel):
    run: str
    cancelling: bool


class Project(BaseModel):
    id: str
    team: str
    name: str
    description: str
    defaultDatabase: str
    createdAt: Timestamp
    createdBy: str
    role: str | None = Field(description="Your role in the project's team.")
    url: str
    hint: str | None = None


class ProjectList(BaseModel):
    projects: list[Project]


class Branch(BaseModel):
    name: str
    revision: str
    subject: str
    committedAt: Timestamp


class BranchList(BaseModel):
    branches: list[Branch]


class Commit(BaseModel):
    revision: str
    author: str
    subject: str
    committedAt: Timestamp


class CommitList(BaseModel):
    commits: list[Commit]


class ProjectDetail(Project):
    branches: list[Branch]
    environments: list[Environment]
    latestRuns: dict[str, RunItem] = Field(description="The newest run per environment that has one.")
    schedules: list[Schedule]


class Deleted(BaseModel):
    deleted: bool
    project: str | None = None
    branch: str | None = None
    schedule: str | None = None
    tables: dict[str, Any] | None = Field(default=None, description="For a project: the tables whose dbt "
                                                                    "properties were removed, and those where "
                                                                    "that failed.")


class FileEntry(BaseModel):
    path: str
    size: int = Field(description="Bytes.")


class FileList(BaseModel):
    ref: str
    revision: str
    files: list[FileEntry]


class FileContent(BaseModel):
    path: str
    ref: str
    revision: str
    content: str


class CommitResult(BaseModel):
    branch: str
    revision: str = Field(description="The new commit.")
    files: list[str] = Field(description="The paths written or deleted.")


class BranchCreated(BaseModel):
    branch: str
    revision: str


class DiffFile(BaseModel):
    path: str
    added: int | None = Field(description="Lines added; null for a binary file.")
    removed: int | None = Field(description="Lines removed; null for a binary file.")


class Diff(BaseModel):
    base: str = Field(description="The base revision.")
    head: str = Field(description="The head revision.")
    files: list[DiffFile]
    patch: str = Field(description="The unified diff, at most 200,000 characters.")
    truncated: bool


class MergeResult(BaseModel):
    revision: str = Field(description="main's head after the merge.")
    merged: bool = Field(description="False when main already contained the branch.")
    hint: str | None = None


class CompileResult(BaseModel):
    run: str
    status: RunStatus
    compiled: dict[str, str] = Field(description="Compiled SQL per selected model; empty for inline SQL.")
    output: str = Field(description="The last lines of dbt's log, with inline SQL's compiled result.")
    error: str | None


class PreviewResult(BaseModel):
    run: str
    status: RunStatus
    rows: list[dict[str, Any]] | None = Field(description="Null when dbt returned no rows; see output.")
    output: str | None = Field(description="The last lines of dbt's log when there are no rows.")


class Column(BaseModel):
    name: str
    type: str | None
    description: str
    index: int | None


class TestCounts(BaseModel):
    pass_: int = Field(alias="pass")
    warn: int
    fail: int
    unknown: int


class TestResult(BaseModel):
    id: str
    name: str
    node: str
    column: str | None
    test: str | None
    severity: str | None
    status: str


class PipelineNode(BaseModel):
    id: str
    name: str
    type: str = Field(description="model, seed, snapshot or source.")
    database: str | None
    databaseId: str | None = Field(description="The platform database the node's catalog is.")
    namespace: str | None
    table: str
    materialized: str | None
    description: str
    tags: list[str]
    path: str | None
    own: bool = Field(description="False for nodes of installed packages.")
    status: str = Field(description="The latest result, never_run, or external for a source.")
    lastRun: str | None
    lastRunAt: Timestamp | None
    executionTime: float | None
    rowsAffected: int | None
    message: str | None
    tests: TestCounts


class NodeDetail(PipelineNode):
    sql: str
    compiledSql: str
    columns: list[Column]
    testResults: list[TestResult]
    environment: EnvironmentName
    structureRun: str


class Edge(BaseModel):
    from_: str = Field(alias="from")
    to: str


class Overwritten(BaseModel):
    ref: str
    run: str
    at: Timestamp


class PipelineGraph(BaseModel):
    project: str
    environment: EnvironmentName
    ref: str | None
    structureRun: str | None = Field(description="The run whose project structure is drawn.")
    revision: str | None = None
    runs: list[RunItem] = Field(description="The latest runs that changed or checked tables.")
    nodes: list[PipelineNode]
    edges: list[Edge]
    dbtVersion: str | None = None
    overwrittenBy: Overwritten | None = Field(
        default=None, description="In development: the newer build of another branch that wrote these tables.")
    hint: str | None = None
    refsWithPipeline: list[str] | None = None


class LineageNode(BaseModel):
    id: str
    distance: int
    type: str


class Lineage(BaseModel):
    environment: EnvironmentName
    structureRun: str
    node: str
    upstream: list[LineageNode]
    downstream: list[LineageNode]


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
             summary="Where dbt is enabled for a team, with each environment's databases")
    async def environments(team: TeamId, caller: Caller):
        return await services.environments(caller, team)

    @api.post("/teams/{team}/environments/{environment}", operation_id="enableEnvironment",
              response_model=Environment, **shape,
              summary="Enable dbt for a team environment (team administrators)")
    async def enable(team: TeamId, environment: EnvironmentName, caller: Caller):
        return await services.enable_environment(caller, team, environment)

    @api.delete("/teams/{team}/environments/{environment}", operation_id="disableEnvironment",
                response_model=Environment, **shape,
                summary="Disable dbt for a team environment (team administrators)")
    async def disable(team: TeamId, environment: EnvironmentName, caller: Caller):
        return await services.disable_environment(caller, team, environment)

    @api.get("/projects", operation_id="listProjects", response_model=ProjectList, **shape,
             summary="Projects of your teams")
    async def projects(caller: Caller):
        return await services.list_projects(caller)

    @api.post("/projects", operation_id="createProject", status_code=201, response_model=Project, **shape,
              summary="Create an empty dbt project (writers)")
    async def create_project(data: Annotated[ProjectInput, Body()], caller: Caller):
        return await services.create_project(caller, data.team, data.name, data.description, data.defaultDatabase)

    @api.get("/projects/{project}", operation_id="getProject", response_model=ProjectDetail, **shape,
             summary="A project with its branches, environments, latest runs and schedules")
    async def project(project: ProjectId, caller: Caller):
        return await services.get_project(caller, project)

    @api.delete("/projects/{project}", operation_id="deleteProject", response_model=Deleted, **shape,
                summary="Forget a project, its history and its dbt properties on tables (team administrators); "
                        "tables stay")
    async def delete_project(
            project: ProjectId, caller: Caller,
            confirm_name: Annotated[str, Query(alias="confirmName", max_length=48,
                                               description="The project's exact name.")],
            force: Annotated[bool, Query(description="Delete even when dbt properties stay on some tables.")] = False):
        return await services.delete_project(caller, project, confirm_name, force)

    @api.get("/projects/{project}/files", operation_id="listFiles", response_model=FileList, **shape,
             summary="Files at a branch or revision")
    async def files(project: ProjectId, caller: Caller, ref: Ref = "main"):
        return await services.list_files(caller, project, ref)

    @api.get("/projects/{project}/files/{path:path}", operation_id="readFile", response_model=FileContent, **shape,
             summary="One file's content")
    async def read_file(project: ProjectId, path: str, caller: Caller, ref: Ref = "main"):
        return await services.read_file(caller, project, path, ref)

    @api.post("/projects/{project}/commits", operation_id="commitFiles", status_code=201,
              response_model=CommitResult, **shape,
              summary="Write, change or delete files as one commit on a branch (writers; never main)")
    async def commit(project: ProjectId, data: Annotated[CommitInput, Body()], caller: Caller):
        return await services.write_files(caller, project, data.branch, data.files, data.message,
                                          data.expectedRevision)

    @api.get("/projects/{project}/branches", operation_id="listBranches", response_model=BranchList, **shape,
             summary="Branches with their heads")
    async def branches(project: ProjectId, caller: Caller):
        return await services.branches(caller, project)

    @api.post("/projects/{project}/branches", operation_id="createBranch", status_code=201,
              response_model=BranchCreated, **shape,
              summary="Create a branch (writers)")
    async def create_branch(project: ProjectId, data: Annotated[BranchInput, Body()], caller: Caller):
        return await services.create_branch(caller, project, data.name, data.source)

    @api.delete("/projects/{project}/branches/{name:path}", operation_id="deleteBranch", response_model=Deleted,
                **shape, summary="Delete a branch (writers; never main)")
    async def delete_branch(project: ProjectId, name: str, caller: Caller):
        return await services.delete_branch(caller, project, name)

    @api.get("/projects/{project}/history", operation_id="getHistory", response_model=CommitList, **shape,
             summary="Commits of a branch, newest first")
    async def history(project: ProjectId, caller: Caller, ref: Ref = "main",
                      limit: Annotated[int, Query(ge=1, le=200)] = 30):
        return await services.history(caller, project, ref, limit)

    @api.get("/projects/{project}/diff", operation_id="getDiff", response_model=Diff, **shape,
             summary="Changes between two branches or revisions")
    async def diff(project: ProjectId, caller: Caller, head: Ref, base: Ref = "main"):
        return await services.diff(caller, project, base, head)

    @api.post("/projects/{project}/merge", operation_id="mergeToMain", response_model=MergeResult, **shape,
              summary="Merge a branch into main (team administrators)")
    async def merge(project: ProjectId, data: Annotated[MergeInput, Body()], caller: Caller):
        return await services.merge_to_main(caller, project, data.branch, data.message, data.revision)

    @api.post("/projects/{project}/compile", operation_id="compileProject", response_model=CompileResult, **shape,
              summary="Compile selected models or inline SQL with a read-only token")
    async def compile(project: ProjectId, data: Annotated[CompileInput, Body()], caller: Caller):
        return await services.compile_project(caller, project, data.environment, data.ref, data.select, data.sql)

    @api.post("/projects/{project}/preview", operation_id="previewModel", response_model=PreviewResult, **shape,
              summary="Up to 100 rows of a model's query with a read-only token")
    async def preview(project: ProjectId, data: Annotated[PreviewInput, Body()], caller: Caller):
        return await services.preview_model(caller, project, data.model, data.environment, data.ref, data.limit)

    @api.post("/projects/{project}/runs", operation_id="runProject", status_code=202, response_model=Run, **shape,
              summary="Run dbt as the team's automation principal")
    async def run_project(project: ProjectId, caller: Caller, data: Annotated[RunInput | None, Body()] = None):
        data = data or RunInput()
        return await services.run_project(caller, project, data.command, data.environment, data.ref, data.select,
                                          data.fullRefresh, data.wait)

    @api.get("/projects/{project}/runs", operation_id="listRuns", response_model=RunList, **shape,
             summary="A project's runs, newest first")
    async def runs(project: ProjectId, caller: Caller, environment: EnvironmentName | None = None,
                   limit: Annotated[int, Query(ge=1, le=200)] = 20):
        return await services.list_runs(caller, project, environment, limit)

    @api.get("/runs/{run}", operation_id="getRun", response_model=Run, **shape,
             summary="A run with its node results")
    async def get_run(run: RunId, caller: Caller,
                      wait: Annotated[int, Query(ge=0, le=600, description=WAIT_HELP)] = 0):
        return await services.get_run(caller, run, wait)

    @api.get("/runs/{run}/logs", operation_id="getRunLogs", response_model=RunLogs, **shape,
             summary="The last lines of a run's dbt log")
    async def logs(run: RunId, caller: Caller, limit: Annotated[int, Query(ge=1, le=5000)] = 500):
        return await services.get_run_logs(caller, run, limit)

    @api.post("/runs/{run}/cancel", operation_id="cancelRun", response_model=CancelResult, **shape,
              summary="Stop a run (writers)")
    async def cancel(run: RunId, caller: Caller):
        return await services.cancel_run(caller, run)

    @api.get("/projects/{project}/schedules", operation_id="listSchedules", response_model=ScheduleList, **shape,
             summary="Schedules of a project")
    async def schedules(project: ProjectId, caller: Caller):
        return await services.list_schedules(caller, project)

    @api.post("/projects/{project}/schedules", operation_id="createSchedule", status_code=201,
              response_model=Schedule, **shape,
              summary="Schedule runs (writers in development, administrators elsewhere)")
    async def create_schedule(project: ProjectId, data: Annotated[ScheduleInput, Body()], caller: Caller):
        return await services.create_schedule(caller, project, data.environment, data.cron, data.command,
                                              data.select, data.ref, data.enabled)

    @api.patch("/schedules/{schedule}", operation_id="updateSchedule", response_model=Schedule, **shape,
               summary="Change a schedule; what is left out stays")
    async def update_schedule(schedule: ScheduleId, data: Annotated[ScheduleChange, Body()], caller: Caller):
        return await services.update_schedule(caller, schedule, data.model_dump(exclude_none=True))

    @api.delete("/schedules/{schedule}", operation_id="deleteSchedule", response_model=Deleted, **shape,
                summary="Delete a schedule")
    async def delete_schedule(schedule: ScheduleId, caller: Caller):
        return await services.delete_schedule(caller, schedule)

    @api.get("/projects/{project}/pipeline", operation_id="getPipelineGraph", response_model=PipelineGraph, **shape,
             summary="The pipeline graph with each node's latest status and tests")
    async def pipeline(project: ProjectId, caller: Caller, environment: EnvironmentName = "development",
                       ref: RunRef = None):
        return await services.get_pipeline_graph(caller, project, environment, ref=ref)

    @api.get("/projects/{project}/nodes/{node}", operation_id="getNode", response_model=NodeDetail, **shape,
             summary="One node: description, columns, SQL, compiled SQL, tests and latest result")
    async def node(project: ProjectId, node: str, caller: Caller, environment: EnvironmentName = "development",
                   ref: RunRef = None):
        return await services.get_node(caller, project, node, environment, ref)

    @api.get("/projects/{project}/lineage/{node}", operation_id="getLineage", response_model=Lineage, **shape,
             summary="Upstream and downstream nodes of a node")
    async def node_lineage(project: ProjectId, node: str, caller: Caller,
                           environment: EnvironmentName = "development",
                           depth: Annotated[int, Query(ge=1, le=20)] = 3, ref: RunRef = None):
        return await services.lineage(caller, project, node, environment, depth, ref)

    return api


def require_json_write(request: Request):
    """Cookie-authenticated writes need the API header: a browser form cannot set it."""
    if request.method not in ("GET", "HEAD") and not request.headers.get("authorization"):
        if request.headers.get("x-iceberg-dbt") != "1":
            raise DbtError(403, "Send the X-Iceberg-Dbt: 1 header with browser requests.", "csrf")
