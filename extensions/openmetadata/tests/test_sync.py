import asyncio
import copy
import html
import logging

import httpx
import pytest

from openmetadata_sync.errors import SyncError
from tests.conftest import CUSTOMERS, DB, ORDERS, REPORT, REVENUE, SHARE, TOKEN, listing

SERVICE = "iceberg-development"
ORDERS_FQN = f"{SERVICE}.{DB}.sales.orders"
REPORT_FQN = f'{SERVICE}.{DB}."sales.eu".report'
METRIC = f"iceberg__{DB}__sales__revenue__revenue"
PRODUCT = f"iceberg-{SHARE}"


def run(coro):
    return asyncio.run(coro)


def test_a_listing_is_published_as_a_data_product(world):
    world.bridge.listings[SHARE] = listing()
    run(world.syncer.cycle(full=True))
    om = world.om
    assert om.live("services/databaseServices") == [SERVICE]
    assert om.live("tables") == sorted([ORDERS_FQN, f"{SERVICE}.{DB}.sales.customers", REPORT_FQN])
    orders = om.entities["tables"][ORDERS_FQN]
    assert orders["tableType"] == "Iceberg" and orders["description"] == "All orders"
    assert orders["owners"][0]["type"] == "team"
    assert om.entities["teams"][f"team-{'e' * 32}"]["displayName"] == "data-team"
    report = om.entities["tables"][REPORT_FQN]
    assert report["tableType"] == "View" and "SELECT" not in str(report)
    metric = om.entities["metrics"][METRIC]
    assert metric["metricType"] == "SUM" and len(metric["assets"]) == 2
    assert metric["domains"] == ["Iceberg Data Platform"]
    ids = {fqn: e["id"] for fqn, e in om.entities["tables"].items()}
    # Tables feed the metrics, and lineage between listed tables comes from the producer conventions.
    assert (ids[ORDERS_FQN], metric["id"]) in om.lineage
    assert (ids[f"{SERVICE}.{DB}.sales.customers"], ids[ORDERS_FQN]) in om.lineage
    assert om.live("dataProducts") == [PRODUCT]
    average = om.entities["metrics"][f"iceberg__{DB}__sales__revenue__average_order"]
    schemas = {e["id"] for e in om.entities["databaseSchemas"].values()}
    assert len(schemas) == 2
    assert {a["id"] for a in om.assets[PRODUCT].values()} == {*ids.values(), *schemas, metric["id"], average["id"]}
    # The description draws the tables and keys, with the same relationships as a table.
    description = html.unescape(om.entities["dataProducts"][PRODUCT]["description"])
    assert "![Tables and keys of orders-product](data:image/svg+xml;base64," in description
    assert "| sales.orders | customer_id | sales.customers (id) |" in description
    assert (world.syncer.status["listings"], world.syncer.status["tables"], world.syncer.status["metrics"]) == (1, 3, 2)


def test_syncing_again_changes_nothing_and_polls_are_cheap(world):
    world.bridge.listings[SHARE] = listing()
    run(world.syncer.cycle(full=True))
    before = copy.deepcopy(world.om.entities)
    run(world.syncer.cycle(full=True))
    assert world.om.entities == before
    assert world.bridge.fetched[-1] == (SHARE, True)  # 304 on the full sync
    calls = len(world.om.calls)
    run(world.syncer.cycle())
    # A poll with an unchanged summary touches neither the listing nor OpenMetadata.
    assert len(world.om.calls) == calls and len(world.bridge.fetched) == 2


def test_schema_edits_arrive_with_the_full_sync(world):
    world.bridge.listings[SHARE] = listing()
    run(world.syncer.cycle(full=True))
    changed = copy.deepcopy(world.bridge.listings[SHARE])
    changed["objects"][0]["comment"] = "Every order"
    world.bridge.listings[SHARE] = changed
    run(world.syncer.cycle())
    assert world.om.entities["tables"][ORDERS_FQN]["description"] == "All orders"
    run(world.syncer.cycle(full=True))
    assert world.om.entities["tables"][ORDERS_FQN]["description"] == "Every order"


