"""Semantic models (Apache Ossie 0.3.0.dev0 in Polaris): projection, listing that tolerates a disabled feature and cleanup."""

import json

import httpx
import pytest

from server.catalog_metadata import semantic_model_details
from server.models import ServiceError
from server.polaris import ROLES, PolarisProvider
from test.conftest import SEMANTIC_MODEL
from test.test_user_portal import HEADERS, JSON, login, promote, users  # noqa: F401 - fixture


def test_document_projection_is_flat_and_keeps_the_stored_definition():
    detail = semantic_model_details("revenue", ["analytics"], SEMANTIC_MODEL)
    assert (detail["specVersion"], detail["entityVersion"], detail["namespace"]) == ("0.3.0.dev0", "3", ["analytics"])
    model = detail["model"]
    assert model["name"] == "Analytics"
    assert model["aiContext"] == [
        {"label": "Instructions", "value": "Count events per user.\nIgnore test users."},
        {"label": "Synonyms", "value": "activity"},
    ]
    dataset = model["datasets"][0]
    assert dataset["table"] == {"namespace": ["analytics"], "name": "events"} and dataset["primaryKey"] == ["id"]
    assert dataset["fields"][0]["expressions"] == [{"dialect": "OSSIE_SQL_2026", "expression": "id"}]
    assert dataset["metrics"][0]["name"] == "users" and dataset["uniqueKeys"] == []
    assert model["metrics"][0]["expressions"] == [{"dialect": "OSSIE_SQL_2026", "expression": "COUNT(*)"},
                                                  {"dialect": "DUCKDB", "expression": "count(*)"}]
    assert model["relationships"][0]["fromColumns"] == ["user_id"]
    assert json.loads(detail["definition"])["datasets"][0]["name"] == "EVENTS"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # Polaris resolves a source as `<namespace>….<table>` in the model's catalog.
        ("analytics.nested.events", (["analytics", "nested"], "events")),
        ("sales.orders", (["sales"], "orders")),
        ("orders", None),
        ("sales..orders", None),
    ],
)
def test_dataset_sources_resolve_every_namespace_level(source, expected):
    loaded = {"document": {"semantic_model": json.dumps({"datasets": [{"name": "D", "source": source}]})}}
    table = semantic_model_details("m", ["own"], loaded)["model"]["datasets"][0]["table"]
    assert (table and (table["namespace"], table["name"])) == expected


@pytest.mark.parametrize(
    ("context", "rows"),
    [
        (None, []),
        ("Use for sales", [{"label": "Context", "value": "Use for sales"}]),
        ({"examples": ["a", {"q": 1}]}, [{"label": "Examples", "value": 'a, {"q": 1}'}]),
    ],
)
def test_ai_context_becomes_label_value_rows(context, rows):
    loaded = {"document": {"semantic_model": json.dumps({"datasets": [], "ai_context": context})}}
    assert semantic_model_details("m", [], loaded)["model"]["aiContext"] == rows


def test_projection_reads_the_model_at_the_root_of_the_document():
    payload = {"version": "0.3.0.dev0", "name": "M", "datasets": [{"name": "A", "source": "ns.t", "unique_keys": [["x"]]}]}
    detail = semantic_model_details("m", ["ns"], {"document": {"version": "0.3.0.dev0",
                                                               "semantic_model": json.dumps(payload)}})
    assert detail["model"]["datasets"][0]["table"] == {"namespace": ["ns"], "name": "t"}
    assert detail["model"]["datasets"][0]["uniqueKeys"] == [["x"]] and detail["model"]["name"] == "M"


def test_a_document_with_a_list_of_models_is_not_read():
    # Ossie 0.3.0.dev0 removed the semantic_model list: one document holds one model.
    old = {"version": "0.2.0", "semantic_model": [{"datasets": [{"name": "A", "source": "t"}]}]}
    detail = semantic_model_details("m", ["ns"], {"document": {"semantic_model": json.dumps(old)}})
    assert detail["model"] is None and "semantic_model" in detail["definition"]


