import asyncio
import json

import pytest

from conversationalbi.api import OPERATIONS
from conversationalbi.config import ROOT
from conversationalbi.mcp_server import create_mcp
from tests.conftest import FLIGHTS, OTHER, OWN_DB, SHARED_DB, TABLES, TEAM

QUERY = {"metrics": ["average_departure_delay"], "dimensions": [{"field": "CARRIER.name"}],
         "order_by": [{"name": "average_departure_delay", "desc": True}]}


def model(database=OWN_DB):
    return {"database": database, "namespace": ["ai_flights"], "name": "flights"}


def enable(stack, environment="development", user="alice", team=TEAM):
    return stack.call("POST", f"/teams/{team}/environments/{environment}", user)


def test_models_need_an_enabled_environment(stack):
    listed = stack.call("GET", "/models").json()
    assert listed["models"] == []
    assert {(n["team"], n["environment"], n["canEnable"]) for n in listed["notEnabled"]} == {
        (TEAM, "development", True), (TEAM, "production", True)}
    assert enable(stack, user="bob").json()["code"] == "forbidden_role"
    assert enable(stack).status_code == 200
    models = stack.call("GET", "/models").json()["models"]
    assert [(m["databaseName"], m["shared"], m["key"]) for m in models] == [
        ("sales", False, f"{OWN_DB}/ai_flights/flights"), ("flights", True, f"{SHARED_DB}/ai_flights/flights")]
    shared = models[1]
    assert shared["readingTeam"] == TEAM and shared["teamName"] != shared["readingTeamName"]


def test_query_own_and_shared_models(stack):
    enable(stack)
    for database in (OWN_DB, SHARED_DB):
        result = stack.call("POST", "/queries", "bob", json={"model": model(database), **QUERY}).json()
        assert result["id"].startswith("res-") and result["rowCount"] == 3
        assert result["joinPaths"] == {"CARRIER.name": ["flight_carrier"]}
        assert [c["name"] for c in result["columns"]] == ["CARRIER.name", "average_departure_delay"]
        assert result["rows"][0][1] >= result["rows"][-1][1]
        assert "LEFT JOIN" in result["sql"]
        # Dated by the snapshot of the metrics' dataset, and shown the way its columns suggest.
        assert result["dataAsOf"] == "2026-09-01T08:00:00Z"
        assert result["recommended"] == {"tool": "show_chart", "arguments": {
            "kind": "bar", "x": "CARRIER.name", "y": ["average_departure_delay"], "horizontal": True}}
    # Every query reads with a read token of the team's principal; never a write token.
    assert {access for _, access, _ in stack.bridge.tokens} == {"read"}
    again = stack.call("GET", f"/results/{result['id']}", "bob").json()
    assert again["rows"] == result["rows"]
    assert stack.call("GET", f"/results/{result['id']}", "alice").json()["code"] == "unknown_result"


def test_outsiders_and_revoked_shares_see_nothing(stack):
    enable(stack)
    assert stack.call("GET", "/models", "dave").json() == {"models": [], "notEnabled": []}
    denied = stack.call("POST", "/queries", "dave", json={"model": model(), **QUERY}).json()
    assert denied["code"] == "unknown_model"
    result = stack.call("POST", "/queries", "bob", json={"model": model(SHARED_DB), **QUERY}).json()
    # The share no longer includes the model: new questions and old results are refused.
    stack.bridge.shared_objects = TABLES
    assert stack.call("POST", "/queries", "bob", json={"model": model(SHARED_DB), **QUERY}).json()["code"] == (
        "unknown_model")
    assert stack.call("GET", f"/results/{result['id']}", "bob").json()["code"] == "unknown_model"


def test_a_share_without_every_table_is_refused(stack):
    enable(stack)
    stack.bridge.shared_objects = [o for o in stack.bridge.shared_objects if o.get("name") != "carriers"]
    refused = stack.call("POST", "/queries", "bob", json={"model": model(SHARED_DB), **QUERY}).json()
    assert refused["code"] == "tables_not_shared" and refused["detail"]["tables"] == ["ai_flights.carriers"]


