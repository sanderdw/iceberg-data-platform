import asyncio
import json

import pytest

from evaluations.api import OPERATIONS
from evaluations.config import ROOT
from evaluations.errors import EvalError
from evaluations.mcp_server import create_mcp
from tests.conftest import DB, MODEL, OTHER, TEAM, TIMESTAMP, YAML, ToolRefused, seconds

SET_FIELDS = {"id", "team", "environment", "name", "description", "agent", "model", "rubric", "targetDatabase",
              "version", "createdAt", "createdBy", "updatedAt", "updatedBy", "role"}
DETAIL_FIELDS = SET_FIELDS | {"yaml", "shownVersion", "versions", "schedules", "runs"}
RUN_FIELDS = {"id", "questionSet", "version", "team", "environment", "llmModel", "status", "triggeredBy", "agent",
              "scheduled", "schedule", "createdAt", "startedAt", "finishedAt", "summary", "error", "arm", "pair"}
CASE_FIELDS = {"name", "question", "kind", "tags", "passed", "scores", "answer", "queries", "toolCalls", "llmModel",
               "inputTokens", "outputTokens", "durationMs", "traceId", "error"}
SCHEDULE_FIELDS = {"id", "questionSet", "cron", "llmModel", "enabled", "nextRunAt", "lastRun", "createdBy",
                   "createdAt"}
ENVIRONMENT = {"team": TEAM, "environment": "development", "enabled": True, "enabledBy": "alice",
               "databases": [{"id": DB, "name": "sales"}]}


def enable(stack, environment="development", user="alice", team=TEAM):
    return stack.call("POST", f"/teams/{team}/environments/{environment}", user)


def refused(response, status, code):
    body = response.json()
    assert (response.status_code, body["code"]) == (status, code), response.text
    assert isinstance(body["error"], str) and body["error"] and set(body) <= {"error", "code", "detail"}


def started(stack, id, user="alice", **body):
    response = stack.call("POST", f"/question-sets/{id}/runs", user, json=body)
    assert response.status_code == 202, response.text
    return response.json()


def test_whoami_and_environments(stack):
    me = stack.call("GET", "/me", "bob").json()
    # Exactly what this extension shows: not the Bridge's answer with keys added.
    assert set(me) == {"user", "memberships", "databases", "extension", "origin", "environments",
                       "enabledEnvironments", "userPortalUrl", "apps", "agent", "judge"}
    assert me["user"] == {"id": "portal-bob", "name": "bob"}
    assert me["memberships"] == [{"team": TEAM, "teamName": TEAM[:9], "role": "writer"}]
    assert me["databases"] == [{"id": DB, "name": "sales", "team": TEAM, "environment": "development",
                                "status": "ready"}]
    assert (me["extension"], me["agent"], me["judge"]) == ("evaluations", "conversationalbi", False)
    assert me["enabledEnvironments"] == [{"team": TEAM, "environments": []}]

    listed = stack.call("GET", f"/teams/{TEAM}/environments", "carol").json()
    assert list(listed) == ["environments"] and [e["environment"] for e in listed["environments"]] == [
        "development", "acceptance", "production"]
    assert listed["environments"][0] == {**ENVIRONMENT, "enabled": False, "enabledBy": None}
    assert listed["environments"][1]["databases"] == []
    refused(stack.call("GET", f"/teams/{OTHER}/environments"), 403, "not_a_member")

    refused(enable(stack, user="bob"), 403, "forbidden_role")
    enabled = enable(stack)
    # The environment, not the service account the Bridge made for it.
    assert enabled.status_code == 200 and enabled.json() == ENVIRONMENT
    assert stack.call("GET", f"/teams/{TEAM}/environments").json()["environments"][0] == ENVIRONMENT
    assert stack.call("GET", "/me").json()["enabledEnvironments"] == [{"team": TEAM, "environments": ["development"]}]

    refused(stack.call("DELETE", f"/teams/{TEAM}/environments/development", "bob"), 403, "forbidden_role")
    disabled = stack.call("DELETE", f"/teams/{TEAM}/environments/development")
    assert disabled.status_code == 200 and disabled.json() == {**ENVIRONMENT, "enabled": False, "enabledBy": None}
    refused(stack.call("DELETE", f"/teams/{TEAM}/environments/development"), 409, "environment_not_enabled")


def test_roles_decide_who_edits_where(stack):
    refused(stack.create("carol"), 403, "forbidden_role")
    refused(stack.create("bob", "production"), 403, "forbidden_role")
    refused(stack.create("dave"), 403, "not_a_member")
    created = stack.create("bob")
    assert created.status_code == 201 and created.json()["version"] == 1
    refused(stack.create("bob"), 409, "name_taken")
    assert stack.create("alice", "production").status_code == 201
    # Others don't see a team's question sets at all.
    assert stack.call("GET", "/question-sets", "dave").json() == {"questionSets": []}
    refused(stack.call("GET", f"/question-sets/{created.json()['id']}", "dave"), 404, "unknown_question_set")


def test_a_question_set_is_shown_field_by_field(stack):
    created = stack.create("bob", description="Flights KPIs", rubric="Be brief.", targetDatabase=DB).json()
    assert set(created) == DETAIL_FIELDS
    assert (created["model"], created["agent"], created["role"]) == (MODEL, "conversationalbi", "writer")
    assert (created["targetDatabase"], created["createdBy"]) == (DB, "bob (via agent)")
    assert TIMESTAMP.match(created["createdAt"]) and created["createdAt"] == created["updatedAt"]
    assert created["versions"] == [{"version": 1, "cases": 3, "note": "created", "createdAt": created["createdAt"],
                                    "createdBy": "bob (via agent)"}]
    assert (created["shownVersion"], created["schedules"], created["runs"]) == (1, [], [])
    # Without a target database there is none, not an empty text.
    plain = stack.create(name="plain").json()
    assert plain["targetDatabase"] is None
    [first, second] = stack.call("GET", "/question-sets", "carol").json()["questionSets"]
    assert set(first) == SET_FIELDS | {"lastRun"} and first["lastRun"] is None and first["role"] == "reader"
    assert {first["name"], second["name"]} == {"flights_checks", "plain"}


