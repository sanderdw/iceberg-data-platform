"""Governed queries compiled from Ossie models; mirrors extensions/conversationalbi/tests/test_semantic.py."""

import datetime
import decimal
import json

import pytest
from pydantic import ValidationError

from test import semantic_flights as flights
from user_portal.semantic import compiler, expressions, ossie, worker
from user_portal.semantic.compiler import QuerySpec, compile_query
from user_portal.semantic.errors import SemanticError


def spec(**kwargs):
    return QuerySpec.model_validate(kwargs)


def run(compiled):
    connection = flights.local(compiled.bindings)
    return connection.execute(compiled.sql, compiled.params).fetchall()


# Parsing what Polaris stores

def test_parses_the_stored_flights_model():
    model = flights.model()
    assert model.name == "Flights"
    assert [d.name for d in model.datasets] == ["RUNWAY", "AIRCRAFT", "AIRPORT", "FLIGHT", "CARRIER", "ROUTE"]
    flight = model.dataset("FLIGHT")
    assert (flight.namespace, flight.table, flight.primary_key) == (("ai_flights",), "flights", ("id",))
    assert flight.field("dep_delay").expression == "dep_delay"
    assert model.metric("on_time_arrival_pct").expression.startswith("100.0 * count(*) FILTER")
    assert "Exclude diverted flights" in model.instructions


@pytest.mark.parametrize(("flag", "expected"), [
    ({"is_time": True}, True), ({"is_time": False}, True), ({}, True),  # Ossie's dimension object
    (True, True), (False, False),  # older documents
    (None, None), ("yes", None),
])
def test_dimensions_follow_the_ossie_object(flag, expected):
    assert ossie.dimension({} if flag is None else {"dimension": flag}) is expected


@pytest.mark.parametrize("wrap", [
    lambda m: {"version": "0.2.0", "semantic_model": [m]},
    lambda m: [m],
    lambda m: m,
])
def test_accepts_every_envelope_shape(wrap):
    inner = json.loads(flights.ENVELOPE["document"]["semantic_model"])["semantic_model"][0]
    loaded = {"document": {"version": "0.2.0", "semantic_model": json.dumps(wrap(inner))}}
    assert ossie.parse(loaded, ["ai_flights"]).metric("cancellation_pct")


def test_rejects_models_it_cannot_trust():
    base = {"datasets": [{"name": "A", "source": "lakehouse.ns.a", "primary_key": ["id"], "fields": []}]}
    with pytest.raises(SemanticError, match="unsupported name"):
        ossie.parse({**base, "metrics": [{"name": "x; drop", "expression": "count(*)"}]}, ["ns"])
    with pytest.raises(SemanticError, match="does not define"):
        ossie.parse({**base, "relationships": [{"name": "r", "from": "A", "to": "B", "from_columns": ["b"],
                                                "to_columns": ["id"]}]}, ["ns"])
    with pytest.raises(SemanticError, match="no datasets"):
        ossie.parse({"document": {"semantic_model": "not json"}}, ["ns"])


@pytest.mark.parametrize(("key", "count", "item"), [
    ("datasets", ossie.MAX_DATASETS + 1, lambda i: {"name": f"D{i}", "source": f"ns.t{i}", "fields": []}),
    ("metrics", ossie.MAX_METRICS + 1, lambda i: {"name": f"m{i}", "expression": "count(*)"}),
    ("relationships", ossie.MAX_RELATIONSHIPS + 1,
     lambda i: {"name": f"r{i}", "from": "A", "to": "A", "from_columns": ["id"], "to_columns": ["id"]}),
])
def test_rejects_models_too_large_to_plan(key, count, item):
    base = {"datasets": [{"name": "A", "source": "ns.a", "fields": []}]}
    with pytest.raises(SemanticError, match=f"has {count} {key}"):
        ossie.parse({**base, key: [item(i) for i in range(count)]}, ["ns"])
    fields = [{"name": f"f{i}", "expression": f"f{i}"} for i in range(ossie.MAX_FIELDS + 1)]
    with pytest.raises(SemanticError, match="fields in dataset 'A'"):
        ossie.parse({"datasets": [{"name": "A", "source": "ns.a", "fields": fields}]}, ["ns"])


