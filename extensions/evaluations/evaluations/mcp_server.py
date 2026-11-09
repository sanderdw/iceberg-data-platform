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

from .api import ImportInput, ModelRef, QuestionSetInput
from .auth import Caller
from .errors import EvalError

LOG = logging.getLogger(__name__)
MAX_BODY = 1024 * 1024
READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False,
                              open_world_hint=False)
INSTRUCTIONS = """Evaluations on the Iceberg Data Platform: question sets with known answers for the platform's AI
agent (Conversational BI), scored runs, model comparisons and schedules.

Start with whoami (your teams, roles and where Evaluations is enabled). A run asks the agent for
the team, so a team administrator enables both Evaluations and Conversational BI in that
environment first (enable_environment here, and in Conversational BI). list_agent_models shows the
semantic models the agent can answer from, and the LLMs a run may choose.

A question set belongs to one team and environment and one semantic model. Its cases are
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

`query` is a governed query on the set's semantic model (as Conversational BI's query tool takes
it); its result is computed again at every run, so cases stay right when the data changes.
`refusal` is the error code the agent must run into and explain without figures. `clarify`
means the agent should ask back. `decline: true` is a question the model can't answer (no such
data, a forecast, an instruction to break the rules): no query, no figures. import_questions
adds the questions of a semantic model's <name>.questions.sql (the semantic-model skill writes it).

run_evaluation scores every case: result_matches, right_metrics, grounded (no number the results
don't hold), refused, clarified, declined, and an LLM judge when the platform configured one. get_run
shows each answer with its scores and reasons. A run whose summary has `reliable: false` got no answer
for more than 5% of its questions (rate limits, an outage): it has no pass rate and says nothing about
the agent, so report that and run it again. compare_runs shows what got better or worse between two
runs; compare_models runs the set once per LLM; measure_semantic_model shows what the semantic model adds
(the same LLM once with it, once with only the table schemas). Runs act for the whole team and cost LLM calls: ask the
user before running in acceptance or production, before compare_models, and before any tool marked
destructive."""