def test_unlisting_removes_and_relisting_restores(world):
    world.bridge.listings[SHARE] = listing()
    run(world.syncer.cycle(full=True))
    table_id = world.om.entities["tables"][ORDERS_FQN]["id"]
    # An object leaves the listing: its table goes, and so does its asset.
    world.bridge.listings[SHARE] = listing(objects=[ORDERS, REVENUE])
    run(world.syncer.cycle())
    assert REPORT_FQN not in world.om.live("tables")
    assert world.om.entities["tables"][REPORT_FQN]["deleted"] is True
    assert world.om.entities["tables"][REPORT_FQN]["id"] not in world.om.assets[PRODUCT]
    # The whole listing goes: tables and metrics are soft-deleted, the data product deleted.
    del world.bridge.listings[SHARE]
    run(world.syncer.cycle())
    assert world.om.live("tables") == [] and world.om.live("metrics") == [] and world.om.live("dataProducts") == []
    assert world.om.entities["tables"][ORDERS_FQN]["deleted"] is True
    world.bridge.listings[SHARE] = listing()
    run(world.syncer.cycle())
    assert world.om.entities["tables"][ORDERS_FQN]["id"] == table_id and ORDERS_FQN in world.om.live("tables")
    assert world.om.live("dataProducts") == [PRODUCT]


def test_entities_of_others_are_never_removed(world):
    world.bridge.listings[SHARE] = listing()
    run(world.syncer.cycle(full=True))
    run(world.om_client.put("metrics", {"name": "someone_elses_metric"}))
    del world.bridge.listings[SHARE]
    run(world.syncer.cycle())
    assert world.om.live("metrics") == ["someone_elses_metric"]


def test_a_failed_listing_blocks_removal(world):
    world.bridge.listings[SHARE] = listing()
    other = "share-" + "b" * 32
    world.bridge.listings[other] = listing(id=other, name="second", objects=[REPORT])
    run(world.syncer.cycle(full=True))
    # The second listing changes, but OpenMetadata refuses its tables: nothing may be removed.
    world.bridge.listings[other] = listing(id=other, name="second", objects=[REPORT, ORDERS])
    del world.bridge.listings[SHARE]
    world.om.forbid.add("tables")
    with pytest.raises(SyncError):
        run(world.syncer.cycle(full=True))
    world.om.forbid.clear()
    assert ORDERS_FQN in world.om.live("tables") and PRODUCT in world.om.live("dataProducts")


def test_teams_are_optional(world):
    world.om.forbid.add("teams")
    world.bridge.listings[SHARE] = listing()
    run(world.syncer.cycle(full=True))
    assert world.om.entities["tables"][ORDERS_FQN]["owners"] == []


@pytest.mark.parametrize(("change", "code"), [
    (lambda b: setattr(b, "refuse", "listing_not_enabled"), "listing_not_enabled"),
    (lambda b: setattr(b, "version", "0.1.0"), "incompatible_contract"),
    (lambda b: setattr(b, "capabilities", ["user-context"]), "incompatible_contract"),
])
def test_the_platform_must_allow_listings(world, change, code):
    change(world.bridge)
    with pytest.raises(SyncError) as exc:
        run(world.syncer.cycle(full=True))
    assert exc.value.code == code


