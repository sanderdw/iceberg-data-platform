"""MCP server: the Evaluations extension for AI agents, with the same capabilities and rules as the REST API.

Agents sign in through Keycloak with the public `ext-<id>-mcp` client (PKCE, loopback redirect),
so every tool runs with the signed-in user's team roles; the agent under evaluation is asked for
the team. Destructive tools are annotated so clients ask the user first.
"""

import logging
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from fastapi.responses import JSONResponse
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field

from .api import ModelRef
from .auth import Caller
from .errors import EvalError

LOG = logging.getLogger(__name__)
MAX_BODY = 1024 * 1024
READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
# Runs reach outside the platform: the agent asks an LLM provider.
RUN = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True)
# Deletes something: clients ask the user first.
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False,
                              open_world_hint=False)
# Switching live evaluation off deletes the conversations Conversational BI kept; the same call again changes nothing.
LIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False)
INSTRUCTIONS = """Evaluations on the Iceberg Data Platform: question sets with known answers for the platform's AI
agent (Conversational BI), scored runs, LLM comparisons, schedules and live checks of real conversations.

Start with whoami (your teams, roles, databases and where Evaluations is enabled). A run asks the
agent for the team, so a team administrator enables both Evaluations and Conversational BI in that
environment first (enable_environment here, and in Conversational BI). list_agent_models shows the
semantic models the agent can answer from, and the LLMs a run may choose.

A question set belongs to one team and environment and one semantic model: create_question_set
makes one, update_question_set changes what you give and keeps the rest. Its cases are
pydantic-evals YAML; each case has a question and exactly one expected outcome:

cases:
- name: delay_by_carrier
  inputs:
    question: Which carrier has the highest average departure delay?
  expected_output:
    query: {metrics: [average_departure_delay], dimensions: [{field: CARRIER.name}],
            order_by: [{name: average_departure_delay, desc: true}]}
- name: runway_split
  inputs:
    question: What is the average delay by runway length?
  expected_output: {refusal: fan_out}
- name: which_airport
  inputs:
    question: What is the on-time rate per airport?
  expected_output: {clarify: true}
  metadata: {tags: [ambiguous], rubric: Asks whether departure or arrival airports are meant.}

`query` is a governed query on the set's semantic model (as Conversational BI's
query_semantic_model tool takes it); its result is computed again at every run, so cases stay right
when the data changes. `refusal` is the error code the agent must run into and explain without
figures. `clarify` means the agent should ask back. `decline: true` is a question the model can't
answer (no such data, a forecast, an instruction to break the rules): no query, no figures.
import_questions adds the questions of a semantic model's <name>.questions.sql (the semantic-model
skill writes it).

run_evaluation scores every case: result_matches, right_metrics, grounded (no number the results
don't hold), refused, clarified, declined, and an LLM judge when the platform configured one. It
returns the queued run; poll get_run until its status is success (finished) or error (it could not
run). get_run shows each answer with its scores and reasons. A run whose summary has
`reliable: false` got no answer for more than 5% of its questions (rate limits, an outage): it has
no pass rate and says nothing about the agent, so report that and run it again. compare_runs shows
what got better or worse between two runs; compare_llm_models runs the set once per LLM;
measure_semantic_model shows what the semantic model adds (the same LLM once with it, once with only
the table schemas). create_schedule runs a set on a cron schedule, at most every 5 minutes.

update_live_evaluation scores a sample of the team's real conversations on a schedule; while it is
on, Conversational BI keeps that team's conversations, and switching it off deletes them.
list_reviews shows the ones that failed a check; update_review dismisses one and promote_review
turns it into a case.

Times are RFC 3339 strings in UTC. Runs act for the whole team and cost LLM calls: ask the user
before running in acceptance or production, before compare_llm_models, before switching live
evaluation on or off, and before any tool marked destructive."""

Environment = Literal["development", "acceptance", "production"]
TeamId = Annotated[str, Field(pattern=r"^team-[a-f0-9]{32}$", description="Team id from whoami.")]
SetId = Annotated[str, Field(pattern=r"^qs-[a-f0-9]{24}$", description="Question set id from list_question_sets.")]
RunId = Annotated[str, Field(pattern=r"^run-[a-f0-9]{24}$", description="Run id.")]
ScheduleId = Annotated[str, Field(pattern=r"^sch-[a-f0-9]{24}$", description="Schedule id from list_schedules.")]
ReviewId = Annotated[str, Field(pattern=r"^rev-[a-f0-9]{24}$", description="Review id from list_reviews.")]
LlmModel = Annotated[str, Field(max_length=200, description="An LLM from list_agent_models' llmModels; empty for "
                                                           "the agent's default.")]
