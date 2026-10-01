import json
import time

from fastapi.testclient import TestClient

from conversationalbi.auth import COOKIE, Session
from tests.conftest import OWN_DB, SHARED_DB, TEAM

TOOLS = [{"name": "show_chart", "description": "Chart a result", "parameters": {"type": "object", "properties": {}}}]


def signed_in(stack, user="bob"):
    login = stack.app.state.login
    session_id = f"session-{user}"
    login.sessions[session_id] = Session(user, "", time.monotonic() + 600, time.monotonic() + 600)
    return session_id


def events(response):
    return [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]


def run_agent(stack, ticket, state=None, messages=None, key="k" * 32):
    client = TestClient(stack.app.state.agent_app)
    body = {"threadId": "thread-1", "runId": "run-1", "state": state or {}, "tools": TOOLS, "context": [],
            "forwardedProps": {}, "messages": messages or [{"id": "m1", "role": "user",
                                                           "content": "Which carrier is most delayed?"}]}
    return client.post("/", json=body, headers={"x-cbi-runtime-key": key, "x-cbi-run-ticket": ticket,
                                                "accept": "text/event-stream"})


def enable(stack):
    stack.call("POST", f"/teams/{TEAM}/environments/development")


def test_scripted_run_selects_queries_and_charts(stack):
    enable(stack)
    ticket = stack.app.state.tickets.issue(signed_in(stack), "sub-bob")
    # The UI already selected the shared model; the agent queries it and asks the UI for a chart.
    selected = {"model": {"database": SHARED_DB, "namespace": ["ai_flights"], "name": "flights"}}
    found = events(run_agent(stack, ticket, selected))
    kinds = [e["type"] for e in found]
    assert kinds[0] == "RUN_STARTED" and kinds[-1] == "RUN_FINISHED"
    calls = [e["toolCallName"] for e in found if e["type"] == "TOOL_CALL_START"]
    assert calls == ["query", "show_chart"]
    result = json.loads(next(e["content"] for e in found if e["type"] == "TOOL_CALL_RESULT"))
    assert result["rowCount"] == 3 and result["rowsShown"] <= 20 and "LEFT JOIN" in result["sql"]
    snapshot = next(e["snapshot"] for e in found if e["type"] == "STATE_SNAPSHOT")
    assert snapshot["lastResultId"] == result["resultId"] and snapshot["model"]["database"] == SHARED_DB
    # Each run re-reads the person's teams from the Bridge.
    assert ("bob", True) in stack.bridge.me_calls


def test_scripted_run_without_a_selection_lists_and_selects(stack):
    enable(stack)
    ticket = stack.app.state.tickets.issue(signed_in(stack), "sub-bob")
    found = events(run_agent(stack, ticket))
    calls = [e["toolCallName"] for e in found if e["type"] == "TOOL_CALL_START"]
    assert calls == ["list_models", "select_model", "query", "show_chart"]
    snapshot = [e["snapshot"] for e in found if e["type"] == "STATE_SNAPSHOT"][-1]
    assert snapshot["model"]["database"] == OWN_DB


def test_the_llm_sees_a_capped_sample(stack):
    enable(stack)
    stack.services.settings.__dict__["llm_rows"] = 1  # Operator cap below the agent spec's 20.
    ticket = stack.app.state.tickets.issue(signed_in(stack), "sub-bob")
    found = events(run_agent(stack, ticket, {"model": {"database": OWN_DB, "namespace": ["ai_flights"],
                                                       "name": "flights"}}))
    result = json.loads(next(e["content"] for e in found if e["type"] == "TOOL_CALL_RESULT"))
    assert result["rowCount"] == 3 and result["rowsShown"] == 1 and len(result["rows"]) == 1


def test_forged_state_reads_nothing_more(stack):
    enable(stack)
    stack.bridge.roles["bob"] = {"team-" + "c" * 32: "reader"}  # Bob left team A.
    ticket = stack.app.state.tickets.issue(signed_in(stack), "sub-bob")
    found = events(run_agent(stack, ticket, {"model": {"database": OWN_DB, "namespace": ["ai_flights"],
                                                       "name": "flights"}, "lastResultId": "nonsense"}))
    assert not any(e["type"] == "TOOL_CALL_RESULT" and '"rows"' in e["content"] for e in found)
    text = "".join(e.get("delta", "") for e in found if e["type"] == "TEXT_MESSAGE_CONTENT")
    assert "No flights model" in text


def test_only_the_runtime_with_a_valid_ticket_reaches_the_agent(stack):
    ticket = stack.app.state.tickets.issue(signed_in(stack), "sub-bob")
    assert run_agent(stack, ticket, key="wrong").status_code == 401
    assert run_agent(stack, "forged").status_code == 401
    other = stack.app.state.tickets.issue(signed_in(stack, "alice"), "sub-bob")
    assert run_agent(stack, other).status_code == 401


def test_the_gateway_proxies_only_the_chat_routes(stack):
    stack.client.cookies.set(COOKIE, signed_in(stack))
    headers = {"x-iceberg-cbi": "1"}
    body = {"threadId": "t-1", "runId": "r-1", "messages": []}
    ok = stack.client.post("/api/copilotkit/agent/analyst/run", json=body, headers=headers)
    assert ok.status_code == 200 and ok.headers["content-type"].startswith("text/event-stream")
    forwarded = stack.runtime_calls[-1]
    assert forwarded.url.path == "/api/copilotkit/agent/analyst/run"
    ticket = forwarded.headers["x-cbi-run-ticket"]
    assert stack.app.state.tickets.resolve(ticket) == ("session-bob", "sub-bob")
    assert "cookie" not in forwarded.headers
    for path in ("threads", "agent/analyst/suggest", "agent/other/run", "transcribe", "memories"):
        assert stack.client.post(f"/api/copilotkit/{path}", json=body, headers=headers).status_code == 404
    assert stack.client.post("/api/copilotkit/agent/analyst/run", json=body).json()["code"] == "csrf"
    # A thread belongs to whoever used it first.
    stack.client.cookies.set(COOKIE, signed_in(stack, "alice"))
    taken = stack.client.post("/api/copilotkit/agent/analyst/connect", json=body, headers=headers)
    assert taken.status_code == 403
    assert stack.client.post("/api/copilotkit/agent/analyst/stop/t-1", headers=headers).status_code == 403
    stack.client.cookies.clear()
    assert stack.client.get("/api/copilotkit/info").status_code == 401
