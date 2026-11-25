"""Another extension asks for a team: only where the team enabled both, with the team's scope."""

import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import pytest

from conversationalbi.conversations import ConversationLog
from conversationalbi.evaluation import instant
from conversationalbi.services import timestamp
from tests.conftest import OTHER, OWN_DB, PROD_DB, SHARED_DB, TEAM
from tests.test_api import TIME

QUERY = {"metrics": ["average_departure_delay"], "dimensions": [{"field": "CARRIER.name"}],
         "order_by": [{"name": "average_departure_delay", "desc": True}]}
FLIGHTS = {"database": OWN_DB, "namespace": ["ai_flights"], "name": "flights"}


def call(stack, path, body, token="svc-evaluations"):
    return stack.client.post("/api/v1/evaluation" + path, json=body, headers={"Authorization": f"Bearer {token}"})


def both(stack, environment="development"):
    stack.call("POST", f"/teams/{TEAM}/environments/{environment}", "alice")
    stack.bridge.team_extensions_[(TEAM, environment)] = {"evaluations"}


def test_only_listed_service_accounts_and_never_people(stack):
    both(stack)
    body = {"team": TEAM, "environment": "development", "question": "Which carrier is late most often?"}
    for token, status, code in (("alice", 403, "forbidden_caller"), ("svc-dbt", 403, "forbidden_caller"),
                                ("forged", 401, "unauthorized")):
        response = call(stack, "/ask", body, token)
        assert (response.status_code, response.json()["code"]) == (status, code)
    assert stack.client.post("/api/v1/evaluation/ask", json=body).json()["code"] == "unauthorized"
    # The service token is good for nothing else.
    assert stack.client.get("/api/v1/semantic-models",
                            headers={"Authorization": "Bearer svc-evaluations"}).status_code == 401


def test_the_team_must_enable_both_extensions(stack):
    body = {"team": TEAM, "environment": "development", "question": "Which carrier is late most often?"}
    assert call(stack, "/ask", body).json()["code"] == "environment_not_enabled"
    stack.call("POST", f"/teams/{TEAM}/environments/development", "alice")
    assert call(stack, "/ask", body).json()["code"] == "extension_not_enabled"
    stack.bridge.team_extensions_[(TEAM, "production")] = {"evaluations"}
    assert call(stack, "/ask", body).json()["code"] == "extension_not_enabled"
    assert call(stack, "/ask", {**body, "team": OTHER}).json()["code"] == "environment_not_enabled"


def test_an_old_platform_is_named(stack):
    both(stack)
    stack.bridge.capabilities_.remove("team-extensions")
    response = call(stack, "/ask", {"team": TEAM, "environment": "development", "question": "Late?"})
    assert (response.status_code, response.json()["code"]) == (503, "unsupported_platform")


def test_the_agent_answers_with_its_tool_calls_and_usage(stack):
    both(stack)
    body = call(stack, "/ask", {"team": TEAM, "environment": "development",
                                "question": "Which carrier is late most often?"}).json()
    assert set(body) == {"answer", "llmModel", "toolCalls", "queries", "usage", "durationMs", "traceId"}
    # The chat agent's own tools keep their names; each call has its arguments and what came back.
    assert [c["name"] for c in body["toolCalls"]] == ["list_models", "select_model", "query"]
    assert all(set(c) == {"name", "args", "result"} for c in body["toolCalls"])
    # Text only: no chart tool in an evaluation.
    [query] = body["queries"]
    assert query["spec"]["metrics"] == ["average_departure_delay"]
    assert query["rowCount"] == 3 and query["rows"][0][0] == "Example Cloudline"
    assert "Example Cloudline has the highest average departure delay" in body["answer"]
    assert body["llmModel"] == "test:flights" and "model" not in body
    assert set(body["usage"]) == {"inputTokens", "outputTokens", "requests"} and body["usage"]["requests"] == 4
    assert isinstance(body["durationMs"], int) and body["traceId"] is None
    # The team's own model, read with Conversational BI's principal for the team; the share is in development too.
    models = body["toolCalls"][0]["result"]["models"]
    assert {(m["model"]["database"], m["shared"]) for m in models} == {(OWN_DB, False), (SHARED_DB, True)}
    assert all(id.startswith("svc-") and access == "read" for id, access, _ in stack.bridge.tokens)


def test_production_sees_production_only(stack):
    both(stack, "production")
    body = call(stack, "/ask", {"team": TEAM, "environment": "production", "question": "Late?"}).json()
    assert body["toolCalls"][0]["result"]["models"] == []
    assert body["answer"] == "No flights model is available to your teams."
    refused = call(stack, "/query", {"team": TEAM, "environment": "production", "model": FLIGHTS, "spec": QUERY})
    assert refused.json()["code"] == "unknown_model"
    assert call(stack, "/query", {"team": TEAM, "environment": "production",
                                  "model": {**FLIGHTS, "database": PROD_DB}, "spec": QUERY}).status_code != 200


