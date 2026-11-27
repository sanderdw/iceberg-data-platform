import asyncio
import json
import re

import pytest

from conversationalbi import evaluation
from conversationalbi.api import OPERATIONS
from conversationalbi.config import ROOT
from conversationalbi.errors import CbiError
from conversationalbi.mcp_server import INSTRUCTIONS, create_mcp
from conversationalbi.services import timestamp
from tests.conftest import FLIGHTS, OTHER, OWN_DB, PARTNER, PROD_DB, SHARED_DB, TABLES, TEAM, ToolRefused

TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")

QUERY = {"metrics": ["average_departure_delay"], "dimensions": [{"field": "CARRIER.name"}],
         "order_by": [{"name": "average_departure_delay", "desc": True}]}


def model(database=OWN_DB):
    return {"database": database, "namespace": ["ai_flights"], "name": "flights"}


def enable(stack, environment="development", user="alice", team=TEAM):
    return stack.call("POST", f"/teams/{team}/environments/{environment}", user)


def test_whoami_shows_only_what_this_extension_shows(stack):
    me = stack.call("GET", "/me").json()
    # Built field by field: nothing else the Bridge says about the caller (such as sharedObjects) comes along.
    assert set(me) == {"user", "memberships", "databases", "extension", "origin", "environments",
                       "enabledEnvironments", "userPortalUrl", "apps", "sharedData", "sharedDatabases"}
    assert me["user"] == {"id": "portal-alice" + "0" * 27, "name": "alice"}
    assert me["memberships"] == [{"team": TEAM, "teamName": TEAM[:9], "role": "admin"}]
    assert me["databases"] == [
        {"id": OWN_DB, "name": "sales", "team": TEAM, "environment": "development", "status": "ready"},
        {"id": PROD_DB, "name": "sales", "team": TEAM, "environment": "production", "status": "ready"}]
    assert me["extension"] == "conversationalbi" and me["origin"] == "http://localhost:3007"
    assert me["environments"] == ["development", "acceptance", "production"]
    assert me["enabledEnvironments"] == [{"team": TEAM, "environments": []}]
    assert me["apps"]["workspace"] == [{"id": "portal", "title": "User portal", "url": "http://localhost:3002"}]
    assert me["sharedData"] is True
    assert me["sharedDatabases"] == [{"id": SHARED_DB, "name": "flights", "team": PARTNER,
                                      "ownerTeamName": "partner-team", "environment": "development",
                                      "recipientTeams": [TEAM]}]
    enable(stack, "production")
    enable(stack)
    assert stack.call("GET", "/me").json()["enabledEnvironments"] == [
        {"team": TEAM, "environments": ["development", "production"]}]
    stack.bridge.capabilities_.remove("shared-data")
    assert stack.call("GET", "/me").json()["sharedData"] is False


def test_models_need_an_enabled_environment(stack):
    listed = stack.call("GET", "/semantic-models").json()
    assert list(listed) == ["semanticModels", "notEnabled"] and listed["semanticModels"] == []
    assert {(n["team"], n["environment"], n["canEnable"]) for n in listed["notEnabled"]} == {
        (TEAM, "development", True), (TEAM, "production", True)}
    assert enable(stack, user="bob").json()["code"] == "forbidden_role"
    assert enable(stack).status_code == 200
    models = stack.call("GET", "/semantic-models").json()["semanticModels"]
    assert [(m["databaseName"], m["shared"], m["key"]) for m in models] == [
        ("sales", False, f"{OWN_DB}/ai_flights/flights"), ("flights", True, f"{SHARED_DB}/ai_flights/flights")]
    own, shared = models
    # The model is named once, by its reference.
    assert own == {"model": model(), "key": f"{OWN_DB}/ai_flights/flights", "databaseName": "sales",
                   "environment": "development", "team": TEAM, "teamName": TEAM[:9], "shared": False,
                   "readingTeam": TEAM, "readingTeamName": TEAM[:9]}
    assert set(shared) == set(own) and shared["model"] == model(SHARED_DB)
    assert shared["readingTeam"] == TEAM and shared["teamName"] != shared["readingTeamName"]
    assert stack.call("GET", "/models").status_code == 404


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
        assert result["dataAsOf"] == "2026-09-01T08:00:00.000Z"
        assert result["snapshots"]["FLIGHT"] == {"id": 1, "committedAt": "2026-09-01T08:00:00.000Z"}
        assert result["model"] == model(database) and result["query"]["order_by"] == QUERY["order_by"]
        assert result["recommended"] == {"tool": "show_chart", "arguments": {
            "kind": "bar", "x": "CARRIER.name", "y": ["average_departure_delay"], "horizontal": True}}
    # Every query reads with a read token of the team's principal; never a write token.
    assert {access for _, access, _ in stack.bridge.tokens} == {"read"}
    again = stack.call("GET", f"/results/{result['id']}", "bob").json()
    assert again["rows"] == result["rows"]
    assert stack.call("GET", f"/results/{result['id']}", "alice").json()["code"] == "unknown_result"


