"""REST API v1. Every operation has an MCP tool of the same capability (OPERATIONS), so agents and
scripts can do everything; the web page uses the same API with the browser session.
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi import Path as PathParam
from pydantic import BaseModel, ConfigDict, Field

from .errors import EvalError

# operationId → MCP tool name. tests/test_api.py keeps both sides complete.
OPERATIONS = {
    "whoami": "whoami", "listEnvironments": "list_environments", "enableEnvironment": "enable_environment",
    "disableEnvironment": "disable_environment", "listAgentModels": "list_agent_models",
    "listQuestionSets": "list_question_sets", "getQuestionSet": "get_question_set",
    "createQuestionSet": "create_question_set", "updateQuestionSet": "update_question_set",
    "importQuestions": "import_questions", "deleteQuestionSet": "delete_question_set",
    "runEvaluation": "run_evaluation", "compareLlmModels": "compare_llm_models",
    "measureSemanticModel": "measure_semantic_model", "listRuns": "list_runs", "getRun": "get_run",
    "compareRuns": "compare_runs", "listSchedules": "list_schedules", "createSchedule": "create_schedule",
    "updateSchedule": "update_schedule", "deleteSchedule": "delete_schedule",
    "getLiveEvaluation": "get_live_evaluation", "updateLiveEvaluation": "update_live_evaluation",
    "listReviews": "list_reviews", "updateReview": "update_review", "promoteReview": "promote_review",
}

EnvironmentName = Literal["development", "acceptance", "production"]
TeamId = Annotated[str, PathParam(pattern=r"^team-[a-f0-9]{32}$")]
SetId = Annotated[str, PathParam(alias="questionSet", pattern=r"^qs-[a-f0-9]{24}$")]
RunId = Annotated[str, PathParam(pattern=r"^run-[a-f0-9]{24}$")]
ScheduleId = Annotated[str, PathParam(pattern=r"^sch-[a-f0-9]{24}$")]
ReviewId = Annotated[str, PathParam(pattern=r"^rev-[a-f0-9]{24}$")]
LLM_HELP = "An LLM the agent should answer with, from list_agent_models' llmModels; empty for its default."
CRON_HELP = "Five-field cron expression in UTC, at most every 5 minutes, such as '0 6 * * 1'."
TARGET_HELP = "The team database the results go to as Iceberg tables; empty for none."
YAML_HELP = "The cases as pydantic-evals YAML; a change is a new version. See get_question_set for the format."


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelRef(Input):
    database: str = Field(pattern=r"^db-[a-f0-9]{32}$")
    namespace: list[str] = Field(min_length=1, max_length=20)
    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,256}$")


class QuestionSetInput(Input):
    team: str = Field(pattern=r"^team-[a-f0-9]{32}$")
    environment: EnvironmentName = "development"
    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,47}$")
    description: str = Field(default="", max_length=500)
    model: ModelRef = Field(description="The semantic model the expected queries run on.")
    yaml: str = Field(max_length=500_000, description=YAML_HELP)
    rubric: str = Field(default="", max_length=2000, description="What the LLM judge looks for.")
    targetDatabase: str = Field(default="", pattern=r"^(db-[a-f0-9]{32})?$", description=TARGET_HELP)


class QuestionSetChange(Input):
    name: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{2,47}$")
    description: str | None = Field(default=None, max_length=500)
    model: ModelRef | None = Field(default=None, description="The semantic model the expected queries run on.")
    yaml: str | None = Field(default=None, max_length=500_000, description=YAML_HELP)
    rubric: str | None = Field(default=None, max_length=2000, description="What the LLM judge looks for.")
    targetDatabase: str | None = Field(default=None, pattern=r"^(db-[a-f0-9]{32})?$", description=TARGET_HELP)
    note: str | None = Field(default=None, max_length=200, description="Why this version, for its history.")


class ImportInput(Input):
    questionsSql: str = Field(max_length=500_000, description=(
        "A semantic model's <name>.questions.sql: `-- Q:` lines with a `-- query_semantic_model:` line each."))


class RunInput(Input):
    llmModel: str = Field(default="", max_length=200, description=LLM_HELP)


class CompareInput(Input):
    llmModels: list[Annotated[str, Field(max_length=200)]] = Field(min_length=2, max_length=5, description=(
        "The LLMs to compare, from list_agent_models' llmModels."))


class ScheduleInput(Input):
    cron: str = Field(max_length=100, description=CRON_HELP)
    llmModel: str = Field(default="", max_length=200, description=LLM_HELP)
    enabled: bool = True


class ScheduleChange(Input):
    cron: str | None = Field(default=None, max_length=100, description=CRON_HELP)
    llmModel: str | None = Field(default=None, max_length=200, description=LLM_HELP)
    enabled: bool | None = None


class LiveChange(Input):
    enabled: bool | None = Field(default=None, description=(
        "On: Conversational BI keeps the team's conversations here. Off: it deletes the ones it kept."))
    samplePct: int | None = Field(default=None, ge=1, le=100, description=(
        "Share of new conversations scored; 20 until set."))
    cron: str | None = Field(default=None, max_length=100, description=(
        "When to check: a five-field cron expression in UTC, at most every 5 minutes; hourly until set."))
    targetDatabase: str | None = Field(default=None, pattern=r"^(db-[a-f0-9]{32})?$", description=(
        "The team database the scores go to as evaluations.live_scores; empty for none."))


class ReviewChange(Input):
    status: Literal["dismissed", "open"] = Field(description=(
        "dismissed: the answer was fine. open: back in the queue."))
    note: str | None = Field(default=None, max_length=500)


class PromoteInput(Input):
    questionSet: str = Field(pattern=r"^qs-[a-f0-9]{24}$")
    name: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_-]{0,59}$")
    expect: dict | None = Field(default=None, description=(
        "query, refusal, clarify or decline; by default the query the agent ran."))


# Responses. Every key a service returns is declared here; a key that is not always there has a default and is
# left out of the response when the service did not return it (response_model_exclude_unset).

Timestamp = Annotated[str, Field(description="RFC 3339 in UTC with milliseconds, such as 2026-11-24T09:15:02.123Z.")]
RunStatus = Literal["queued", "running", "success", "error"]


class Error(BaseModel):
    error: str = Field(description="What went wrong, for a person.")
    code: str = Field(description="A stable code for programs, such as unknown_question_set.")
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
    agent: str = Field(description="The agent under evaluation: the extension that answers the questions.")
    judge: bool = Field(description="Whether the platform configured an LLM judge.")


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


class SemanticModel(BaseModel):
    database: str
    namespace: list[str]
    name: str


class AgentSemanticModel(BaseModel):
    model: SemanticModel = Field(description="The reference a question set takes as its model.")
    name: str
    databaseName: str
    environment: str
    shared: bool = Field(description="Shared with the team by another team.")


class AgentModels(BaseModel):
    semanticModels: list[AgentSemanticModel]
    llmModels: list[str] = Field(description="The LLMs a run may choose.")
    schemaOnly: bool = Field(description="Whether measure_semantic_model's control arm is allowed.")


class Run(BaseModel):
    id: str
    questionSet: str
    version: int = Field(description="The question set's version the run used.")
    team: str
    environment: EnvironmentName
    llmModel: str | None = Field(description="The LLM asked for; null for the agent's default.")
    status: RunStatus
    triggeredBy: str = Field(description="The person's name, or \"schedule\".")
    agent: str | None = Field(description="The MCP client when an agent started the run.")
    scheduled: bool
    schedule: str | None = Field(description="The schedule's id, for a scheduled run.")
    createdAt: Timestamp
    startedAt: Timestamp | None
    finishedAt: Timestamp | None
    summary: dict[str, Any] = Field(description=(
        "Empty until the run has finished. Then its totals: cases, answered, passed, failed, errors, reliable, "
        "passRate, scorers, inputTokens, outputTokens, medianSeconds, p95Seconds, llmModels, and published: where "
        "the results went, or why not."))
    error: str | None
    arm: Literal["semantic", "control"] = Field(description=(
        "semantic: with the semantic model. control: the same LLM with only the table schemas."))
    pair: str | None = Field(description="Shared by the two runs of measure_semantic_model.")


class RunList(BaseModel):
    runs: list[Run]


class Score(BaseModel):
    value: bool
    reason: str | None


class CaseResult(BaseModel):
    name: str
    question: str
    kind: Literal["query", "refusal", "clarify", "decline"]
    tags: list[str]
    passed: bool
    scores: dict[str, Score]
    answer: str
    queries: list[dict[str, Any]] = Field(description="The queries the agent ran, as it sent them, with results.")
    toolCalls: list[dict[str, Any]]
    llmModel: str | None = Field(description="The LLM that answered.")
    inputTokens: int
    outputTokens: int
    durationMs: int
    traceId: str | None
    error: str | None = Field(description="Why the question got no answer or no score.")


class RunDetail(Run):
    questionSetName: str
    cases: list[CaseResult]


class Measurement(BaseModel):
    pair: str
    control: Run
    semantic: Run


class Change(BaseModel):
    fixed: int
    broken: int
    same: int
    noAnswer: int


class ComparedAnswer(BaseModel):
    passed: bool
    scores: dict[str, Score]
    answer: str


class ComparedCase(BaseModel):
    name: str
    question: str
    change: Literal["fixed", "broken", "same", "no_answer"]
    baseline: ComparedAnswer | None
    candidate: ComparedAnswer | None


class Rates(BaseModel):
    questions: int
    control: float
    semantic: float
    points: float


class Uplift(Rates):
    byKind: dict[str, Rates]


class Comparison(BaseModel):
    baseline: Run
    candidate: Run
    reliable: bool
    change: Change
    cases: list[ComparedCase]
    uplift: Uplift | None = Field(default=None, description=(
        "Only between the two runs of measure_semantic_model: what the semantic model adds."))


class Schedule(BaseModel):
    id: str
    questionSet: str
    cron: str
    llmModel: str | None = Field(description="Null for the agent's default.")
    enabled: bool
    nextRunAt: Timestamp | None = Field(description="Null while the schedule is not enabled.")
    lastRun: str | None = Field(description="The id of the last run the schedule started.")
    createdBy: str
    createdAt: Timestamp


class ScheduleList(BaseModel):
    schedules: list[Schedule]


class QuestionSet(BaseModel):
    id: str
    team: str
    environment: EnvironmentName
    name: str
    description: str
    agent: str = Field(description="The agent under evaluation.")
    model: SemanticModel
    rubric: str
    targetDatabase: str | None
    version: int
    createdAt: Timestamp
    createdBy: str
    updatedAt: Timestamp
    updatedBy: str
    role: str | None = Field(description="Your role in the question set's team.")


class QuestionSetItem(QuestionSet):
    lastRun: Run | None


class QuestionSetList(BaseModel):
    questionSets: list[QuestionSetItem]


class Version(BaseModel):
    version: int
    cases: int = Field(description="How many cases the version has.")
    note: str | None
    createdAt: Timestamp
    createdBy: str


class QuestionSetDetail(QuestionSet):
    yaml: str = Field(description="The cases of shownVersion.")
    shownVersion: int
    versions: list[Version]
    schedules: list[Schedule]
    runs: list[Run] = Field(description="The 10 latest runs.")
    imported: int | None = Field(default=None, description="After an import: how many questions it read.")


class Deleted(BaseModel):
    deleted: bool
    questionSet: str | None = None
    schedule: str | None = None
    note: str | None = None


class LiveEvaluation(BaseModel):
    team: str
    environment: EnvironmentName
    enabled: bool
    samplePct: int
    cron: str
    targetDatabase: str | None
    nextRunAt: Timestamp | None = Field(description="The next check; null while live evaluation is off.")
    updatedBy: str | None
    updatedAt: Timestamp | None
    lastChecked: Timestamp | None
    lastSummary: dict[str, Any] | None = Field(description=(
        "The last check: conversations, checked, flagged and published, or error."))
    openReviews: int
    note: str | None = None


class Review(BaseModel):
    id: str
    team: str
    environment: EnvironmentName
    conversation: str
    askedAt: Timestamp
    question: str
    answer: str
    model: SemanticModel
    queries: list[dict[str, Any]]
    llmModel: str | None
    traceId: str | None
    scores: dict[str, Score]
    status: Literal["open", "dismissed", "promoted"]
    createdAt: Timestamp
    reviewedBy: str | None
    reviewedAt: Timestamp | None
    note: str | None
    promotedTo: str | None = Field(description="<question set id>:<case name>, once promoted.")


class ReviewList(BaseModel):
    reviews: list[Review]


class Promotion(BaseModel):
    review: Review
    case: str = Field(description="The new case's name.")
    questionSet: QuestionSetDetail


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
             summary="Your teams, roles, databases and where Evaluations is enabled")
    async def whoami(caller: Caller):
        return await services.whoami(caller)

    @api.get("/teams/{team}/environments", operation_id="listEnvironments", response_model=EnvironmentList, **shape,
             summary="Where Evaluations is enabled for a team, with each environment's databases")
    async def environments(team: TeamId, caller: Caller):
        return await services.environments(caller, team)

    @api.post("/teams/{team}/environments/{environment}", operation_id="enableEnvironment",
              response_model=Environment, **shape,
              summary="Enable Evaluations for a team environment (team administrators)")
    async def enable(team: TeamId, environment: EnvironmentName, caller: Caller):
        return await services.enable_environment(caller, team, environment)

    @api.delete("/teams/{team}/environments/{environment}", operation_id="disableEnvironment",
                response_model=Environment, **shape,
                summary="Disable Evaluations for a team environment (team administrators)")
    async def disable(team: TeamId, environment: EnvironmentName, caller: Caller):
        return await services.disable_environment(caller, team, environment)

    @api.get("/teams/{team}/environments/{environment}/agent-models", operation_id="listAgentModels",
             response_model=AgentModels, **shape,
             summary="The semantic models the agent answers from for a team, and the LLMs a run may choose")
    async def agent_models(team: TeamId, environment: EnvironmentName, caller: Caller):
        return await services.agent_models(caller, team, environment)

    @api.get("/question-sets", operation_id="listQuestionSets", response_model=QuestionSetList, **shape,
             summary="Question sets of your teams, with their last run")
    async def question_sets(caller: Caller):
        return await services.list_question_sets(caller)

    @api.post("/question-sets", operation_id="createQuestionSet", status_code=201,
              response_model=QuestionSetDetail, **shape, summary="Create a question set")
    async def create(data: Annotated[QuestionSetInput, Body()], caller: Caller):
        return await services.create_question_set(caller, data.model_dump())

    @api.get("/question-sets/{questionSet}", operation_id="getQuestionSet", response_model=QuestionSetDetail,
             **shape, summary="A question set with its cases (YAML), versions, schedules and latest runs")
    async def question_set(question_set: SetId, caller: Caller,
                           version: Annotated[int | None, Query(ge=1, description="An older version.")] = None):
        return await services.get_question_set(caller, question_set, version)

    @api.patch("/question-sets/{questionSet}", operation_id="updateQuestionSet", response_model=QuestionSetDetail,
               **shape, summary="Change a question set; what is left out stays, and new cases are a new version")
    async def update(question_set: SetId, data: Annotated[QuestionSetChange, Body()], caller: Caller):
        return await services.update_question_set(caller, question_set, data.model_dump(exclude_none=True))

    @api.post("/question-sets/{questionSet}/import", operation_id="importQuestions",
              response_model=QuestionSetDetail, **shape,
              summary="Add the questions of a semantic model's <name>.questions.sql")
    async def import_(question_set: SetId, data: Annotated[ImportInput, Body()], caller: Caller):
        return await services.import_questions(caller, question_set, data.questionsSql)

    @api.delete("/question-sets/{questionSet}", operation_id="deleteQuestionSet", response_model=Deleted, **shape,
                summary="Delete a question set with its versions, runs and schedules (results in Iceberg stay)")
    async def delete(question_set: SetId, caller: Caller, confirm_name: Annotated[str, Query(
            alias="confirmName", max_length=48, description="The question set's exact name.")]):
        return await services.delete_question_set(caller, question_set, confirm_name)

    @api.post("/question-sets/{questionSet}/runs", operation_id="runEvaluation", status_code=202,
              response_model=Run, **shape,
              summary="Ask the agent every question of the set and score the answers")
    async def run(question_set: SetId, caller: Caller, data: Annotated[RunInput | None, Body()] = None):
        return await services.run_evaluation(caller, question_set, (data or RunInput()).llmModel)

    @api.post("/question-sets/{questionSet}/compare-llm-models", operation_id="compareLlmModels", status_code=202,
              response_model=RunList, **shape, summary="One run per LLM, to compare them with compare_runs")
    async def compare_llm_models(question_set: SetId, data: Annotated[CompareInput, Body()], caller: Caller):
        return await services.compare_llm_models(caller, question_set, data.llmModels)

    @api.post("/question-sets/{questionSet}/measure-semantic-model", operation_id="measureSemanticModel",
              status_code=202, response_model=Measurement, **shape,
              summary="What the semantic model adds: one run with it and one with only the table schemas")
    async def measure(question_set: SetId, caller: Caller, data: Annotated[RunInput | None, Body()] = None):
        return await services.measure_semantic_model(caller, question_set, (data or RunInput()).llmModel)

    @api.get("/question-sets/{questionSet}/runs", operation_id="listRuns", response_model=RunList, **shape,
             summary="Runs of a question set, newest first")
    async def runs(question_set: SetId, caller: Caller, limit: Annotated[int, Query(ge=1, le=200)] = 20):
        return await services.list_runs(caller, question_set, limit)

    @api.get("/runs/{run}", operation_id="getRun", response_model=RunDetail, **shape,
             summary="A run with every case: answer, scores and why")
    async def get_run(run: RunId, caller: Caller):
        return await services.get_run(caller, run)

    @api.get("/runs/{baseline}/compare/{candidate}", operation_id="compareRuns", response_model=Comparison, **shape,
             summary="Two runs of one question set, case by case: what got better, what got worse")
    async def compare(baseline: RunId, candidate: RunId, caller: Caller):
        return await services.compare_runs(caller, baseline, candidate)

    @api.get("/question-sets/{questionSet}/schedules", operation_id="listSchedules", response_model=ScheduleList,
             **shape, summary="A question set's schedules")
    async def schedules(question_set: SetId, caller: Caller):
        return await services.list_schedules(caller, question_set)

    @api.post("/question-sets/{questionSet}/schedules", operation_id="createSchedule", status_code=201,
              response_model=Schedule, **shape,
              summary="Run a question set on a cron schedule in UTC (team administrators)")
    async def create_schedule(question_set: SetId, data: Annotated[ScheduleInput, Body()], caller: Caller):
        return await services.create_schedule(caller, question_set, data.cron, data.llmModel, data.enabled)

    @api.patch("/schedules/{schedule}", operation_id="updateSchedule", response_model=Schedule, **shape,
               summary="Change a schedule; what is left out stays (team administrators)")
    async def update_schedule(schedule: ScheduleId, data: Annotated[ScheduleChange, Body()], caller: Caller):
        return await services.update_schedule(caller, schedule, data.model_dump(exclude_none=True))

    @api.delete("/schedules/{schedule}", operation_id="deleteSchedule", response_model=Deleted, **shape,
                summary="Delete a schedule (team administrators)")
    async def delete_schedule(schedule: ScheduleId, caller: Caller):
        return await services.delete_schedule(caller, schedule)

    @api.get("/teams/{team}/environments/{environment}/live", operation_id="getLiveEvaluation",
             response_model=LiveEvaluation, **shape,
             summary="Live evaluation of a team environment: its settings, last check and open reviews")
    async def get_live_evaluation(team: TeamId, environment: EnvironmentName, caller: Caller):
        return await services.get_live_evaluation(caller, team, environment)

    @api.patch("/teams/{team}/environments/{environment}/live", operation_id="updateLiveEvaluation",
               response_model=LiveEvaluation, **shape,
               summary="Switch live evaluation on or off, or change its settings; what is left out stays (team "
                       "administrators)")
    async def update_live(team: TeamId, environment: EnvironmentName, data: Annotated[LiveChange, Body()],
                          caller: Caller):
        return await services.update_live_evaluation(caller, team, environment, data.model_dump(exclude_none=True))

    @api.get("/teams/{team}/environments/{environment}/reviews", operation_id="listReviews",
             response_model=ReviewList, **shape, summary="Live conversations that failed a check, newest first")
    async def reviews(team: TeamId, environment: EnvironmentName, caller: Caller,
                      status: Annotated[Literal["open", "dismissed", "promoted"] | None, Query()] = None,
                      limit: Annotated[int, Query(ge=1, le=500)] = 100):
        return await services.list_reviews(caller, team, environment, status, limit)

    @api.patch("/reviews/{review}", operation_id="updateReview", response_model=Review, **shape,
               summary="Dismiss a review or put it back in the queue")
    async def update_review(review: ReviewId, data: Annotated[ReviewChange, Body()], caller: Caller):
        return await services.update_review(caller, review, data.status, data.note)

    @api.post("/reviews/{review}/promote", operation_id="promoteReview", response_model=Promotion, **shape,
              summary="Turn a reviewed conversation into a case of a question set")
    async def promote(review: ReviewId, data: Annotated[PromoteInput, Body()], caller: Caller):
        return await services.promote_review(caller, review, data.questionSet, data.name, data.expect)

    return api


def require_json_write(request: Request):
    """Cookie-authenticated writes need the API header: a browser form cannot set it."""
    if request.method not in ("GET", "HEAD") and not request.headers.get("authorization"):
        if request.headers.get("x-iceberg-evaluations") != "1":
            raise EvalError(403, "Send the X-Iceberg-Evaluations: 1 header with browser requests.", "csrf")