def test_models_of_the_team_and_the_llms_to_choose(stack):
    both(stack)
    body = call(stack, "/models", {"team": TEAM, "environment": "development"}).json()
    assert set(body) == {"semanticModels", "llmModels", "schemaOnly"}
    assert body["semanticModels"] == [
        {"model": FLIGHTS, "name": "flights", "databaseName": "sales", "environment": "development", "shared": False},
        {"model": {**FLIGHTS, "database": SHARED_DB}, "name": "flights", "databaseName": "flights",
         "environment": "development", "shared": True}]
    assert body["llmModels"] == ["test:flights"] and body["schemaOnly"] is False


def test_ground_truth_and_its_refusals(stack):
    both(stack)
    body = call(stack, "/query", {"team": TEAM, "environment": "development", "model": FLIGHTS, "spec": QUERY}).json()
    assert set(body) == {"modelName", "query", "columns", "rows", "rowCount", "truncated", "joinPaths", "sql",
                         "dataAsOf"}
    assert body["rowCount"] == 3 and body["rows"][0][0] == "Example Cloudline"
    assert body["joinPaths"] == {"CARRIER.name": ["flight_carrier"]}
    assert body["dataAsOf"] == "2026-09-01T08:00:00.000Z" and body["truncated"] is False
    # The query spec is a document format: order_by stays as it is written in a question set.
    assert body["query"]["order_by"] == QUERY["order_by"] and "LEFT JOIN" in body["sql"]
    refused = call(stack, "/query", {"team": TEAM, "environment": "development", "model": FLIGHTS,
                                     "spec": {"metrics": ["average_departure_delay"],
                                              "dimensions": [{"field": "AIRPORT.name"}]}})
    assert (refused.status_code, refused.json()["code"]) == (422, "ambiguous_join")


def test_other_llms_only_from_the_allowlist(stack):
    both(stack)
    ask = {"team": TEAM, "environment": "development", "question": "Late?"}
    response = call(stack, "/ask", {**ask, "llmModel": "openai:gpt-unlisted"})
    assert (response.status_code, response.json()["code"]) == (422, "unknown_llm_model")
    assert call(stack, "/ask", {**ask, "llmModel": "test:flights"}).json()["llmModel"] == "test:flights"
    # `model` is a semantic model everywhere; an LLM named that way is refused, never ignored.
    for old in ({"model": "openai:gpt-unlisted"}, {"llm": "openai:gpt-unlisted"}):
        response = call(stack, "/ask", {**ask, **old})
        assert (response.status_code, response.json()["code"]) == (422, "invalid_request")


def chat(stack, question="Which carrier is most delayed?", user="bob"):
    from fastapi.testclient import TestClient

    from tests.test_agent import signed_in

    ticket = stack.app.state.tickets.issue(signed_in(stack, user), "sub-" + user)
    body = {"threadId": "thread-1", "runId": "run-1", "state": {"model": FLIGHTS}, "tools": [], "context": [],
            "forwardedProps": {}, "messages": [{"id": "m1", "role": "user", "content": question}]}
    TestClient(stack.app.state.agent_app).post("/", json=body, headers={
        "x-cbi-runtime-key": "k" * 32, "x-cbi-run-ticket": ticket, "accept": "text/event-stream"})