def test_prefers_duckdb_then_ansi_sql():
    model = ossie.parse({"datasets": [{"name": "A", "source": "t", "fields": []}], "metrics": [
        {"name": "m", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "count(*)"},
                                                  {"dialect": "DUCKDB", "expression": "count(A.x)"}]}}]}, ["ns"])
    assert model.metric("m").expression == "count(A.x)"
    assert model.dataset("A").namespace == ("ns",)


# The expression allowlist

@pytest.mark.parametrize("sql", [
    "avg(FLIGHT.dep_delay)",
    "100.0 * count(*) FILTER (WHERE NOT FLIGHT.cancelled) / nullif(count(*), 0)",
    "count(DISTINCT tail_nr)", "CASE WHEN x > 1 THEN 'a' ELSE 'b' END", "date_trunc('month', d)",
    "coalesce(a, 0) + round(b, 2)", "a IN (1, 2)", "CAST(a AS VARCHAR)",
])
def test_allows_scalar_and_aggregate_sql(sql):
    expressions.parse(sql)


@pytest.mark.parametrize("sql", [
    "read_csv('/etc/passwd')", "(SELECT 1)", "getenv('HOME')", "now()", "a IN (SELECT 1)", "count(*); DROP x",
    "sum(x) FROM t", "*", "query('SELECT 1')", "a -> 'x'", "read_parquet('s3://other/bucket')",
])
def test_refuses_anything_else(sql):
    with pytest.raises(SemanticError) as exc:
        expressions.parse(sql)
    assert exc.value.code == "unsafe_expression"


@pytest.mark.parametrize(("sql", "additive"), [
    ("sum(FLIGHT.distance)", True), ("count(*)", True), ("count(*) FILTER (WHERE NOT cancelled)", True),
    ("count(DISTINCT tail_nr)", False), ("avg(FLIGHT.dep_delay)", False), ("100.0 * sum(a) / count(*)", False),
])
def test_only_sums_and_counts_add_up(sql, additive):
    assert expressions.is_additive(expressions.parse(sql)) is additive


# Compiling governed queries

def test_metric_by_a_direct_dimension_matches_hand_written_sql():
    model = flights.model()
    compiled = compile_query(model, flights.schemas(model), spec(
        metrics=["average_departure_delay", "cancellation_pct"], dimensions=[{"field": "CARRIER.name"}],
        order_by=[{"name": "average_departure_delay", "desc": True}]))
    assert compiled.join_paths == {"CARRIER.name": ["flight_carrier"]}
    assert [b["view"] for b in compiled.bindings] == ["CARRIER", "FLIGHT"]
    expected = flights.local().execute("""
        SELECT c.name, avg(f.dep_delay), 100.0 * count(*) FILTER (WHERE f.cancelled) / nullif(count(*), 0)
        FROM FLIGHT f LEFT JOIN CARRIER c ON f.carrier_code = c.code GROUP BY ALL ORDER BY 2 DESC NULLS LAST
    """).fetchall()
    assert run(compiled) == expected
    assert [c["kind"] for c in compiled.columns] == ["dimension", "metric", "metric"]
    assert compiled.metrics[0]["unit"] == "minutes"


def test_shortest_unique_path_wins():
    model = flights.model()
    # CARRIER is reachable directly and through AIRCRAFT; the direct relationship is used unless `via` says otherwise.
    via = compile_query(model, flights.schemas(model), spec(
        metrics=["average_departure_delay"], dimensions=[{"field": "CARRIER.name", "via": ["aircraft_carrier"]}]))
    assert via.join_paths == {"CARRIER.name": ["flight_aircraft", "aircraft_carrier"]}


def test_ambiguous_paths_need_via_and_can_use_both():
    model = flights.model()
    with pytest.raises(SemanticError) as exc:
        compile_query(model, flights.schemas(model), spec(
            metrics=["average_departure_delay"], dimensions=[{"field": "AIRPORT.name"}]))
    assert exc.value.code == "ambiguous_join"
    assert exc.value.detail["options"] == [["flight_route", "route_departure_airport"],
                                           ["flight_route", "route_destination_airport"]]
    both = compile_query(model, flights.schemas(model), spec(
        metrics=["average_arrival_delay"],
        dimensions=[{"field": "AIRPORT.code", "via": ["route_departure_airport"]},
                    {"field": "AIRPORT.code", "via": ["route_destination_airport"]}],
        order_by=[{"name": "average_arrival_delay", "desc": True}], limit=3))
    names = [c["name"] for c in both.columns]
    assert names[:2] == ["AIRPORT__route_departure_airport.code", "AIRPORT__route_destination_airport.code"]
    expected = flights.local().execute("""
        SELECT o.code, d.code, avg(f.arr_delay) FROM FLIGHT f JOIN ROUTE r ON f.route_id = r.id
        JOIN AIRPORT o ON r.orig_airport_code = o.code JOIN AIRPORT d ON r.dest_airport_code = d.code
        GROUP BY ALL ORDER BY 3 DESC NULLS LAST LIMIT 4
    """).fetchall()
    assert run(both) == expected


