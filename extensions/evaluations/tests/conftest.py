"""Fakes for the platform side: the Bridge (contract 0.1.3), Keycloak tokens and the agent (Conversational BI)."""

import json
import re
import time
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from evaluations.app import create_app
from evaluations.auth import Caller
from evaluations.config import Settings
from evaluations.errors import EvalError

TEAM = "team-" + "a" * 32
OTHER = "team-" + "c" * 32
DB = "db-" + "1" * 32
MODEL = {"database": DB, "namespace": ["ai_flights"], "name": "flights"}
DELAYS = [["Example Cloudline", 16.923885187083545], ["Example Jetways", 12.5], ["Example Air", 9.25]]
QUERY = {"metrics": ["average_departure_delay"], "dimensions": [{"field": "CARRIER.name"}],
         "order_by": [{"name": "average_departure_delay", "desc": True}]}
LLM_MODELS = ["test:flights", "openai:gpt-test"]
# Every time in a response, and every time Conversational BI takes or gives.
TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")


def seconds(timestamp):
    assert isinstance(timestamp, str) and TIMESTAMP.match(timestamp), timestamp
    return datetime.fromisoformat(timestamp).timestamp()


def conversation(number, at, question, answer, **fields):
    """A chat run as Conversational BI's evaluation API gives it: never the person who asked."""
    return {"id": "conv-" + str(number) * 24, "at": at, "team": TEAM, "environment": "development", "model": MODEL,
            "question": question, "answer": answer,
            "toolCalls": [{"name": "query", "args": QUERY, "result": {"rowCount": 3}}],
            "queries": [{"spec": {**QUERY, "limit": 100}, "rowCount": 3, "rowsShown": 3, "rows": DELAYS}],
            "llmModel": "google:gemini", "inputTokens": 900, "outputTokens": 80, "traceId": "ab" * 16, **fields}


class ToolRefused(Exception):
    """An MCP tool answered with an error."""

YAML = """cases:
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
"""


def settings(tmp_path, **overrides):
    values = {"bridge_url": "http://bridge:3005", "extension_id": "evaluations", "client_id": "ext-evaluations",
              "client_secret": "s" * 48, "mcp_client_id": "ext-evaluations-mcp",
              "issuer": "http://localhost:8080/realms/iceberg", "internal_issuer": "http://localhost:8080/realms/iceberg",
              "origin": "http://localhost:3012", "state_dir": tmp_path}
    return Settings(**{**values, **overrides})


class FakeBridge:
    def __init__(self):
        self.roles = {"alice": {TEAM: "admin"}, "bob": {TEAM: "writer"}, "carol": {TEAM: "reader"},
                      "dave": {OTHER: "admin"}}
        self.automation_ = []
        self.tokens = []

    async def discovery(self, refresh=False):
        return {"contractVersion": "0.1.3", "capabilities": ["team-extensions"], "userPortalUrl": "http://localhost:3002",
                "storage": {"internalEndpoint": "http://rustfs:9000"}}

    async def close(self):
        pass

    async def service_token(self):
        return "svc"

    async def me(self, token):
        """More than Evaluations shows: the Bridge also tells where a database is stored and what is shared."""
        teams = self.roles[token]
        return {"user": {"id": "portal-" + token, "name": token, "keycloakId": "kc-" + token},
                "memberships": [{"team": t, "teamName": t[:9], "role": r, "joinedAt": 1} for t, r in teams.items()],
                "databases": [{"id": DB, "name": "sales", "team": TEAM, "environment": "development", "status": "ready",
                               "storageLocation": "s3://sales"}] if TEAM in teams else [],
                "sharedDatabases": [{"id": "db-" + "9" * 32, "name": "shared", "team": OTHER}]}

    async def team_automation(self, token, team):
        return [a for a in self.automation_ if a["team"] == team]

    async def enable(self, token, team, environment):
        if self.roles[token].get(team) not in ("admin", "bucket-admin"):
            raise EvalError(403, "Only team administrators can enable an extension.", "forbidden_role")
        item = {"id": "svc-" + str(len(self.automation_)).rjust(32, "0"), "team": team, "environment": environment,
                "extension": "evaluations", "status": "active", "createdBy": token}
        self.automation_.append(item)
        return item

    async def revoke(self, token, id):
        self.automation_ = [a for a in self.automation_ if a["id"] != id]
        return {"revoked": True}

    async def scope(self, id):
        item = next(a for a in self.automation_ if a["id"] == id)
        own = [{"id": DB, "name": "sales", "team": TEAM, "environment": "development", "status": "ready",
                "storageLocation": "s3://sales"}]
        return {**item, "databases": [d for d in own if (d["team"], d["environment"]) == (item["team"],
                                                                                           item["environment"])]}

    async def catalog_token(self, id, access, purpose):
        self.tokens.append((id, access, purpose))
        return {"accessToken": f"token-{id}-{access}", "catalog": {"internalUri": "http://polaris:8181/api/catalog"}}

    async def principal_for(self, team, environment):
        for item in self.automation_:
            if (item["team"], item["environment"]) == (team, environment):
                return item
        raise EvalError(409, "Evaluations is not enabled.", "environment_not_enabled")


