import json

import pytest
from pydantic import ValidationError

from conversationalbi.errors import CbiError
from conversationalbi.semantic import compiler, expressions, ossie
from conversationalbi.semantic.compiler import QuerySpec, compile_query
from tests import flights


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
    (True, None), (False, None), (None, None), ("yes", None),  # only the object marks a dimension
])
def test_dimensions_follow_the_ossie_object(flag, expected):
    assert ossie.dimension({} if flag is None else {"dimension": flag}) is expected


def test_reads_the_model_at_the_root_of_the_document():
    inner = json.loads(flights.ENVELOPE["document"]["semantic_model"])
    assert inner["version"] == "0.3.0.dev0" and inner["name"] == "Flights"
    assert ossie.parse(inner).metric("cancellation_pct")
    # Ossie 0.3.0.dev0 removed the semantic_model list, and an earlier document is not read.
    old = {"document": {"semantic_model": json.dumps({"version": "0.2.0", "semantic_model": [inner]})}}
    with pytest.raises(CbiError, match="no datasets"):
        ossie.parse(old)


def test_rejects_models_it_cannot_trust():
    base = {"datasets": [{"name": "A", "source": "ns.a", "primary_key": ["id"], "fields": []}]}
    with pytest.raises(CbiError, match="unsupported name"):
        ossie.parse({**base, "metrics": [{"name": "x; drop", "expression": {"sql": "count(*)"}}]})
    with pytest.raises(CbiError, match="does not define"):
        ossie.parse({**base, "relationships": [{"name": "r", "from": "A", "to": "B", "from_columns": ["b"],
                                                "to_columns": ["id"]}]})
    with pytest.raises(CbiError, match="no datasets"):
        ossie.parse({"document": {"semantic_model": "not json"}})


@pytest.mark.parametrize(("key", "count", "item"), [
    ("datasets", ossie.MAX_DATASETS + 1, lambda i: {"name": f"D{i}", "source": f"ns.t{i}", "fields": []}),
    ("metrics", ossie.MAX_METRICS + 1, lambda i: {"name": f"m{i}", "expression": {"sql": "count(*)"}}),
    ("relationships", ossie.MAX_RELATIONSHIPS + 1,
     lambda i: {"name": f"r{i}", "from": "A", "to": "A", "from_columns": ["id"], "to_columns": ["id"]}),
])
def test_rejects_models_too_large_to_plan(key, count, item):
    base = {"datasets": [{"name": "A", "source": "ns.a", "fields": [{"name": "id", "expression": {"sql": "id"}}]}]}
    with pytest.raises(CbiError, match=f"has {count} {key}"):
        ossie.parse({**base, key: [item(i) for i in range(count)]})
    fields = [{"name": f"f{i}", "expression": {"sql": f"f{i}"}} for i in range(ossie.MAX_FIELDS + 1)]
    with pytest.raises(CbiError, match="fields in dataset 'A'"):
        ossie.parse({"datasets": [{"name": "A", "source": "ns.a", "fields": fields}]})


def test_prefers_duckdb_then_the_portable_sql_then_ansi_sql():
    def metric(expression):
        model = ossie.parse({"datasets": [{"name": "A", "source": "ns.t", "fields": []}], "metrics": [
            {"name": "m", "expression": expression}]})
        found = model.metric("m")
        return found.expression, found.dialect

    ansi, duckdb = {"dialect": "ANSI_SQL", "expression": "count(*)"}, {"dialect": "DUCKDB", "expression": "count(A.x)"}
    assert metric({"sql": "count(A.y)", "dialects": [ansi, duckdb]}) == ("count(A.x)", "DUCKDB")
    assert metric({"sql": "count(A.y)", "dialects": [ansi]}) == ("count(A.y)", "OSSIE_SQL_2026")
    # The top-level sql wins over an OSSIE_SQL_2026 entry.
    portable = {"dialect": "OSSIE_SQL_2026", "expression": "1"}
    assert metric({"sql": "count(A.y)", "dialects": [portable]})[0] == "count(A.y)"
    assert metric({"dialects": [ansi]}) == ("count(*)", "ANSI_SQL")
    assert metric({"dialects": [{"dialect": "SNOWFLAKE", "expression": "count(*)"}]}) == ("", "")


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
    with pytest.raises(CbiError) as exc:
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
    with pytest.raises(CbiError) as exc:
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
    with pytest.raises(CbiError) as exc:
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
    with pytest.raises(CbiError) as exc:
        compile_query(model, flights.schemas(model), spec(
            metrics=["on_time_arrival_pct"], dimensions=[{"field": "CARRIER.name", "grain": "month"}]))
    assert exc.value.code == "invalid_grain"