def test_conversations_are_kept_only_while_live_evaluation_is_on(stack):
    stack.call("POST", f"/teams/{TEAM}/environments/development", "alice")
    since = {"team": TEAM, "environment": "development"}
    stack.bridge.team_extensions_[(TEAM, "development")] = {"evaluations"}
    # Enabling Evaluations alone keeps nothing.
    chat(stack)
    body = call(stack, "/conversations", since).json()
    assert body["conversations"] == [] and not body["recording"]
    assert call(stack, "/recording", {**since, "enabled": True}).json() == {"recording": True, "retentionDays": 30}
    chat(stack, "And which one cancels most?")
    body = call(stack, "/conversations", since).json()
    assert set(body) == {"conversations", "recording", "retentionDays"}
    assert body["recording"] and body["retentionDays"] == 30
    [found] = body["conversations"]
    # A projection in camelCase, without who asked.
    assert set(found) == {"id", "at", "team", "environment", "model", "question", "answer", "toolCalls", "queries",
                          "llmModel", "inputTokens", "outputTokens", "traceId"}
    assert found["id"].startswith("conv-") and (found["team"], found["environment"]) == (TEAM, "development")
    assert found["question"] == "And which one cancels most?"
    assert "highest average departure delay" in found["answer"]
    assert [c["name"] for c in found["toolCalls"]] == ["query"] and found["queries"][0]["rowCount"] == 3
    assert found["llmModel"] == "test:flights" and found["model"] == FLIGHTS and found["traceId"] is None
    assert isinstance(found["inputTokens"], int) and isinstance(found["outputTokens"], int)
    # Neither returned nor recorded: the column that held the person's id stays empty.
    log = stack.app.state.conversations
    assert [row["user"] for row in log.db.execute("SELECT user FROM conversations")] == [""]
    assert "portal-bob" not in str(body)
    # `at` is RFC 3339 with milliseconds, and the cursor for the next read.
    assert TIME.match(found["at"])
    assert call(stack, "/conversations", {**since, "since": found["at"]}).json()["conversations"] == []
    assert call(stack, "/conversations", {**since, "since": "2026-01-01T00:00:00Z"}).json()["conversations"] == [
        found]
    for refused in (0, 1795511702.5, "yesterday", "2026-11-24T09:15:02", "2026-11-24"):
        response = call(stack, "/conversations", {**since, "since": refused})
        assert (response.status_code, response.json()["code"]) == (422, "invalid_request"), refused
    # Switching live evaluation off stops the record and deletes what was kept.
    assert call(stack, "/recording", {**since, "enabled": False}).json()["recording"] is False
    chat(stack)
    body = call(stack, "/conversations", since).json()
    assert body["conversations"] == [] and not body["recording"]
    # Never for a team that did not enable Evaluations.
    stack.bridge.team_extensions_[(TEAM, "development")] = set()
    assert call(stack, "/conversations", since).json()["code"] == "extension_not_enabled"
    assert call(stack, "/recording", {**since, "enabled": True}).json()["code"] == "extension_not_enabled"


def test_recording_ends_when_the_team_disables_evaluations(stack):
    stack.call("POST", f"/teams/{TEAM}/environments/development", "alice")
    stack.bridge.team_extensions_[(TEAM, "development")] = {"evaluations"}
    call(stack, "/recording", {"team": TEAM, "environment": "development", "enabled": True})
    chat(stack)
    log = stack.app.state.conversations
    assert len(log.since(TEAM, "development", None, 10)) == 1
    # Disabled without switching live evaluation off first: the next chat ends the record.
    stack.bridge.team_extensions_[(TEAM, "development")] = set()
    chat(stack)
    assert not log.recording(TEAM, "development") and log.since(TEAM, "development", None, 10) == []


def test_old_conversations_are_pruned(stack):
    import time

    stack.call("POST", f"/teams/{TEAM}/environments/development", "alice")
    stack.bridge.team_extensions_[(TEAM, "development")] = {"evaluations"}
    call(stack, "/recording", {"team": TEAM, "environment": "development", "enabled": True})
    chat(stack)
    log = stack.app.state.conversations
    assert len(log.since(TEAM, "development", None, 10)) == 1
    log.pruned = 0
    log.prune(now=time.time() + 31 * 86400)
    assert log.since(TEAM, "development", None, 10) == []


def test_since_is_a_cursor_that_never_repeats_or_skips(tmp_path, monkeypatch):
    """Conversations are read page by page with the last `at` as `since`, also ones stored to the microsecond."""
    from conversationalbi import conversations

    log = ConversationLog(tmp_path / "conversations.sqlite3", 30)
    entry = {"team": TEAM, "environment": "development", "model": FLIGHTS, "question": "Late?", "answer": "Yes.",
             "toolCalls": [], "queries": [], "llmModel": "test:flights", "inputTokens": 1, "outputTokens": 2,
             "traceId": "ab" * 16}
    assert log.enabled
    # Three in the same instant: each gets a millisecond of its own.
    monkeypatch.setattr(conversations.time, "time", lambda: 1795511702.1234567)
    for _ in range(3):
        log.add(entry)
    # One kept by an earlier version, with the time as it was stored then.
    with sqlite3.connect(log.path) as db:
        db.execute("UPDATE conversations SET at = 1795511702.1259999, user = 'portal-bob' "
                   "WHERE at = (SELECT max(at) FROM conversations)")
    found = log.since(TEAM, "development", None, 10)
    assert [c["at"] for c in found] == ["2026-11-24T09:15:02.123Z", "2026-11-24T09:15:02.124Z",
                                        "2026-11-24T09:15:02.126Z"]
    assert found[0]["traceId"] == "ab" * 16 and "user" not in found[2]
    read, since = [], None
    for _ in range(4):
        page = log.since(TEAM, "development", since, 1)
        read += page
        since = instant(page[-1]["at"]) if page else since
    assert read == found
    assert log.since(OTHER, "development", None, 10) == [] and log.since(TEAM, "production", None, 10) == []
    assert instant("2026-11-24T10:15:02.123+01:00") == instant("2026-11-24T09:15:02.123Z") == 1795511702123
    assert timestamp(instant("2026-11-24T09:15:02.123Z") / 1000) == "2026-11-24T09:15:02.123Z"