class FakeAgent:
    """Conversational BI's evaluation API: a good answer for the delay question, the rest per `behaviour`."""

    def __init__(self):
        self.asked = []
        self.control_asked = []
        self.behaviour = "good"
        self.schema_only = True
        # Questions with one of these words get no answer, as under a rate limit.
        self.failing = ()
        # Conversational BI's conversation record of the team (live evaluation).
        self.recorded = []
        self.recording_ = {}
        # Every `since` a check asked for.
        self.since = []

    async def close(self):
        pass

    async def models(self, team, environment):
        return {"semanticModels": [{"model": MODEL, "name": "flights", "databaseName": "sales",
                                    "environment": environment, "shared": False}],
                "llmModels": list(LLM_MODELS), "schemaOnly": self.schema_only}

    async def ask(self, team, environment, question, llm_model=None):
        self.asked.append((team, environment, question, llm_model))
        if llm_model and llm_model not in LLM_MODELS:
            raise EvalError(422, "This LLM is not one the platform allows for evaluation.", "unknown_llm_model")
        if self.failing and any(word in question for word in self.failing):
            raise EvalError(502, "The LLM is rate limited.", "agent_error")
        usage = {"inputTokens": 1000, "outputTokens": 100, "requests": 3}
        base = {"llmModel": llm_model or "test:flights", "usage": usage, "durationMs": 1200, "traceId": "ab" * 16}
        if "carrier" in question:
            rows = DELAYS if self.behaviour == "good" else [["Example Air", 9.25]]
            figure = "16.9" if self.behaviour == "good" else "42.0"
            return {**base, "answer": f"{rows[0][0]} has the highest average departure delay, {figure} minutes.",
                    "toolCalls": [{"name": "query", "args": QUERY, "result": {"rowCount": len(rows)}}],
                    "queries": [{"spec": QUERY, "resultId": "res-" + "1" * 24, "rowCount": len(rows),
                                 "rowsShown": len(rows), "rows": rows}]}
        if "runway" in question:
            refusal = {"error": "Joining RUNWAY would repeat FLIGHT rows.", "code": "fan_out"}
            return {**base, "answer": "Splitting by runway length would count flights twice, so I can't.",
                    "toolCalls": [{"name": "query", "args": {}, "result": refusal}],
                    "queries": [{"spec": {}, **refusal}]}
        return {**base, "answer": "Do you mean departure or arrival airports?", "toolCalls": [], "queries": []}

    async def recording(self, team, environment, enabled):
        self.recording_[(team, environment)] = enabled
        return {"recording": enabled, "retentionDays": 30}

    async def conversations(self, team, environment, since, limit=200):
        self.since.append(since)
        found = [c for c in self.recorded if seconds(c["at"]) > seconds(since)][:limit]
        return {"conversations": found, "recording": self.recording_.get((team, environment), False),
                "retentionDays": 30}

    async def ask_control(self, team, environment, model, question, llm_model=None):
        """Without the semantic layer: right on the carriers, rounded by its SQL, and no refusal."""
        if not self.schema_only:
            raise EvalError(409, "The control arm is off.", "schema_only_disabled")
        self.control_asked.append((team, environment, model, question, llm_model))
        base = {"llmModel": llm_model or "test:flights",
                "usage": {"inputTokens": 500, "outputTokens": 50, "requests": 2}, "durationMs": 800,
                "traceId": "cd" * 16}
        if "carrier" in question:
            rows = [[n, round(v, 2)] for n, v in DELAYS]
            call = {"name": "run_sql", "args": {"sql": "SELECT 1"}, "result": {"rowCount": 3}}
            return {**base, "answer": "Example Cloudline, 16.92 minutes.", "toolCalls": [call],
                    "queries": [{"spec": {"sql": "SELECT 1"}, "rowCount": 3, "rowsShown": 3, "rows": rows}]}
        rows = [[7.5]]
        call = {"name": "run_sql", "args": {"sql": "SELECT 2"}, "result": {"rowCount": 1}}
        return {**base, "answer": "About 7.5.", "toolCalls": [call],
                "queries": [{"spec": {"sql": "SELECT 2"}, "rowCount": 1, "rowsShown": 1, "rows": rows}]}

    async def truth(self, team, environment, model, spec):
        return {"modelName": model["name"], "query": spec, "columns": [], "rows": DELAYS, "rowCount": 3,
                "truncated": False, "joinPaths": {}, "sql": "SELECT 1", "dataAsOf": "2026-11-24T09:15:02Z"}