def test_cases_are_validated_and_versioned(stack):
    bad = stack.create(yaml="cases:\n- name: x\n  inputs: {question: q}\n  expected_output: {clarify: true, "
                            "refusal: fan_out}\n")
    assert bad.status_code == 422 and "exactly one" in bad.json()["error"]
    refused(stack.create(yaml="cases: []\n"), 422, "invalid_request")
    # A new one needs a team, a name, a model and cases: each is required.
    for missing in ("team", "name", "model", "yaml"):
        body = {"team": TEAM, "name": "flights_checks", "model": MODEL, "yaml": YAML}
        del body[missing]
        refused(stack.call("POST", "/question-sets", json=body), 422, "invalid_request")
    id = stack.create().json()["id"]
    # Creating and changing are two operations: POST no longer changes one, and takes no snake_case.
    refused(stack.call("POST", "/question-sets", json={"id": id, "description": "x"}), 422, "invalid_request")
    refused(stack.call("POST", "/question-sets", json={"team": TEAM, "name": "other_set", "model": MODEL,
                                                       "yaml": YAML, "target_database": DB}), 422, "invalid_request")
    changed = stack.call("PATCH", f"/question-sets/{id}", json={"yaml": YAML.replace("fan_out", "no_path"),
                                                                 "note": "runway refusal"})
    assert changed.status_code == 200 and set(changed.json()) == DETAIL_FIELDS
    changed = changed.json()
    assert changed["version"] == 2 and [v["note"] for v in changed["versions"]] == ["runway refusal", "created"]
    assert "fan_out" in stack.call("GET", f"/question-sets/{id}?version=1").json()["yaml"]
    refused(stack.call("GET", f"/question-sets/{id}?version=9"), 404, "unknown_version")
    # What is left out stays.
    described = stack.call("PATCH", f"/question-sets/{id}", json={"description": "Flights KPIs",
                                                                   "targetDatabase": DB}).json()
    assert (described["version"], described["description"], described["targetDatabase"]) == (2, "Flights KPIs", DB)
    renamed = stack.call("PATCH", f"/question-sets/{id}", "bob", json={"name": "flights_kpis",
                                                                       "targetDatabase": ""}).json()
    assert (renamed["name"], renamed["description"], renamed["targetDatabase"]) == ("flights_kpis", "Flights KPIs",
                                                                                    None)
    assert renamed["updatedBy"] == "bob (via agent)" and "no_path" in renamed["yaml"]
    refused(stack.call("PATCH", f"/question-sets/{id}", json={"yaml": "cases: []\n"}), 422, "invalid_request")
    refused(stack.call("PATCH", f"/question-sets/{id}", json={"target_database": DB}), 422, "invalid_request")
    refused(stack.call("PATCH", f"/question-sets/{id}", "carol", json={"description": "x"}), 403, "forbidden_role")
    stack.create(name="second_set")
    refused(stack.call("PATCH", f"/question-sets/{id}", json={"name": "second_set"}), 409, "name_taken")


def test_import_from_questions_sql(stack):
    id = stack.create().json()["id"]
    sql = ("-- Q: Which carrier cancels most?\n"
           '-- query_semantic_model: {"metrics": ["cancellation_pct"], "dimensions": [{"field": "CARRIER.name"}]}\n'
           "SELECT 1;\n\n-- Q: A question without a query\nSELECT 2;\n")
    refused(stack.call("POST", f"/question-sets/{id}/import", json={"questions_sql": sql}), 422, "invalid_request")
    body = stack.call("POST", f"/question-sets/{id}/import", json={"questionsSql": sql}).json()
    assert set(body) == DETAIL_FIELDS | {"imported"} and body["imported"] == 1 and body["version"] == 2
    assert "which_carrier_cancels_most" in body["yaml"] and "cancellation_pct" in body["yaml"]
    refused(stack.call("POST", f"/question-sets/{id}/import", json={"questionsSql": "SELECT 1"}), 422,
            "invalid_questions")


def test_delete_needs_the_name(stack):
    id = stack.create().json()["id"]
    refused(stack.call("DELETE", f"/question-sets/{id}"), 422, "invalid_request")
    refused(stack.call("DELETE", f"/question-sets/{id}?confirmName=flights"), 422, "confirmation_mismatch")
    refused(stack.call("DELETE", f"/question-sets/{id}?confirmName=flights_checks", "carol"), 403, "forbidden_role")
    # A run that is still going keeps its question set.
    run = stack.services.store.create_run(id, "alice")
    refused(stack.call("DELETE", f"/question-sets/{id}?confirmName=flights_checks"), 409, "run_in_progress")
    stack.services.store.update_run(run["id"], status="error")
    assert stack.call("GET", f"/question-sets/{id}").status_code == 200
    # In development a writer may, as before; elsewhere only an administrator.
    deleted = stack.call("DELETE", f"/question-sets/{id}?confirmName=flights_checks", "bob")
    assert deleted.status_code == 200 and deleted.json() == {
        "deleted": True, "questionSet": id, "note": "Results already written to the team's database stay there."}
    refused(stack.call("GET", f"/question-sets/{id}"), 404, "unknown_question_set")
    production = stack.create(environment="production").json()["id"]
    refused(stack.call("DELETE", f"/question-sets/{production}?confirmName=flights_checks", "bob"), 403,
            "forbidden_role")