def flights_with(*fields, metric_context=None):
    """The flights model with extra FLIGHT fields; it flags no dimensions, so every field is one."""
    inner = json.loads(flights.ENVELOPE["document"]["semantic_model"])
    next(d for d in inner["datasets"] if d["name"] == "FLIGHT")["fields"].extend(
        {**f, "expression": {"sql": f["expression"]}} for f in fields)
    if metric_context:
        next(m for m in inner["metrics"] if m["name"] == "cancellation_pct")["ai_context"] = metric_context
    return inner


@pytest.mark.parametrize("datatype", ["Date", "DateTime", "DateTimeTz"])
def test_ossie_datatypes_give_derived_fields_a_grain_and_typed_filters(datatype):
    model = ossie.parse(flights_with(
        {"name": "departed", "expression": "CAST(scheduled_departure AS TIMESTAMP)", "datatype": datatype},
        {"name": "delay_quarters", "expression": "CAST(dep_delay / 15 AS BIGINT)", "datatype": "Integer"},
    ))
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
    assert ossie.parse(inner).synonyms == {
        "flight": ["trip"], "FLIGHT.late": ["delayed"], "cancellation_pct": ["cancel rate"]}


def test_example_questions_come_from_the_model_context():
    inner = flights_with()
    inner["ai_context"]["examples"] = ["  Which carrier is late most often? ", "", 7, "x" * 400]
    assert ossie.parse(inner).examples == ("Which carrier is late most often?", "x" * 300)
    assert flights.model().examples == ()


def test_values_never_become_sql():
    model = flights.model()
    compiled = compile_query(model, flights.schemas(model), spec(
        metrics=["cancellation_pct"], filters=[{"field": "CARRIER.name", "op": "=", "value": "x' OR 1=1 --"}]))
    assert "OR 1=1" not in compiled.sql and compiled.params == ["x' OR 1=1 --"]
    assert run(compiled) == [(None,)]


@pytest.mark.parametrize("datatype", ["decimal) OR 1=1 OR (1=0", "decimal(10,2)) OR (1=1", "Date) OR (1=1"])
def test_a_declared_datatype_never_becomes_sql(datatype):
    # A shared model's datatype is as untrusted as its expressions; only known types become a cast.
    model = ossie.parse(flights_with({"name": "delay_cost", "expression": "dep_delay * 2", "datatype": datatype}))
    compiled = compile_query(model, flights.schemas(model), spec(
        metrics=["cancellation_pct"], filters=[{"field": "FLIGHT.delay_cost", "op": "=", "value": -999999}]))
    assert "1=1" not in compiled.sql and compiled.sql.count("?") == 1
    assert run(compiled) == [(None,)]


@pytest.mark.parametrize(("kind", "placeholder"), [
    ("decimal(10, 2)", "CAST(? AS DECIMAL(10, 2))"), ("decimal(38)", "CAST(? AS DECIMAL(38, 0))"),
    ("decimal(99,2)", "?"), ("decimal(4,6)", "?"), ("Date", "CAST(? AS DATE)"), ("timestamp(6)", "?"), (None, "?"),
])
def test_only_known_types_become_casts(kind, placeholder):
    assert compiler.typed(kind) == placeholder


@pytest.mark.parametrize(("op", "value", "where"), [
    ("=", True, "(cancelled OR dep_delay > 15) = true"),
    ("is_null", None, "(cancelled OR dep_delay > 15) IS NULL"),
    ("is_not_null", None, "(cancelled OR dep_delay > 15) IS NOT NULL"),
])
def test_derived_fields_keep_their_meaning_in_filters(op, value, where):
    model = ossie.parse(flights_with(
        {"name": "disrupted", "expression": "cancelled OR dep_delay > 15", "datatype": "Boolean"}))
    compiled = compile_query(model, flights.schemas(model), spec(
        metrics=["cancellation_pct"], filters=[{"field": "FLIGHT.disrupted", "op": op, "value": value}]))
    expected = flights.local().execute(
        f"SELECT 100.0 * count(*) FILTER (WHERE cancelled) / nullif(count(*), 0) FROM FLIGHT WHERE {where}").fetchall()
    assert run(compiled) == expected


