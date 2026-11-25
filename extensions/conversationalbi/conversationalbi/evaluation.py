"""Questions from another extension for a team, never for a person: the Evaluations extension's way in.

The caller is the service account of an extension listed in BI_AGENT_CALLERS (by default
`evaluations`). It names a team and an environment, and Conversational BI answers only when the
Bridge says that team enabled both extensions there (capability `team-extensions`): a team
administrator enabling both is the consent. Conversational BI then reads what its own automation
principal for that team and environment reads, the team's databases there and the shares the team
received: the scope every member of the team has. What people asked in the chat is not reachable
this way.

- POST /api/v1/evaluation/models: the semantic models the team can ask about there, and the LLMs
  an evaluation may choose.
- POST /api/v1/evaluation/ask: the chat agent answers one question, in text, and reports its tool
  calls, the governed queries it ran, token usage and the trace id.
- POST /api/v1/evaluation/ask-schema-only: the control arm, what the same LLM does with only the table
  schemas (agent/control.py); only with BI_EVAL_SCHEMA_ONLY set.
- POST /api/v1/evaluation/query: a governed query, the ground truth an answer is scored against.
- POST /api/v1/evaluation/recording: start or stop keeping the team's chat runs there. Evaluations
  calls it when a team administrator switches live evaluation on or off; stopping deletes them.
- POST /api/v1/evaluation/conversations: the team's recorded chat runs since a time, for live
  evaluation (conversations.py); recorded only while live evaluation is on.

In every request and response `model` is a semantic model, by its reference {database, namespace,
name}, and `llmModel` names an LLM. `toolCalls[].args` and `queries[].spec` are the chat agent's
own tool arguments, as the agent sent them.
"""

import asyncio
import dataclasses
import time
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, Request
from pydantic import AfterValidator, BaseModel, Field
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_core import to_jsonable_python

from .agent import control
from .agent.deps import CbiDeps, CbiState
from .agent.models import resolve_model
from .api import ERRORS, SemanticModelRef, Timestamp
from .errors import CbiError
from .semantic.compiler import ModelRef, QuerySpec

CAPABILITY = "team-extensions"
# The REST operations for other extensions; not MCP tools (api.OPERATIONS).
OPERATIONS = {"evaluationModels", "evaluationAsk", "evaluationAskSchemaOnly", "evaluationQuery",
              "evaluationRecording", "evaluationConversations"}
TEXT_ONLY = ("This question comes from an automated evaluation, not from the chat window: charts, tables and KPI "
             "cards are not available. Answer in text, from the query results.")

Team = Annotated[str, Field(pattern=r"^team-[a-f0-9]{32}$")]
Environment = Literal["development", "acceptance", "production"]


EPOCH = datetime.fromtimestamp(0, UTC)


def instant(value):
    """An RFC 3339 time in whole milliseconds since 1970, the precision of every time this API returns."""
    try:
        found = datetime.fromisoformat(value)
    except ValueError:
        found = None
    if found is None or found.tzinfo is None:
        raise ValueError("expected an RFC 3339 time with its offset, such as 2026-11-24T09:15:02.123Z")
    return (found - EPOCH) // timedelta(milliseconds=1)


def rfc3339(value):
    instant(value)
    return value


LlmModel = Annotated[str | None, Field(max_length=200, description="The LLM that answers: LLM_MODEL when left out, "
                                                                   "otherwise one of llmModels in /models.")]


class Input(BaseModel):
    """What every operation needs: the team and environment to answer for. Unknown fields are refused."""
    model_config = {"extra": "forbid"}

    team: Team
    environment: Environment


class Scope(Input):
    pass


class Ask(Input):
    question: str = Field(min_length=1, max_length=2000)
    llmModel: LlmModel = None


class SchemaOnlyAsk(Input):
    model: ModelRef
    question: str = Field(min_length=1, max_length=2000)
    llmModel: LlmModel = None


class Recording(Input):
    enabled: bool


class Since(Input):
    since: Annotated[str, Field(max_length=40), AfterValidator(rfc3339)] | None = Field(
        default=None, description="RFC 3339, the `at` of the last conversation you read: only later ones are "
                                  "returned, oldest first. Leave it out to start with the oldest.")
    limit: int = Field(default=100, ge=1, le=500)