class FakeVerifier:
    def __init__(self):
        # The Keycloak client the token was issued to: the MCP client for an agent, the other for a script.
        self.client = "ext-evaluations-mcp"

    async def verify(self, token):
        if token not in ("alice", "bob", "carol", "dave"):
            raise EvalError(401, "Sign in again.", "unauthorized")
        return Caller(token, "sub-" + token, token, self.client, 4102444800.0)


@pytest.fixture
def stack(tmp_path):
    bridge, agent, verifier = FakeBridge(), FakeAgent(), FakeVerifier()
    app = create_app(settings(tmp_path), bridge=bridge, verifier=verifier, agent=agent)
    with TestClient(app) as client:
        def call(method, path, user="alice", **kwargs):
            return client.request(method, "/api/v1" + path, headers={"Authorization": f"Bearer {user}"}, **kwargs)

        def wait(run_id, user="alice"):
            for _ in range(200):
                run = call("GET", f"/runs/{run_id}", user).json()
                if run["status"] not in ("queued", "running"):
                    return run
                time.sleep(0.02)
            raise AssertionError("The run did not finish")

        def create(user="alice", environment="development", yaml=YAML, name="flights_checks", **fields):
            return call("POST", "/question-sets", user, json={
                "team": TEAM, "environment": environment, "name": name, "model": MODEL, "yaml": yaml, **fields})

        def rpc(method, user, **params):
            response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                                   headers={"Authorization": f"Bearer {user}",
                                            "Accept": "application/json, text/event-stream"})
            return response.json()["result"]

        def tool(tool, user="alice", /, **arguments):
            """Calls an MCP tool as an agent does. Returns its result; a refusal raises ToolRefused with its text."""
            result = rpc("tools/call", user, name=tool, arguments=arguments)
            text = "".join(block.get("text", "") for block in result["content"])
            if result.get("isError"):
                raise ToolRefused(text)
            return json.loads(text)

        def tools():
            return {t["name"]: t for t in rpc("tools/list", "alice")["tools"]}

        yield SimpleNamespace(client=client, call=call, bridge=bridge, agent=agent, verifier=verifier, app=app,
                              wait=wait, create=create, tool=tool, tools=tools, services=app.state.services)