def test_outsiders_and_revoked_shares_see_nothing(stack):
    enable(stack)
    assert stack.call("GET", "/semantic-models", "dave").json() == {"semanticModels": [], "notEnabled": []}
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
    shared = next(m for m in stack.call("GET", "/semantic-models").json()["semanticModels"] if m["shared"])
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
    assert [m["shared"] for m in stack.call("GET", "/semantic-models").json()["semanticModels"]] == [False]
    assert stack.call("POST", "/queries", json={"model": model(SHARED_DB), **QUERY}).json()["code"] == "unknown_model"


def test_describe_explains_joins(stack):
    enable(stack)
    described = stack.call("GET", "/semantic-models/describe", params={
        "database": OWN_DB, "namespace": ["ai_flights"], "model": "flights"}).json()
    # `database` is an id everywhere, here inside the reference; the database's name is `databaseName`.
    assert described["model"] == model() and described["databaseName"] == "sales" and "database" not in described
    assert described["environment"] == "development" and described["shared"] is False
    assert [m["name"] for m in described["metrics"]] == [
        "average_departure_delay", "average_arrival_delay", "on_time_arrival_pct", "cancellation_pct"]
    assert {m["dataset"] for m in described["metrics"]} == {"FLIGHT"}
    dims = {d["field"]: d for d in described["dimensions"]["FLIGHT"]}
    assert dims["AIRPORT.name"]["ambiguous"] and dims["CARRIER.name"]["path"] == ["flight_carrier"]
    fields = {f["name"]: f for d in described["datasets"] if d["name"] == "FLIGHT" for f in d["fields"]}
    assert fields["date"]["time"] and not fields["carrier_code"]["time"]
    assert described["examples"] == [] and described["metrics"][0]["synonyms"] == []
    missing = stack.call("GET", "/semantic-models/describe", params={
        "database": OWN_DB, "namespace": ["ai_flights"], "model": "nope"})
    assert missing.status_code == 404 and missing.json()["code"] == "unknown_model"
    # The model is named `model`, as in the tools; `name` is no longer a parameter.
    old = stack.call("GET", "/semantic-models/describe", params={
        "database": OWN_DB, "namespace": ["ai_flights"], "name": "flights"})
    assert old.status_code == 422 and old.json()["code"] == "invalid_request"
    assert stack.call("GET", "/models/describe", params={
        "database": OWN_DB, "namespace": ["ai_flights"], "name": "flights"}).status_code == 404


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
    path = f"/teams/{TEAM}/environments"
    enabled = enable(stack, "production")
    assert enabled.status_code == 200
    # The environment, never the Bridge's service account.
    assert enabled.json() == {"team": TEAM, "environment": "production", "enabled": True, "enabledBy": "alice",
                              "databases": [{"id": PROD_DB, "name": "sales"}], "sharedDatabases": []}
    listed = stack.call("GET", path, "bob").json()
    assert list(listed) == ["environments"]
    assert [e["environment"] for e in listed["environments"]] == ["development", "acceptance", "production"]
    assert listed["environments"][2] == enabled.json()
    assert listed["environments"][0] == {
        "team": TEAM, "environment": "development", "enabled": False, "enabledBy": None,
        "databases": [{"id": OWN_DB, "name": "sales"}], "sharedDatabases": [{"id": SHARED_DB, "name": "flights"}]}
    assert not any("principal" in e for e in listed["environments"])
    disabled = stack.call("DELETE", f"{path}/production")
    assert disabled.status_code == 200
    assert disabled.json() == {**enabled.json(), "enabled": False, "enabledBy": None}
    again = stack.call("DELETE", f"{path}/production")
    assert again.status_code == 409 and again.json()["code"] == "environment_not_enabled"