def test_a_run_scores_every_case(stack):
    id = stack.create().json()["id"]
    refused(stack.call("POST", f"/question-sets/{id}/runs", json={}), 409, "environment_not_enabled")
    enable(stack)
    run = started(stack, id, "bob")
    assert set(run) == RUN_FIELDS
    # Who started it and through what are two fields; the stored text holds both.
    assert (run["status"], run["triggeredBy"], run["agent"]) == ("queued", "bob", "ext-evaluations-mcp")
    assert (run["questionSet"], run["llmModel"], run["scheduled"], run["schedule"]) == (id, None, False, None)
    assert (run["arm"], run["pair"], run["summary"], run["error"]) == ("semantic", None, {}, None)
    assert TIMESTAMP.match(run["createdAt"]) and run["startedAt"] is None and run["finishedAt"] is None
    done = stack.wait(run["id"])
    # A finished run is `success`, as in the other extensions; it is stored as `done`.
    assert done["status"] == "success", done["error"]
    assert stack.services.store.run(run["id"])["status"] == "done"
    assert set(done) == RUN_FIELDS | {"questionSetName", "cases"} and done["questionSetName"] == "flights_checks"
    assert seconds(done["createdAt"]) <= seconds(done["startedAt"]) <= seconds(done["finishedAt"])
    summary = done["summary"]
    assert (summary["cases"], summary["passed"], summary["passRate"]) == (3, 3, 1.0)
    assert summary["scorers"]["result_matches"] == {"passed": 1, "total": 1}
    assert summary["scorers"]["grounded"] == {"passed": 3, "total": 3}
    assert summary["inputTokens"] == 3000
    # The LLMs that answered: `model` always means a semantic model. No target database, so nothing published.
    assert summary["llmModels"] == ["test:flights"] and "models" not in summary and summary["published"] is None
    cases = {c["name"]: c for c in done["cases"]}
    assert all(set(c) == CASE_FIELDS for c in cases.values())
    assert set(cases["delay_by_carrier"]["scores"]) == {"result_matches", "right_metrics", "grounded"}
    assert set(cases["runway_split"]["scores"]) == {"refused", "grounded"}
    assert set(cases["which_airport"]["scores"]) == {"clarified", "grounded"}
    delay = cases["delay_by_carrier"]
    assert (delay["traceId"], delay["llmModel"], delay["error"]) == ("ab" * 16, "test:flights", None)
    assert (delay["inputTokens"], delay["outputTokens"], delay["durationMs"]) == (1000, 100, 1200)
    assert delay["toolCalls"][0]["name"] == "query" and delay["queries"][0]["rowCount"] == 3
    # The agent was asked for the team, in the set's environment.
    assert {(t, e) for t, e, _, _ in stack.agent.asked} == {(TEAM, "development")}
    # From a script, with a token of the extension's other client, no agent is named.
    stack.verifier.client = "ext-evaluations"
    script = started(stack, id, "bob")
    assert (script["triggeredBy"], script["agent"]) == ("bob", None)
    listed = stack.call("GET", "/question-sets").json()["questionSets"][0]["lastRun"]
    assert set(listed) == RUN_FIELDS and listed["id"] == script["id"]


def test_a_worse_answer_shows_in_the_comparison(stack):
    id = stack.create().json()["id"]
    enable(stack)
    good = stack.wait(started(stack, id)["id"])
    stack.agent.behaviour = "bad"
    refused(stack.call("POST", f"/question-sets/{id}/runs", json={"llm_model": "openai:gpt-test"}), 422,
            "invalid_request")
    run = started(stack, id, llmModel="openai:gpt-test")
    assert run["llmModel"] == "openai:gpt-test"
    bad = stack.wait(run["id"])
    case = next(c for c in bad["cases"] if c["name"] == "delay_by_carrier")
    assert not case["passed"] and case["llmModel"] == "openai:gpt-test"
    assert case["scores"]["result_matches"]["reason"].startswith("Row 1 differs")
    assert case["scores"]["grounded"]["reason"] == "Not in any result: 42."
    compared = stack.call("GET", f"/runs/{good['id']}/compare/{bad['id']}").json()
    assert set(compared) == {"baseline", "candidate", "reliable", "change", "cases"}
    # The counts are fields; what happened to one case is a value.
    assert compared["change"] == {"fixed": 0, "broken": 1, "same": 2, "noAnswer": 0}
    assert set(compared["baseline"]) == set(compared["candidate"]) == RUN_FIELDS
    assert (compared["baseline"]["llmModel"], compared["candidate"]["llmModel"]) == (None, "openai:gpt-test")
    broken = next(c for c in compared["cases"] if c["change"] == "broken")
    assert broken["name"] == "delay_by_carrier" and set(broken["baseline"]) == {"passed", "scores", "answer"}
    assert stack.agent.asked[-1][3] == "openai:gpt-test"
    # Runs of other teams are as unknown as runs that are not there.
    refused(stack.call("GET", f"/runs/{good['id']}", "dave"), 404, "unknown_run")
    refused(stack.call("GET", f"/runs/{good['id']}/compare/{bad['id']}", "dave"), 404, "unknown_run")


def test_an_llm_the_platform_does_not_allow_ends_the_run(stack):
    id = stack.create().json()["id"]
    enable(stack)
    run = stack.wait(started(stack, id, llmModel="unknown:llm")["id"])
    # Conversational BI refuses with unknown_llm_model; no question got an answer, so the run says why once.
    assert (run["status"], run["summary"], run["cases"]) == ("error", {}, [])
    assert "not one the platform allows" in run["error"]


def many_cases(count):
    return "cases:\n" + "".join(f"- name: q{i}\n  inputs:\n    question: Which carrier number {i}?\n"
                                f"  expected_output: {{clarify: true}}\n" for i in range(count))