Environment = Literal["development", "acceptance", "production"]
TeamId = Annotated[str, Field(pattern=r"^team-[a-f0-9]{32}$", description="Team id from whoami.")]
SetId = Annotated[str, Field(pattern=r"^qs-[a-f0-9]{24}$", description="Question set id from list_question_sets.")]
RunId = Annotated[str, Field(pattern=r"^run-[a-f0-9]{24}$", description="Run id.")]
LlmModel = Annotated[str, Field(max_length=200, description="An LLM from list_agent_models' llmModels; empty for "
                                                           "the agent's default.")]


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
        """Your teams with your role in each, where Evaluations is enabled, and whether an LLM judge is set up."""
        return await call(services.whoami)

    @mcp.tool(annotations=READ)
    async def list_environments(team: TeamId) -> dict[str, Any]:
        """Where Evaluations is enabled for a team."""
        return {"environments": await call(services.environments, team)}

    @mcp.tool(annotations=WRITE)
    async def enable_environment(team: TeamId, environment: Environment) -> dict[str, Any]:
        """Let Evaluations ask the agent for a team environment (team administrators). Conversational BI must be
        enabled there too."""
        return await call(services.enable_environment, team, environment)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def disable_environment(team: TeamId, environment: Environment) -> dict[str, Any]:
        """Revoke Evaluations for a team environment; its runs and schedules stop working."""
        return await call(services.disable_environment, team, environment)

    @mcp.tool(annotations=READ)
    async def list_agent_models(team: TeamId, environment: Environment) -> dict[str, Any]:
        """The semantic models the agent answers from for a team environment, and the LLMs a run may choose."""
        return await call(services.agent_models, team, environment)

    @mcp.tool(annotations=READ)
    async def list_question_sets() -> dict[str, Any]:
        """Question sets of your teams, with your role and each set's latest run."""
        return await call(services.list_question_sets)

    @mcp.tool(annotations=READ)
    async def get_question_set(id: SetId, version: Annotated[int | None, Field(ge=1)] = None) -> dict[str, Any]:
        """A question set: its cases as YAML, versions, schedules and latest runs."""
        return await call(services.get_question_set, id, version)

    @mcp.tool(annotations=WRITE)
    async def save_question_set(
            id: Annotated[str | None, Field(pattern=r"^qs-[a-f0-9]{24}$",
                                            description="Leave out to create a question set.")] = None,
            team: Annotated[str, Field(pattern=r"^(team-[a-f0-9]{32})?$")] = "",
            environment: Environment = "development",
            name: Annotated[str | None, Field(pattern=r"^[a-z][a-z0-9_-]{2,47}$")] = None,
            description: Annotated[str | None, Field(max_length=500)] = None,
            model: Annotated[ModelRef | None, Field(description="The semantic model (from list_agent_models).")] = None,
            yaml: Annotated[str | None, Field(max_length=500_000, description="The cases as YAML (see the "
                                                                              "instructions); a change is a new "
                                                                              "version.")] = None,
            rubric: Annotated[str | None, Field(max_length=2000)] = None,
            target_database: Annotated[str | None, Field(pattern=r"^(db-[a-f0-9]{32})?$", description=(
                "The team database results go to as Iceberg tables; empty for none."))] = None,
            note: Annotated[str | None, Field(max_length=200)] = None) -> dict[str, Any]:
        """Create a question set (team, environment, name, model, yaml) or change one (id plus what changes)."""
        data = QuestionSetInput(id=id, team=team, environment=environment, name=name, description=description,
                                model=model, yaml=yaml, rubric=rubric, target_database=target_database, note=note)
        return await call(services.save_question_set, data)

    @mcp.tool(annotations=WRITE)
    async def import_questions(id: SetId, questions_sql: Annotated[str, Field(max_length=500_000)]) -> dict[str, Any]:
        """Add the questions of a semantic model's <name>.questions.sql to a question set; same names are replaced."""
        return await call(services.import_questions, id, ImportInput(questions_sql=questions_sql).questions_sql)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_question_set(id: SetId) -> dict[str, Any]:
        """Delete a question set with its runs and schedules; result tables in Iceberg stay."""
        return await call(services.delete_question_set, id)

    @mcp.tool(annotations=WRITE)
    async def run_evaluation(id: SetId, llm_model: LlmModel = "") -> dict[str, Any]:
        """Ask the agent every question of the set and score the answers. Returns the queued run; poll get_run."""
        return await call(services.run_evaluation, id, llm_model)

    @mcp.tool(annotations=WRITE)
    async def compare_models(id: SetId, llm_models: Annotated[list[str], Field(min_length=2, max_length=5)]
                             ) -> dict[str, Any]:
        """One run per LLM; compare them with compare_runs once they are done. Each run costs LLM calls."""
        return await call(services.compare_models, id, llm_models)

    @mcp.tool(annotations=WRITE)
    async def measure_semantic_model(id: SetId, llm_model: LlmModel = "") -> dict[str, Any]:
        """What the semantic model adds: one run with it and one where the same LLM has only the table schemas and
        writes SQL. Costs two runs of LLM calls. When both are done, compare_runs(control, semantic) has `uplift`."""
        return await call(services.measure_semantic_model, id, llm_model)

    @mcp.tool(annotations=READ)
    async def list_runs(id: SetId) -> dict[str, Any]:
        """Runs of a question set, newest first, with their pass rates."""
        return await call(services.list_runs, id)

    @mcp.tool(annotations=READ)
    async def get_run(run: RunId) -> dict[str, Any]:
        """A run with every case: the agent's answer, its queries, each score and why."""
        return await call(services.get_run, run)

    @mcp.tool(annotations=READ)
    async def compare_runs(baseline: RunId, candidate: RunId) -> dict[str, Any]:
        """Two runs of one question set, case by case: fixed, broken or the same."""
        return await call(services.compare_runs, baseline, candidate)

    @mcp.tool(annotations=WRITE)
    async def set_schedule(id: SetId, cron: Annotated[str, Field(max_length=100, description=(
            "Five-field cron in UTC, such as '0 6 * * 1'."))], llm_model: LlmModel = "", enabled: bool = True,
            schedule_id: Annotated[str | None, Field(pattern=r"^sch-[a-f0-9]{24}$")] = None) -> dict[str, Any]:
        """Run a question set on a schedule, or change a schedule (team administrators)."""
        return await call(services.set_schedule, id, cron, llm_model, enabled, schedule_id)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_schedule(id: SetId, schedule_id: Annotated[str, Field(pattern=r"^sch-[a-f0-9]{24}$")]
                              ) -> dict[str, Any]:
        """Delete a schedule (team administrators)."""
        return await call(services.delete_schedule, id, schedule_id)

    @mcp.tool(annotations=READ)
    async def get_live(team: TeamId, environment: Environment) -> dict[str, Any]:
        """Live evaluation of a team environment: settings, last check and the number of open reviews."""
        return await call(services.get_live, team, environment)

    @mcp.tool(annotations=WRITE)
    async def set_live(team: TeamId, environment: Environment, enabled: bool,
                       sample_pct: Annotated[int, Field(ge=1, le=100)] = 20,
                       cron: Annotated[str, Field(max_length=100)] = "0 * * * *",
                       target_database: Annotated[str, Field(pattern=r"^(db-[a-f0-9]{32})?$")] = ""
                       ) -> dict[str, Any]:
        """Score a sample of the team's real Conversational BI conversations on a schedule (team administrators).
        Conversational BI keeps the team's conversations while Evaluations is enabled there: ask the user first."""
        return await call(services.set_live, team, environment, enabled, sample_pct, cron, target_database)

    @mcp.tool(annotations=READ)
    async def list_reviews(team: TeamId, environment: Environment,
                           status: Literal["open", "dismissed", "promoted"] | None = None) -> dict[str, Any]:
        """Live conversations that failed a check: question, answer, the agent's queries and why."""
        return await call(services.list_reviews, team, environment, status)

    @mcp.tool(annotations=WRITE)
    async def review_conversation(review: Annotated[str, Field(pattern=r"^rev-[a-f0-9]{24}$")],
                                  action: Literal["dismiss", "reopen"],
                                  note: Annotated[str, Field(max_length=500)] = "") -> dict[str, Any]:
        """Dismiss a review (the answer was fine) or reopen it."""
        return await call(services.review_conversation, review, action, note)

    @mcp.tool(annotations=WRITE)
    async def promote_to_case(review: Annotated[str, Field(pattern=r"^rev-[a-f0-9]{24}$")], question_set: SetId,
                              name: Annotated[str | None, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,59}$")] = None,
                              expect: Annotated[dict | None, Field(description=(
                                  "query, refusal, clarify or decline; by default the query the agent ran: check "
                                  "with the user that it is right."))] = None) -> dict[str, Any]:
        """Turn a reviewed live conversation into a regression case of a question set."""
        return await call(services.promote_to_case, review, question_set, name, expect)

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