def test_every_error_has_the_same_body(stack, monkeypatch):
    def refused(response, status, code):
        body = response.json()
        assert response.status_code == status and body["code"] == code, response.text
        assert isinstance(body["error"], str) and body["error"] and set(body) <= {"error", "code", "detail"}

    refused(stack.call("GET", "/nothing-here"), 404, "not_found")
    refused(stack.client.get("/nothing/here"), 404, "not_found")
    refused(stack.call("PUT", "/me"), 405, "method_not_allowed")
    refused(stack.call("GET", "/me", "mallory"), 401, "unauthorized")
    refused(stack.call("GET", f"/teams/{TEAM}/environments/test"), 405, "method_not_allowed")
    refused(stack.call("POST", f"/teams/{TEAM}/environments/test"), 422, "invalid_request")
    refused(stack.call("GET", "/results/res-" + "0" * 24), 404, "unknown_result")
    refused(stack.call("POST", "/queries", json={"model": model(), **QUERY}), 409, "environment_not_enabled")
    refused(stack.call("POST", "/queries", "dave", json={"model": model(), **QUERY}), 404, "unknown_model")
    refused(stack.client.post("/api/v1/evaluation/ask", json={}), 401, "unauthorized")

    async def broken(caller):
        raise RuntimeError("boom")
    monkeypatch.setattr(stack.services, "whoami", broken)
    refused(stack.call("GET", "/me"), 500, "internal_error")
    assert CbiError(418, "x").code == "internal_error" and CbiError(405, "x").code == "method_not_allowed"
    assert CbiError(500, "x").code == "internal_error"


def test_times_are_rfc_3339_with_milliseconds():
    assert timestamp(None) is None
    assert timestamp(1795511702.123) == "2026-11-24T09:15:02.123Z"
    # Rounded to the millisecond, the way a `since` is compared (conversations.py).
    assert timestamp(1795511702.1236) == "2026-11-24T09:15:02.124Z" and timestamp(0) == "1970-01-01T00:00:00.000Z"
    assert TIME.match(timestamp(1795511702))


def test_tools_return_what_the_api_returns(stack):
    """An MCP tool gives the REST operation's body, unchanged and without a wrapper of its own."""
    environment = stack.tool("enable_environment", team=TEAM, environment="development")
    assert environment == {"team": TEAM, "environment": "development", "enabled": True, "enabledBy": "alice",
                           "databases": [{"id": OWN_DB, "name": "sales"}],
                           "sharedDatabases": [{"id": SHARED_DB, "name": "flights"}]}
    where = {"database": OWN_DB, "namespace": ["ai_flights"], "model": "flights"}
    for tool, arguments, path, params in (
            ("whoami", {}, "/me", None),
            ("list_environments", {"team": TEAM}, f"/teams/{TEAM}/environments", None),
            ("list_semantic_models", {}, "/semantic-models", None),
            ("describe_semantic_model", where, "/semantic-models/describe", where)):
        assert stack.tool(tool, "bob", **arguments) == stack.call("GET", path, "bob", params=params).json(), tool
    assert list(stack.tool("list_environments", team=TEAM)) == ["environments"]
    assert list(stack.tool("list_semantic_models")) == ["semanticModels", "notEnabled"]
    # A query answers with a sample for the LLM; the whole result is the REST operation's, by id.
    answer = stack.tool("query_semantic_model", "bob", **where, **QUERY)
    assert answer["rowCount"] == 3 and answer["dataAsOf"] == "2026-09-01T08:00:00.000Z"
    assert answer["rowsUrl"] == f"http://localhost:3007/api/v1/results/{answer['resultId']}"
    whole = stack.call("GET", f"/results/{answer['resultId']}", "bob").json()
    assert whole["rows"] == answer["rows"] and whole["sql"] == answer["sql"]
    # `model` is the semantic model's reference in both, and its display name is `modelName`.
    assert answer["model"] == whole["model"] == model() and answer["modelName"] == whole["modelName"]
    page = stack.tool("get_result", "bob", result=answer["resultId"], offset=1, limit=1)
    assert page["rows"] == whole["rows"][1:2] and page["offset"] == 1 and page["model"] == model()
    with pytest.raises(ToolRefused, match=r"\[unknown_result\]"):
        stack.tool("get_result", result=answer["resultId"])
    with pytest.raises(ToolRefused, match=r"\[ambiguous_join\]"):
        stack.tool("query_semantic_model", "bob", **where, metrics=["average_departure_delay"],
                   dimensions=[{"field": "AIRPORT.name"}])
    with pytest.raises(ToolRefused, match=r"\[forbidden_role\]"):
        stack.tool("enable_environment", "bob", team=TEAM, environment="production")
    disabled = stack.tool("disable_environment", team=TEAM, environment="development")
    assert disabled == {**environment, "enabled": False, "enabledBy": None}
    with pytest.raises(ToolRefused, match=r"\[environment_not_enabled\]"):
        stack.tool("disable_environment", team=TEAM, environment="development")


