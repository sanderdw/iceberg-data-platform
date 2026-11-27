"""The client side of Conversational BI's evaluation API, and the fake of that API the other tests use.

Conversational BI owns the contract (its contracts/conversationalbi-api/v1/openapi.yaml). The last test
holds this extension's requests and the fake's answers against it, so the two cannot drift apart
unnoticed; it is skipped where that file is not checked out next to this extension.
"""

import asyncio
import json

import httpx
import pytest

from evaluations.agent_client import AgentClient
from evaluations.config import ROOT
from evaluations.errors import EvalError
from evaluations.services import seconds, timestamp
from tests.conftest import MODEL, QUERY, TEAM, TIMESTAMP, FakeAgent, FakeBridge, conversation, settings

CONTRACT = ROOT.parent / "conversationalbi" / "contracts" / "conversationalbi-api" / "v1" / "openapi.yaml"
SINCE = "2026-11-24T09:15:02.123Z"
# Operation → the response model its row in the agreement names.
RESPONSES = {"/models": "EvaluationModels", "/ask": "EvaluationAnswer", "/ask-schema-only": "EvaluationAnswer",
             "/query": "EvaluationQueryResult", "/recording": "EvaluationRecording",
             "/conversations": "EvaluationConversations"}


def client(tmp_path, answer=None, status=200):
    """An AgentClient whose requests are kept, each answered with `answer`."""
    sent = []

    def handle(request):
        sent.append((request.url.path, json.loads(request.content), request.headers["authorization"]))
        return httpx.Response(status, json=answer if answer is not None else {})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    return AgentClient(settings(tmp_path), FakeBridge(), http), sent


def requests(tmp_path):
    """Every kind of request this extension sends, by operation."""
    agent, sent = client(tmp_path, {"semanticModels": [], "llmModels": [], "schemaOnly": False, "llmModel": "x"})

    async def all_calls():
        await agent.models(TEAM, "development")
        await agent.ask(TEAM, "development", "Which carrier is late most?")
        await agent.ask(TEAM, "development", "Which carrier is late most?", "openai:gpt-test")
        await agent.ask_control(TEAM, "development", MODEL, "Which carrier is late most?")
        await agent.ask_control(TEAM, "development", MODEL, "Which carrier is late most?", "openai:gpt-test")
        await agent.truth(TEAM, "development", MODEL, QUERY)
        await agent.recording(TEAM, "development", True)
        await agent.conversations(TEAM, "development", SINCE, 200)

    asyncio.run(all_calls())
    return sent


def test_times_are_rfc_3339_with_milliseconds():
    assert timestamp(None) is None
    assert timestamp(0) == "1970-01-01T00:00:00.000Z"
    assert timestamp(1795511702.1234) == "2026-11-24T09:15:02.123Z" and TIMESTAMP.match(timestamp(1795511702.1234))
    # What Conversational BI gives as a conversation's time comes back unchanged as the next `since`.
    assert timestamp(seconds(SINCE)) == SINCE


def test_requests_name_the_llm_as_llm_model(tmp_path):
    sent = requests(tmp_path)
    scope = {"team": TEAM, "environment": "development"}
    assert [path for path, _, _ in sent] == ["/api/v1/evaluation" + p for p in (
        "/models", "/ask", "/ask", "/ask-schema-only", "/ask-schema-only", "/query", "/recording",
        "/conversations")]
    assert all(token == "Bearer svc" for _, _, token in sent)
    bodies = [body for _, body, _ in sent]
    assert bodies[0] == scope
    # Without a choice the agent uses its default; with one, the field is llmModel in both arms.
    assert bodies[1] == {**scope, "question": "Which carrier is late most?"}
    assert bodies[2] == {**bodies[1], "llmModel": "openai:gpt-test"}
    # `model` is always the semantic model.
    assert bodies[3] == {**scope, "model": MODEL, "question": "Which carrier is late most?"}
    assert bodies[4] == {**bodies[3], "llmModel": "openai:gpt-test"} and "llm" not in bodies[4]
    assert bodies[5] == {**scope, "model": MODEL, "spec": QUERY}
    assert bodies[6] == {**scope, "enabled": True}
    assert bodies[7] == {**scope, "since": SINCE, "limit": 200}


