"""The catalog's projection of the Bridge table-property conventions: typed, capped and link-safe."""

import json

from user_portal.catalog import object_details, producer

P = "iceberg-data-platform."
EXTENSIONS = {"dbt": "http://localhost:3004"}


def values(**extra):
    return {
        P + "producer": "dbt", P + "producer.version": "iceberg-dbt 0.1.0",
        P + "producer.url": "http://localhost:3004/#/projects/prj-1/pipeline?node=model.shop.orders",
        P + "producer.run-at": "2026-09-29T20:00:00+00:00", P + "producer.source-revision": "a" * 40,
        P + "producer.node": "model.shop.orders",
        P + "lineage.inputs": json.dumps([{"database": "db-1", "namespace": ["staging"], "name": "stg_orders"}]),
        P + "quality.status": "pass", P + "quality.summary": "3 passed", **extra,
    }


def test_producer_is_projected_with_its_lineage_and_quality():
    result = producer(values(), EXTENSIONS)
    assert result["name"] == "dbt" and result["extension"] == "dbt"
    assert result["url"].startswith("http://localhost:3004/#/projects/prj-1")
    assert result["inputs"] == [{"database": "db-1", "namespace": ["staging"], "name": "stg_orders"}]
    assert result["quality"] == {"status": "pass", "summary": "3 passed", "checkedAt": None}
    assert producer({"comment": "x"}, EXTENSIONS) is None


def test_untrusted_values_are_neutralized():
    hostile = producer(values(**{
        P + "producer.url": "https://evil.example/#/phish",
        P + "lineage.inputs": json.dumps([{"database": 1}, "x", {"database": "db-2", "namespace": "a", "name": "b"},
                                          *({"database": "db-3", "namespace": ["n"], "name": f"t{i}"} for i in range(80))]),
        P + "quality.status": "<script>", P + "producer": "d" * 500,
    }), EXTENSIONS)
    assert hostile["url"] is None and hostile["extension"] is None
    assert len(hostile["inputs"]) == 50 and all(i["database"] == "db-3" for i in hostile["inputs"])
    assert hostile["quality"] is None and len(hostile["name"]) == 40
    assert producer(values(**{P + "producer.url": "javascript:alert(1)"}), EXTENSIONS)["url"] is None
    assert producer(values(**{P + "lineage.inputs": "not json"}), EXTENSIONS)["inputs"] == []


def test_table_details_carry_comment_and_producer():
    loaded = {"metadata": {"table-uuid": "u", "format-version": 2, "current-schema-id": 0, "schemas": [
        {"schema-id": 0, "fields": [{"id": 1, "name": "id", "type": "int", "doc": "Key"}]}],
        "properties": {**values(), "comment": "Orders per day"}}}
    detail = object_details("table", loaded, EXTENSIONS)
    assert detail["comment"] == "Orders per day" and detail["producer"]["node"] == "model.shop.orders"
    assert detail["columns"][0]["doc"] == "Key"
    assert object_details("table", loaded)["producer"]["url"] is None