def test_tool_names_parameters_and_annotations(stack):
    tools = stack.tools()

    def parameters(tool):
        return tools[tool]["inputSchema"]["properties"]

    def hints(tool):
        a = tools[tool]["annotations"]
        return a["readOnlyHint"], a["destructiveHint"], a["idempotentHint"], a["openWorldHint"]

    assert set(tools) == {"whoami", "list_environments", "enable_environment", "disable_environment",
                          "list_semantic_models", "describe_semantic_model", "query_semantic_model", "get_result"}
    # A semantic model is `model` beside `database` and `namespace`; a result is `result`. Never `name` or an id.
    assert set(parameters("describe_semantic_model")) == {"database", "namespace", "model"}
    assert set(parameters("query_semantic_model")) == {"database", "namespace", "model", "metrics", "dimensions",
                                                       "filters", "order_by", "limit"}
    assert set(parameters("get_result")) == {"result", "offset", "limit"}
    assert not [name for name in tools if {"id", "name", "result_id"} & set(parameters(name))]
    for tool in ("whoami", "list_environments", "list_semantic_models", "describe_semantic_model",
                 "query_semantic_model", "get_result"):
        assert hints(tool) == (True, False, True, False), tool
    assert hints("enable_environment") == (False, False, True, False)
    assert hints("disable_environment") == (False, True, True, False)
    told = INSTRUCTIONS + " ".join(tool["description"] for tool in tools.values())
    assert "query" not in tools
    for old in ("list_models", "describe_model", "result_id"):
        assert old not in tools and not re.search(rf"\b{old}\b", told), old
    for new in ("list_semantic_models", "describe_semantic_model", "query_semantic_model", "get_result"):
        assert new in INSTRUCTIONS, new


def test_the_contract_describes_errors_and_the_shared_shapes(stack):
    schema = stack.app.openapi()
    schemas = schema["components"]["schemas"]
    assert {"Error", "Me", "Environment", "EnvironmentList", "SemanticModelList", "EvaluationModels",
            "EvaluationAnswer", "EvaluationQueryResult", "EvaluationRecording",
            "EvaluationConversations"} <= set(schemas)
    assert "HTTPValidationError" not in schemas
    assert set(schemas["Error"]["properties"]) == {"error", "code", "detail"}
    assert schemas["Error"]["required"] == ["error", "code"]
    operations = {op["operationId"]: (method, path, op) for path, item in schema["paths"].items()
                  for method, op in item.items()}
    for id, (_, _, op) in operations.items():
        for status in ("401", "403", "404", "409", "422", "500", "502", "503"):
            assert op["responses"][status]["content"]["application/json"]["schema"]["$ref"].endswith("/Error"), id

    def answer(id, status="200"):
        return operations[id][2]["responses"][status]["content"]["application/json"]["schema"]["$ref"].split("/")[-1]

    assert answer("whoami") == "Me" and answer("listEnvironments") == "EnvironmentList"
    assert answer("enableEnvironment") == answer("disableEnvironment") == "Environment"
    assert answer("listSemanticModels") == "SemanticModelList"
    assert operations["listSemanticModels"][:2] == ("get", "/api/v1/semantic-models")
    assert operations["describeSemanticModel"][:2] == ("get", "/api/v1/semantic-models/describe")
    assert operations["querySemanticModel"][:2] == ("post", "/api/v1/queries")
    assert {p["name"] for p in operations["describeSemanticModel"][2]["parameters"]} == {
        "database", "namespace", "model"}
    assert set(schemas["Me"]["properties"]) == {
        "user", "memberships", "databases", "extension", "origin", "environments", "enabledEnvironments",
        "userPortalUrl", "apps", "sharedData", "sharedDatabases"}
    assert set(schemas["SharedDatabase"]["properties"]) == {"id", "name", "team", "ownerTeamName", "environment",
                                                            "recipientTeams"}
    assert set(schemas["Environment"]["properties"]) == {"team", "environment", "enabled", "enabledBy", "databases",
                                                         "sharedDatabases"}
    assert set(schemas["SemanticModel"]["properties"]) == {
        "model", "key", "databaseName", "environment", "team", "teamName", "shared", "readingTeam",
        "readingTeamName"}
    # A governed query keeps its document format, order_by included.
    assert "order_by" in schemas["QueryInput"]["properties"]
    # The evaluation API: one name for an LLM, `model` for a semantic model.
    assert answer("evaluationModels") == "EvaluationModels"
    assert answer("evaluationAsk") == answer("evaluationAskSchemaOnly") == "EvaluationAnswer"
    assert answer("evaluationQuery") == "EvaluationQueryResult"
    assert answer("evaluationRecording") == "EvaluationRecording"
    assert answer("evaluationConversations") == "EvaluationConversations"
    assert set(schemas["EvaluationModels"]["properties"]) == {"semanticModels", "llmModels", "schemaOnly"}
    assert set(schemas["Ask"]["properties"]) == {"team", "environment", "question", "llmModel"}
    assert set(schemas["SchemaOnlyAsk"]["properties"]) == {"team", "environment", "model", "question", "llmModel"}
    assert set(schemas["EvaluationAnswer"]["properties"]) == {"answer", "llmModel", "toolCalls", "queries", "usage",
                                                              "durationMs", "traceId"}
    assert set(schemas["Usage"]["properties"]) == {"inputTokens", "outputTokens", "requests"}
    assert set(schemas["EvaluationQueryResult"]["properties"]) == {
        "modelName", "query", "columns", "rows", "rowCount", "truncated", "joinPaths", "sql", "dataAsOf"}
    assert set(schemas["EvaluationRecording"]["properties"]) == {"recording", "retentionDays"}
    assert set(schemas["EvaluationConversations"]["properties"]) == {"conversations", "recording", "retentionDays"}
    assert set(schemas["EvaluationConversation"]["properties"]) == {
        "id", "at", "team", "environment", "model", "question", "answer", "toolCalls", "queries", "llmModel",
        "inputTokens", "outputTokens", "traceId"}
    assert schemas["EvaluationConversation"]["properties"]["at"]["type"] == "string"
    since = schemas["Since"]["properties"]["since"]
    assert {"string", "null"} == {option["type"] for option in since["anyOf"]}
    # No path parameter is called id.
    assert not [path for path in schema["paths"] if "{id}" in path]