@pytest.mark.parametrize(("kwargs", "code"), [
    ({"metrics": ["nope"]}, "unknown_metric"),
    ({"metrics": ["cancellation_pct"], "dimensions": [{"field": "CARRIER.nope"}]}, "unknown_field"),
    ({"metrics": ["cancellation_pct"], "dimensions": [{"field": "NOPE.name"}]}, "unknown_field"),
    ({"metrics": ["cancellation_pct"], "order_by": [{"name": "x"}]}, "invalid_order"),
])
def test_names_must_exist(kwargs, code):
    model = flights.model()
    with pytest.raises(CbiError) as exc:
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
    inner = json.loads(flights.ENVELOPE["document"]["semantic_model"])
    inner["metrics"].append({"name": "leak", "expression": {"sql": "max(getenv('HOME'))"}})
    model = ossie.parse(inner)
    with pytest.raises(CbiError) as exc:
        compile_query(model, flights.schemas(model), spec(metrics=["leak"]))
    assert exc.value.code == "unsafe_expression"


def test_reachable_dimensions_for_describe():
    model = flights.model()
    found = {e["field"]: e for e in compiler.reachable(model, "FLIGHT")}
    assert found["CARRIER.name"]["path"] == ["flight_carrier"]
    assert found["AIRPORT.name"]["ambiguous"] is True
    assert "RUNWAY.length" not in found


# Apache Ossie 0.3.0.dev0 name resolution: fields, source.column, dataset-scoped metrics and AGG

SHOP = {
    "version": "0.3.0.dev0", "name": "shop",
    "datasets": [
        {"name": "orders", "source": "shop.orders", "primary_key": ["order_id"], "fields": [
            {"name": "order_id", "expression": {"sql": "o_id"}},
            {"name": "customer_id", "expression": {"sql": "o_cust"}},
            {"name": "net", "expression": {"sql": "amount - discount"}},
            {"name": "status", "expression": {"sql": "upper(o_status)"}, "dimension": {}}],
         "metrics": [
            {"name": "net_total", "expression": {"sql": "SUM(net)"}},
            {"name": "gross_total", "expression": {"sql": "SUM(source.amount)"}},
            {"name": "order_count", "expression": {"sql": "COUNT(*)"}}]},
        # The one side's key is a unique key, and its field is computed.
        {"name": "customers", "source": "shop.customers", "unique_keys": [["id"]], "fields": [
            {"name": "id", "expression": {"sql": "CAST(c_ref AS INTEGER)"}},
            {"name": "region", "expression": {"sql": "c_region"}, "dimension": {}}]},
    ],
    "relationships": [{"name": "orders_customers", "from": "orders", "to": "customers",
                       "from_columns": ["CUSTOMER_ID"], "to_columns": ["id"]}],
    "metrics": [
        {"name": "net_per_order", "expression": {"sql": "AGG(orders.net_total) / AGG(orders.order_count)"}},
        {"name": "gross", "expression": {"sql": "agg(Orders.gross_total)"}},
        {"name": "big_orders", "expression": {"sql": "COUNT(*) FILTER (WHERE orders.net > 10)"}},
    ],
}


def shop_tables():
    import duckdb

    connection = duckdb.connect()
    connection.execute("CREATE TABLE orders AS SELECT * FROM (VALUES (1, 10, 20.0, 5.0, 'open'), "
                       "(2, 10, 8.0, 0.0, 'done'), (3, 11, 30.0, 10.0, 'open')) "
                       "t(o_id, o_cust, amount, discount, o_status)")
    connection.execute("CREATE TABLE customers AS SELECT * FROM (VALUES ('10', 'north'), ('11', 'south')) "
                       "t(c_ref, c_region)")
    return connection


