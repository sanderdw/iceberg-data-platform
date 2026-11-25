"""Live evaluation: real conversations scored on a schedule; the doubtful ones reviewed and promoted."""

import asyncio

import pytest
from pyiceberg.catalog.sql import SqlCatalog

from evaluations import lakehouse
from tests.conftest import DB, MODEL, TEAM, TIMESTAMP, ToolRefused, conversation, seconds

LIVE = f"/teams/{TEAM}/environments/development/live"
REVIEWS = f"/teams/{TEAM}/environments/development/reviews"
LIVE_FIELDS = {"team", "environment", "enabled", "samplePct", "cron", "targetDatabase", "nextRunAt", "updatedBy",
               "updatedAt", "lastChecked", "lastSummary", "openReviews"}
REVIEW_FIELDS = {"id", "team", "environment", "conversation", "askedAt", "question", "answer", "model", "queries",
                 "llmModel", "traceId", "scores", "status", "createdAt", "reviewedBy", "reviewedAt", "note",
                 "promotedTo"}
GOOD = conversation(1, "2026-11-24T09:15:02.123Z", "Which carrier is late most?",
                    "Example Cloudline, 16.9 minutes on average.")
INVENTED = conversation(2, "2026-11-24T09:16:40.500Z", "How late is Jetways?",
                        "Example Jetways averages 31.4 minutes.")


def live(stack, user="alice", **body):
    return stack.call("PATCH", LIVE, user, json={"enabled": True, "samplePct": 100, **body})


def tick(stack):
    stack.services.store.update_live(TEAM, "development", next_run_at=0)
    return stack.client.portal.call(stack.app.state.live.tick)


def refused(response, status, code):
    assert (response.status_code, response.json()["code"]) == (status, code), response.text


def test_only_administrators_switch_it_on(stack):
    # Before anyone set it up: off, with the defaults a first change starts from.
    unset = stack.call("GET", LIVE, "carol").json()
    assert unset == {"team": TEAM, "environment": "development", "enabled": False, "samplePct": 20,
                     "cron": "0 * * * *", "targetDatabase": None, "nextRunAt": None, "updatedBy": None,
                     "updatedAt": None, "lastChecked": None, "lastSummary": None, "openReviews": 0}
    refused(live(stack), 409, "environment_not_enabled")
    stack.call("POST", f"/teams/{TEAM}/environments/development")
    refused(live(stack, "bob"), 403, "forbidden_role")
    refused(live(stack, cron="hourly"), 422, "invalid_cron")
    # A check at most every 5 minutes, as every schedule on the platform.
    refused(live(stack, cron="* * * * *"), 422, "invalid_cron")
    # The body is camelCase now.
    refused(stack.call("PATCH", LIVE, json={"enabled": True, "sample_pct": 100}), 422, "invalid_request")
    assert stack.agent.recording_ == {}
    body = live(stack, targetDatabase=DB).json()
    assert set(body) == LIVE_FIELDS | {"note"} and "keeps this team's conversations" in body["note"]
    assert (body["enabled"], body["samplePct"], body["cron"], body["targetDatabase"]) == (True, 100, "0 * * * *", DB)
    assert body["updatedBy"] == "alice (via agent)" and seconds(body["nextRunAt"]) > seconds(body["updatedAt"])
    assert stack.agent.recording_ == {(TEAM, "development"): True}
    shown = stack.call("GET", LIVE, "carol").json()
    assert set(shown) == LIVE_FIELDS and shown == {k: body[k] for k in LIVE_FIELDS}