@pytest.mark.parametrize(
    ("dimension", "flags"),
    [
        ({"is_time": True}, (True, True)),  # Ossie: a time dimension
        ({"is_time": False}, (True, False)),
        ({}, (True, False)),  # an object without is_time is still a dimension
        (True, (False, False)),  # only the object marks a dimension
        (None, (False, False)),
        (False, (False, False)),
        ("yes", (False, False)),
    ],
)
def test_dimensions_follow_the_ossie_object(dimension, flags):
    field = {"name": "f"} if dimension is None else {"name": "f", "dimension": dimension}
    loaded = {"document": {"semantic_model": json.dumps({"datasets": [{"name": "D", "source": "t", "fields": [field]}]})}}
    shown = semantic_model_details("m", [], loaded)["model"]["datasets"][0]["fields"][0]
    assert (shown["dimension"], shown["timeDimension"]) == flags


@pytest.mark.parametrize(("dimension", "datatype", "time"), [
    ({}, "DateTimeTz", True),  # without is_time, a temporal datatype makes it a time dimension
    ({}, "Time", True),
    ({}, "String", False),
    ({"is_time": False}, "Date", False),  # an explicit is_time wins
])
def test_time_dimensions_follow_the_ossie_datatype(dimension, datatype, time):
    field = {"name": "f", "dimension": dimension, "datatype": datatype}
    loaded = {"document": {"semantic_model": json.dumps({"datasets": [{"name": "D", "source": "t", "fields": [field]}]})}}
    assert semantic_model_details("m", [], loaded)["model"]["datasets"][0]["fields"][0]["timeDimension"] is time


def test_fields_and_metrics_show_their_synonyms():
    model = {"datasets": [{"name": "D", "source": "t", "fields": [
                 {"name": "f", "ai_context": {"synonyms": ["eff", 3]}}, {"name": "g", "ai_context": "text"}],
                 "metrics": [{"name": "n", "ai_context": {"synonyms": ["en"]}}]}],
             "metrics": [{"name": "m", "ai_context": {"synonyms": ["em"]}}]}
    shown = semantic_model_details("m", [], {"document": {"semantic_model": json.dumps(model)}})["model"]
    assert [f["synonyms"] for f in shown["datasets"][0]["fields"]] == [["eff"], []]
    assert shown["metrics"][0]["synonyms"] == ["em"] and shown["datasets"][0]["metrics"][0]["synonyms"] == ["en"]


def test_labels_and_ai_context_of_datasets_fields_and_metrics():
    model = {"datasets": [{"name": "D", "source": "t", "ai_context": {"instructions": "One row per day."},
                           "fields": [{"name": "f", "label": "AirportCode",
                                       "ai_context": {"synonyms": ["eff"], "examples": ["QAA", "QAB"]}}]}],
             "metrics": [{"name": "m", "ai_context": {"synonyms": ["em"], "instructions": "Never sum."}}]}
    shown = semantic_model_details("m", [], {"document": {"semantic_model": json.dumps(model)}})["model"]
    dataset = shown["datasets"][0]
    assert dataset["aiContext"] == [{"label": "Instructions", "value": "One row per day."}]
    # Synonyms have their own list; the rest of a field's or metric's AI context is kept as rows.
    assert dataset["fields"][0]["label"] == "AirportCode"
    assert dataset["fields"][0]["aiContext"] == [{"label": "Examples", "value": "QAA, QAB"}]
    assert shown["metrics"][0]["aiContext"] == [{"label": "Instructions", "value": "Never sum."}]


@pytest.mark.parametrize("stored", ["not json", "[1, 2]", "42", "null", '{"semantic_model": 7}'])
def test_projection_survives_documents_it_cannot_read(stored):
    detail = semantic_model_details("m", ["ns"], {"document": {"version": "1", "semantic_model": stored}})
    assert detail["model"] is None and detail["definition"]


def test_projection_survives_wrong_types_inside_a_model():
    junk = {"datasets": [7, {"name": ["x"], "fields": "no", "primary_key": "id"}], "metrics": {"a": 1}, "relationships": [None]}
    detail = semantic_model_details("m", [], {"document": {"semantic_model": json.dumps(junk)}})
    assert detail["model"]["datasets"][0]["fields"] == [] and detail["model"]["metrics"] == []
    assert detail["model"]["datasets"][0]["metrics"] == [] and detail["model"]["datasets"][0]["primaryKey"] == []