def test_the_team_that_received_every_table_reads_a_shared_model(stack):
    # Alice is in two recipient teams: OTHER received only the model, TEAM the model and its tables.
    stack.bridge.roles["alice"] = {OTHER: "admin", TEAM: "admin"}
    stack.bridge.recipients = [OTHER, TEAM]
    stack.bridge.received = {OTHER: [FLIGHTS]}
    enable(stack, team=OTHER)
    enable(stack)
    shared = next(m for m in stack.call("GET", "/models").json()["models"] if m["shared"])
    assert shared["readingTeam"] == TEAM
    result = stack.call("POST", "/queries", json={"model": model(SHARED_DB), **QUERY}).json()
    assert result["rowCount"] == 3
    reader = next(a["id"] for a in stack.bridge.automation_ if a["team"] == TEAM)
    assert {principal for principal, _, _ in stack.bridge.tokens} == {reader}
    # Without a team that received every table, the question is refused and names what is missing.
    stack.bridge.received[TEAM] = [FLIGHTS, *[o for o in TABLES if o["name"] != "routes"]]
    refused = stack.call("POST", "/queries", json={"model": model(SHARED_DB), **QUERY}).json()
    assert refused["code"] == "tables_not_shared" and refused["detail"]["tables"] == ["ai_flights.routes"]


def test_a_result_ends_when_a_table_it_read_leaves_the_share(stack):
    enable(stack)
    result = stack.call("POST", "/queries", "bob", json={"model": model(SHARED_DB), **QUERY}).json()
    assert stack.call("GET", f"/results/{result['id']}", "bob").status_code == 200
    # The model stays shared, but the carriers table this result read does not.
    stack.bridge.shared_objects = [o for o in [FLIGHTS, *TABLES] if o["name"] != "carriers"]
    assert stack.call("GET", f"/results/{result['id']}", "bob").json()["code"] == "table_not_readable"


def test_without_shared_data_only_own_models(stack):
    enable(stack)
    stack.bridge.capabilities_ = ["user-context", "automation-principals", "automation-tokens"]
    assert [m["shared"] for m in stack.call("GET", "/models").json()["models"]] == [False]
    assert stack.call("POST", "/queries", json={"model": model(SHARED_DB), **QUERY}).json()["code"] == "unknown_model"


def test_describe_explains_joins(stack):
    enable(stack)
    described = stack.call("GET", "/models/describe", params={
        "database": OWN_DB, "namespace": ["ai_flights"], "name": "flights"}).json()
    assert [m["name"] for m in described["metrics"]] == [
        "average_departure_delay", "average_arrival_delay", "on_time_arrival_pct", "cancellation_pct"]
    assert {m["dataset"] for m in described["metrics"]} == {"FLIGHT"}
    dims = {d["field"]: d for d in described["dimensions"]["FLIGHT"]}
    assert dims["AIRPORT.name"]["ambiguous"] and dims["CARRIER.name"]["path"] == ["flight_carrier"]


@pytest.mark.parametrize(("query", "code"), [
    ({"dimensions": [{"field": "AIRPORT.name"}]}, "ambiguous_join"),
    ({"dimensions": [{"field": "RUNWAY.length"}]}, "fan_out"),
])
def test_refusals_are_explained(stack, query, code):
    enable(stack)
    refused = stack.call("POST", "/queries", json={"model": model(), "metrics": ["average_departure_delay"], **query})
    assert refused.status_code == 422 and refused.json()["code"] == code


def test_browser_writes_need_the_csrf_header(stack):
    response = stack.client.post("/api/v1/queries", json={"model": model(), **QUERY})
    assert response.json()["code"] == "csrf"


def test_environment_admin_rules(stack):
    assert enable(stack, user="dave").json()["code"] == "forbidden_role"
    assert stack.call("GET", f"/teams/{OTHER}/environments", "alice").json()["code"] == "not_a_member"
    enable(stack, "production")
    envs = {e["environment"]: e for e in stack.call("GET", f"/teams/{TEAM}/environments").json()}
    assert envs["production"]["enabled"] and not envs["development"]["enabled"]
    assert envs["development"]["sharedDatabases"] == ["flights"]
    assert stack.call("DELETE", f"/teams/{TEAM}/environments/production").json()["disabled"]


def test_every_operation_is_an_mcp_tool(stack):
    schema = stack.client.get("/api/v1/openapi.json").json()
    operations = {op["operationId"] for path in schema["paths"].values() for op in path.values()}
    assert operations == set(OPERATIONS)
    mcp = create_mcp(stack.services.settings, stack.services, None)
    assert {t.name for t in asyncio.run(mcp.list_tools())} == set(OPERATIONS.values())


def test_committed_contract_matches_the_implementation(stack):
    committed = ROOT / "contracts" / "conversationalbi-api" / "v1" / "openapi.yaml"
    assert json.loads(committed.read_text()) == stack.client.get("/api/v1/openapi.json").json(), (
        "Regenerate with `uv run python -m scripts.api_contract`")