class GroundTruth(Input):
    model: ModelRef
    spec: QuerySpec


# Responses.

class EvaluationSemanticModel(BaseModel):
    model: SemanticModelRef
    name: str
    databaseName: str
    environment: Environment
    shared: bool


class EvaluationModels(BaseModel):
    semanticModels: list[EvaluationSemanticModel]
    llmModels: list[str] = Field(description="LLM_MODEL first, then the others an evaluation may choose.")
    schemaOnly: bool = Field(description="Whether the control arm (ask-schema-only) is switched on.")


class ToolCall(BaseModel):
    name: str = Field(description="The chat agent's own tool, such as list_models or query.")
    args: dict[str, Any]
    result: Any = Field(description="What the agent got back; null when the call had no return.")


class Usage(BaseModel):
    inputTokens: int
    outputTokens: int
    requests: int


class EvaluationAnswer(BaseModel):
    answer: str
    llmModel: str = Field(description="The LLM that answered.")
    toolCalls: list[ToolCall]
    queries: list[dict[str, Any]] = Field(description="The governed queries the agent ran: spec with resultId, "
                                                      "columns, rowCount, rowsShown, rows and joinPaths, or with "
                                                      "error and code when it was refused.")
    usage: Usage
    durationMs: int
    traceId: str | None


class EvaluationQueryResult(BaseModel):
    modelName: str
    query: dict[str, Any]
    columns: list[dict[str, Any]]
    rows: list[list[Any]]
    rowCount: int
    truncated: bool
    joinPaths: dict[str, list[str]]
    sql: str
    dataAsOf: Timestamp | None = Field(description="When the metrics' dataset was last committed.")


class EvaluationRecording(BaseModel):
    recording: bool
    retentionDays: int


class EvaluationConversation(BaseModel):
    id: str
    at: Timestamp
    team: str
    environment: Environment
    model: SemanticModelRef
    question: str
    answer: str
    toolCalls: list[ToolCall]
    queries: list[dict[str, Any]]
    llmModel: str = Field(description="The LLM that answered.")
    inputTokens: int
    outputTokens: int
    traceId: str | None


class EvaluationConversations(BaseModel):
    conversations: list[EvaluationConversation]
    recording: bool = Field(description="Whether the team's chat runs are being kept there now.")
    retentionDays: int


def trace_id():
    try:
        from opentelemetry import trace
    except ImportError:
        return None
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None


def transcript(messages):
    """Every tool call with its arguments and what the agent got back."""
    returns = {p.tool_call_id: p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)}
    calls = []
    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolCallPart):
                found = returns.get(part.tool_call_id)
                calls.append({"name": part.tool_name, "args": part.args_as_dict(),
                              "result": to_jsonable_python(found.content) if found else None})
    return calls


def queries(calls):
    """The governed queries the agent ran, refused ones included: what scoring needs."""
    found = []
    for call in calls:
        if call["name"] not in ("query", "run_sql") or not isinstance(call["result"], dict):
            continue
        result = call["result"]
        found.append({"spec": call["args"], **({"error": result["error"], "code": result.get("code")}
                                               if "error" in result else
                                               {k: result.get(k) for k in ("resultId", "columns", "rowCount",
                                                                           "rowsShown", "rows", "joinPaths")})})
    return found