def test_catalog_lists_semantic_models_and_opens_one(users):  # noqa: F811
    c = users.client
    login(c)
    params = {"database": users.databases[0], "namespace": "analytics"}
    contents = c.get("/api/contents", params=params).json()
    assert contents["semanticModels"] == [{"name": "revenue", "namespace": ["analytics"]}]
    detail = c.get("/api/details", params={**params, "kind": "semantic-model", "name": "revenue"})
    assert detail.status_code == 200 and detail.json()["model"]["metrics"][0]["name"] == "event_count"
    assert "actual-user-token" not in detail.text
    # The database root has no namespace, so it lists none.
    assert c.get("/api/contents", params={"database": users.databases[0]}).json()["semanticModels"] == []
    assert c.get("/api/details", params={**params, "kind": "semantic-model"}).status_code == 422


def test_opening_a_model_says_when_the_feature_is_off(users, monkeypatch):  # noqa: F811
    login(users.client)
    get = users.directory.http.get
    monkeypatch.setattr(
        users.directory.http, "get",
        lambda url, **kw: httpx.Response(406, text="hidden") if "semantic-models" in url else get(url, **kw),
    )
    params = {"database": users.databases[0], "namespace": "analytics", "kind": "semantic-model", "name": "revenue"}
    result = users.client.get("/api/details", params=params)
    assert result.status_code == 406 and "switched off" in result.text and "hidden" not in result.text


@pytest.mark.parametrize("status", [403, 404, 406])
def test_listing_is_empty_when_the_feature_is_off_or_not_granted(users, monkeypatch, status):  # noqa: F811
    login(users.client)
    get = users.directory.http.get

    def refuse_models(url, **kwargs):
        return httpx.Response(status, text="hidden") if "semantic-models" in url else get(url, **kwargs)

    monkeypatch.setattr(users.directory.http, "get", refuse_models)
    contents = users.client.get("/api/contents", params={"database": users.databases[0], "namespace": "analytics"})
    assert contents.status_code == 200
    assert contents.json()["semanticModels"] == [] and contents.json()["tables"][0]["name"] == "events"


def test_provider_outage_is_not_hidden_as_an_empty_list(users, monkeypatch):  # noqa: F811
    login(users.client)
    get = users.directory.http.get
    monkeypatch.setattr(
        users.directory.http,
        "get",
        lambda url, **kw: httpx.Response(503, headers={"Retry-After": "0"}) if "semantic-models" in url else get(url, **kw),
    )
    result = users.client.get("/api/contents", params={"database": users.databases[0], "namespace": "analytics"})
    assert result.status_code == 503 and "Try again" in result.text


def test_a_shared_database_shows_exactly_the_models_its_share_names(users):  # noqa: F811
    # The share role cannot list, so names come from the share; the portal never asks Polaris for a listing.
    directory, calls = users.directory, users.calls
    shared = {"shared": True, "sharedObjects": [
        {"kind": "table", "namespace": ["analytics"], "name": "events"},
        {"kind": "semantic-model", "namespace": ["analytics"], "name": "revenue"},
        {"kind": "semantic-model", "namespace": ["other"], "name": "elsewhere"},
    ]}
    directory.database = lambda *a, **kw: shared
    expected = [{"name": "revenue", "namespace": ["analytics"]}]
    assert directory.contents(None, "x", ["analytics"])["semanticModels"] == expected
    assert directory.list_semantic_models(None, "x", ["analytics"]) == expected and not calls
    with pytest.raises(ServiceError) as denied:
        directory.details(None, "x", ["analytics"], "semantic-model", "private")
    assert denied.value.status == 403 and not calls


def test_a_renamed_shared_model_stays_in_the_recipients_catalog(users, monkeypatch):  # noqa: F811
    # Polaris keeps the grant on the entity, so the model is still readable under its new name.
    from server.models import ShareInput
    from test.test_user_portal import share_body

    provider = users.provider
    _, _, recipient = users.teams
    db = users.databases[0]
    provider.create_share(ShareInput.model_validate(share_body(db, external=False, recipientTeam=recipient)), users.account)
    listing = provider.list_shares

    def renamed(*args, **kwargs):
        return [{**s, "extraGrants": [{"kind": "semantic-model", "namespace": ["analytics"], "name": "renamed",
                                       "privilege": "SEMANTIC_MODEL_READ"}]} for s in listing(*args, **kwargs)]

    monkeypatch.setattr(provider, "list_shares", renamed)
    promote(users, {recipient: "reader"})
    login(users.client)
    shared = next(d for d in users.client.get("/api/workspace").json()["databases"] if d["id"] == db)
    assert {"kind": "semantic-model", "namespace": ["analytics"], "name": "renamed", "granted": True} in shared["sharedObjects"]