def test_refuses_a_fan_out():
    model = flights.model()
    with pytest.raises(SemanticError) as exc:
        compile_query(model, flights.schemas(model), spec(
            metrics=["average_departure_delay"], dimensions=[{"field": "RUNWAY.length"}]))
    assert exc.value.code == "fan_out"


def test_time_grain_and_typed_filters():
    model = flights.model()
    compiled = compile_query(model, flights.schemas(model), spec(
        metrics=["on_time_arrival_pct"], dimensions=[{"field": "FLIGHT.date", "grain": "week"}],
        filters=[{"field": "FLIGHT.date", "op": "between", "value": ["2026-01-01", "2026-01-14"]},
                 {"field": "CARRIER.name", "op": "in", "value": ["Example Cloudline", "Example Aurora Air"]},
                 {"metric": "on_time_arrival_pct", "op": ">", "value": 0}]))
    assert "BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)" in compiled.sql
    assert "HAVING" in compiled.sql and compiled.params[-1] == 0
    rows = run(compiled)
    assert rows and all(str(r[0]) <= "2026-01-14" for r in rows)
    with pytest.raises(SemanticError) as exc:
        compile_query(model, flights.schemas(model), spec(
            metrics=["on_time_arrival_pct"], dimensions=[{"field": "CARRIER.name", "grain": "month"}]))
    assert exc.value.code == "invalid_grain"


def flights_with(*fields, metric_context=None):
    """The flights model with extra FLIGHT fields; it flags no dimensions, so every field is one."""
    inner = json.loads(flights.ENVELOPE["document"]["semantic_model"])["semantic_model"][0]
    next(d for d in inner["datasets"] if d["name"] == "FLIGHT")["fields"].extend(fields)
    if metric_context:
        next(m for m in inner["metrics"] if m["name"] == "cancellation_pct")["ai_context"] = metric_context
    return inner


@pytest.mark.parametrize("datatype", ["Date", "DateTime", "DateTimeTz"])
def test_ossie_datatypes_give_derived_fields_a_grain_and_typed_filters(datatype):
    model = ossie.parse(flights_with(
        {"name": "departed", "expression": "CAST(scheduled_departure AS TIMESTAMP)", "datatype": datatype},
        {"name": "delay_quarters", "expression": "CAST(dep_delay / 15 AS BIGINT)", "datatype": "Integer"},
    ), ["ai_flights"])
    compiled = compile_query(model, flights.schemas(model), spec(
        metrics=["cancellation_pct"], dimensions=[{"field": "FLIGHT.departed", "grain": "month"}],
        filters=[{"field": "FLIGHT.delay_quarters", "op": ">=", "value": 0}]))
    assert "date_trunc('month'" in compiled.sql and ">= CAST(? AS BIGINT)" in compiled.sql
    assert run(compiled)


def test_synonyms_of_fields_and_metrics_join_the_model_map():
    inner = flights_with({"name": "late", "expression": "dep_delay > 15", "datatype": "Boolean",
                          "ai_context": {"synonyms": ["delayed", 7]}},
                         metric_context={"synonyms": ["cancel rate"]})
    inner["ai_context"]["synonyms"] = {"flight": ["trip"]}
    assert ossie.parse(inner, ["ai_flights"]).synonyms == {
        "flight": ["trip"], "FLIGHT.late": ["delayed"], "cancellation_pct": ["cancel rate"]}


def test_values_never_become_sql():
    model = flights.model()
    compiled = compile_query(model, flights.schemas(model), spec(
        metrics=["cancellation_pct"], filters=[{"field": "CARRIER.name", "op": "=", "value": "x' OR 1=1 --"}]))
    assert "OR 1=1" not in compiled.sql and compiled.params == ["x' OR 1=1 --"]
    assert run(compiled) == [(None,)]