class Evaluation:
    def __init__(self, settings, services, verifier, agent, llm=None, log=None):
        self.log = log
        self.settings = settings
        self.services = services
        self.verifier = verifier
        self.agent = agent
        self.llm = llm
        self.models = {}
        self.slots = asyncio.Semaphore(settings.eval_concurrency)
        self.control = control.create_control_agent()

    async def caller(self, request: Request):
        header = request.headers.get("authorization", "")
        if header[:7].lower() != "bearer ":
            raise CbiError(401, "Send the calling extension's service-account token.", "unauthorized")
        return await self.verifier.verify_extension(header[7:].strip())

    async def scoped(self, caller, team, environment):
        """The caller for this team and environment, or why Conversational BI won't answer for it."""
        bridge = self.services.bridge
        if CAPABILITY not in await bridge.capabilities():
            raise CbiError(503, "The platform is too old for evaluations: its Extension Bridge has no "
                                "team-extensions capability.", "unsupported_platform")
        principal = await bridge.principal_for(team, environment)
        if caller.extension not in await bridge.team_extensions(team, environment):
            raise CbiError(403, f"The team has not enabled {caller.extension} in {environment}. A team administrator "
                                "enables it there.", "extension_not_enabled")
        scope = await bridge.scope(principal["id"])
        context = {
            "user": {"id": f"extension:{caller.extension}", "name": caller.extension},
            "memberships": [{"team": team, "teamName": team, "role": "reader"}],
            "databases": [d for d in scope.get("databases", []) if d.get("status", "ready") == "ready"],
            "sharedDatabases": [{**d, "recipientTeams": [team], "sharedWithTeam": team,
                                 "ownerTeamName": d.get("ownerTeamName", "")}
                                for d in scope.get("sharedDatabases", [])],
        }
        return dataclasses.replace(caller, team=team, environment=environment, context=context)

    def llm_model(self, name):
        """None for the agent's own LLM_MODEL; another LLM only from BI_EVAL_MODELS."""
        if not name or name == self.settings.model:
            if self.llm is not None and self.llm.status != "ready":
                raise CbiError(503, f"The chat has no working LLM: {self.llm.problem}", "model_unavailable")
            return None
        if name not in self.settings.eval_models:
            raise CbiError(422, "This LLM is not in BI_EVAL_MODELS.", "unknown_llm_model")
        if name not in self.models:
            choice = resolve_model(dataclasses.replace(self.settings, model=name, model_configured=True))
            if choice.status != "ready":
                raise CbiError(503, f"{name}: {choice.problem}", "model_unavailable")
            self.models[name] = choice.model
        return self.models[name]

    async def list_models(self, caller, data: Scope):
        scoped = await self.scoped(caller, data.team, data.environment)
        found = await self.services.list_models(scoped)
        return {"semanticModels": [{k: m[k] for k in ("model", "name", "databaseName", "environment", "shared")}
                                   for m in found["models"]],
                "llmModels": [self.settings.model, *self.settings.eval_models],
                "schemaOnly": self.settings.eval_schema_only}

    async def ask(self, caller, data: Ask):
        scoped = await self.scoped(caller, data.team, data.environment)
        model = self.llm_model(data.llmModel)
        deps = CbiDeps(state=CbiState(), caller=scoped, services=self.services, me=scoped.context)
        started = time.monotonic()
        async with self.slots:
            result = await self.agent.run(data.question, deps=deps, model=model, instructions=TEXT_ONLY)
        return self.report(result, started, data.llmModel)

    def report(self, result, started, asked):
        messages = result.all_messages()
        calls = transcript(messages)
        usage = result.usage
        responses = [m for m in messages if isinstance(m, ModelResponse)]
        return {
            "answer": result.output if isinstance(result.output, str) else str(result.output),
            "llmModel": (responses[-1].model_name if responses else None) or asked or self.settings.model,
            "toolCalls": calls, "queries": queries(calls),
            "usage": {"inputTokens": usage.input_tokens, "outputTokens": usage.output_tokens,
                      "requests": usage.requests},
            "durationMs": round((time.monotonic() - started) * 1000), "traceId": trace_id(),
        }

    async def ask_schema_only(self, caller, data: SchemaOnlyAsk):
        if not self.settings.eval_schema_only:
            raise CbiError(409, "The control arm is off: a platform administrator sets BI_EVAL_SCHEMA_ONLY=true in "
                                "Conversational BI, because there an LLM writes SQL.", "schema_only_disabled")
        scoped = await self.scoped(caller, data.team, data.environment)
        llm = self.llm_model(data.llmModel)
        access, model, schemas, unreadable = await self.services.load(scoped, data.model, scoped.context)
        if unreadable:
            raise CbiError(403, "A table of this model is missing or not readable.", "table_not_readable",
                           {"tables": unreadable})
        found = control.tables(model, schemas)
        catalog, discovery = self.services.catalog, await self.services.bridge.discovery()
        job = {"uri": await catalog.uri(), "warehouse": data.model.database,
               "token": await catalog.token(access["principal"]),
               "s3Endpoint": discovery["storage"]["internalEndpoint"],
               "bindings": [{k: t[k] for k in ("view", "namespace", "table")} for t in found]}
        deps = control.ControlDeps(services=self.services, job=job, views={t["view"] for t in found})
        started = time.monotonic()
        async with self.slots:
            result = await self.control.run(data.question, deps=deps, model=llm or self.agent.model,
                                            instructions=control.prompt(found))
        return self.report(result, started, data.llmModel)

    async def recording(self, caller, data: Recording):
        await self.scoped(caller, data.team, data.environment)
        if not (self.log and self.log.enabled):
            if data.enabled:
                raise CbiError(409, "Conversational BI keeps no conversations here: BI_CONVERSATION_RETENTION_DAYS "
                                    "is 0 or its state directory is not writable.", "recording_unavailable")
        else:
            self.log.set_recording(data.team, data.environment, data.enabled)
        return {"recording": data.enabled, "retentionDays": self.settings.conversation_retention_days}

    async def conversations(self, caller, data: Since):
        await self.scoped(caller, data.team, data.environment)
        after = None if data.since is None else instant(data.since)
        found = self.log.since(data.team, data.environment, after, data.limit) if self.log else []
        return {"conversations": found,
                "recording": bool(self.log and self.log.recording(data.team, data.environment)),
                "retentionDays": self.settings.conversation_retention_days}

    async def query(self, caller, data: GroundTruth):
        scoped = await self.scoped(caller, data.team, data.environment)
        result = await self.services.run_query(scoped, data.model, data.spec, me=scoped.context)
        return {k: result[k] for k in ("modelName", "query", "columns", "rows", "rowCount", "truncated",
                                       "joinPaths", "sql", "dataAsOf")}