def test_a_rate_limited_run_has_no_pass_rate(stack):
    id = stack.create(yaml=many_cases(4)).json()["id"]
    enable(stack)
    good = stack.wait(started(stack, id)["id"])
    assert good["summary"]["reliable"] and good["summary"]["errors"] == 0
    stack.agent.failing = ("number 1?", "number 2?")
    bad = stack.wait(started(stack, id)["id"])
    summary = bad["status"], bad["summary"]["reliable"], bad["summary"]["passRate"], bad["summary"]["errors"]
    assert summary == ("success", False, None, 2)
    compared = stack.call("GET", f"/runs/{good['id']}/compare/{bad['id']}").json()
    assert not compared["reliable"] and compared["change"]["noAnswer"] == 2 and compared["change"]["broken"] == 0
    assert sorted(c["name"] for c in compared["cases"] if c["change"] == "no_answer") == ["q1", "q2"]


def test_a_few_unanswered_questions_stay_out_of_the_rate(stack):
    id = stack.create(yaml=many_cases(20)).json()["id"]
    enable(stack)
    stack.agent.failing = ("number 7?",)
    run = stack.wait(started(stack, id)["id"])
    s = run["summary"]
    # 1 of 20 is exactly the 5% allowed; the 19 answers are 'carrier' questions the fake answers with a query.
    assert (s["reliable"], s["answered"], s["errors"]) == (True, 19, 1)
    assert s["passRate"] == round(s["passed"] / 19, 4)
    unanswered = next(c for c in run["cases"] if c["name"] == "q7")
    assert unanswered["error"].endswith("rate limited.") and unanswered["llmModel"] is None


def test_runs_are_listed_newest_first_up_to_a_limit(stack):
    id = stack.create().json()["id"]
    enable(stack)
    ids = [stack.wait(started(stack, id)["id"])["id"] for _ in range(3)]
    listed = stack.call("GET", f"/question-sets/{id}/runs").json()
    assert list(listed) == ["runs"] and [r["id"] for r in listed["runs"]] == ids[::-1]
    assert all(set(r) == RUN_FIELDS for r in listed["runs"])
    assert [r["id"] for r in stack.call("GET", f"/question-sets/{id}/runs?limit=2").json()["runs"]] == ids[:0:-1]
    # Outside its range a limit is refused, never quietly lowered.
    for limit in (0, 201):
        refused(stack.call("GET", f"/question-sets/{id}/runs?limit={limit}"), 422, "invalid_request")


def measured(stack, id, user="alice"):
    response = stack.call("POST", f"/question-sets/{id}/measure-semantic-model", user, json={})
    assert response.status_code == 202, response.text
    pair = response.json()
    assert set(pair) == {"pair", "control", "semantic"}
    assert set(pair["control"]) == set(pair["semantic"]) == RUN_FIELDS
    assert pair["control"]["pair"] == pair["semantic"]["pair"] == pair["pair"]
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
    assert compared["change"] == {"fixed": 2, "broken": 0, "same": 1, "noAnswer": 0}
    uplift = compared["uplift"]
    assert (uplift["questions"], uplift["control"], uplift["semantic"], uplift["points"]) == (3, 0.3333, 1.0, 66.7)
    assert uplift["byKind"]["query"] == {"questions": 1, "control": 1.0, "semantic": 1.0, "points": 0.0}
    assert uplift["byKind"]["refusal"]["points"] == 100.0


def test_measuring_follows_the_roles_and_the_switch(stack):
    id = stack.create().json()["id"]
    enable(stack)
    refused(stack.call("POST", f"/question-sets/{id}/measure-semantic-model", "carol", json={}), 403,
            "forbidden_role")
    stack.agent.schema_only = False
    refused(stack.call("POST", f"/question-sets/{id}/measure-semantic-model", json={}), 409, "schema_only_disabled")
    assert stack.call("GET", f"/question-sets/{id}/runs").json()["runs"] == []


def test_no_uplift_from_a_run_that_got_too_few_answers(stack):
    id = stack.create().json()["id"]
    enable(stack)
    stack.agent.failing = ("carrier", "runway")
    control, semantic = measured(stack, id)
    compared = stack.call("GET", f"/runs/{control['id']}/compare/{semantic['id']}").json()
    assert semantic["summary"]["reliable"] is False and not compared["reliable"] and "uplift" not in compared


def test_compare_llm_models_starts_one_run_each(stack):
    id = stack.create().json()["id"]
    enable(stack)
    refused(stack.call("POST", f"/question-sets/{id}/compare-models", json={"llmModels": ["a", "b"]}), 404,
            "not_found")
    refused(stack.call("POST", f"/question-sets/{id}/compare-llm-models", json={"llmModels": ["test:flights"]}),
            422, "invalid_request")
    refused(stack.call("POST", f"/question-sets/{id}/compare-llm-models",
                       json={"llm_models": ["test:flights", "openai:gpt-test"]}), 422, "invalid_request")
    response = stack.call("POST", f"/question-sets/{id}/compare-llm-models",
                          json={"llmModels": ["test:flights", "openai:gpt-test"]})
    assert response.status_code == 202 and list(response.json()) == ["runs"]
    runs = response.json()["runs"]
    assert [r["llmModel"] for r in runs] == ["test:flights", "openai:gpt-test"]
    assert all(set(r) == RUN_FIELDS for r in runs)
    for run in runs:
        assert stack.wait(run["id"])["status"] == "success"