def schema_only(stack, body=None, token="svc-evaluations"):
    return call(stack, "/ask-schema-only", {"team": TEAM, "environment": "development", "model": FLIGHTS,
                                            "question": "Which carrier is late most often?", **(body or {})}, token)


@pytest.fixture
def control(stack, monkeypatch):
    both(stack)
    evaluation = stack.app.state.evaluation
    monkeypatch.setattr(evaluation, "settings", replace(evaluation.settings, eval_schema_only=True))


def test_the_control_arm_is_off_by_default(stack):
    both(stack)
    response = schema_only(stack)
    assert (response.status_code, response.json()["code"]) == (409, "schema_only_disabled")


def test_the_control_arm_writes_sql_on_the_models_tables_only(stack, control):
    body = schema_only(stack).json()
    assert set(body) == {"answer", "llmModel", "toolCalls", "queries", "usage", "durationMs", "traceId"}
    assert body["llmModel"] == "test:flights"
    assert [c["name"] for c in body["toolCalls"]] == ["run_sql"]
    [query] = body["queries"]
    assert "avg(f.dep_delay)" in query["spec"]["sql"] and query["rowCount"] == 3
    assert query["rows"][0][0] == "Example Cloudline" and body["answer"].startswith("Example Cloudline")
    job = stack.executor.jobs[-1]
    assert {b["view"] for b in job["bindings"]} == {"flights", "routes", "airports", "carriers", "aircraft", "runways"}
    assert job["params"] == [] and job["explainSqlErrors"]


def test_the_control_arm_sees_schemas_and_nothing_the_model_adds(stack, control):
    from conversationalbi.agent import control as arm
    from tests import flights

    model = flights.model()
    text = arm.prompt(arm.tables(model, flights.schemas(model)))
    assert "flights(" in text and "dep_delay" in text
    for added in ("average_departure_delay", "on_time_arrival_pct", "flight_carrier", "An airline", "Ontology"):
        assert added not in text


def test_the_control_arm_binds_a_table_once_however_many_datasets_read_it():
    from conversationalbi.agent import control as arm

    def views(*datasets):
        model = SimpleNamespace(datasets=[SimpleNamespace(name=name, namespace=namespace, table=table)
                                          for name, namespace, table in datasets])
        return [t["view"] for t in arm.tables(model, {d[0]: {"id": "INTEGER"} for d in datasets})]

    assert views(("origin", ["geo"], "airports"), ("destination", ["geo"], "airports")) == ["airports"]
    assert views(("origin", ["geo"], "airports"), ("hub", ["ops"], "airports")) == ["geo_airports", "ops_airports"]
    assert views(("bare", [], "geo_airports"), ("origin", ["geo"], "airports"), ("hub", ["ops"], "airports")) == [
        "geo_airports", "geo_airports_2", "ops_airports"]


def test_the_control_arm_has_the_same_scope_rules(stack, control):
    assert schema_only(stack, {"environment": "production"}).json()["code"] == "environment_not_enabled"
    assert schema_only(stack, {"team": OTHER}).json()["code"] == "environment_not_enabled"
    assert schema_only(stack, token="alice").json()["code"] == "forbidden_caller"
    assert schema_only(stack, {"model": {**FLIGHTS, "database": PROD_DB}}).json()["code"] == "unknown_model"
    assert schema_only(stack, {"llmModel": "openai:gpt-unlisted"}).json()["code"] == "unknown_llm_model"
    assert schema_only(stack, {"llm": "openai:gpt-unlisted"}).json()["code"] == "invalid_request"


def test_a_long_record_stays_readable_json():
    """Cut text is not JSON: a record that is too long loses its results, then its entries."""
    import json

    from conversationalbi.conversations import MAX_JSON, bounded

    small = [{"name": "query", "args": {"metrics": ["delay"]}, "result": {"rows": [[1]]}}]
    assert json.loads(bounded(small)) == small
    large = [{"name": "query", "args": {"metrics": ["delay"]}, "result": {"rows": [["x" * 100] * 5000]}}]
    assert json.loads(bounded(large)) == [{"name": "query", "args": {"metrics": ["delay"]}}]
    assert bounded([{"name": "n" * MAX_JSON}]) == "[]"