def test_a_shared_model_opens_with_the_recipients_own_token(users):  # noqa: F811
    login(users.client)
    shared = {"shared": True, "sharedObjects": [
        {"kind": "table", "namespace": ["analytics"], "name": "events"},
        {"kind": "semantic-model", "namespace": ["analytics"], "name": "revenue"},
    ]}
    users.directory.database = lambda *a, **kw: shared
    params = {"database": users.databases[0], "namespace": "analytics", "kind": "semantic-model", "name": "revenue"}
    detail = users.client.get("/api/details", params=params)
    assert detail.status_code == 200 and detail.json()["model"]["name"] == "Analytics"
    assert users.calls[-1].url.path.endswith("/semantic-models/revenue")
    assert users.calls[-1].headers["Authorization"] == "Bearer actual-user-token"


def test_sharing_a_model_warns_about_or_adds_the_tables_it_reads(users):  # noqa: F811
    c, db = users.client, users.databases[0]
    login(c)
    promote(users, {users.teams[0]: "admin"})
    model = {"kind": "semantic-model", "namespace": ["analytics"], "name": "revenue"}
    events = {"kind": "table", "namespace": ["analytics"], "name": "events"}
    other = {"kind": "table", "namespace": ["analytics"], "name": "sessions"}
    body = {"database": db, "name": "partner", "objects": [other, model]}
    created = c.post("/api/shares", json=body, headers=JSON)
    assert created.status_code == 201, created.text
    assert created.json()["warnings"] == ["revenue reads analytics.events, which is not selected."]
    assert created.json()["addedTables"] == [] and events not in created.json()["share"]["objects"]
    id = created.json()["share"]["id"]
    # The API does what selecting a model does in the portal when asked to.
    updated = c.patch(f"/api/shares/{id}", params={"includeModelTables": "true"}, json={"objects": [other, model]}, headers=JSON)
    assert updated.status_code == 200, updated.text
    assert updated.json()["addedTables"] == [events] and updated.json()["warnings"] == []
    assert {(o["kind"], o["name"]) for o in updated.json()["share"]["objects"]} == {
        ("table", "events"), ("table", "sessions"), ("semantic-model", "revenue"),
    }
    # Other fields stay as they were: only the selection was supplied.
    assert updated.json()["share"]["name"] == "partner"
    again = c.patch(f"/api/shares/{id}", json={"objects": [events, model]}, headers=JSON)
    assert again.json()["warnings"] == [] and again.json()["addedTables"] == []


@pytest.mark.parametrize(("status", "created"), [(404, 201), (503, 503)])
def test_a_missing_model_is_skipped_but_an_outage_fails_the_share(users, monkeypatch, status, created):  # noqa: F811
    # Skipping an unavailable model would share it without the tables that includeModelTables adds.
    c, db = users.client, users.databases[0]
    login(c)
    promote(users, {users.teams[0]: "admin"})
    get = users.directory.http.get
    monkeypatch.setattr(
        users.directory.http,
        "get",
        lambda url, **kw: httpx.Response(status, headers={"Retry-After": "0"}) if "semantic-models" in url else get(url, **kw),
    )
    model = {"kind": "semantic-model", "namespace": ["analytics"], "name": "revenue"}
    sessions = {"kind": "table", "namespace": ["analytics"], "name": "sessions"}
    body = {"database": db, "name": "partner", "objects": [sessions, model]}
    result = c.post("/api/shares", params={"includeModelTables": "true"}, json=body, headers=JSON)
    assert result.status_code == created, result.text
    if created == 201:
        assert result.json()["addedTables"] == [] and result.json()["warnings"] == []


def test_reader_role_can_list_and_read_models_and_only_writers_change_them():
    assert {p for p in ROLES["reader"] if p.startswith("SEMANTIC_MODEL")} == {"SEMANTIC_MODEL_LIST", "SEMANTIC_MODEL_READ"}
    # CATALOG_MANAGE_CONTENT covers create, update and drop (checked against Polaris 1.8.0).
    assert "CATALOG_MANAGE_CONTENT" in ROLES["writer"] and "CATALOG_MANAGE_CONTENT" in ROLES["admin"]