def test_schedules_are_for_administrators(stack):
    id = stack.create().json()["id"]
    enable(stack)
    refused(stack.call("POST", f"/question-sets/{id}/schedules", "bob", json={"cron": "0 6 * * 1"}), 403,
            "forbidden_role")
    refused(stack.call("POST", f"/question-sets/{id}/schedules", json={"cron": "every day"}), 422, "invalid_cron")
    # At most every 5 minutes, as everywhere on the platform.
    refused(stack.call("POST", f"/question-sets/{id}/schedules", json={"cron": "*/2 * * * *"}), 422, "invalid_cron")
    refused(stack.call("POST", f"/question-sets/{id}/schedules", json={"cron": "0 6 * * 1", "llm_model": "x"}), 422,
            "invalid_request")
    created = stack.call("POST", f"/question-sets/{id}/schedules", json={"cron": "*/5 * * * *"})
    assert created.status_code == 201
    schedule = created.json()
    assert set(schedule) == SCHEDULE_FIELDS
    assert (schedule["questionSet"], schedule["cron"], schedule["llmModel"]) == (id, "*/5 * * * *", None)
    assert (schedule["enabled"], schedule["lastRun"], schedule["createdBy"]) == (True, None, "alice (via agent)")
    assert seconds(schedule["nextRunAt"]) > seconds(schedule["createdAt"])
    assert stack.call("GET", f"/question-sets/{id}/schedules", "carol").json() == {"schedules": [schedule]}
    assert stack.call("GET", f"/question-sets/{id}").json()["schedules"] == [schedule]

    store = stack.services.store
    store.update_schedule(schedule["id"], next_run_at=0)
    [run_id] = stack.client.portal.call(stack.app.state.scheduler.tick)
    run = stack.wait(run_id)
    # Stored as "schedule sch-…": the response says that a schedule started it, and which.
    assert store.run(run_id)["triggered_by"] == f"schedule {schedule['id']}"
    assert (run["triggeredBy"], run["agent"], run["scheduled"], run["schedule"]) == (
        "schedule", None, True, schedule["id"])
    assert stack.call("GET", f"/question-sets/{id}/schedules").json()["schedules"][0]["lastRun"] == run_id

    # A change keeps what it leaves out; a schedule that is off has no next run.
    path = f"/schedules/{schedule['id']}"
    refused(stack.call("POST", f"/question-sets/{id}/schedules?schedule_id={schedule['id']}",
                       json={"cron": "*/1 * * * *"}), 422, "invalid_cron")
    refused(stack.call("PATCH", path, "bob", json={"enabled": False}), 403, "forbidden_role")
    refused(stack.call("PATCH", path, "dave", json={"enabled": False}), 404, "unknown_schedule")
    refused(stack.call("PATCH", path, json={"cron": "* * * * *"}), 422, "invalid_cron")
    off = stack.call("PATCH", path, json={"enabled": False, "llmModel": "openai:gpt-test"})
    assert off.status_code == 200 and off.json() == {
        **schedule, "enabled": False, "nextRunAt": None, "llmModel": "openai:gpt-test", "lastRun": run_id}
    moved = stack.call("PATCH", path, json={"cron": "0 6 * * 1", "enabled": True}).json()
    assert (moved["cron"], moved["llmModel"], moved["enabled"]) == ("0 6 * * 1", "openai:gpt-test", True)
    assert seconds(moved["nextRunAt"]) > seconds(schedule["createdAt"])
    assert stack.call("PATCH", path, json={"llmModel": ""}).json()["llmModel"] is None

    # A schedule is addressed by its own id, without its question set.
    refused(stack.call("DELETE", f"/question-sets/{id}/schedules/{schedule['id']}"), 404, "not_found")
    refused(stack.call("DELETE", path, "bob"), 403, "forbidden_role")
    deleted = stack.call("DELETE", path)
    assert deleted.status_code == 200 and deleted.json() == {"deleted": True, "schedule": schedule["id"]}
    refused(stack.call("DELETE", path), 404, "unknown_schedule")
    assert stack.call("GET", f"/question-sets/{id}/schedules").json() == {"schedules": []}


def test_agent_models_need_an_enabled_environment(stack):
    refused(stack.call("GET", f"/teams/{TEAM}/environments/development/agent-models"), 409,
            "environment_not_enabled")
    enable(stack)
    body = stack.call("GET", f"/teams/{TEAM}/environments/development/agent-models").json()
    assert body == {"semanticModels": [{"model": MODEL, "name": "flights", "databaseName": "sales",
                                        "environment": "development", "shared": False}],
                    "llmModels": ["test:flights", "openai:gpt-test"], "schemaOnly": True}
    refused(stack.call("GET", f"/teams/{OTHER}/environments/development/agent-models"), 403, "not_a_member")


def test_browser_writes_need_the_csrf_header(stack):
    response = stack.client.post("/api/v1/question-sets", json={})
    assert response.status_code in (401, 403)


def test_every_error_has_the_same_body(stack, monkeypatch):
    refused(stack.call("GET", "/nothing-here"), 404, "not_found")
    refused(stack.client.get("/nothing/here"), 404, "not_found")
    refused(stack.call("PUT", "/me"), 405, "method_not_allowed")
    # Live evaluation is changed with PATCH, and a review too.
    refused(stack.call("PUT", f"/teams/{TEAM}/environments/development/live", json={"enabled": True}), 405,
            "method_not_allowed")
    refused(stack.call("POST", "/reviews/rev-" + "0" * 24, json={"action": "dismiss"}), 405, "method_not_allowed")
    refused(stack.call("GET", "/me", "mallory"), 401, "unauthorized")
    refused(stack.call("GET", f"/teams/{TEAM}/environments/test/live"), 422, "invalid_request")
    refused(stack.call("GET", "/question-sets/qs-" + "0" * 24), 404, "unknown_question_set")
    refused(stack.call("GET", "/runs/run-" + "0" * 24), 404, "unknown_run")
    refused(stack.call("PATCH", "/schedules/sch-" + "0" * 24, json={}), 404, "unknown_schedule")
    refused(stack.call("PATCH", "/reviews/rev-" + "0" * 24, json={"status": "open"}), 404, "unknown_review")

    async def broken(caller):
        raise RuntimeError("boom")
    monkeypatch.setattr(stack.services, "whoami", broken)
    refused(stack.call("GET", "/me"), 500, "internal_error")
    assert EvalError(418, "x").code == "internal_error" and EvalError(405, "x").code == "method_not_allowed"
    assert (EvalError(500, "x").code, EvalError(504, "x").code) == ("internal_error", "timeout")


