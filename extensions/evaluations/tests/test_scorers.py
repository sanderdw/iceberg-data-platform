import asyncio
from types import SimpleNamespace

import pytest

from evaluations.scorers import Clarified, Grounded, Refused, ResultMatches, RightMetrics, numbers

ROWS = [["Cloudline", 16.923885], ["Jetways", 12.5], ["Air", 9.25]]
ORDERED = {"query": {"metrics": ["delay"], "order_by": [{"name": "delay", "desc": True}]}}
UNORDERED = {"query": {"metrics": ["delay"]}}


def ctx(answer="", rows=ROWS, expected=ORDERED, truth=None, question="Which carrier is late?", queries=None,
        calls=()):
    queries = queries if queries is not None else [
        {"spec": {"metrics": ["delay"]}, "rows": rows, "rowCount": len(rows)}]
    agent = {"answer": answer, "queries": queries, "toolCalls": list(calls)}
    return SimpleNamespace(inputs={"question": question}, expected_output=expected, metadata={},
                           output={"agent": agent, "truth": truth or {"rows": ROWS, "rowCount": 3}})


def value(result):
    return result.value if hasattr(result, "value") else result


def test_ordered_results_compare_row_by_row():
    assert value(ResultMatches().evaluate(ctx())) is True
    swapped = [ROWS[1], ROWS[0], ROWS[2]]
    found = ResultMatches().evaluate(ctx(rows=swapped))
    assert found.value is False and found.reason.startswith("Row 1 differs")
    # Extra columns and fewer rows shown are fine; rounding to 6 significant digits too.
    assert value(ResultMatches().evaluate(ctx(rows=[["Cloudline", 16.9238851, "XC"]]))) is True


def test_unordered_results_need_the_same_count_and_rows():
    assert value(ResultMatches().evaluate(ctx(rows=list(reversed(ROWS)), expected=UNORDERED))) is True
    assert value(ResultMatches().evaluate(ctx(rows=ROWS[:2], expected=UNORDERED))) is False
    assert value(ResultMatches().evaluate(ctx(rows=[["Other", 1.0], *ROWS[1:]], expected=UNORDERED))) is False


def test_no_query_or_a_broken_case_fails_with_a_reason():
    assert "no successful query" in ResultMatches().evaluate(ctx(queries=[])).reason
    broken = ResultMatches().evaluate(ctx(truth={"error": "x", "code": "unknown_metric"}))
    assert broken.value is False and "fix the case" in broken.reason
    assert ResultMatches().evaluate(ctx(expected={"refusal": "fan_out"})) == {}


def test_any_query_of_the_agent_may_match():
    exploring = [{"spec": {"metrics": ["delay"]}, "rows": ROWS, "rowCount": 3},
                 {"spec": {"metrics": ["delay"]}, "rows": [["2026-01-30T00:00:00", 16.4]], "rowCount": 1}]
    found = ResultMatches().evaluate(ctx(queries=exploring))
    assert found.value is True and found.reason.endswith("(query 1 of 2)")
    assert value(RightMetrics().evaluate(ctx(queries=exploring)))


def test_the_control_arm_has_no_metrics_or_refusal_codes():
    control = ctx(answer="That would count flights twice.", expected={"refusal": "fan_out"})
    control.output["arm"] = "control"
    assert value(Refused().evaluate(control)) is True
    control = ctx(answer="It is 16.9.", expected={"refusal": "fan_out"})
    control.output["arm"] = "control"
    assert value(Refused().evaluate(control)) is False
    metrics = ctx()
    metrics.output["arm"] = "control"
    assert RightMetrics().evaluate(metrics) == {}


@pytest.mark.parametrize(("agent", "matches"), [
    (16.92, True), (16.9, True), (16.8, False), (17.0, False), (16.923885, True)])
def test_a_rounded_number_is_compared_at_its_own_precision(agent, matches):
    assert value(ResultMatches().evaluate(ctx(rows=[["Cloudline", agent]]))) is matches


def test_right_metrics():
    assert value(RightMetrics().evaluate(ctx())) is True
    assert RightMetrics().evaluate(ctx(expected={"query": {"metrics": ["delay", "cancellations"]}})).reason == \
        "Missing: cancellations."


@pytest.mark.parametrize(("answer", "grounded"), [
    ("Cloudline is late most: 16.9 minutes on average.", True),
    ("Cloudline: 16,9 minutes; the top 3 in 2026.", True),
    ("Cloudline leads with 17 minutes.", True),
    ("Cloudline leads with 18 minutes.", False),
    ("Jetways averages 12.50 minutes and Air 9.3.", True),
    ("Only 1,250 flights were late.", False),
])
def test_grounded_numbers(answer, grounded):
    assert value(Grounded().evaluate(ctx(answer=answer))) is grounded


def test_figures_from_definitions_sql_and_dates_count_as_seen():
    calls = [{"name": "query", "args": {"filters": [{"field": "FLIGHT.date", "op": ">=", "value": "2026-01-29"}]},
              "result": {"metrics": [{"definition": "Arrivals less than 15 minutes late."}],
                         "sql": "WHERE date >= current_date - INTERVAL 30 DAY"}}]
    answer = "On time means less than 15 minutes late; over the last 30 days, since 29 January, 62.7% of 3 carriers."
    rows = [["Cloudline", 62.71]]
    assert value(Grounded().evaluate(ctx(answer=answer, rows=rows, calls=calls)))
    assert not value(Grounded().evaluate(ctx(answer="Since 29 January, 58.1% arrived on time.", rows=rows)))


def test_a_day_is_a_day_with_or_without_a_time():
    truth = {"rows": [["2026-01-01T00:00:00", 16.1], ["2026-01-02T00:00:00", 15.0]], "rowCount": 2}
    agent_rows = [["2026-01-01", 16.1], ["2026-01-02", 15.0]]
    assert value(ResultMatches().evaluate(ctx(rows=agent_rows, truth=truth)))


def test_numbers_read_both_separators():
    assert numbers("1,234.5 and 16,9 and -3") == [(1234.5, 1), (16.9, 1), (-3.0, 0)]


def test_refused_needs_the_code_and_no_figures():
    expected = {"refusal": "fan_out"}
    calls = [{"name": "query", "result": {"error": "x", "code": "fan_out"}}]
    assert value(Refused().evaluate(ctx(answer="That split counts flights twice.", expected=expected, calls=calls)))
    stated = Refused().evaluate(ctx(answer="It is 16.9 anyway.", expected=expected, calls=calls))
    assert stated.value is False and "16.9" in stated.reason
    assert Refused().evaluate(ctx(answer="No.", expected=expected)).value is False


def test_clarified_asks_back_without_a_query():
    expected = {"clarify": True}
    assert value(Clarified().evaluate(ctx(answer="Departure or arrival?", expected=expected, queries=[])))
    assert Clarified().evaluate(ctx(answer="Departure or arrival?", expected=expected)).value is False


def test_the_judge_needs_a_model_and_a_rubric():
    from evaluations.scorers import Judge

    assert asyncio.run(Judge(rubric="Polite.").evaluate(ctx())) == {}
    assert asyncio.run(Judge(model="test").evaluate(ctx())) == {}
