"""Another extension asks for a team: only where the team enabled both, with the team's scope."""

from dataclasses import replace

import pytest

from tests.conftest import OTHER, OWN_DB, PROD_DB, SHARED_DB, TEAM

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
    assert stack.client.get("/api/v1/models", headers={"Authorization": "Bearer svc-evaluations"}).status_code == 401


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
    assert [c["name"] for c in body["toolCalls"]] == ["list_models", "select_model", "query"]
    # Text only: no chart tool in an evaluation.
    [query] = body["queries"]
    assert query["spec"]["metrics"] == ["average_departure_delay"]
    assert query["rowCount"] == 3 and query["rows"][0][0] == "Example Cloudline"
    assert "Example Cloudline has the highest average departure delay" in body["answer"]
    assert body["model"] == "test:flights" and body["usage"]["requests"] == 4
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
    assert {(m["model"]["database"], m["shared"]) for m in body["models"]} == {(OWN_DB, False), (SHARED_DB, True)}
    assert body["llmModels"] == ["test:flights"] and body["schemaOnly"] is False


def test_ground_truth_and_its_refusals(stack):
    both(stack)
    body = call(stack, "/query", {"team": TEAM, "environment": "development", "model": FLIGHTS, "spec": QUERY}).json()
    assert body["rowCount"] == 3 and body["rows"][0][0] == "Example Cloudline"
    assert body["joinPaths"] == {"CARRIER.name": ["flight_carrier"]}
    refused = call(stack, "/query", {"team": TEAM, "environment": "development", "model": FLIGHTS,
                                     "spec": {"metrics": ["average_departure_delay"],
                                              "dimensions": [{"field": "AIRPORT.name"}]}})
    assert (refused.status_code, refused.json()["code"]) == (422, "ambiguous_join")


@pytest.mark.parametrize(("model", "status", "code"), [
    ("openai:gpt-unlisted", 422, "unknown_model_choice"),
])
def test_other_models_only_from_the_allowlist(stack, model, status, code):
    both(stack)
    response = call(stack, "/ask", {"team": TEAM, "environment": "development", "question": "Late?", "model": model})
    assert (response.status_code, response.json()["code"]) == (status, code)


def chat(stack, question="Which carrier is most delayed?", user="bob"):
    from fastapi.testclient import TestClient

    from tests.test_agent import signed_in

    ticket = stack.app.state.tickets.issue(signed_in(stack, user), "sub-" + user)
    body = {"threadId": "thread-1", "runId": "run-1", "state": {"model": FLIGHTS}, "tools": [], "context": [],
            "forwardedProps": {}, "messages": [{"id": "m1", "role": "user", "content": question}]}
    TestClient(stack.app.state.agent_app).post("/", json=body, headers={
        "x-cbi-runtime-key": "k" * 32, "x-cbi-run-ticket": ticket, "accept": "text/event-stream"})


def test_conversations_are_kept_only_for_teams_that_enabled_evaluations(stack):
    stack.call("POST", f"/teams/{TEAM}/environments/development", "alice")
    chat(stack)
    since = {"team": TEAM, "environment": "development"}
    stack.bridge.team_extensions_[(TEAM, "development")] = {"evaluations"}
    assert call(stack, "/conversations", since).json()["conversations"] == []
    chat(stack, "And which one cancels most?")
    body = call(stack, "/conversations", since).json()
    assert body["recording"] and body["retentionDays"] == 30
    [found] = body["conversations"]
    assert found["question"] == "And which one cancels most?" and found["user"] == "portal-bob".ljust(39, "0")[:39]
    assert "highest average departure delay" in found["answer"]
    assert [c["name"] for c in found["tool_calls"]] == ["query"] and found["queries"][0]["rowCount"] == 3
    assert found["llm_model"] == "test:flights" and found["model"] == FLIGHTS
    # Later ones only, and never for a team that did not enable Evaluations.
    assert call(stack, "/conversations", {**since, "since": found["at"]}).json()["conversations"] == []
    stack.bridge.team_extensions_[(TEAM, "development")] = set()
    assert call(stack, "/conversations", since).json()["code"] == "extension_not_enabled"


def test_old_conversations_are_pruned(stack):
    import time

    stack.call("POST", f"/teams/{TEAM}/environments/development", "alice")
    stack.bridge.team_extensions_[(TEAM, "development")] = {"evaluations"}
    chat(stack)
    log = stack.app.state.conversations
    log.pruned = 0
    log.prune(now=time.time() + 31 * 86400)
    assert log.since(TEAM, "development", 0, 10) == []


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
    assert (response.status_code, response.json()["code"]) == (403, "schema_only_disabled")


def test_the_control_arm_writes_sql_on_the_models_tables_only(stack, control):
    body = schema_only(stack).json()
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


def test_the_control_arm_has_the_same_scope_rules(stack, control):
    assert schema_only(stack, {"environment": "production"}).json()["code"] == "environment_not_enabled"
    assert schema_only(stack, {"team": OTHER}).json()["code"] == "environment_not_enabled"
    assert schema_only(stack, token="alice").json()["code"] == "forbidden_caller"
    assert schema_only(stack, {"model": {**FLIGHTS, "database": PROD_DB}}).json()["code"] == "unknown_model"
    assert schema_only(stack, {"llm": "openai:gpt-unlisted"}).json()["code"] == "unknown_model_choice"