def test_tools_return_what_the_api_returns(stack):
    """An MCP tool gives the REST operation's body, unchanged and without a wrapper of its own."""
    created = stack.tool("create_question_set", "bob", team=TEAM, name="flights_checks", model=MODEL, yaml=YAML,
                         description="Flights KPIs", target_database=DB)
    id = created["id"]
    assert created == stack.call("GET", f"/question-sets/{id}", "bob").json()
    assert (created["targetDatabase"], created["environment"]) == (DB, "development")
    changed = stack.tool("update_question_set", question_set=id, rubric="Be brief.", target_database="")
    assert (changed["rubric"], changed["description"], changed["targetDatabase"]) == ("Be brief.", "Flights KPIs",
                                                                                      None)
    imported = stack.tool("import_questions", question_set=id, questions_sql=(
        '-- Q: Which carrier cancels most?\n-- query_semantic_model: {"metrics": ["cancellation_pct"]}\nSELECT 1;'))
    assert imported["imported"] == 1 and imported["version"] == 2
    with pytest.raises(ToolRefused, match=r"\[environment_not_enabled\]"):
        stack.tool("run_evaluation", question_set=id)
    environment = stack.tool("enable_environment", team=TEAM, environment="development")
    assert environment == ENVIRONMENT
    run = stack.tool("run_evaluation", "bob", question_set=id, llm_model="openai:gpt-test")
    assert set(run) == RUN_FIELDS and (run["llmModel"], run["triggeredBy"]) == ("openai:gpt-test", "bob")
    done = stack.wait(run["id"])
    compared = stack.tool("compare_llm_models", question_set=id, llm_models=["test:flights", "openai:gpt-test"])
    assert list(compared) == ["runs"] and [r["llmModel"] for r in compared["runs"]] == ["test:flights",
                                                                                         "openai:gpt-test"]
    pair = stack.tool("measure_semantic_model", question_set=id)
    for started_run in (*compared["runs"], pair["control"], pair["semantic"]):
        stack.wait(started_run["id"])
    schedule = stack.tool("create_schedule", question_set=id, cron="0 6 * * 1", llm_model="test:flights")
    assert set(schedule) == SCHEDULE_FIELDS and schedule["llmModel"] == "test:flights"
    assert stack.tool("update_schedule", schedule=schedule["id"], enabled=False) == {
        **schedule, "enabled": False, "nextRunAt": None}
    for tool, arguments, path in (
            ("whoami", {}, "/me"),
            ("list_environments", {"team": TEAM}, f"/teams/{TEAM}/environments"),
            ("list_agent_models", {"team": TEAM, "environment": "development"},
             f"/teams/{TEAM}/environments/development/agent-models"),
            ("list_question_sets", {}, "/question-sets"),
            ("get_question_set", {"question_set": id}, f"/question-sets/{id}"),
            ("get_question_set", {"question_set": id, "version": 1}, f"/question-sets/{id}?version=1"),
            ("list_runs", {"question_set": id}, f"/question-sets/{id}/runs"),
            ("list_runs", {"question_set": id, "limit": 1}, f"/question-sets/{id}/runs?limit=1"),
            ("get_run", {"run": run["id"]}, f"/runs/{run['id']}"),
            ("compare_runs", {"baseline": run["id"], "candidate": compared["runs"][0]["id"]},
             f"/runs/{run['id']}/compare/{compared['runs'][0]['id']}"),
            ("compare_runs", {"baseline": pair["control"]["id"], "candidate": pair["semantic"]["id"]},
             f"/runs/{pair['control']['id']}/compare/{pair['semantic']['id']}"),
            ("list_schedules", {"question_set": id}, f"/question-sets/{id}/schedules"),
            ("get_live_evaluation", {"team": TEAM, "environment": "development"},
             f"/teams/{TEAM}/environments/development/live"),
            ("list_reviews", {"team": TEAM, "environment": "development"},
             f"/teams/{TEAM}/environments/development/reviews"),
            ("list_reviews", {"team": TEAM, "environment": "development", "status": "open", "limit": 5},
             f"/teams/{TEAM}/environments/development/reviews?status=open&limit=5")):
        assert stack.tool(tool, "carol", **arguments) == stack.call("GET", path, "carol").json(), tool
    assert stack.tool("get_run", run=run["id"]) == done
    assert list(stack.tool("list_environments", team=TEAM)) == ["environments"]
    assert stack.tool("delete_schedule", schedule=schedule["id"]) == {"deleted": True, "schedule": schedule["id"]}
    with pytest.raises(ToolRefused, match=r"\[unknown_schedule\]"):
        stack.tool("delete_schedule", schedule=schedule["id"])
    with pytest.raises(ToolRefused, match=r"\[invalid_cron\]"):
        stack.tool("create_schedule", question_set=id, cron="* * * * *")
    with pytest.raises(ToolRefused, match=r"\[confirmation_mismatch\]"):
        stack.tool("delete_question_set", question_set=id, confirm_name="nope")
    deleted = stack.tool("delete_question_set", question_set=id, confirm_name="flights_checks")
    assert deleted["deleted"] is True and deleted["questionSet"] == id
    disabled = stack.tool("disable_environment", team=TEAM, environment="development")
    assert disabled == {**ENVIRONMENT, "enabled": False, "enabledBy": None}
    with pytest.raises(ToolRefused, match=r"\[environment_not_enabled\]"):
        stack.tool("disable_environment", team=TEAM, environment="development")