def test_every_operation_is_an_mcp_tool(stack):
    schema = stack.client.get("/api/v1/openapi.json").json()
    operations = {op["operationId"] for path in schema["paths"].values() for op in path.values()}
    # Except the ones only other extensions call.
    assert operations == set(OPERATIONS) | evaluation.OPERATIONS
    assert OPERATIONS["listSemanticModels"] == "list_semantic_models"
    assert OPERATIONS["describeSemanticModel"] == "describe_semantic_model"
    assert OPERATIONS["querySemanticModel"] == "query_semantic_model"
    mcp = create_mcp(stack.services.settings, stack.services, None)
    assert {t.name for t in asyncio.run(mcp.list_tools())} == set(OPERATIONS.values())


def test_committed_contract_matches_the_implementation(stack):
    committed = ROOT / "contracts" / "conversationalbi-api" / "v1" / "openapi.yaml"
    assert json.loads(committed.read_text()) == stack.client.get("/api/v1/openapi.json").json(), (
        "Regenerate with `uv run python -m scripts.api_contract`")


def test_a_sign_in_belongs_to_the_browser_that_started_it():
    """A callback URL sent to another browser signs nobody in (login CSRF)."""
    import asyncio

    import httpx
    import pytest

    from conversationalbi.auth import BrowserLogin
    from conversationalbi.errors import CbiError
    from tests.conftest import FakeVerifier, settings

    token = httpx.MockTransport(lambda request: httpx.Response(200, json={"access_token": "alice",
                                                                          "refresh_token": "r"}))
    login = BrowserLogin(settings(), FakeVerifier(), httpx.AsyncClient(transport=token))
    url, binding = login.start("/#/ask")
    state = dict(part.split("=", 1) for part in url.split("?", 1)[1].split("&"))["state"]
    with pytest.raises(CbiError):
        asyncio.run(login.finish(state, "code", "someone-elses-binding"))
    # The failed attempt used up the state.
    with pytest.raises(CbiError):
        asyncio.run(login.finish(state, "code", binding))
    url, binding = login.start("/#/ask")
    state = dict(part.split("=", 1) for part in url.split("?", 1)[1].split("&"))["state"]
    session, target = asyncio.run(login.finish(state, "code", binding))
    assert session in login.sessions and target == "/#/ask"


def test_the_login_route_sets_the_binding_cookie(stack):
    response = stack.client.get("/auth/login", follow_redirects=False)
    assert response.status_code == 303
    cookie = response.headers["set-cookie"]
    assert "iceberg_conversationalbi_login=" in cookie and "HttpOnly" in cookie and "Path=/auth/callback" in cookie
    state = response.headers["location"].split("state=", 1)[1].split("&", 1)[0]
    stack.client.cookies.clear()
    failed = stack.client.get(f"/auth/callback?state={state}&code=x", follow_redirects=False)
    assert failed.headers["location"] == "/?signin=failed"