def test_the_loop_reports_errors_without_the_token(world, caplog, monkeypatch):
    world.bridge.listings[SHARE] = listing()
    world.om.forbid.add("tables")

    async def once(seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", once)
    with caplog.at_level(logging.DEBUG), pytest.raises(asyncio.CancelledError):
        run(world.syncer.run())
    assert world.syncer.status["state"] == "error" and world.syncer.status["lastErrorCode"] == "catalog_forbidden"
    assert world.syncer.status["openMetadataVersion"] == "2.0.5"
    assert TOKEN not in caplog.text and "bridge-secret" not in caplog.text
    assert TOKEN not in repr(world.settings)


def test_a_listing_revoked_during_a_sync_is_skipped(world):
    world.bridge.listings[SHARE] = listing()
    original = world.bridge.handle

    def revoked(request):
        if request.url.path.endswith(SHARE):
            return httpx.Response(404, json={"error": "This listing does not exist.", "code": "not_found"})
        return original(request)

    world.bridge_client.http._transport.handler = revoked
    run(world.syncer.cycle(full=True))
    assert world.om.live("tables") == [] and world.syncer.cache == {}


def test_foreign_keys_wait_for_the_tables_they_refer_to(world):
    # orders comes before customers, and an employee table refers to itself: every key still lands.
    employees = {"kind": "table", "namespace": ["sales"], "name": "employees", "uuid": "u9", "comment": "",
                 "columns": [{"id": 1, "name": "id", "type": "long", "required": True, "doc": "", "children": []},
                             {"id": 2, "name": "manager_id", "type": "long", "required": False, "doc": "",
                              "children": []}],
                 "partitionFields": [], "rowCount": None, "updatedAt": None, "producer": None}
    model = copy.deepcopy(REVENUE)
    model["models"][0]["datasets"].append({
        "name": "EMPLOYEES", "description": "", "source": "lakehouse.sales.employees",
        "table": {"namespace": ["sales"], "name": "employees"}, "primaryKey": ["id"], "aiContext": [], "fields": []})
    model["models"][0]["relationships"].append({"name": "manager", "from": "EMPLOYEES", "to": "EMPLOYEES",
                                                "fromColumns": ["manager_id"], "toColumns": ["id"]})
    item = listing()
    item["objects"] = [employees, *item["objects"][:-1], model]
    world.bridge.listings[SHARE] = item
    run(world.syncer.cycle(full=True))
    assert world.syncer.status["lastErrorCode"] is None
    tables = world.om.entities["tables"]
    keys = {fqn.rsplit(".", 1)[1]: [c["constraintType"] for c in t.get("tableConstraints", [])]
            for fqn, t in tables.items()}
    assert keys["orders"] == ["PRIMARY_KEY", "FOREIGN_KEY"] and keys["employees"] == ["PRIMARY_KEY", "FOREIGN_KEY"]


def test_changed_descriptions_reach_openmetadata_and_its_own_ones_stay(world):
    world.bridge.listings[SHARE] = listing()
    run(world.syncer.cycle(full=True))
    om = world.om
    assert om.patches == []  # Nothing to correct on first publish, despite the stored `&#43;` escaping.
    customers = f"{SERVICE}.{DB}.sales.customers"
    # Someone documents a column in OpenMetadata that the platform leaves empty.
    om.entities["tables"][customers]["columns"][0]["description"] = "Written in the catalog"
    changed = listing()
    changed["version"] = "v2"
    changed["description"] = "Orders of the webshop + returns"
    changed["objects"][0] = {**ORDERS, "comment": "Every order",
                             "columns": [{**ORDERS["columns"][0], "doc": "Order id"}, *ORDERS["columns"][1:]]}
    world.bridge.listings[SHARE] = changed
    run(world.syncer.cycle(full=True))
    orders = om.entities["tables"][ORDERS_FQN]
    assert orders["description"] == "Every order" and orders["columns"][0]["description"] == "Order id"
    assert om.entities["dataProducts"][PRODUCT]["description"].startswith("Orders of the webshop &#43; returns")
    assert om.entities["tables"][customers]["columns"][0]["description"] == "Written in the catalog"
    patches = len(om.patches)
    run(world.syncer.cycle(full=True))
    assert len(om.patches) == patches  # Settled: a further sync patches nothing.


def test_semantic_models_add_terms_notes_and_a_section(world):
    item = listing()
    model = copy.deepcopy(REVENUE)
    model["models"][0]["aiContext"] = [{"label": "Instructions", "value": "Amounts are in EUR."}]
    model["models"][0]["datasets"][0]["description"] = "A webshop order."
    model["models"][0]["datasets"][1]["aiContext"] = [{"label": "Instructions", "value": "One row per customer"}]
    region = model["models"][0]["datasets"][1]["fields"][0]
    region.update(label="Region", aiContext=[{"label": "Examples", "value": "north, south"}])
    model["models"][0]["metrics"][0]["aiContext"] = [{"label": "Instructions", "value": "Never sum over time."}]
    item["objects"][-1] = model
    world.bridge.listings[SHARE] = item
    run(world.syncer.cycle(full=True))
    om = world.om
    glossary = "Iceberg Data Platform"
    model_term = f"iceberg__{DB}__sales__revenue"
    root = f"{glossary}.{model_term}"
    orders_term, customers_term = f"{root}.{model_term}__ORDERS", f"{root}.{model_term}__CUSTOMERS"
    ordered_at_term = f"{orders_term}.{model_term}__ORDERS__ordered_at"
    region_term = f"{customers_term}.{model_term}__CUSTOMERS__region"
    assert sorted(om.live("glossaryTerms")) == sorted(
        [root, orders_term, customers_term, ordered_at_term, region_term])
    # Field labels are tags of the extension's classification, on the column and on the field's term.
    label = f"{glossary} labels.Region"
    assert om.live("classifications") == [f"{glossary} labels"] and om.live("tags") == [label]
    term = om.entities["glossaryTerms"][region_term]
    assert term["displayName"] == "region"
    assert term["description"] == "Field region of CUSTOMERS.\n\nExamples: north, south."
    assert [t["tagFQN"] for t in term["tags"]] == [label]
    customers = om.entities["tables"][f"{SERVICE}.{DB}.sales.customers"]
    assert customers["description"] == (
        "In semantic model Revenue, CUSTOMERS.\n\nInstructions: One row per customer.")
    region = next(c for c in customers["columns"] if c["name"] == "region")
    assert region["description"] == "Sales region"  # the platform's own doc stays
    assert [(t["tagFQN"], t["source"]) for t in region["tags"]] == [
        (region_term, "Glossary"), (label, "Classification")]
    metric = om.entities["metrics"][f"iceberg__{DB}__sales__revenue__revenue"]
    assert "Instructions: Never sum over time." in metric["description"]
    assert om.entities["glossaryTerms"][root]["displayName"] == "Revenue"
    orders = om.entities["tables"][ORDERS_FQN]
    assert orders["description"] == "All orders\n\nIn semantic model Revenue, ORDERS: A webshop order."
    assert [t["tagFQN"] for t in orders["tags"]] == [orders_term]
    ordered_at = next(c for c in orders["columns"] if c["name"] == "ordered_at")
    assert ordered_at["description"] == "Order time" and [t["tagFQN"] for t in ordered_at["tags"]] == [
        ordered_at_term]
    # A column the platform documents keeps its own description.
    assert next(c for c in orders["columns"] if c["name"] == "id")["description"] == "Order number"
    description = html.unescape(om.entities["dataProducts"][PRODUCT]["description"])
    assert "### Semantic model Revenue\n\nWebshop revenue\n\n**Instructions:** Amounts are in EUR." in description
    assert "- **ORDERS** (sales.orders): A webshop order." in description
    assert "- **revenue**: Sum of order amounts" in description
    # Without the model, its terms go, and so do their tags on the tables.
    item = listing(objects=[ORDERS, CUSTOMERS])
    item["version"] = "v2"
    world.bridge.listings[SHARE] = item
    run(world.syncer.cycle(full=True))
    assert om.live("glossaryTerms") == [] and om.live("tags") == []
    for table in (ORDERS_FQN, f"{SERVICE}.{DB}.sales.customers"):
        found = om.entities["tables"][table]
        assert not found.get("tags") and not any(c.get("tags") for c in found["columns"])


def test_fields_with_the_same_name_get_terms_of_their_own(world):
    # OpenMetadata wants term names unique in a glossary; many datasets have an `id` or a `name`.
    model = copy.deepcopy(REVENUE)
    for dataset, text in zip(model["models"][0]["datasets"], ("Order number", "Customer number"), strict=True):
        dataset["fields"].append({"name": "id", "label": "", "description": text, "datatype": "Integer",
                                  "dimension": False, "timeDimension": False, "synonyms": [], "aiContext": [],
                                  "expressions": []})
    item = listing()
    item["objects"][-1] = model
    world.bridge.listings[SHARE] = item
    run(world.syncer.cycle(full=True))
    assert world.syncer.status["lastErrorCode"] is None
    ids = {e["description"] for e in world.om.entities["glossaryTerms"].values() if e["displayName"] == "id"}
    assert ids == {"Order number", "Customer number"}
