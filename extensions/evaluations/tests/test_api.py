import asyncio
import json

from evaluations.api import OPERATIONS
from evaluations.config import ROOT
from evaluations.mcp_server import create_mcp
from tests.conftest import MODEL, OTHER, TEAM, YAML


def enable(stack, environment="development", user="alice", team=TEAM):
    return stack.call("POST", f"/teams/{team}/environments/{environment}", user)


def test_roles_decide_who_edits_where(stack):
    assert stack.create("carol").json()["code"] == "forbidden_role"
    assert stack.create("bob", "production").json()["code"] == "forbidden_role"
    created = stack.create("bob")
    assert created.status_code == 200 and created.json()["version"] == 1
    assert stack.create("bob").json()["code"] == "name_taken"
    assert stack.create("alice", "production").status_code == 200
    # Others don't see a team's question sets at all.
    assert stack.call("GET", "/question-sets", "dave").json()["questionSets"] == []
    assert stack.call("GET", f"/question-sets/{created.json()['id']}", "dave").json()["code"] == "unknown_question_set"


def test_cases_are_validated_and_versioned(stack):
    bad = stack.create(yaml="cases:\n- name: x\n  inputs: {question: q}\n  expected_output: {clarify: true, "
                            "refusal: fan_out}\n")
    assert bad.status_code == 422 and "exactly one" in bad.json()["error"]
    id = stack.create().json()["id"]
    changed = stack.call("POST", "/question-sets", json={"id": id, "yaml": YAML.replace("fan_out", "no_path"),
                                                          "note": "runway refusal"}).json()
    assert changed["version"] == 2 and [v["note"] for v in changed["versions"]] == ["runway refusal", "created"]
    assert "fan_out" in stack.call("GET", f"/question-sets/{id}?version=1").json()["yaml"]
    renamed = stack.call("POST", "/question-sets", json={"id": id, "description": "Flights KPIs"}).json()
    assert renamed["version"] == 2 and renamed["description"] == "Flights KPIs"


def test_import_from_questions_sql(stack):
    id = stack.create().json()["id"]
    sql = ("-- Q: Which carrier cancels most?\n"
           '-- query_semantic_model: {"metrics": ["cancellation_pct"], "dimensions": [{"field": "CARRIER.name"}]}\n'
           "SELECT 1;\n\n-- Q: A question without a query\nSELECT 2;\n")
    body = stack.call("POST", f"/question-sets/{id}/import", json={"questions_sql": sql}).json()
    assert body["imported"] == 1 and body["version"] == 2
    assert "which_carrier_cancels_most" in body["yaml"] and "cancellation_pct" in body["yaml"]
    assert stack.call("POST", f"/question-sets/{id}/import", json={"questions_sql": "SELECT 1"}).json()["code"] == \
        "invalid_questions"


def test_a_run_scores_every_case(stack):
    id = stack.create().json()["id"]
    assert stack.call("POST", f"/question-sets/{id}/runs", json={}).json()["code"] == "environment_not_enabled"
    enable(stack)
    run = stack.call("POST", f"/question-sets/{id}/runs", "bob", json={}).json()
    assert run["status"] == "queued" and run["triggered_by"] == "bob (via agent)"
    done = stack.wait(run["id"])
    assert done["status"] == "done", done["error"]
    summary = done["summary"]
    assert (summary["cases"], summary["passed"], summary["passRate"]) == (3, 3, 1.0)
    assert summary["scorers"]["result_matches"] == {"passed": 1, "total": 1}
    assert summary["scorers"]["grounded"] == {"passed": 3, "total": 3}
    assert summary["inputTokens"] == 3000
    cases = {c["name"]: c for c in done["cases"]}
    assert set(cases["delay_by_carrier"]["scores"]) == {"result_matches", "right_metrics", "grounded"}
    assert set(cases["runway_split"]["scores"]) == {"refused", "grounded"}
    assert set(cases["which_airport"]["scores"]) == {"clarified", "grounded"}
    assert cases["delay_by_carrier"]["trace_id"] == "ab" * 16
    # The agent was asked for the team, in the set's environment.
    assert {(t, e) for t, e, _, _ in stack.agent.asked} == {(TEAM, "development")}