def test_a_change_keeps_what_it_leaves_out(stack):
    stack.call("POST", f"/teams/{TEAM}/environments/development")
    live(stack, cron="*/10 * * * *", targetDatabase=DB)
    # Only the sample changes: it stays on, with its schedule and its database.
    changed = stack.call("PATCH", LIVE, json={"samplePct": 50}).json()
    assert (changed["enabled"], changed["samplePct"], changed["cron"], changed["targetDatabase"]) == (
        True, 50, "*/10 * * * *", DB)
    assert stack.agent.recording_ == {(TEAM, "development"): True}
    cleared = stack.call("PATCH", LIVE, json={"targetDatabase": ""}).json()
    assert (cleared["targetDatabase"], cleared["samplePct"], cleared["enabled"]) == (None, 50, True)
    off = stack.call("PATCH", LIVE, json={"enabled": False}).json()
    # Off, with its settings kept for the next time; a setting that is off has no next check.
    assert (off["enabled"], off["samplePct"], off["cron"], off["nextRunAt"]) == (False, 50, "*/10 * * * *", None)
    assert stack.agent.recording_ == {(TEAM, "development"): False}


def test_conversational_bi_records_only_while_it_is_on(stack):
    stack.call("POST", f"/teams/{TEAM}/environments/development")
    live(stack)
    body = live(stack, enabled=False).json()
    assert not body["enabled"] and "deleted the ones it kept" in body["note"]
    assert stack.agent.recording_ == {(TEAM, "development"): False}
    # A check tells Conversational BI again when it lost its record.
    live(stack)
    stack.agent.recording_.clear()
    tick(stack)
    assert stack.agent.recording_ == {(TEAM, "development"): True}
    # A reader can't end it by trying to disable Evaluations.
    refused(stack.call("DELETE", f"/teams/{TEAM}/environments/development", "carol"), 403, "forbidden_role")
    assert stack.agent.recording_ == {(TEAM, "development"): True}
    # Disabling Evaluations ends live evaluation and the record with it.
    assert stack.call("DELETE", f"/teams/{TEAM}/environments/development").json()["enabled"] is False
    assert stack.agent.recording_ == {(TEAM, "development"): False}
    stack.call("POST", f"/teams/{TEAM}/environments/development")
    assert not stack.call("GET", LIVE).json()["enabled"]


def test_a_check_flags_invented_figures_for_review(stack):
    stack.call("POST", f"/teams/{TEAM}/environments/development")
    live(stack)
    stack.agent.recorded = [GOOD, INVENTED]
    assert tick(stack) == [(TEAM, "development")]
    setting = stack.call("GET", LIVE).json()
    assert setting["lastSummary"] == {"conversations": 2, "checked": 2, "flagged": 1, "published": None}
    assert TIMESTAMP.match(setting["lastChecked"]) and setting["openReviews"] == 1
    # Where the check stopped reading stays inside, in seconds as it is stored.
    assert "watermark" not in setting
    assert stack.services.store.live(TEAM, "development")["watermark"] == seconds(INVENTED["at"])
    [review] = stack.call("GET", REVIEWS, "carol").json()["reviews"]
    assert set(review) == REVIEW_FIELDS
    assert (review["question"], review["conversation"], review["askedAt"]) == (
        "How late is Jetways?", INVENTED["id"], INVENTED["at"])
    assert (review["model"], review["llmModel"], review["traceId"]) == (MODEL, "google:gemini", "ab" * 16)
    assert review["scores"]["grounded"] == {"value": False, "reason": "Not in any result: 31.4."}
    assert (review["status"], review["reviewedBy"], review["reviewedAt"], review["note"], review["promotedTo"]) == (
        "open", None, None, None, None)
    # Only new conversations next time: Conversational BI is asked for those after the last one, as a time string.
    tick(stack)
    assert stack.agent.since == ["1970-01-01T00:00:00.000Z", INVENTED["at"]]
    assert stack.call("GET", LIVE).json()["lastSummary"]["checked"] == 0


def test_reviews_are_listed_up_to_a_limit(stack):
    stack.call("POST", f"/teams/{TEAM}/environments/development")
    live(stack)
    stack.agent.recorded = [conversation(n, f"2026-11-24T09:1{n}:00.000Z", f"How late is carrier {n}?",
                                         "It averages 31.4 minutes.") for n in range(1, 4)]
    tick(stack)
    listed = stack.call("GET", REVIEWS).json()
    assert list(listed) == ["reviews"] and len(listed["reviews"]) == 3
    # Newest first.
    assert [r["question"][-2] for r in stack.call("GET", f"{REVIEWS}?limit=2").json()["reviews"]] == ["3", "2"]
    for limit in (0, 501):
        refused(stack.call("GET", f"{REVIEWS}?limit={limit}"), 422, "invalid_request")
    assert len(stack.tool("list_reviews", team=TEAM, environment="development", limit=1)["reviews"]) == 1


