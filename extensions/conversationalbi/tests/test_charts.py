import pytest

from conversationalbi.charts import recommend

CARRIER = {"name": "CARRIER.name", "kind": "dimension", "type": "string"}
AIRPORT = {"name": "AIRPORT.name", "kind": "dimension", "type": "string"}
MONTH = {"name": "FLIGHT.date:month", "kind": "dimension", "type": "date", "grain": "month"}
DAY = {"name": "FLIGHT.date", "kind": "dimension", "type": "date"}
DELAY = {"name": "average_departure_delay", "kind": "metric"}
FLIGHTS = {"name": "flights", "kind": "metric", "additive": True}


def shown(columns, rows):
    found = recommend(columns, rows)
    return found["tool"], found["arguments"]


def test_a_single_number_is_a_kpi():
    assert shown([DELAY], [[12.5]]) == ("show_kpi", {"column": "average_departure_delay"})
    assert shown([DELAY, FLIGHTS], [[12.5, 40]])[0] == "show_table"


@pytest.mark.parametrize("time", [MONTH, DAY])
def test_time_is_a_line(time):
    rows = [["2026-01-01", 1, 2], ["2026-02-01", 3, 4]]
    assert shown([time, DELAY, FLIGHTS], rows) == ("show_chart", {
        "kind": "line", "x": time["name"], "y": ["average_departure_delay", "flights"]})
    split = [["A", "2026-01-01", 1], ["B", "2026-02-01", 2]]
    assert shown([CARRIER, time, DELAY], split) == ("show_chart", {
        "kind": "line", "x": time["name"], "y": ["average_departure_delay"], "series": "CARRIER.name"})


def test_categories_are_bars_sideways_when_long_or_many():
    assert shown([CARRIER, DELAY], [["KL", 3], ["HV", 4]]) == ("show_chart", {
        "kind": "bar", "x": "CARRIER.name", "y": ["average_departure_delay"]})
    assert shown([AIRPORT, DELAY], [["Amsterdam Schiphol", 3], ["Eindhoven", 4]])[1]["horizontal"] is True
    assert shown([CARRIER, DELAY], [[f"C{i}", i] for i in range(9)])[1]["horizontal"] is True
    assert shown([CARRIER, DELAY], [[f"C{i}", i] for i in range(41)])[0] == "show_table"


def test_a_dimension_with_one_value_is_left_out():
    rows = [["2026-01-01", "Example Cloudline", 17], ["2026-01-01", "Example Aurora Air", 15]]
    assert shown([MONTH, CARRIER, DELAY], rows) == ("show_chart", {
        "kind": "bar", "x": "CARRIER.name", "y": ["average_departure_delay"], "horizontal": True})


def test_only_metrics_that_add_up_stack():
    rows = [["AMS", "KL", 3], ["AMS", "HV", 4], ["EIN", "KL", 1]]
    assert shown([AIRPORT, CARRIER, FLIGHTS], rows) == ("show_chart", {
        "kind": "bar", "x": "AIRPORT.name", "y": ["flights"], "series": "CARRIER.name", "stacked": True})
    assert "stacked" not in shown([AIRPORT, CARRIER, DELAY], rows)[1]


@pytest.mark.parametrize(("columns", "rows"), [
    ([CARRIER, DELAY], []),
    ([CARRIER, DELAY], [["KL", 3]]),
    ([AIRPORT, CARRIER, MONTH, DELAY], [["AMS", "KL", "2026-01-01", 3], ["EIN", "HV", "2026-02-01", 4]]),
    ([AIRPORT, CARRIER, DELAY, FLIGHTS], [["AMS", "KL", 3, 1], ["EIN", "HV", 4, 2]]),
])
def test_everything_else_is_a_table(columns, rows):
    assert shown(columns, rows) == ("show_table", {})
