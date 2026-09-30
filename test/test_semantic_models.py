"""Semantic models (Apache Ossie, Polaris 1.8): projection, listing that tolerates a disabled feature and cleanup."""

import json

import httpx
import pytest

from server.models import ServiceError
from server.polaris import ROLES, PolarisProvider
from test.conftest import SEMANTIC_MODEL
from test.test_user_portal import HEADERS, login, users  # noqa: F401 - fixture
from user_portal.catalog import semantic_model_details


def test_document_projection_is_flat_and_keeps_the_stored_definition():
    detail = semantic_model_details("revenue", ["analytics"], SEMANTIC_MODEL)
    assert (detail["specVersion"], detail["entityVersion"], detail["namespace"]) == ("0.2.0", "3", ["analytics"])
    model = detail["models"][0]
    assert model["name"] == "Analytics"
    assert model["aiContext"] == [
        {"label": "Instructions", "value": "Count events per user.\nIgnore test users."},
        {"label": "Synonyms", "value": "activity"},
    ]
    dataset = model["datasets"][0]
    assert dataset["table"] == {"namespace": ["analytics"], "name": "events"} and dataset["primaryKey"] == ["id"]
    assert dataset["fields"][0]["expressions"] == [{"dialect": "ANSI_SQL", "expression": "id"}]
    assert model["relationships"][0]["fromColumns"] == ["user_id"]
    assert json.loads(detail["definition"])["semantic_model"][0]["datasets"][0]["name"] == "EVENTS"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("lakehouse.analytics.nested.events", (["analytics", "nested"], "events")),
        ("lakehouse.sales.orders", (["sales"], "orders")),
        ("sales.orders", (["sales"], "orders")),
        ("orders", (["own"], "orders")),
    ],
)
def test_dataset_sources_resolve_every_namespace_level(source, expected):
    loaded = {"document": {"semantic_model": json.dumps({"datasets": [{"name": "D", "source": source}]})}}
    table = semantic_model_details("m", ["own"], loaded)["models"][0]["datasets"][0]["table"]
    assert (table["namespace"], table["name"]) == expected


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
    assert semantic_model_details("m", [], loaded)["models"][0]["aiContext"] == rows


@pytest.mark.parametrize(
    "payload",
    [
        {"datasets": [{"name": "A", "source": "t"}]},
        [{"datasets": [{"name": "A", "source": "t"}]}],
        {"version": "0.2.0", "semantic_model": [{"datasets": [{"name": "A", "source": "t"}]}]},
    ],
)
def test_projection_accepts_one_model_a_list_or_a_whole_document(payload):
    detail = semantic_model_details("m", ["ns"], {"document": {"version": "1", "semantic_model": json.dumps(payload)}})
    # A source without a namespace reads a table in the model's own namespace.
    assert detail["models"][0]["datasets"][0]["table"] == {"namespace": ["ns"], "name": "t"}


@pytest.mark.parametrize("stored", ["not json", "[1, 2]", "42", "null", '{"semantic_model": 7}'])
def test_projection_survives_documents_it_cannot_read(stored):
    detail = semantic_model_details("m", ["ns"], {"document": {"version": "1", "semantic_model": stored}})
    assert detail["models"] == [] and detail["definition"]


def test_projection_survives_wrong_types_inside_a_model():
    junk = {"datasets": [7, {"name": ["x"], "fields": "no", "primary_key": "id"}], "metrics": {"a": 1}, "relationships": [None]}
    detail = semantic_model_details("m", [], {"document": {"semantic_model": json.dumps(junk)}})
    assert detail["models"][0]["datasets"][0]["fields"] == [] and detail["models"][0]["metrics"] == []


def test_catalog_lists_semantic_models_and_opens_one(users):  # noqa: F811
    c = users.client
    login(c)
    params = {"database": users.databases[0], "namespace": "analytics"}
    contents = c.get("/api/contents", params=params).json()
    assert contents["semanticModels"] == [{"name": "revenue", "namespace": ["analytics"]}]
    detail = c.get("/api/details", params={**params, "kind": "semantic-model", "name": "revenue"})
    assert detail.status_code == 200 and detail.json()["models"][0]["metrics"][0]["name"] == "event_count"
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


def test_a_shared_model_opens_with_the_recipients_own_token(users):  # noqa: F811
    login(users.client)
    shared = {"shared": True, "sharedObjects": [
        {"kind": "table", "namespace": ["analytics"], "name": "events"},
        {"kind": "semantic-model", "namespace": ["analytics"], "name": "revenue"},
    ]}
    users.directory.database = lambda *a, **kw: shared
    params = {"database": users.databases[0], "namespace": "analytics", "kind": "semantic-model", "name": "revenue"}
    detail = users.client.get("/api/details", params=params)
    assert detail.status_code == 200 and detail.json()["models"][0]["name"] == "Analytics"
    assert users.calls[-1].url.path.endswith("/semantic-models/revenue")
    assert users.calls[-1].headers["Authorization"] == "Bearer actual-user-token"


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