def test_a_review_becomes_a_regression_case(stack):
    stack.call("POST", f"/teams/{TEAM}/environments/development")
    id = stack.create().json()["id"]
    live(stack)
    stack.agent.recorded = [INVENTED, conversation(3, "2026-11-24T09:20:00.000Z", "What about tomorrow?",
                                                   "About 12.5 minutes.", queries=[])]
    tick(stack)
    reviews = stack.call("GET", REVIEWS).json()["reviews"]
    no_query = next(r for r in reviews if r["question"] == "What about tomorrow?")
    with_query = next(r for r in reviews if r["question"] == "How late is Jetways?")
    refused(stack.call("POST", f"/reviews/{with_query['id']}/promote", "carol", json={"questionSet": id}), 403,
            "forbidden_role")
    refused(stack.call("POST", f"/reviews/{with_query['id']}/promote", "dave", json={"questionSet": id}), 404,
            "unknown_review")
    refused(stack.call("POST", f"/reviews/{with_query['id']}/promote", json={"question_set": id}), 422,
            "invalid_request")
    refused(stack.call("POST", f"/reviews/{no_query['id']}/promote", json={"questionSet": id}), 422,
            "expect_required")
    declined = stack.call("POST", f"/reviews/{no_query['id']}/promote",
                          json={"questionSet": id, "expect": {"decline": True}}).json()
    assert set(declined) == {"review", "case", "questionSet"} and declined["case"] == "what_about_tomorrow"
    assert set(declined["review"]) == REVIEW_FIELDS and declined["review"]["status"] == "promoted"
    assert declined["review"]["promotedTo"] == f"{id}:what_about_tomorrow"
    assert declined["review"]["reviewedBy"] == "alice (via agent)" and TIMESTAMP.match(
        declined["review"]["reviewedAt"])
    promoted = stack.tool("promote_review", review=with_query["id"], question_set=id)
    assert promoted["questionSet"]["version"] == 3 and promoted["questionSet"]["id"] == id
    assert "how_late_is_jetways" in promoted["questionSet"]["yaml"] and "- live" in promoted["questionSet"]["yaml"]
    assert promoted["questionSet"] == stack.call("GET", f"/question-sets/{id}").json()
    refused(stack.call("POST", f"/reviews/{with_query['id']}/promote", json={"questionSet": id,
                       "name": "how_late_is_jetways"}), 409, "name_taken")


def test_dismissed_and_reopened(stack):
    stack.call("POST", f"/teams/{TEAM}/environments/development")
    live(stack)
    stack.agent.recorded = [INVENTED]
    tick(stack)
    [review] = stack.call("GET", REVIEWS).json()["reviews"]
    path = f"/reviews/{review['id']}"
    # The new status is given, not an action.
    refused(stack.call("PATCH", path, "bob", json={"action": "dismiss"}), 422, "invalid_request")
    refused(stack.call("PATCH", path, "bob", json={"status": "promoted"}), 422, "invalid_request")
    refused(stack.call("PATCH", path, "carol", json={"status": "dismissed"}), 403, "forbidden_role")
    done = stack.call("PATCH", path, "bob", json={"status": "dismissed", "note": "Rounded."})
    assert done.status_code == 200 and set(done.json()) == REVIEW_FIELDS
    assert (done.json()["status"], done.json()["note"], done.json()["reviewedBy"]) == (
        "dismissed", "Rounded.", "bob (via agent)")
    assert stack.call("GET", f"{REVIEWS}?status=open").json()["reviews"] == []
    assert stack.call("GET", LIVE).json()["openReviews"] == 0
    # The note stays unless another is given.
    reopened = stack.tool("update_review", "bob", review=review["id"], status="open")
    assert (reopened["status"], reopened["note"]) == ("open", "Rounded.")
    assert reopened == stack.call("GET", f"{REVIEWS}?status=open").json()["reviews"][0]
    with pytest.raises(ToolRefused, match=r"\[unknown_review\]"):
        stack.tool("update_review", review="rev-" + "0" * 24, status="open")