def test_tool_parameters_and_annotations(stack):
    tools = stack.tools()
    assert len(tools) == 26
    # The names that changed, and the ones they replace.
    assert not {"save_question_set", "compare_models", "set_schedule", "get_live", "set_live",
                "review_conversation", "promote_to_case"} & set(tools)

    def parameters(tool):
        return tools[tool]["inputSchema"]["properties"]

    def hints(tool):
        a = tools[tool]["annotations"]
        return a["readOnlyHint"], a["destructiveHint"], a["idempotentHint"], a["openWorldHint"]

    # A resource is named by its noun, never "id".
    assert not [name for name in tools if {"id", "schedule_id"} & set(parameters(name))]
    for tool in ("get_question_set", "update_question_set", "import_questions", "delete_question_set",
                 "run_evaluation", "compare_llm_models", "measure_semantic_model", "list_runs", "list_schedules",
                 "create_schedule", "promote_review"):
        assert "question_set" in parameters(tool), tool
    assert set(parameters("create_question_set")) == {"team", "name", "model", "yaml", "environment", "description",
                                                      "rubric", "target_database"}
    assert set(tools["create_question_set"]["inputSchema"]["required"]) == {"team", "name", "model", "yaml"}
    assert set(parameters("update_question_set")) == {"question_set", "name", "description", "model", "yaml",
                                                      "rubric", "target_database", "note"}
    assert tools["update_question_set"]["inputSchema"]["required"] == ["question_set"]
    assert set(parameters("delete_question_set")) == {"question_set", "confirm_name"}
    assert set(parameters("create_schedule")) == {"question_set", "cron", "llm_model", "enabled"}
    assert set(parameters("update_schedule")) == {"schedule", "cron", "llm_model", "enabled"}
    assert set(parameters("delete_schedule")) == {"schedule"}
    assert set(parameters("update_live_evaluation")) == {"team", "environment", "enabled", "sample_pct", "cron",
                                                         "target_database"}
    assert set(tools["update_live_evaluation"]["inputSchema"]["required"]) == {"team", "environment"}
    assert set(parameters("update_review")) == {"review", "status", "note"}
    assert parameters("update_review")["status"]["enum"] == ["dismissed", "open"]
    assert set(parameters("promote_review")) == {"review", "question_set", "name", "expect"}
    assert set(parameters("compare_llm_models")) == {"question_set", "llm_models"}
    limit = parameters("list_runs")["limit"]
    assert (limit["default"], limit["minimum"], limit["maximum"]) == (20, 1, 200)
    limit = parameters("list_reviews")["limit"]
    assert (limit["default"], limit["minimum"], limit["maximum"]) == (100, 1, 500)

    read = (True, False, True, False)
    for tool in ("whoami", "list_environments", "list_agent_models", "list_question_sets", "get_question_set",
                 "list_runs", "get_run", "compare_runs", "list_schedules", "get_live_evaluation", "list_reviews"):
        assert hints(tool) == read, tool
    # A run makes the agent call an LLM provider, outside the platform.
    for tool in ("run_evaluation", "compare_llm_models", "measure_semantic_model"):
        assert hints(tool) == (False, False, False, True), tool
    for tool in ("delete_question_set", "delete_schedule", "disable_environment"):
        assert hints(tool) == (False, True, False, False), tool
    # Switching live evaluation off deletes the kept conversations; the same call twice changes nothing more.
    assert hints("update_live_evaluation") == (False, True, True, False)
    for tool in ("enable_environment", "create_question_set", "update_question_set", "import_questions",
                 "create_schedule", "update_schedule", "update_review", "promote_review"):
        assert hints(tool) == (False, False, False, False), tool