def test_a_worse_answer_shows_in_the_comparison(stack):
    id = stack.create().json()["id"]
    enable(stack)
    good = stack.wait(stack.call("POST", f"/question-sets/{id}/runs", json={}).json()["id"])
    stack.agent.behaviour = "bad"
    started = stack.call("POST", f"/question-sets/{id}/runs", json={"llm_model": "openai:gpt-test"}).json()
    bad = stack.wait(started["id"])
    case = next(c for c in bad["cases"] if c["name"] == "delay_by_carrier")
    assert not case["passed"]
    assert case["scores"]["result_matches"]["reason"].startswith("Row 1 differs")
    assert case["scores"]["grounded"]["reason"] == "Not in any result: 42."
    compared = stack.call("GET", f"/runs/{good['id']}/compare/{bad['id']}").json()
    assert compared["change"] == {"fixed": 0, "broken": 1, "same": 2, "no_answer": 0}
    assert stack.agent.asked[-1][3] == "openai:gpt-test"


def many_cases(count):
    return "cases:\n" + "".join(f"- name: q{i}\n  inputs:\n    question: Which carrier number {i}?\n"
                                f"  expected_output: {{clarify: true}}\n" for i in range(count))


def test_a_rate_limited_run_has_no_pass_rate(stack):
    id = stack.create(yaml=many_cases(4)).json()["id"]
    enable(stack)
    good = stack.wait(stack.call("POST", f"/question-sets/{id}/runs", json={}).json()["id"])
    assert good["summary"]["reliable"] and good["summary"]["errors"] == 0
    stack.agent.failing = ("number 1?", "number 2?")
    bad = stack.wait(stack.call("POST", f"/question-sets/{id}/runs", json={}).json()["id"])
    summary = bad["status"], bad["summary"]["reliable"], bad["summary"]["passRate"], bad["summary"]["errors"]
    assert summary == ("done", False, None, 2)
    compared = stack.call("GET", f"/runs/{good['id']}/compare/{bad['id']}").json()
    assert not compared["reliable"] and compared["change"]["no_answer"] == 2 and compared["change"]["broken"] == 0


def test_a_few_unanswered_questions_stay_out_of_the_rate(stack):
    id = stack.create(yaml=many_cases(20)).json()["id"]
    enable(stack)
    stack.agent.failing = ("number 7?",)
    run = stack.wait(stack.call("POST", f"/question-sets/{id}/runs", json={}).json()["id"])
    s = run["summary"]
    # 1 of 20 is exactly the 5% allowed; the 19 answers are 'carrier' questions the fake answers with a query.
    assert (s["reliable"], s["answered"], s["errors"]) == (True, 19, 1)
    assert s["passRate"] == round(s["passed"] / 19, 4)
    assert next(c for c in run["cases"] if c["name"] == "q7")["error"].endswith("rate limited.")


def measured(stack, id, user="alice"):
    pair = stack.call("POST", f"/question-sets/{id}/measure-semantic-model", user, json={}).json()
    return stack.wait(pair["control"]["id"]), stack.wait(pair["semantic"]["id"])


def test_the_control_arm_measures_what_the_model_adds(stack):
    id = stack.create().json()["id"]
    enable(stack)
    control, semantic = measured(stack, id)
    assert (control["arm"], semantic["arm"]) == ("control", "semantic") and control["pair"] == semantic["pair"]
    # The same model reference and question reach the control arm, for the team.
    assert {(t, e, m["name"]) for t, e, m, _, _ in stack.agent.control_asked} == {(TEAM, "development", "flights")}
    by_name = {c["name"]: c for c in control["cases"]}
    assert "right_metrics" not in by_name["delay_by_carrier"]["scores"] and by_name["delay_by_carrier"]["passed"]
    assert not by_name["runway_split"]["passed"]
    compared = stack.call("GET", f"/runs/{control['id']}/compare/{semantic['id']}").json()
    assert compared["change"] == {"fixed": 2, "broken": 0, "same": 1, "no_answer": 0}
    uplift = compared["uplift"]
    assert (uplift["questions"], uplift["control"], uplift["semantic"], uplift["points"]) == (3, 0.3333, 1.0, 66.7)
    assert uplift["byKind"]["query"] == {"questions": 1, "control": 1.0, "semantic": 1.0, "points": 0.0}
    assert uplift["byKind"]["refusal"]["points"] == 100.0