def test_live_evaluation_through_the_tools(stack):
    stack.call("POST", f"/teams/{TEAM}/environments/development")
    on = stack.tool("update_live_evaluation", team=TEAM, environment="development", enabled=True, sample_pct=100)
    assert set(on) == LIVE_FIELDS | {"note"} and (on["enabled"], on["samplePct"]) == (True, 100)
    kept = stack.tool("update_live_evaluation", team=TEAM, environment="development", cron="*/15 * * * *")
    assert (kept["enabled"], kept["samplePct"], kept["cron"]) == (True, 100, "*/15 * * * *")
    assert stack.tool("get_live_evaluation", "carol", team=TEAM, environment="development") == stack.call(
        "GET", LIVE, "carol").json()
    with pytest.raises(ToolRefused, match=r"\[invalid_cron\]"):
        stack.tool("update_live_evaluation", team=TEAM, environment="development", cron="* * * * *")


def test_scores_go_to_live_scores(tmp_path):
    from tests.conftest import FakeBridge, settings

    bridge = FakeBridge()
    asyncio.run(bridge.enable("alice", TEAM, "development"))
    catalog = SqlCatalog("local", uri=f"sqlite:///{tmp_path}/c.db", warehouse=f"file://{tmp_path}/w")
    store = lakehouse.Lakehouse(settings(tmp_path), bridge, catalog=catalog)
    rows = [{**INVENTED, "askedAt": seconds(INVENTED["at"]), "passed": False,
             "scores": {"grounded": {"value": False, "reason": "Not 31.4."}}}]
    assert asyncio.run(store.publish_live(TEAM, "development", DB, rows)) == "sales.evaluations.live_scores"
    [row] = catalog.load_table(("evaluations", "live_scores")).scan().to_arrow().to_pylist()
    assert (row["conversation_id"], row["grounded"], row["judge"]) == (INVENTED["id"], False, None)
    # The table is as it was: the conversation's keys are read from the new names.
    assert (row["llm_model"], row["trace_id"]) == ("google:gemini", "ab" * 16)
    assert row["asked_at"].timestamp() == seconds(INVENTED["at"])


def test_a_check_writes_the_scores_of_what_it_read(tmp_path):
    """From Conversational BI's answer to the team's table, through a whole check."""
    from fastapi.testclient import TestClient

    from evaluations.app import create_app
    from tests.conftest import FakeAgent, FakeBridge, FakeVerifier, settings

    bridge, agent = FakeBridge(), FakeAgent()
    catalog = SqlCatalog("local", uri=f"sqlite:///{tmp_path}/c.db", warehouse=f"file://{tmp_path}/w")
    store = lakehouse.Lakehouse(settings(tmp_path), bridge, catalog=catalog)
    app = create_app(settings(tmp_path), bridge=bridge, verifier=FakeVerifier(), agent=agent, lakehouse=store)
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer alice"}
        client.post(f"/api/v1/teams/{TEAM}/environments/development", headers=headers)
        client.patch("/api/v1" + LIVE, headers=headers, json={"enabled": True, "samplePct": 100,
                                                              "targetDatabase": DB})
        agent.recorded = [GOOD, INVENTED]
        app.state.services.store.update_live(TEAM, "development", next_run_at=0)
        client.portal.call(app.state.live.tick)
        summary = client.get("/api/v1" + LIVE, headers=headers).json()["lastSummary"]
    assert summary == {"conversations": 2, "checked": 2, "flagged": 1, "published": "sales.evaluations.live_scores"}
    rows = catalog.load_table(("evaluations", "live_scores")).scan().to_arrow().to_pylist()
    assert {(r["conversation_id"], r["passed"]) for r in rows} == {(GOOD["id"], True), (INVENTED["id"], False)}
    assert {r["asked_at"].timestamp() for r in rows} == {seconds(GOOD["at"]), seconds(INVENTED["at"])}