Cron = Annotated[str, Field(max_length=100, description="Five-field cron expression in UTC, at most every 5 "
                                                        "minutes, such as '0 6 * * 1'.")]
TargetDatabase = Annotated[str, Field(pattern=r"^(db-[a-f0-9]{32})?$", description=(
    "The team database the results go to as Iceberg tables, by id from whoami; empty for none."))]
Yaml = Annotated[str, Field(max_length=500_000, description="The cases as YAML (see the instructions).")]


class Verifier:
    def __init__(self, verifier):
        self.verifier = verifier

    async def verify_token(self, token):
        try:
            caller = await self.verifier.verify(token)
        except EvalError:
            return None
        return AccessToken(token=token, client_id=caller.client, scopes=[], expires_at=int(caller.expires_at),
                           subject=caller.subject, claims={"name": caller.name, "azp": caller.client})


def current():
    access = get_access_token()
    if access is None:
        raise ToolError("Sign in through Keycloak to use these tools.")
    return Caller(access.token, access.subject, access.claims.get("name", ""), access.client_id,
                  float(access.expires_at or 0))


def create_mcp(settings, services, verifier):
    mcp = MCPServer(
        "iceberg-evaluations", title="Evaluations", instructions=INSTRUCTIONS, token_verifier=Verifier(verifier),
        auth=AuthSettings(issuer_url=settings.issuer, resource_server_url=settings.origin + "/mcp",
                          validate_token_resource=False),
        log_level="WARNING",
    )

    async def call(fn, *args, **kwargs):
        try:
            return await fn(current(), *args, **kwargs)
        except EvalError as exc:
            detail = f" Detail: {exc.detail}" if exc.detail else ""
            raise ToolError(f"{exc} [{exc.code}]{detail}") from None

    @mcp.tool(annotations=READ)
    async def whoami() -> dict[str, Any]:
        """Your teams with your role in each, their databases, where Evaluations is enabled, and whether an LLM
        judge is set up."""
        return await call(services.whoami)

    @mcp.tool(annotations=READ)
    async def list_environments(team: TeamId) -> dict[str, Any]:
        """Where Evaluations is enabled for a team, and the databases of each environment."""
        return await call(services.environments, team)

    @mcp.tool(annotations=WRITE)
    async def enable_environment(team: TeamId, environment: Environment) -> dict[str, Any]:
        """Let Evaluations ask the agent for a team environment (team administrators). Conversational BI must be
        enabled there too. Returns the environment, as list_environments shows it. Ask the user first."""
        return await call(services.enable_environment, team, environment)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def disable_environment(team: TeamId, environment: Environment) -> dict[str, Any]:
        """Disable Evaluations for a team environment (team administrators): its runs and schedules stop working
        and live evaluation ends there. Returns the environment, as list_environments shows it."""
        return await call(services.disable_environment, team, environment)

    @mcp.tool(annotations=READ)
    async def list_agent_models(team: TeamId, environment: Environment) -> dict[str, Any]:
        """The semantic models the agent answers from for a team environment (semanticModels), the LLMs a run may
        choose (llmModels), and whether measure_semantic_model is allowed (schemaOnly)."""
        return await call(services.agent_models, team, environment)

    @mcp.tool(annotations=READ)
    async def list_question_sets() -> dict[str, Any]:
        """Question sets of your teams, with your role and each set's last run."""
        return await call(services.list_question_sets)

    @mcp.tool(annotations=READ)
    async def get_question_set(question_set: SetId, version: Annotated[int | None, Field(ge=1)] = None
                               ) -> dict[str, Any]:
        """A question set: its cases as YAML (of an older version, when given), versions, schedules and latest
        runs."""
        return await call(services.get_question_set, question_set, version)

    @mcp.tool(annotations=WRITE)
    async def create_question_set(
            team: TeamId, name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{2,47}$")],
            model: Annotated[ModelRef, Field(description="The semantic model, as list_agent_models gives it.")],
            yaml: Yaml, environment: Environment = "development",
            description: Annotated[str, Field(max_length=500)] = "",
            rubric: Annotated[str, Field(max_length=2000, description="What the LLM judge looks for.")] = "",
            target_database: TargetDatabase = "") -> dict[str, Any]:
        """Create a question set for a semantic model (writers in development, team administrators elsewhere)."""
        return await call(services.create_question_set, {
            "team": team, "environment": environment, "name": name, "description": description,
            "model": model.model_dump(), "yaml": yaml, "rubric": rubric, "targetDatabase": target_database})

    @mcp.tool(annotations=WRITE)
    async def update_question_set(
            question_set: SetId, name: Annotated[str | None, Field(pattern=r"^[a-z][a-z0-9_-]{2,47}$")] = None,
            description: Annotated[str | None, Field(max_length=500)] = None,
            model: Annotated[ModelRef | None, Field(description="Another semantic model.")] = None,
            yaml: Annotated[str | None, Field(max_length=500_000, description=(
                "All cases as YAML (see the instructions); saved as a new version."))] = None,
            rubric: Annotated[str | None, Field(max_length=2000)] = None,
            target_database: TargetDatabase | None = None,
            note: Annotated[str | None, Field(max_length=200, description=(
                "Why this version, for its history."))] = None) -> dict[str, Any]:
        """Change a question set. Give only what changes; the rest stays. New yaml is a new version, and the
        earlier versions stay."""
        data = {"name": name, "description": description, "model": model.model_dump() if model else None,
                "yaml": yaml, "rubric": rubric, "targetDatabase": target_database, "note": note}
        return await call(services.update_question_set, question_set, {k: v for k, v in data.items() if v is not None})

    @mcp.tool(annotations=WRITE)
    async def import_questions(question_set: SetId, questions_sql: Annotated[str, Field(max_length=500_000)]
                               ) -> dict[str, Any]:
        """Add the questions of a semantic model's <name>.questions.sql to a question set, as a new version; same
        names are replaced."""
        return await call(services.import_questions, question_set, questions_sql)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_question_set(question_set: SetId, confirm_name: Annotated[str, Field(max_length=48)]
                                  ) -> dict[str, Any]:
        """Delete a question set with its versions, runs and schedules; result tables in Iceberg stay.
        confirm_name must be the question set's name. Ask the user first."""
        return await call(services.delete_question_set, question_set, confirm_name)

    @mcp.tool(annotations=RUN)
    async def run_evaluation(question_set: SetId, llm_model: LlmModel = "") -> dict[str, Any]:
        """Ask the agent every question of the set and score the answers. Returns the queued run; poll get_run
        until its status is success or error."""
        return await call(services.run_evaluation, question_set, llm_model)

    @mcp.tool(annotations=RUN)
    async def compare_llm_models(question_set: SetId, llm_models: Annotated[list[LlmModel], Field(
            min_length=2, max_length=5)]) -> dict[str, Any]:
        """One run per LLM; compare them with compare_runs once they have finished. Each run costs LLM calls."""
        return await call(services.compare_llm_models, question_set, llm_models)

    @mcp.tool(annotations=RUN)
    async def measure_semantic_model(question_set: SetId, llm_model: LlmModel = "") -> dict[str, Any]:
        """What the semantic model adds: one run with it and one where the same LLM has only the table schemas and
        writes SQL. Costs two runs of LLM calls. When both have finished, compare_runs(control, semantic) has
        `uplift`."""
        return await call(services.measure_semantic_model, question_set, llm_model)

    @mcp.tool(annotations=READ)
    async def list_runs(question_set: SetId, limit: Annotated[int, Field(ge=1, le=200)] = 20) -> dict[str, Any]:
        """Runs of a question set, newest first, with their pass rates."""
        return await call(services.list_runs, question_set, limit)

    @mcp.tool(annotations=READ)
    async def get_run(run: RunId) -> dict[str, Any]:
        """A run with every case: the agent's answer, its queries, each score and why."""
        return await call(services.get_run, run)

    @mcp.tool(annotations=READ)
    async def compare_runs(baseline: RunId, candidate: RunId) -> dict[str, Any]:
        """Two runs of one question set, case by case: fixed, broken, the same, or no_answer."""
        return await call(services.compare_runs, baseline, candidate)

    @mcp.tool(annotations=READ)
    async def list_schedules(question_set: SetId) -> dict[str, Any]:
        """A question set's schedules, with when each runs next."""
        return await call(services.list_schedules, question_set)

    @mcp.tool(annotations=WRITE)
    async def create_schedule(question_set: SetId, cron: Cron, llm_model: LlmModel = "", enabled: bool = True
                              ) -> dict[str, Any]:
        """Run a question set on a schedule (team administrators). A question set can have several."""
        return await call(services.create_schedule, question_set, cron, llm_model, enabled)

    @mcp.tool(annotations=WRITE)
    async def update_schedule(schedule: ScheduleId, cron: Cron | None = None, llm_model: LlmModel | None = None,
                              enabled: bool | None = None) -> dict[str, Any]:
        """Change a schedule (team administrators). Give only what changes; the rest stays."""
        data = {"cron": cron, "llmModel": llm_model, "enabled": enabled}
        return await call(services.update_schedule, schedule, {k: v for k, v in data.items() if v is not None})

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_schedule(schedule: ScheduleId) -> dict[str, Any]:
        """Delete a schedule (team administrators)."""
        return await call(services.delete_schedule, schedule)

    @mcp.tool(annotations=READ)
    async def get_live_evaluation(team: TeamId, environment: Environment) -> dict[str, Any]:
        """Live evaluation of a team environment: settings, last check and the number of open reviews."""
        return await call(services.get_live_evaluation, team, environment)

    @mcp.tool(annotations=LIVE)
    async def update_live_evaluation(
            team: TeamId, environment: Environment, enabled: bool | None = None,
            sample_pct: Annotated[int | None, Field(ge=1, le=100, description=(
                "Share of new conversations scored; 20 until set."))] = None,
            cron: Annotated[str | None, Field(max_length=100, description=(
                "When to check: five-field cron in UTC, at most every 5 minutes; hourly until set."))] = None,
            target_database: Annotated[str | None, Field(pattern=r"^(db-[a-f0-9]{32})?$", description=(
                "The team database the scores go to as evaluations.live_scores; empty for none."))] = None
            ) -> dict[str, Any]:
        """Score a sample of the team's real Conversational BI conversations on a schedule (team administrators).
        Give only what changes; the rest stays. While it is enabled, Conversational BI keeps the team's
        conversations in that environment. Switching it off stops that and deletes the kept conversations. Ask
        the user first, either way."""
        data = {"enabled": enabled, "samplePct": sample_pct, "cron": cron, "targetDatabase": target_database}
        return await call(services.update_live_evaluation, team, environment,
                          {k: v for k, v in data.items() if v is not None})

    @mcp.tool(annotations=READ)
    async def list_reviews(team: TeamId, environment: Environment,
                           status: Literal["open", "dismissed", "promoted"] | None = None,
                           limit: Annotated[int, Field(ge=1, le=500)] = 100) -> dict[str, Any]:
        """Live conversations that failed a check, newest first: question, answer, the agent's queries and why."""
        return await call(services.list_reviews, team, environment, status, limit)

    @mcp.tool(annotations=WRITE)
    async def update_review(review: ReviewId, status: Literal["dismissed", "open"],
                            note: Annotated[str | None, Field(max_length=500)] = None) -> dict[str, Any]:
        """Dismiss a review (status dismissed: the answer was fine) or put it back in the queue (status open)."""
        return await call(services.update_review, review, status, note)

    @mcp.tool(annotations=WRITE)
    async def promote_review(review: ReviewId, question_set: SetId,
                             name: Annotated[str | None, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,59}$")] = None,
                             expect: Annotated[dict | None, Field(description=(
                                 "query, refusal, clarify or decline; by default the query the agent ran: check "
                                 "with the user that it is right."))] = None) -> dict[str, Any]:
        """Turn a reviewed live conversation into a regression case of a question set, as a new version."""
        return await call(services.promote_review, review, question_set, name, expect)

    return mcp