@pytest.mark.parametrize(("kwargs", "code"), [
    ({"metrics": ["nope"]}, "unknown_metric"),
    ({"metrics": ["cancellation_pct"], "dimensions": [{"field": "CARRIER.nope"}]}, "unknown_field"),
    ({"metrics": ["cancellation_pct"], "dimensions": [{"field": "NOPE.name"}]}, "unknown_field"),
    ({"metrics": ["cancellation_pct"], "order_by": [{"name": "x"}]}, "invalid_order"),
])
def test_names_must_exist(kwargs, code):
    model = flights.model()
    with pytest.raises(SemanticError) as exc:
        compile_query(model, flights.schemas(model), spec(**kwargs))
    assert exc.value.code == code


def test_input_validation():
    with pytest.raises(ValidationError):
        spec(metrics=["cancellation_pct"], filters=[{"field": "FLIGHT.date", "op": "in", "value": "2026-01-01"}])
    with pytest.raises(ValidationError):
        spec(metrics=["cancellation_pct"], dimensions=[{"field": "FLIGHT.date; DROP"}])
    with pytest.raises(ValidationError):
        spec(metrics=[], limit=10)


def test_an_unsafe_metric_from_a_shared_model_is_refused():
    inner = json.loads(flights.ENVELOPE["document"]["semantic_model"])["semantic_model"][0]
    inner["metrics"].append({"name": "leak", "expression": "max(getenv('HOME'))"})
    model = ossie.parse(inner, ["ai_flights"])
    with pytest.raises(SemanticError) as exc:
        compile_query(model, flights.schemas(model), spec(metrics=["leak"]))
    assert exc.value.code == "unsafe_expression"


def test_reachable_dimensions_for_describe():
    model = flights.model()
    found = {e["field"]: e for e in compiler.reachable(model, "FLIGHT")}
    assert found["CARRIER.name"]["path"] == ["flight_carrier"]
    assert found["AIRPORT.name"]["ambiguous"] is True
    assert "RUNWAY.length" not in found


def test_flagged_dimensions_limit_what_a_query_may_group_by():
    model = ossie.parse(flights.stored(flagged=True), ["ai_flights"])
    schemas = flights.schemas(model)
    weekly = compile_query(model, schemas, QuerySpec(
        metrics=["cancellation_pct"], dimensions=[{"field": "FLIGHT.date", "grain": "week"}]))
    rows = flights.local().execute(weekly.sql, weekly.params).fetchall()
    assert rows and all(0 <= pct <= 100 for _, pct in rows)
    # A measure is not a dimension once the model flags its dimensions.
    with pytest.raises(SemanticError, match="no dimension 'dep_delay'"):
        compile_query(model, schemas, QuerySpec(metrics=["cancellation_pct"], dimensions=[{"field": "FLIGHT.dep_delay"}]))


def test_plain_values_stay_exact_in_a_browser():
    assert worker.plain(2**60) == str(2**60) and worker.plain(42) == 42
    assert worker.plain(decimal.Decimal("12.50")) == 12.5
    assert worker.plain(decimal.Decimal("1" * 20)) == "1" * 20
    assert worker.plain(float("nan")) is None
    assert worker.plain(datetime.date(2026, 1, 2)) == "2026-01-02"


def test_execute_reports_truncation():
    result = worker.execute(flights.local(), 'SELECT id FROM "FLIGHT" ORDER BY id', [], 5)
    assert len(result["rows"]) == 5 and result["truncated"] and result["columns"][0]["name"] == "id"


def test_a_locked_session_cannot_read_local_files_and_answers_in_utc():
    connection = worker.connect()
    worker.lock(connection)
    assert worker.execute(connection, "SELECT current_setting('TimeZone')", [], 1)["rows"] == [["UTC"]]
    assert worker.execute(connection, "SELECT TIMESTAMPTZ '2026-09-30 23:30:00+00'::DATE", [], 1)["rows"] == [["2026-09-30"]]
    with pytest.raises(Exception, match="disabled"):
        connection.execute("SELECT * FROM read_csv('/etc/passwd')")
    with pytest.raises(Exception, match="configuration"):
        connection.execute("SET disabled_filesystems = ''")