@pytest.mark.parametrize(("status", "code", "raised"), [
    (422, "unknown_llm_model", 422), (409, "schema_only_disabled", 409), (403, "not_enabled", 403),
    (500, "internal_error", 502)])
def test_a_refusal_keeps_its_code(tmp_path, status, code, raised):
    agent, _ = client(tmp_path, {"error": "Refused.", "code": code}, status)
    with pytest.raises(EvalError) as refused:
        asyncio.run(agent.ask_control(TEAM, "development", MODEL, "Why?", "unknown:llm"))
    assert (refused.value.status, refused.value.code, str(refused.value)) == (raised, code, "Refused.")
    # A refused expected query is an answer of its own: an expected refusal is a valid ground truth.
    assert asyncio.run(agent.truth(TEAM, "development", MODEL, QUERY)) == {
        "error": "Refused.", "code": code, "status": status}


def test_an_older_conversational_bi_is_named(tmp_path):
    agent, _ = client(tmp_path, {"models": [], "llmModels": [], "schemaOnly": False})
    with pytest.raises(EvalError) as refused:
        asyncio.run(agent.models(TEAM, "development"))
    assert (refused.value.status, refused.value.code) == (503, "agent_unsupported")
    # It would answer with its default LLM whatever a run asks for: refused, not read.
    agent, _ = client(tmp_path, {"answer": "42", "model": "test:flights", "toolCalls": [], "queries": []})
    for ask in (agent.ask(TEAM, "development", "Why?", "openai:gpt-test"),
                agent.ask_control(TEAM, "development", MODEL, "Why?", "openai:gpt-test")):
        with pytest.raises(EvalError) as refused:
            asyncio.run(ask)
        assert (refused.value.status, refused.value.code) == (503, "agent_unsupported")
    agent, _ = client(tmp_path, {"detail": "Not Found"}, 404)
    with pytest.raises(EvalError) as refused:
        asyncio.run(agent.models(TEAM, "development"))
    assert (refused.value.status, refused.value.code) == (503, "agent_unsupported")


def fake_answers():
    """What the fake answers, by operation: the shapes every other test of this extension relies on."""
    agent = FakeAgent()
    agent.recorded = [conversation(1, SINCE, "Which carrier is late most?", "Example Cloudline."),
                      conversation(2, "2026-11-24T09:16:40.500Z", "What about tomorrow?", "About 12.5 minutes.",
                                   queries=[], toolCalls=[])]

    async def all_answers():
        return {
            "/models": [await agent.models(TEAM, "development")],
            "/ask": [await agent.ask(TEAM, "development", question, llm_model)
                     for question in ("Which carrier is late most?", "Delay by runway length?", "Per airport?")
                     for llm_model in (None, "openai:gpt-test")],
            "/ask-schema-only": [await agent.ask_control(TEAM, "development", MODEL, question, llm_model)
                                 for question in ("Which carrier is late most?", "Per airport?")
                                 for llm_model in (None, "openai:gpt-test")],
            "/query": [await agent.truth(TEAM, "development", MODEL, QUERY)],
            "/recording": [await agent.recording(TEAM, "development", True)],
            "/conversations": [await agent.conversations(TEAM, "development", "1970-01-01T00:00:00.000Z"),
                               await agent.conversations(TEAM, "development", SINCE, 1)],
        }

    return asyncio.run(all_answers())