class Endpoint:
    """ASGI endpoint for /mcp that forwards to the transport of the running lifespan, with a body cap."""

    def __init__(self):
        self.app = None

    async def __call__(self, scope, receive, send):
        if self.app is None:
            await JSONResponse({"error": "The MCP transport is not running."}, status_code=503)(scope, receive, send)
            return
        body, more = bytearray(), True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                break
            body.extend(message.get("body", b""))
            more = message.get("more_body", False)
            if len(body) > MAX_BODY:
                await JSONResponse({"error": "Request is too large."}, status_code=413)(scope, receive, send)
                return
        replayed = False

        async def replay():
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send)


def mount(app, mcp, settings):
    endpoint = Endpoint()
    app.add_route("/mcp", endpoint)
    metadata = {"resource": settings.origin + "/mcp", "authorization_servers": [settings.issuer],
                "bearer_methods_supported": ["header"], "scopes_supported": ["openid", "profile"],
                "resource_name": "Evaluations"}

    @app.get("/.well-known/oauth-protected-resource", include_in_schema=False)
    @app.get("/.well-known/oauth-protected-resource/mcp", include_in_schema=False)
    def resource_metadata():
        return metadata

    @asynccontextmanager
    async def running():
        endpoint.app = mcp.streamable_http_app(
            streamable_http_path="/mcp", stateless_http=True, json_response=True,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            endpoint.app = None

    return running
