"""The LLM behind the agent: `LLM_MODEL`, or a scripted stand-in for tests and CI.

`LLM_MODEL` is any Pydantic AI model string (`openai:`, `anthropic:`, `google:`, `bedrock:`, …) plus
`anthropic-bedrock:<model id>`, Claude through the Anthropic client on Amazon Bedrock. A model that
cannot be built (unknown, no key, SDK missing) never stops the gateway: the chat answers with what is
missing instead, and the REST API and MCP server keep working.

`LLM_MODEL=test:flights` (only with `BI_ALLOW_TEST_MODEL=1`) answers every question the same way,
without an LLM: it selects the flights model, asks for the average departure delay by carrier,
shows it the way the query recommends (a bar chart) and summarizes it. It exercises the whole
path from the chat UI through the runtime and the agent to the query worker and back.
"""

import json
import logging
import re
from dataclasses import dataclass

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models import Model, infer_model
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from ..errors import CbiError

LOG = logging.getLogger(__name__)

QUERY = {"metrics": ["average_departure_delay"], "dimensions": [{"field": "CARRIER.name"}],
         "order_by": [{"name": "average_departure_delay", "desc": True}], "limit": 20}


def content(part):
    value = part.content
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return {"text": value}
    return value if isinstance(value, dict) else {"value": value}


def scripted(messages, info: AgentInfo):
    start = max((i for i, m in enumerate(messages) if isinstance(m, ModelRequest)
                 and any(isinstance(p, UserPromptPart) for p in m.parts)), default=0)
    done = {p.tool_name: content(p) for m in messages[start:] if isinstance(m, ModelRequest)
            for p in m.parts if isinstance(p, ToolReturnPart)}
    selected = "Selected semantic model:" in (info.instructions or "")
    tools = {t.name for t in info.function_tools}

    def call(name, args):
        return ModelResponse(parts=[ToolCallPart(name, args, tool_call_id=f"call-{name}-{len(messages)}")])

    if "query" not in done:
        if not selected and "select_model" not in done:
            if "list_models" not in done:
                return call("list_models", {})
            models = done["list_models"].get("models", [])
            model = next((m["model"] for m in models if m["model"]["name"] == "flights"), None)
            if model is None:
                return ModelResponse(parts=[TextPart("No flights model is available to your teams.")])
            return call("select_model", model)
        return call("query", QUERY)
    result = done["query"]
    if "error" in result:
        return ModelResponse(parts=[TextPart(f"The query failed: {result['error']}")])
    shown = result["recommended"]
    if shown["tool"] in tools and shown["tool"] not in done:
        return call(shown["tool"], {**shown["arguments"], "title": "Average departure delay by carrier"})
    top = result["rows"][0] if result.get("rows") else None
    finding = f"{top[0]} has the highest average departure delay, {top[1]:.1f} minutes. " if top else ""
    return ModelResponse(parts=[TextPart(
        finding + "Metric: average_departure_delay, the mean of FLIGHT.dep_delay in minutes; cancelled flights have "
                  "no delay and are left out. Split by carrier through flight_carrier. All dates in the data set.")])


async def scripted_stream(messages, info):
    response = scripted(messages, info)
    for part in response.parts:
        if isinstance(part, TextPart):
            yield part.content
        else:
            yield {0: DeltaToolCall(name=part.tool_name, json_args=json.dumps(part.args),
                                    tool_call_id=part.tool_call_id)}


def build_model(settings):
    if settings.model.startswith("test:"):
        if not settings.allow_test_model or settings.model != "test:flights":
            raise CbiError(503, "The scripted test model needs LLM_MODEL=test:flights and BI_ALLOW_TEST_MODEL=1.",
                           "misconfigured")
        return FunctionModel(scripted, stream_function=scripted_stream, model_name="test:flights")
    prefix, _, name = settings.model.partition(":")
    if prefix == "anthropic-bedrock":
        # Pydantic AI has no model string for the Anthropic client on Bedrock. It reads
        # AWS_BEARER_TOKEN_BEDROCK or the AWS credential chain, and AWS_REGION.
        from anthropic import AsyncAnthropicBedrockMantle
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider
        return AnthropicModel(name, provider=AnthropicProvider(anthropic_client=AsyncAnthropicBedrockMantle()))
    return infer_model(settings.model)


@dataclass(frozen=True)
class ModelChoice:
    model: Model
    status: str  # ready, not_configured, unknown_model, missing_credentials, missing_package or misconfigured
    problem: str | None = None


def problem_text(exc):
    """The first sentence of the error, without Pydantic AI's hint to try its keyless test model."""
    text = str(exc).split(" To try Pydantic AI", 1)[0].strip()
    first = re.split(r"(?<=\.)\s", text, maxsplit=1)[0][:200]
    return first if first.endswith(".") else first + "."


def unavailable(message):
    async def stream(messages, info):
        yield message

    return FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart(message)]), stream_function=stream,
                         model_name="unavailable")


def resolve_model(settings):
    try:
        model = build_model(settings)
    except Exception as exc:  # noqa: BLE001 - a broken LLM setting must not stop the API and MCP server
        problem = problem_text(exc)
        if isinstance(exc, ImportError):
            status = "missing_package"
        elif isinstance(exc, CbiError):
            status = "misconfigured"
        elif not settings.model_configured:
            status, problem = "not_configured", "LLM_MODEL is not set."
        elif problem.startswith("Unknown model"):
            status = "unknown_model"
        else:
            status = "missing_credentials"
        LOG.error("The chat has no LLM: %s", problem if status == "not_configured" else f"{settings.model}: {problem}")
        message = (f"The chat has no working LLM yet: {problem} The platform administrator sets LLM_MODEL and its "
                   "credentials in this extension's llm.env. The REST API and MCP server work without it.")
        return ModelChoice(unavailable(message), status, problem)
    LOG.info("Chat model: %s", settings.model)
    return ModelChoice(model, "ready")