def test_the_fake_answers_in_the_agreed_shapes():
    """The agreement itself, for a checkout without Conversational BI's contract."""
    answers = fake_answers()
    assert set(answers["/models"][0]) == {"semanticModels", "llmModels", "schemaOnly"}
    assert set(answers["/models"][0]["semanticModels"][0]) == {"model", "name", "databaseName", "environment",
                                                               "shared"}
    for answer in answers["/ask"] + answers["/ask-schema-only"]:
        assert set(answer) == {"answer", "llmModel", "toolCalls", "queries", "usage", "durationMs", "traceId"}
        assert set(answer["usage"]) == {"inputTokens", "outputTokens", "requests"}
    assert {a["llmModel"] for a in answers["/ask"]} == {"test:flights", "openai:gpt-test"}
    assert set(answers["/query"][0]) == {"modelName", "query", "columns", "rows", "rowCount", "truncated",
                                         "joinPaths", "sql", "dataAsOf"}
    assert answers["/recording"][0] == {"recording": True, "retentionDays": 30}
    first, second = answers["/conversations"]
    assert set(first) == {"conversations", "recording", "retentionDays"} and len(first["conversations"]) == 2
    # Only the ones after `since`, and never who asked.
    assert [c["id"] for c in second["conversations"]] == ["conv-" + "2" * 24]
    for item in first["conversations"]:
        assert set(item) == {"id", "at", "team", "environment", "model", "question", "answer", "toolCalls",
                             "queries", "llmModel", "inputTokens", "outputTokens", "traceId"}
        assert TIMESTAMP.match(item["at"]) and item["model"] == MODEL


def resolved(contract, schema):
    while "$ref" in schema:
        schema = contract["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
    return schema


def undeclared(contract, schema, value, where="$"):
    """The keys in `value` that the contract's schema does not declare: a renamed field must not linger."""
    schema = resolved(contract, schema)
    options = [resolved(contract, s) for s in schema.get("anyOf", []) if s.get("type") != "null"]
    if len(options) == 1:
        schema = options[0]
    if isinstance(value, dict) and "properties" in schema:
        strict = schema.get("additionalProperties") in (None, False)
        found = [f"{where}.{key}" for key in value if key not in schema["properties"]] if strict else []
        return found + [problem for key, item in value.items() if key in schema["properties"]
                        for problem in undeclared(contract, schema["properties"][key], item, f"{where}.{key}")]
    if isinstance(value, list) and "items" in schema:
        return [problem for i, item in enumerate(value)
                for problem in undeclared(contract, schema["items"], item, f"{where}[{i}]")]
    return []


def test_requests_and_the_fake_match_conversational_bis_contract(tmp_path):
    if not CONTRACT.is_file():
        pytest.skip("Conversational BI's contract is not checked out next to this extension.")
    Draft202012Validator = pytest.importorskip("jsonschema").Draft202012Validator

    contract = json.loads(CONTRACT.read_text())

    def operation(path):
        return contract["paths"]["/api/v1/evaluation" + path]["post"]

    def problems(schema, value):
        checker = Draft202012Validator({**schema, "components": contract["components"]})
        return [f"{'/'.join(str(p) for p in e.absolute_path) or '$'}: {e.message}" for e in checker.iter_errors(value)]

    wrong = []
    for path, body, _ in requests(tmp_path):
        path = path.removeprefix("/api/v1/evaluation")
        schema = operation(path)["requestBody"]["content"]["application/json"]["schema"]
        wrong += [f"request {path} {p}" for p in problems(schema, body) + undeclared(contract, schema, body)]
    for path, answers in fake_answers().items():
        schema = operation(path)["responses"]["200"]["content"]["application/json"]["schema"]
        if schema.get("$ref", "").rsplit("/", 1)[-1] != RESPONSES[path]:
            wrong.append(f"response {path}: the contract names no {RESPONSES[path]} model")
            continue
        for answer in answers:
            wrong += [f"response {path} {p}" for p in problems(schema, answer) + undeclared(contract, schema, answer)]
    # The parts of the agreement that a schema check alone would let through.
    schemas = contract["components"]["schemas"]
    conversations = resolved(contract, schemas.get("EvaluationConversations", {}).get("properties", {}).get(
        "conversations", {}).get("items", {}))
    if "user" in conversations.get("properties", {"user": None}):
        wrong.append("response /conversations: a conversation must not hold the person who asked (`user`)")
    since = resolved(contract, operation("/conversations")["requestBody"]["content"]["application/json"]["schema"])
    if "string" not in json.dumps(since["properties"]["since"]):
        wrong.append("request /conversations: `since` is an RFC 3339 string")
    assert not wrong, ("This extension and Conversational BI's contract disagree. If the contract is the older "
                       "one, regenerate it in extensions/conversationalbi:\n" + "\n".join(sorted(set(wrong))))