def test_the_contract_describes_errors_and_the_shared_shapes(stack):
    schema = stack.app.openapi()
    schemas = schema["components"]["schemas"]
    assert {"Error", "Me", "Environment", "EnvironmentList", "Run", "RunList", "Schedule", "ScheduleList", "Deleted",
            "QuestionSetList", "ReviewList", "AgentModels", "LiveEvaluation", "Review", "Comparison"} <= set(schemas)
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
    assert answer("listAgentModels") == "AgentModels" and answer("listQuestionSets") == "QuestionSetList"
    assert answer("createQuestionSet", "201") == answer("updateQuestionSet") == answer("getQuestionSet") == \
        answer("importQuestions") == "QuestionSetDetail"
    assert answer("runEvaluation", "202") == "Run" and answer("getRun") == "RunDetail"
    assert answer("compareLlmModels", "202") == answer("listRuns") == "RunList"
    assert answer("measureSemanticModel", "202") == "Measurement" and answer("compareRuns") == "Comparison"
    assert answer("listSchedules") == "ScheduleList"
    assert answer("createSchedule", "201") == answer("updateSchedule") == "Schedule"
    assert answer("deleteQuestionSet") == answer("deleteSchedule") == "Deleted"
    assert answer("getLiveEvaluation") == answer("updateLiveEvaluation") == "LiveEvaluation"
    assert answer("listReviews") == "ReviewList" and answer("updateReview") == "Review"
    assert answer("promoteReview") == "Promotion"

    assert set(schemas["Run"]["properties"]) == set(RUN_FIELDS) and "published" not in schemas["Run"]["properties"]
    assert set(schemas["CaseResult"]["properties"]) == CASE_FIELDS
    assert set(schemas["Schedule"]["properties"]) == SCHEDULE_FIELDS
    assert set(schemas["QuestionSetItem"]["properties"]) == SET_FIELDS | {"lastRun"}
    assert set(schemas["QuestionSetDetail"]["properties"]) == DETAIL_FIELDS | {"imported"}
    assert "watermark" not in schemas["LiveEvaluation"]["properties"]
    assert set(schemas["Change"]["properties"]) == {"fixed", "broken", "same", "noAnswer"}

    paths = {id: found[:2] for id, found in operations.items()}
    assert paths["createQuestionSet"] == ("post", "/api/v1/question-sets")
    assert paths["updateQuestionSet"] == ("patch", "/api/v1/question-sets/{questionSet}")
    assert paths["deleteQuestionSet"] == ("delete", "/api/v1/question-sets/{questionSet}")
    assert paths["compareLlmModels"] == ("post", "/api/v1/question-sets/{questionSet}/compare-llm-models")
    assert paths["listSchedules"] == ("get", "/api/v1/question-sets/{questionSet}/schedules")
    assert paths["createSchedule"] == ("post", "/api/v1/question-sets/{questionSet}/schedules")
    assert paths["updateSchedule"] == ("patch", "/api/v1/schedules/{schedule}")
    assert paths["deleteSchedule"] == ("delete", "/api/v1/schedules/{schedule}")
    assert paths["getLiveEvaluation"] == ("get", "/api/v1/teams/{team}/environments/{environment}/live")
    assert paths["updateLiveEvaluation"] == ("patch", "/api/v1/teams/{team}/environments/{environment}/live")
    assert paths["updateReview"] == ("patch", "/api/v1/reviews/{review}")
    assert paths["promoteReview"] == ("post", "/api/v1/reviews/{review}/promote")
    # No path parameter is called id.
    assert not [path for path in schema["paths"] if "{id}" in path]

    def query(id):
        return {p["name"]: p for p in operations[id][2].get("parameters", []) if p["in"] == "query"}

    assert query("deleteQuestionSet")["confirmName"]["required"] is True
    assert set(query("createSchedule")) == set()
    limit = query("listRuns")["limit"]["schema"]
    assert (limit["default"], limit["minimum"], limit["maximum"]) == (20, 1, 200)
    assert set(query("listReviews")) == {"status", "limit"}
    limit = query("listReviews")["limit"]["schema"]
    assert (limit["default"], limit["minimum"], limit["maximum"]) == (100, 1, 500)

    def body(name):
        return set(schemas[name]["properties"])

    assert body("QuestionSetInput") == {"team", "environment", "name", "description", "model", "yaml", "rubric",
                                        "targetDatabase"}
    assert set(schemas["QuestionSetInput"]["required"]) == {"team", "name", "model", "yaml"}
    assert body("QuestionSetChange") == {"name", "description", "model", "yaml", "rubric", "targetDatabase", "note"}
    assert body("ImportInput") == {"questionsSql"} and body("RunInput") == {"llmModel"}
    assert body("CompareInput") == {"llmModels"} and body("ScheduleInput") == {"cron", "llmModel", "enabled"}
    assert body("ScheduleChange") == {"cron", "llmModel", "enabled"}
    assert body("LiveChange") == {"enabled", "samplePct", "cron", "targetDatabase"}
    assert "required" not in schemas["LiveChange"]
    assert body("ReviewChange") == {"status", "note"} and body("PromoteInput") == {"questionSet", "name", "expect"}
    assert schemas["ReviewChange"]["properties"]["status"]["enum"] == ["dismissed", "open"]


def test_every_operation_has_an_mcp_tool_and_the_contract_is_current(stack):
    schema = stack.client.get("/api/v1/openapi.json").json()
    operations = {op["operationId"] for path in schema["paths"].values() for op in path.values()}
    assert operations == set(OPERATIONS) and len(OPERATIONS) == 26
    mcp = create_mcp(stack.app.state.services.settings, stack.app.state.services, None)
    assert {t.name for t in asyncio.run(mcp.list_tools())} == set(OPERATIONS.values())
    committed = ROOT / "contracts" / "evaluations-api" / "v1" / "openapi.yaml"
    assert json.loads(committed.read_text()) == schema, (
        "Regenerate extensions/evaluations/contracts/evaluations-api/v1/openapi.yaml with "
        "`uv run python -m scripts.api_contract`")


def test_a_sign_in_belongs_to_the_browser_that_started_it(tmp_path):
    """A callback URL sent to another browser signs nobody in (login CSRF)."""
    import httpx

    from evaluations.auth import BrowserLogin
    from tests.conftest import FakeVerifier, settings

    token = httpx.MockTransport(lambda request: httpx.Response(200, json={"access_token": "alice",
                                                                          "refresh_token": "r"}))
    login = BrowserLogin(settings(tmp_path), FakeVerifier(), httpx.AsyncClient(transport=token))
    url, binding = login.start("/#/runs")
    state = dict(part.split("=", 1) for part in url.split("?", 1)[1].split("&"))["state"]
    with pytest.raises(EvalError):
        asyncio.run(login.finish(state, "code", "someone-elses-binding"))
    # The failed attempt used up the state.
    with pytest.raises(EvalError):
        asyncio.run(login.finish(state, "code", binding))
    url, binding = login.start("/#/runs")
    state = dict(part.split("=", 1) for part in url.split("?", 1)[1].split("&"))["state"]
    session, target = asyncio.run(login.finish(state, "code", binding))
    assert session in login.sessions and target == "/#/runs"


def test_the_login_route_sets_the_binding_cookie(stack):
    response = stack.client.get("/auth/login", follow_redirects=False)
    assert response.status_code == 303
    cookie = response.headers["set-cookie"]
    assert "iceberg_evaluations_login=" in cookie and "HttpOnly" in cookie and "Path=/auth/callback" in cookie
    state = response.headers["location"].split("state=", 1)[1].split("&", 1)[0]
    stack.client.cookies.clear()
    failed = stack.client.get(f"/auth/callback?state={state}&code=x", follow_redirects=False)
    assert failed.headers["location"] == "/?signin=failed"
