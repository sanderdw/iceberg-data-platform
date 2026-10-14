from openmetadata_sync import mapping as m
from tests.conftest import CUSTOMERS, DB, ORDERS, REVENUE, SHARE, listing


def test_names_are_quoted_like_openmetadata():
    item = listing()
    assert m.service_name("iceberg", "development") == "iceberg-development"
    assert m.table_fqn("iceberg", item, ["sales", "eu"], "report") == f'iceberg-development.{DB}."sales.eu".report'
    assert m.data_product_name("iceberg", item) == f"iceberg-{SHARE}"
    assert m.clean('a"b>c::d\x01') == "a'b)c:d"
    assert len(m.clean("x" * 400)) == 256


def test_columns_map_iceberg_types():
    columns = {c["name"]: c for c in (m.column(c) for c in ORDERS["columns"])}
    assert columns["id"] == {"name": "id", "dataType": "BIGINT", "dataTypeDisplay": "long",
                             "description": "Order number", "constraint": "NOT_NULL"}
    assert columns["amount"] == {"name": "amount", "dataType": "DECIMAL", "precision": 10, "scale": 2,
                                 "dataTypeDisplay": "decimal(10,2)"}
    assert columns["ordered_at"]["dataType"] == "TIMESTAMPZ"
    assert columns["address"]["children"] == [{"name": "city", "dataType": "STRING", "dataTypeDisplay": "string"}]
    assert (columns["tags"]["dataType"], columns["tags"]["arrayDataType"]) == ("ARRAY", "STRING")
    assert "children" not in columns["tags"]
    assert columns["lines"]["arrayDataType"] == "STRUCT"
    assert [c["name"] for c in columns["lines"]["children"]] == ["sku"]
    assert m.data_type("fixed[16]") == {"dataType": "FIXED", "dataLength": 16}
    assert m.data_type("binary") == {"dataType": "BYTES"}
    assert m.data_type("something-new") == {"dataType": "UNKNOWN"}


def test_partitions_and_metric_types():
    assert m.partition(ORDERS["partitionFields"]) == {"columns": [
        {"columnName": "ordered_at", "intervalType": "TIME-UNIT", "interval": "day"}]}
    assert m.partition([{"name": "b", "sourceColumn": "id", "transform": "bucket[16]"},
                        {"name": "i", "sourceColumn": "region", "transform": "identity"}]) == {"columns": [
        {"columnName": "id", "intervalType": "OTHER", "interval": "bucket[16]"},
        {"columnName": "region", "intervalType": "COLUMN-VALUE"}]}
    assert m.partition([]) is None
    assert m.metric_type("SUM(amount)") == "SUM"
    assert m.metric_type(" avg ( x ) ") == "AVERAGE"
    assert m.metric_type("SUM(a) / COUNT(*)") == "RATIO"
    assert m.metric_type("COUNT(DISTINCT id) + 1") == "OTHER"
    # A FILTER clause or a constant scale keeps the aggregate; 100 * a / b is a percentage.
    assert m.metric_type("count(*) FILTER (WHERE t.pct > 100 AND t.flag IN ('OK')) / 4.0") == "COUNT"
    assert m.metric_type("max(t.load) FILTER (WHERE t.flag IN ('OK', 'ESTIMATED'))") == "MAX"
    assert m.metric_type("100.0 * count(*) FILTER (WHERE bad) / nullif(count(*), 0)") == "PERCENTAGE"
    assert m.metric_type("SUM(a) FILTER (WHERE x) / SUM(b)") == "RATIO"


def test_tables_never_carry_view_sql_or_locations():
    item = listing()
    body = m.table_request("iceberg", item, item["objects"][2], [])
    assert body["tableType"] == "View" and body["description"] == "Monthly"
    assert set(body) == {"name", "tableType", "columns", "databaseSchema", "owners", "description"}
    table = m.table_request("iceberg", item, CUSTOMERS, [])
    assert "description" not in table and "tablePartition" not in table


def test_metrics_dimensions_and_keys():
    item = listing()
    metrics = m.metric_requests("iceberg", item, REVENUE, "Iceberg Data Platform", [])
    [(name, revenue, tables), (_, average, _)] = metrics
    assert name == f"iceberg__{DB}__sales__revenue__revenue"
    assert revenue["metricExpression"] == {"language": "SQL", "code": "SUM(ORDERS.amount)"}
    assert revenue["metricType"] == "SUM" and average["metricType"] == "RATIO"
    assert revenue["dimensions"] == [
        {"name": "ORDERS.ordered_at", "type": "TIME", "description": "Order time", "expression": "ordered_at"},
        {"name": "CUSTOMERS.region", "type": "CATEGORICAL"}]
    assert "Also called: sales." in revenue["description"]
    assert tables == [(("sales",), "orders"), (("sales",), "customers")]
    keys = m.constraints("iceberg", item)
    assert keys[(("sales",), "orders")] == [
        {"constraintType": "PRIMARY_KEY", "columns": ["id"]},
        {"constraintType": "FOREIGN_KEY", "columns": ["customer_id"],
         "referredColumns": [f"iceberg-development.{DB}.sales.customers.id"]}]
    # Without the customers table listed, the foreign key and its primary key are left out.
    partial = listing(objects=[ORDERS, REVENUE])
    assert m.constraints("iceberg", partial)[(("sales",), "orders")] == [
        {"constraintType": "PRIMARY_KEY", "columns": ["id"]}]


def test_lineage_and_data_product():
    item = listing()
    assert m.lineage_inputs("iceberg", item) == [((("sales",), "customers"), (("sales",), "orders"))]
    product = m.data_product_request("iceberg", item, "Iceberg Data Platform", [])
    assert product["name"] == f"iceberg-{SHARE}" and product["displayName"] == "orders-product"
    assert "data@example.test" in product["description"] and "metadata only" in product["description"]
    assert product["domains"] == ["Iceberg Data Platform"]
