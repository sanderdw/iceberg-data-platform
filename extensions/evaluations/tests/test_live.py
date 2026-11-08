"""Live evaluation: real conversations scored on a schedule; the doubtful ones reviewed and promoted."""

import asyncio

from pyiceberg.catalog.sql import SqlCatalog

from evaluations import lakehouse
from tests.conftest import DB, DELAYS, MODEL, QUERY, TEAM

GOOD = {"id": "conv-" + "1" * 24, "at": 100.0, "question": "Which carrier is late most?", "model": MODEL,
        "answer": "Example Cloudline, 16.9 minutes on average.", "llm_model": "google:gemini", "trace_id": "ab" * 16,
        "tool_calls": [{"name": "query", "args": QUERY, "result": {"rowCount": 3}}],
        "queries": [{"spec": {**QUERY, "limit": 100}, "rowCount": 3, "rowsShown": 3, "rows": DELAYS}]}
INVENTED = {**GOOD, "id": "conv-" + "2" * 24, "at": 200.0, "question": "How late is Jetways?",
            "answer": "Example Jetways averages 31.4 minutes."}


def live(stack, user="alice", **body):
    return stack.call("PUT", f"/teams/{TEAM}/environments/development/live", user,
                      json={"enabled": True, "sample_pct": 100, **body})


def tick(stack):
    stack.app.state.services.store.update_live(TEAM, "development", next_run_at=0)
    return stack.client.portal.call(stack.app.state.live.tick)


def test_only_administrators_switch_it_on(stack):
    assert live(stack).json()["code"] == "environment_not_enabled"
    stack.call("POST", f"/teams/{TEAM}/environments/development")
    assert live(stack, "bob").json()["code"] == "forbidden_role"
    assert live(stack, cron="hourly").json()["code"] == "invalid_cron"
    body = live(stack).json()
    assert body["enabled"] and "keeps this team's conversations" in body["note"]
    assert stack.call("GET", f"/teams/{TEAM}/environments/development/live", "carol").json()["openReviews"] == 0


def test_a_check_flags_invented_figures_for_review(stack):
    stack.call("POST", f"/teams/{TEAM}/environments/development")
    live(stack)
    stack.agent.recorded = [GOOD, INVENTED]
    assert tick(stack) == [(TEAM, "development")]
    setting = stack.call("GET", f"/teams/{TEAM}/environments/development/live").json()
    assert setting["last_summary"] == {"conversations": 2, "checked": 2, "flagged": 1, "published": ""}
    assert setting["watermark"] == 200.0 and setting["openReviews"] == 1
    [review] = stack.call("GET", f"/teams/{TEAM}/environments/development/reviews", "carol").json()["reviews"]
    assert review["question"] == "How late is Jetways?"
    assert review["scores"]["grounded"] == {"value": False, "reason": "Not in any result: 31.4."}
    # Only new conversations next time.
    tick(stack)
    assert stack.call("GET", f"/teams/{TEAM}/environments/development/live").json()["last_summary"]["checked"] == 0


def test_a_review_becomes_a_regression_case(stack):
    stack.call("POST", f"/teams/{TEAM}/environments/development")
    id = stack.create().json()["id"]
    live(stack)
    stack.agent.recorded = [INVENTED, {**INVENTED, "id": "conv-" + "3" * 24, "at": 300.0, "queries": [],
                                       "question": "What about tomorrow?", "answer": "About 12.5 minutes."}]
    tick(stack)
    reviews = stack.call("GET", f"/teams/{TEAM}/environments/development/reviews").json()["reviews"]
    no_query = next(r for r in reviews if r["question"] == "What about tomorrow?")
    with_query = next(r for r in reviews if r["question"] == "How late is Jetways?")
    assert stack.call("POST", f"/reviews/{with_query['id']}/promote", "carol",
                      json={"question_set": id}).json()["code"] == "forbidden_role"
    refused = stack.call("POST", f"/reviews/{no_query['id']}/promote", json={"question_set": id}).json()
    assert refused["code"] == "expect_required"
    declined = stack.call("POST", f"/reviews/{no_query['id']}/promote",
                          json={"question_set": id, "expect": {"decline": True}}).json()
    assert declined["case"] == "what_about_tomorrow" and declined["review"]["status"] == "promoted"
    promoted = stack.call("POST", f"/reviews/{with_query['id']}/promote", json={"question_set": id}).json()
    assert promoted["questionSet"]["version"] == 3
    assert "how_late_is_jetways" in promoted["questionSet"]["yaml"] and "- live" in promoted["questionSet"]["yaml"]
    assert stack.call("POST", f"/reviews/{with_query['id']}/promote", json={"question_set": id,
                      "name": "how_late_is_jetways"}).json()["code"] == "name_taken"


def test_dismissed_and_reopened(stack):
    stack.call("POST", f"/teams/{TEAM}/environments/development")
    live(stack)
    stack.agent.recorded = [INVENTED]
    tick(stack)
    [review] = stack.call("GET", f"/teams/{TEAM}/environments/development/reviews").json()["reviews"]
    done = stack.call("POST", f"/reviews/{review['id']}", "bob", json={"action": "dismiss", "note": "Rounded."}).json()
    assert done["status"] == "dismissed" and done["note"] == "Rounded."
    assert stack.call("GET", f"/teams/{TEAM}/environments/development/reviews?status=open").json()["reviews"] == []
    assert stack.call("POST", f"/reviews/{review['id']}", "bob", json={"action": "reopen"}).json()["status"] == "open"


def test_scores_go_to_live_scores(tmp_path):
    from tests.conftest import FakeBridge, settings

    bridge = FakeBridge()
    asyncio.run(bridge.enable("alice", TEAM, "development"))
    catalog = SqlCatalog("local", uri=f"sqlite:///{tmp_path}/c.db", warehouse=f"file://{tmp_path}/w")
    store = lakehouse.Lakehouse(settings(tmp_path), bridge, catalog=catalog)
    rows = [{**INVENTED, "passed": False, "scores": {"grounded": {"value": False, "reason": "Not 31.4."}}}]
    assert asyncio.run(store.publish_live(TEAM, "development", DB, rows)) == "sales.evaluations.live_scores"
    [row] = catalog.load_table(("evaluations", "live_scores")).scan().to_arrow().to_pylist()
    assert (row["conversation_id"], row["grounded"], row["judge"]) == (INVENTED["id"], False, None)
