"""REST API v1. Every operation has an MCP tool of the same capability (OPERATIONS), so agents and
scripts can do everything; the web page uses the same API with the browser session.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi import Path as PathParam
from pydantic import BaseModel, ConfigDict, Field

from .errors import EvalError

# operationId → MCP tool name. tests/test_api.py keeps both sides complete.
OPERATIONS = {
    "whoami": "whoami", "listEnvironments": "list_environments", "enableEnvironment": "enable_environment",
    "disableEnvironment": "disable_environment", "listAgentModels": "list_agent_models",
    "listQuestionSets": "list_question_sets", "getQuestionSet": "get_question_set",
    "saveQuestionSet": "save_question_set", "importQuestions": "import_questions",
    "deleteQuestionSet": "delete_question_set", "runEvaluation": "run_evaluation", "compareModels": "compare_models",
    "measureSemanticModel": "measure_semantic_model",
    "listRuns": "list_runs", "getRun": "get_run", "compareRuns": "compare_runs", "setSchedule": "set_schedule",
    "deleteSchedule": "delete_schedule", "getLive": "get_live", "setLive": "set_live", "listReviews": "list_reviews",
    "reviewConversation": "review_conversation", "promoteToCase": "promote_to_case",
}

Environment = Literal["development", "acceptance", "production"]
TeamId = Annotated[str, PathParam(pattern=r"^team-[a-f0-9]{32}$")]
SetId = Annotated[str, PathParam(pattern=r"^qs-[a-f0-9]{24}$")]
RunId = Annotated[str, PathParam(pattern=r"^run-[a-f0-9]{24}$")]
ScheduleId = Annotated[str, PathParam(pattern=r"^sch-[a-f0-9]{24}$")]
ReviewId = Annotated[str, PathParam(pattern=r"^rev-[a-f0-9]{24}$")]
LlmModel = Annotated[str, Field(default="", max_length=200, description=(
    "An LLM the agent should answer with, from list_agent_models' llmModels; empty for its default."))]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelRef(Input):
    database: str = Field(pattern=r"^db-[a-f0-9]{32}$")
    namespace: list[str] = Field(min_length=1, max_length=20)
    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,256}$")


class QuestionSetInput(Input):
    id: str | None = Field(default=None, pattern=r"^qs-[a-f0-9]{24}$", description="Leave out to create one.")
    team: str = Field(default="", pattern=r"^(team-[a-f0-9]{32})?$", description="For a new question set.")
    environment: Environment = "development"
    name: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{2,47}$")
    description: str | None = Field(default=None, max_length=500)
    model: ModelRef | None = Field(default=None, description="The semantic model the expected queries run on.")
    yaml: str | None = Field(default=None, max_length=500_000, description=(
        "The cases as pydantic-evals YAML; a change is a new version. See get_question_set for the format."))
    rubric: str | None = Field(default=None, max_length=2000, description="What the LLM judge looks for.")
    target_database: str | None = Field(default=None, pattern=r"^(db-[a-f0-9]{32})?$", description=(
        "The team database the results go to as Iceberg tables; empty for none."))
    note: str | None = Field(default=None, max_length=200, description="Why this version, for its history.")


class ImportInput(Input):
    questions_sql: str = Field(max_length=500_000, description=(
        "A semantic model's <name>.questions.sql: `-- Q:` lines with a `-- query_semantic_model:` line each."))


class RunInput(Input):
    llm_model: LlmModel = ""


class CompareInput(Input):
    llm_models: list[str] = Field(min_length=2, max_length=5)


class ScheduleInput(Input):
    cron: str = Field(max_length=100, description="Five-field cron in UTC, such as '0 6 * * 1'.")
    llm_model: LlmModel = ""
    enabled: bool = True


class LiveInput(Input):
    enabled: bool
    sample_pct: int = Field(default=20, ge=1, le=100, description="Share of new conversations scored.")
    cron: str = Field(default="0 * * * *", max_length=100, description="When to check, five-field cron in UTC.")
    target_database: str = Field(default="", pattern=r"^(db-[a-f0-9]{32})?$", description=(
        "The team database the scores go to as evaluations.live_scores; empty for none."))


class ReviewInput(Input):
    action: Literal["dismiss", "reopen"]
    note: str = Field(default="", max_length=500)


class PromoteInput(Input):
    question_set: str = Field(pattern=r"^qs-[a-f0-9]{24}$")
    name: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_-]{0,59}$")
    expect: dict | None = Field(default=None, description=(
        "query, refusal, clarify or decline; by default the query the agent ran."))


def router(services, caller_dependency):
    api = APIRouter(prefix="/api/v1")
    Caller = Annotated[object, Depends(caller_dependency)]

    @api.get("/me", operation_id="whoami", summary="Your teams, roles and where Evaluations is enabled")
    async def whoami(caller: Caller):
        return await services.whoami(caller)

    @api.get("/teams/{team}/environments", operation_id="listEnvironments",
             summary="Where Evaluations is enabled for a team")
    async def environments(team: TeamId, caller: Caller):
        return await services.environments(caller, team)

    @api.post("/teams/{team}/environments/{environment}", operation_id="enableEnvironment",
              summary="Enable Evaluations for a team environment (team administrators)")
    async def enable(team: TeamId, environment: Environment, caller: Caller):
        return await services.enable_environment(caller, team, environment)

    @api.delete("/teams/{team}/environments/{environment}", operation_id="disableEnvironment",
                summary="Disable Evaluations for a team environment (team administrators)")
    async def disable(team: TeamId, environment: Environment, caller: Caller):
        return await services.disable_environment(caller, team, environment)

    @api.get("/teams/{team}/environments/{environment}/agent-models", operation_id="listAgentModels",
             summary="The semantic models the agent answers from for a team, and the LLMs a run may choose")
    async def agent_models(team: TeamId, environment: Environment, caller: Caller):
        return await services.agent_models(caller, team, environment)

    @api.get("/question-sets", operation_id="listQuestionSets", summary="Question sets of your teams")
    async def question_sets(caller: Caller):
        return await services.list_question_sets(caller)

    @api.get("/question-sets/{id}", operation_id="getQuestionSet",
             summary="A question set with its cases (YAML), versions, schedules and latest runs")
    async def question_set(id: SetId, caller: Caller,
                           version: Annotated[int | None, Query(ge=1, description="An older version.")] = None):
        return await services.get_question_set(caller, id, version)

    @api.post("/question-sets", operation_id="saveQuestionSet",
              summary="Create a question set, or change one; new cases are a new version")
    async def save(data: Annotated[QuestionSetInput, Body()], caller: Caller):
        return await services.save_question_set(caller, data)

    @api.post("/question-sets/{id}/import", operation_id="importQuestions",
              summary="Add the questions of a semantic model's <name>.questions.sql")
    async def import_(id: SetId, data: Annotated[ImportInput, Body()], caller: Caller):
        return await services.import_questions(caller, id, data.questions_sql)

    @api.delete("/question-sets/{id}", operation_id="deleteQuestionSet",
                summary="Delete a question set with its runs and schedules (results in Iceberg stay)")
    async def delete(id: SetId, caller: Caller):
        return await services.delete_question_set(caller, id)

    @api.post("/question-sets/{id}/runs", operation_id="runEvaluation",
              summary="Ask the agent every question of the set and score the answers")
    async def run(id: SetId, data: Annotated[RunInput, Body()], caller: Caller):
        return await services.run_evaluation(caller, id, data.llm_model)

    @api.post("/question-sets/{id}/compare-models", operation_id="compareModels",
              summary="One run per LLM, to compare them with compare_runs")
    async def compare_models(id: SetId, data: Annotated[CompareInput, Body()], caller: Caller):
        return await services.compare_models(caller, id, data.llm_models)

    @api.post("/question-sets/{id}/measure-semantic-model", operation_id="measureSemanticModel",
              summary="What the semantic model adds: one run with it and one with only the table schemas")
    async def measure(id: SetId, data: Annotated[RunInput, Body()], caller: Caller):
        return await services.measure_semantic_model(caller, id, data.llm_model)

    @api.get("/question-sets/{id}/runs", operation_id="listRuns", summary="Runs of a question set, newest first")
    async def runs(id: SetId, caller: Caller):
        return await services.list_runs(caller, id)

    @api.get("/runs/{run}", operation_id="getRun", summary="A run with every case: answer, scores and why")
    async def get_run(run: RunId, caller: Caller):
        return await services.get_run(caller, run)

    @api.get("/runs/{baseline}/compare/{candidate}", operation_id="compareRuns",
             summary="Two runs of one question set, case by case: what got better, what got worse")
    async def compare(baseline: RunId, candidate: RunId, caller: Caller):
        return await services.compare_runs(caller, baseline, candidate)

    @api.post("/question-sets/{id}/schedules", operation_id="setSchedule",
              summary="Run a question set on a cron schedule (team administrators)")
    async def schedule(id: SetId, data: Annotated[ScheduleInput, Body()], caller: Caller,
                       schedule_id: Annotated[str | None, Query(pattern=r"^sch-[a-f0-9]{24}$",
                                                                description="Change this schedule.")] = None):
        return await services.set_schedule(caller, id, data.cron, data.llm_model, data.enabled, schedule_id)

    @api.delete("/question-sets/{id}/schedules/{schedule}", operation_id="deleteSchedule",
                summary="Delete a schedule (team administrators)")
    async def delete_schedule(id: SetId, schedule: ScheduleId, caller: Caller):
        return await services.delete_schedule(caller, id, schedule)

    @api.get("/teams/{team}/environments/{environment}/live", operation_id="getLive",
             summary="Live evaluation of a team environment: its settings, last check and open reviews")
    async def get_live(team: TeamId, environment: Environment, caller: Caller):
        return await services.get_live(caller, team, environment)

    @api.put("/teams/{team}/environments/{environment}/live", operation_id="setLive",
             summary="Switch live evaluation on or off (team administrators)")
    async def set_live(team: TeamId, environment: Environment, data: Annotated[LiveInput, Body()], caller: Caller):
        return await services.set_live(caller, team, environment, data.enabled, data.sample_pct, data.cron,
                                       data.target_database)

    @api.get("/teams/{team}/environments/{environment}/reviews", operation_id="listReviews",
             summary="Live conversations that failed a check, newest first")
    async def reviews(team: TeamId, environment: Environment, caller: Caller,
                      status: Annotated[Literal["open", "dismissed", "promoted"] | None, Query()] = None):
        return await services.list_reviews(caller, team, environment, status)

    @api.post("/reviews/{review}", operation_id="reviewConversation", summary="Dismiss or reopen a review")
    async def review(review: ReviewId, data: Annotated[ReviewInput, Body()], caller: Caller):
        return await services.review_conversation(caller, review, data.action, data.note)

    @api.post("/reviews/{review}/promote", operation_id="promoteToCase",
              summary="Turn a reviewed conversation into a case of a question set")
    async def promote(review: ReviewId, data: Annotated[PromoteInput, Body()], caller: Caller):
        return await services.promote_to_case(caller, review, data.question_set, data.name, data.expect)

    return api


def require_json_write(request: Request):
    """Cookie-authenticated writes need the API header: a browser form cannot set it."""
    if request.method not in ("GET", "HEAD") and not request.headers.get("authorization"):
        if request.headers.get("x-iceberg-evaluations") != "1":
            raise EvalError(403, "Send the X-Iceberg-Evaluations: 1 header with browser requests.", "csrf")