def test_dataset_scoped_metrics_agg_and_computed_join_keys_answer_correctly():
    model = ossie.parse(SHOP)
    assert [m.name for m in model.metrics] == ["net_per_order", "gross", "big_orders", "orders.net_total",
                                               "orders.gross_total", "orders.order_count"]
    compiled = compile_query(model, {}, spec(
        metrics=["orders.net_total", "orders.gross_total", "net_per_order", "gross", "big_orders"],
        dimensions=[{"field": "customers.region"}], order_by=[{"name": "customers.region"}]))
    # The join compares the relationship's fields, so both computed expressions appear.
    assert 'ON "orders"."o_cust" = (CAST("customers"."c_ref" AS INT))' in compiled.sql
    assert shop_tables().execute(compiled.sql, compiled.params).fetchall() == [
        ("north", 23.0, 28.0, 11.5, 28.0, 1), ("south", 20.0, 30.0, 20.0, 30.0, 1)]
    filtered = compile_query(model, {}, spec(metrics=["orders.order_count"],
                                             filters=[{"field": "orders.status", "op": "=", "value": "OPEN"}]))
    assert shop_tables().execute(filtered.sql, filtered.params).fetchall() == [(2,)]


@pytest.mark.parametrize(("sql", "dataset", "message"), [
    ("SUM(net)", None, "names fields as dataset.field"),
    ("SUM(orders.amount)", None, "not a field of orders"),
    ("SUM(orders.net_total)", None, r"AGG\(orders.net_total\)"),
    ("AGG(orders.net)", None, "AGG evaluates a dataset-scoped metric"),
    ("AGG(net_per_order)", None, "AGG evaluates a dataset-scoped metric"),
    ("AGG(orders.net_total) FILTER (WHERE orders.net > 1)", None, "no FILTER"),
    ('SUM(orders."net")', None, "not a field of orders"),  # quoted identifiers match exactly, upper-cased
    ("SUM(customers.region)", "orders", "metric that reads other datasets belongs in the model's metrics"),
    ("SUM(o_id)", "orders", r"Write source.o_id"),
    ("SUM(net_total)", "orders", "a metric cannot refer to a metric"),
    ('SUM("source".amount)', "orders", "reads its fields by name"),
    ("AGG(source.amount)", "orders", "no measures"),
    ("METRIC(SUM(net))", "orders", "reserved"),
])
def test_references_follow_ossie_name_resolution(sql, dataset, message):
    from conversationalbi.semantic import resolve

    model = ossie.parse(SHOP)
    metric = ossie.Metric(f"{dataset}.x" if dataset else "x", sql, dataset=dataset)
    with pytest.raises(CbiError, match=message):
        resolve.metric(model, metric)


def test_identifiers_compare_without_regard_to_case_unless_quoted():
    from conversationalbi.semantic import resolve

    model = ossie.parse(SHOP)
    for sql in ("SUM(ORDERS.Net)", 'SUM(orders."NET")', "SUM(SOURCE.amount) + SUM(Net)"):
        resolve.metric(model, ossie.Metric("orders.x" if "SOURCE" in sql else "x", sql,
                                           dataset="orders" if "SOURCE" in sql else None))
    # A vendor dialect keeps its own quoting rules: there, a quoted name compares without regard to case.
    resolve.metric(model, ossie.Metric("x", 'SUM(orders."net")', dialect="DUCKDB"))


def test_a_field_named_source_hides_the_source_columns():
    from conversationalbi.semantic import resolve

    shop = json.loads(json.dumps(SHOP))
    shop["datasets"][0]["fields"].append({"name": "source", "expression": {"sql": "o_channel"}})
    model = ossie.parse(shop)
    with pytest.raises(CbiError, match="reads its fields by name"):
        resolve.metric(model, model.metric("orders.gross_total"))


def test_models_with_clashing_names_are_refused():
    shop = json.loads(json.dumps(SHOP))
    shop["datasets"][0]["metrics"].append({"name": "NET", "expression": {"sql": "SUM(net)"}})
    with pytest.raises(CbiError, match="names field or metric 'NET' twice"):
        ossie.parse(shop)
    shop = json.loads(json.dumps(SHOP))
    shop["relationships"][0]["to_columns"] = ["c_ref"]
    with pytest.raises(CbiError, match="not a field of customers"):
        ossie.parse(shop)