class Cleanup(PolarisProvider):
    """Wire-level stub of the catalog API for clear_namespace."""

    def __init__(self, models, feature=True):
        self.models, self.feature, self.calls = list(models), feature, []

    def request(self, path, method="GET", body=None, retry=True):
        self.calls.append((method, path))
        if "/semantic-models" in path:
            if not self.feature:
                raise ServiceError(406, "This feature is not enabled.")
            if method == "DELETE":
                self.models.remove(path.rsplit("/", 1)[1])
                return None
            return {"identifiers": [{"namespace": ["sales"], "name": m} for m in self.models]}
        return {"namespaces": [], "identifiers": []}


def test_clearing_a_namespace_deletes_its_semantic_models_before_the_namespace():
    p = Cleanup(["revenue", "churn"])
    p.clear_namespace("/api/catalog/v1/db-1", ["sales"])
    deletes = [path for method, path in p.calls if method == "DELETE"]
    assert deletes == [
        "/api/catalog/polaris/v1/db-1/namespaces/sales/semantic-models/revenue",
        "/api/catalog/polaris/v1/db-1/namespaces/sales/semantic-models/churn",
        "/api/catalog/v1/db-1/namespaces/sales",
    ]
    assert p.models == []


def test_clearing_a_namespace_works_when_the_feature_is_off():
    p = Cleanup([], feature=False)
    p.clear_namespace("/api/catalog/v1/db-1", ["sales"])
    assert p.calls[-1] == ("DELETE", "/api/catalog/v1/db-1/namespaces/sales")


class Loop(PolarisProvider):
    def __init__(self):
        self.calls = 0

    def request(self, path, method="GET", body=None, retry=True):
        self.calls += 1
        return {"identifiers": [], "next-page-token": "same"}


def test_a_repeated_page_token_stops_instead_of_looping():
    p = Loop()
    with pytest.raises(ServiceError, match="invalid pagination"):
        p.pages("/api/catalog/v1/db-1/namespaces", "namespaces")
    assert p.calls == 2


def test_the_portal_stores_the_same_envelope_as_the_notebook_client():
    from user_portal.notebook.semantic import SemanticModels
    from user_portal.semantic.rules import document

    model = {"name": "m", "datasets": [{"name": "D", "source": "ns.t", "fields": []}]}
    assert document(model) == SemanticModels(None, "").document(model)
    assert json.loads(document(model)["semantic_model"]) == {"version": "0.3.0.dev0", **model}


def test_missing_descriptions_come_from_the_table_comment_and_column_docs():
    from user_portal.semantic import rules

    model = {"name": "Flights", "datasets": [{"name": "FLIGHT", "source": "marts.fct_flights", "fields": [
        {"name": "carrier", "expression": {"sql": "carrier"}},
        {"name": "late", "expression": {"sql": "arr_delay >= 15"}},
        {"name": "arr_delay", "expression": {"sql": "arr_delay"}, "description": "Kept as written."},
        {"name": "delay_minutes", "expression": {"dialects": [{"dialect": "DUCKDB", "expression": "arr_delay"}]}},
    ]}]}
    docs = {"FLIGHT": {"comment": "One row per flight.",
                       "columns": {"carrier": "Operating carrier.", "arr_delay": "Arrival delay in minutes."}}}
    filled = rules.inherit_descriptions(model, docs)
    dataset = filled["datasets"][0]
    assert dataset["description"] == "One row per flight."
    assert [f.get("description") for f in dataset["fields"]] == ["Operating carrier.", None, "Kept as written.",
                                                                 "Arrival delay in minutes."]
    assert "description" not in model["datasets"][0]  # the caller's model is left as it was