def test_measuring_follows_the_roles_and_the_switch(stack):
    id = stack.create().json()["id"]
    enable(stack)
    assert stack.call("POST", f"/question-sets/{id}/measure-semantic-model", "carol", json={}).json()["code"] == \
        "forbidden_role"
    stack.agent.schema_only = False
    refused = stack.call("POST", f"/question-sets/{id}/measure-semantic-model", json={})
    assert (refused.status_code, refused.json()["code"]) == (409, "schema_only_disabled")
    assert stack.call("GET", f"/question-sets/{id}/runs").json()["runs"] == []


def test_no_uplift_from_a_run_that_got_too_few_answers(stack):
    id = stack.create().json()["id"]
    enable(stack)
    stack.agent.failing = ("carrier", "runway")
    control, semantic = measured(stack, id)
    compared = stack.call("GET", f"/runs/{control['id']}/compare/{semantic['id']}").json()
    assert semantic["summary"]["reliable"] is False and not compared["reliable"] and "uplift" not in compared


def test_compare_models_starts_one_run_each(stack):
    id = stack.create().json()["id"]
    enable(stack)
    assert stack.call("POST", f"/question-sets/{id}/compare-models",
                      json={"llm_models": ["test:flights"]}).status_code == 422
    runs = stack.call("POST", f"/question-sets/{id}/compare-models",
                      json={"llm_models": ["test:flights", "openai:gpt-test"]}).json()["runs"]
    assert [r["llm_model"] for r in runs] == ["test:flights", "openai:gpt-test"]
    for run in runs:
        assert stack.wait(run["id"])["status"] == "done"


def test_schedules_are_for_administrators(stack):
    id = stack.create().json()["id"]
    enable(stack)
    assert stack.call("POST", f"/question-sets/{id}/schedules", "bob", json={"cron": "0 6 * * 1"}).json()["code"] == \
        "forbidden_role"
    assert stack.call("POST", f"/question-sets/{id}/schedules", json={"cron": "every day"}).json()["code"] == \
        "invalid_cron"
    schedule = stack.call("POST", f"/question-sets/{id}/schedules", json={"cron": "0 6 * * 1"}).json()
    services = stack.app.state.services
    services.store.update_schedule(schedule["id"], next_run_at=0)
    [run_id] = stack.client.portal.call(stack.app.state.scheduler.tick)
    assert stack.wait(run_id)["triggered_by"] == f"schedule {schedule['id']}"
    assert stack.call("DELETE", f"/question-sets/{id}/schedules/{schedule['id']}").json() == {"deleted": True}


def test_agent_models_need_an_enabled_environment(stack):
    assert stack.call("GET", f"/teams/{TEAM}/environments/development/agent-models").json()["code"] == \
        "environment_not_enabled"
    enable(stack)
    body = stack.call("GET", f"/teams/{TEAM}/environments/development/agent-models").json()
    assert body["models"][0]["model"] == MODEL
    assert stack.call("GET", f"/teams/{OTHER}/environments/development/agent-models").json()["code"] == \
        "not_a_member"


def test_browser_writes_need_the_csrf_header(stack):
    response = stack.client.post("/api/v1/question-sets", json={})
    assert response.status_code in (401, 403)


def test_every_operation_is_an_mcp_tool(stack):
    schema = stack.client.get("/api/v1/openapi.json").json()
    operations = {op["operationId"] for path in schema["paths"].values() for op in path.values()}
    assert operations == set(OPERATIONS)
    mcp = create_mcp(stack.app.state.services.settings, stack.app.state.services, None)
    assert {t.name for t in asyncio.run(mcp.list_tools())} == set(OPERATIONS.values())


def test_committed_contract_matches_the_implementation(stack):
    committed = ROOT / "contracts" / "evaluations-api" / "v1" / "openapi.yaml"
    assert json.loads(committed.read_text()) == stack.client.get("/api/v1/openapi.json").json(), (
        "Regenerate with `uv run python -m scripts.api_contract`")