def router(evaluation):
    api = APIRouter(prefix="/api/v1/evaluation", tags=["evaluation"], responses=ERRORS)
    Caller = Annotated[object, Depends(evaluation.caller)]

    @api.post("/models", operation_id="evaluationModels", response_model=EvaluationModels,
              summary="The semantic models a team can ask about, and the LLMs an evaluation may choose")
    async def models(data: Annotated[Scope, Body()], caller: Caller):
        return await evaluation.list_models(caller, data)

    @api.post("/ask", operation_id="evaluationAsk", response_model=EvaluationAnswer,
              summary="The chat agent answers one question for a team (service accounts in BI_AGENT_CALLERS)")
    async def ask(data: Annotated[Ask, Body()], caller: Caller):
        return await evaluation.ask(caller, data)

    @api.post("/ask-schema-only", operation_id="evaluationAskSchemaOnly", response_model=EvaluationAnswer,
              summary="The control arm: the same LLM with only the table schemas (with BI_EVAL_SCHEMA_ONLY)")
    async def ask_schema_only(data: Annotated[SchemaOnlyAsk, Body()], caller: Caller):
        return await evaluation.ask_schema_only(caller, data)

    @api.post("/query", operation_id="evaluationQuery", response_model=EvaluationQueryResult,
              summary="A governed query for a team: the ground truth to score an answer against")
    async def query(data: Annotated[GroundTruth, Body()], caller: Caller):
        return await evaluation.query(caller, data)

    @api.post("/recording", operation_id="evaluationRecording", response_model=EvaluationRecording,
              summary="Start or stop keeping a team's chat runs for live evaluation; stopping deletes them")
    async def recording(data: Annotated[Recording, Body()], caller: Caller):
        return await evaluation.recording(caller, data)

    @api.post("/conversations", operation_id="evaluationConversations", response_model=EvaluationConversations,
              summary="The team's recorded chat runs, oldest first: only while live evaluation is on")
    async def conversations(data: Annotated[Since, Body()], caller: Caller):
        return await evaluation.conversations(caller, data)

    return api