def valid_model():
    field = {"description": "A field.", "expression": {"sql": "x"}}
    return {
        "version": "0.3.0.dev0", "name": "shop", "description": "Orders. One row per order.",
        "ai_context": {"instructions": "Time: UTC. Grain: order. Missing values: none. Owner: Sales. "
                                       "Refresh: nightly. Classification: internal."},
        "datasets": [
            {"name": "ORDERS", "source": "shop.orders", "description": "Orders.", "primary_key": ["id"],
             "fields": [{**field, "name": "id", "expression": {"sql": "o_id"}},
                        {**field, "name": "customer", "expression": {"sql": "o_cust"}},
                        {**field, "name": "net", "expression": {"sql": "amount - discount"}}],
             "metrics": [{"name": "net_total", "description": "Net. Unit: euro.", "expression": {"sql": "SUM(net)"}}]},
            {"name": "CUSTOMER", "source": "shop.customers", "description": "Customers.",
             "unique_keys": [["id"]], "fields": [{**field, "name": "id", "expression": {"sql": "c_id"}}]},
        ],
        "relationships": [{"name": "orders_customer", "from": "ORDERS", "to": "CUSTOMER",
                           "from_columns": ["customer"], "to_columns": ["id"]}],
        "metrics": [{"name": "per_order", "description": "Net per order. Unit: euro.",
                     "expression": {"sql": "AGG(ORDERS.net_total) / COUNT(ORDERS.id)"}}],
        "nonportable_extensions": [{"vendor_name": "DBT", "data": {"project": "shop", "tags": [1, None]}}],
    }


def test_a_complete_0_3_model_passes_and_is_stored_with_its_version():
    from user_portal.semantic import rules

    columns = {"ORDERS": {"o_id", "o_cust", "amount", "discount"}, "CUSTOMER": {"c_id"}}
    model, warnings = rules.validate({k: v for k, v in valid_model().items() if k != "version"}, columns)
    assert warnings == [] and model["version"] == "0.3.0.dev0"
    assert json.loads(rules.document(model)["semantic_model"]) == valid_model()


@pytest.mark.parametrize(("change", "error"), [
    (lambda m: m.update(version="0.2.0"), "stores Apache Ossie 0.3.0.dev0 documents"),
    (lambda m: m["metrics"][0]["expression"].update(sql="SUM(ORDERS.amount)"), "not a field of ORDERS"),
    (lambda m: m["metrics"][0]["expression"].update(sql="SUM(net)"), "names fields as dataset.field"),
    (lambda m: m["datasets"][0]["metrics"][0]["expression"].update(sql="SUM(o_id)"), "Write source.o_id"),
    (lambda m: m["datasets"][0]["fields"][2]["expression"].update(sql="amount - rebate"),
     "reads rebate, which is not a column of ORDERS's table"),
    (lambda m: m["datasets"][0]["fields"][2]["expression"].update(sql="SUM(amount)"), "is an aggregate"),
    (lambda m: m["datasets"][0].update(primary_key=["id", "ID"]), "names a field twice"),
    (lambda m: m["datasets"][0]["fields"].append({"name": "NET_TOTAL", "description": "Clash.",
                                                   "expression": {"sql": "1"}}), "share one namespace"),
    (lambda m: m["datasets"][0]["fields"][0].update(expression={"dialects": [{"dialect": "DUCKDB"}]}),
     "expression.dialects"),
    (lambda m: m.update(nonportable_extensions=[{"vendor_name": "DBT", "data": {"x": float("nan")}}]),
     "a JSON object"),
    (lambda m: m["datasets"][0].update(custom_extensions=[]), "no key 'custom_extensions'"),
])
def test_publishing_refuses_what_ossie_forbids(change, error):
    from user_portal.semantic import rules
    from user_portal.semantic.errors import SemanticError

    model = valid_model()
    change(model)
    columns = {"ORDERS": {"o_id", "o_cust", "amount", "discount"}, "CUSTOMER": {"c_id"}}
    with pytest.raises(SemanticError, match=error):
        rules.validate(model, columns)


@pytest.mark.parametrize(("change", "warning"), [
    (lambda m: m["datasets"][0]["metrics"][0]["expression"].update(sql="AGG(source.net)"), "no measures"),
    (lambda m: m["metrics"][0]["expression"].update(sql="SUM(ORDERS.net) / COUNT(DISTINCT CUSTOMER.id)"),
     "anchored on one dataset"),
    (lambda m: m["metrics"][0]["expression"].update(sql="max(timezone('UTC', ORDERS.id))"), "governed queries refuse"),
    (lambda m: m["datasets"][1].update(unique_keys=[["id"], ["ID"]]), "declared twice"),
    (lambda m: m["relationships"][0].update(to_columns=["customer"], **{"from": "CUSTOMER", "to": "ORDERS"},
                                            from_columns=["id"]), "not a whole primary or unique key"),
])
def test_publishing_warns_about_what_governed_queries_cannot_run(change, warning):
    from user_portal.semantic import rules

    model = valid_model()
    change(model)
    _, warnings = rules.validate(model)
    assert any(warning in w for w in warnings), warnings
