"""The notebook client for Polaris semantic models, against a stand-in for the 1.8 wire protocol."""

import json

import httpx
import pytest

from user_portal.notebook import semantic
from user_portal.notebook.semantic import (
    NotebookAuth,
    SemanticModelConflict,
    SemanticModelError,
    SemanticModels,
)

BASE = "http://polaris:8181/api/catalog/polaris/v1/db-1/namespaces/sales/semantic-models"


class Store:
    """Just enough of Polaris: versions, conflict on stale PUT, 403 for a reader, 406 when disabled."""

    def __init__(self, *, role="writer", enabled=True):
        self.models, self.role, self.enabled, self.requests = {}, role, enabled, []

    def __call__(self, request):
        self.requests.append(request)
        path = request.url.path.removeprefix("/api/catalog/polaris/v1/db-1/namespaces/sales/semantic-models").strip("/")
        if not self.enabled:
            return httpx.Response(406, json={"error": {"message": "Feature not enabled: ENABLE_SEMANTIC_MODELS"}})
        if request.method != "GET" and self.role == "reader":
            return httpx.Response(403, json={"error": {"message": "secret-details"}})
        body = json.loads(request.content) if request.content else None
        if request.method == "GET" and not path:
            token = request.url.params.get("pageToken")
            names = sorted(self.models)
            page = names[:1] if not token else names[1:]
            return httpx.Response(
                200,
                json={
                    "identifiers": [{"namespace": ["sales"], "name": n} for n in page],
                    "next-page-token": None if token or len(names) < 2 else "second",
                },
            )
        if request.method == "GET":
            if path not in self.models:
                return httpx.Response(404, json={})
            document, version = self.models[path]
            return httpx.Response(200, json={"document": document, "entity-version": str(version)})
        if request.method == "POST":
            if body["name"] in self.models:
                return httpx.Response(409, json={})
            self.models[body["name"]] = (body["document"], 1)
            return httpx.Response(200, json={"document": body["document"], "entity-version": "1"})
        if request.method == "PUT":
            _, version = self.models[path]
            if body["entity-version"] != str(version):
                return httpx.Response(409, json={})
            self.models[path] = (body["document"], version + 1)
            return httpx.Response(200, json={"document": body["document"], "entity-version": str(version + 1)})
        if request.method == "DELETE":
            if self.models.pop(path, None) is None:
                return httpx.Response(404, json={})
            return httpx.Response(204)
        raise AssertionError(request)


def models_for(store, version="0.2.0.dev0"):
    return SemanticModels(httpx.Client(transport=httpx.MockTransport(store)), BASE, version)


MODEL = {"name": "Sales", "datasets": [{"name": "ORDERS", "source": "lakehouse.sales.orders"}], "metrics": []}


def test_model_is_stored_as_a_whole_ossie_document_in_the_versioned_envelope():
    store = Store()
    assert models_for(store).create("sales", MODEL) == "1"
    document, _ = store.models["sales"]
    assert document["version"] == "0.2.0.dev0"
    assert json.loads(document["semantic_model"]) == {"version": "0.2.0.dev0", "semantic_model": [MODEL]}
    create = store.requests[0]
    assert create.method == "POST" and json.loads(create.content)["name"] == "sales"


def test_publish_creates_then_replaces_and_load_returns_the_model():
    models = models_for(Store())
    assert models.publish("sales", MODEL) == ("created", "1")
    changed = {**MODEL, "description": "Orders"}
    assert models.publish("sales", changed) == ("updated", "2")
    assert models.load("sales") == (changed, "2")
    assert models.load("missing") is None


def test_stale_entity_version_is_a_conflict_and_changes_nothing():
    models = models_for(Store())
    models.create("sales", MODEL)
    assert models.update("sales", {**MODEL, "description": "first"}, "1") == "2"
    with pytest.raises(SemanticModelConflict, match="changed by someone else"):
        models.update("sales", {**MODEL, "description": "second"}, "1")
    assert models.load("sales")[0]["description"] == "first"


def test_names_follow_continuation_tokens():
    store = Store()
    models = models_for(store)
    for name in ("b", "a"):
        models.create(name, MODEL)
    assert models.names() == ["a", "b"]


def test_names_with_special_characters_are_encoded_in_the_path():
    store = Store()
    assert models_for(store).load("a/b") is None
    assert store.requests[0].url.raw_path.endswith(b"/semantic-models/a%2Fb")


def test_a_model_stored_without_the_ossie_wrapper_still_loads():
    store = Store()
    store.models["old"] = ({"version": "0.2.0", "semantic_model": json.dumps(MODEL)}, 4)
    assert models_for(store).load("old") == (MODEL, "4")


def test_publish_replaces_a_model_a_teammate_created_meanwhile():
    store = Store()
    models = models_for(store)
    real_load, loads = models.load, []

    def load(name):
        # The first load misses the teammate's model; creating it then answers 409.
        loads.append(name)
        return None if len(loads) == 1 else real_load(name)

    models.load = load
    store.models["sales"] = (models.document({**MODEL, "description": "theirs"}), 1)
    assert models.publish("sales", MODEL) == ("updated", "2")
    assert len(loads) == 2 and [r.method for r in store.requests] == ["POST", "GET", "PUT"]
    assert real_load("sales") == (MODEL, "2")


def test_every_request_uses_the_current_session_token(monkeypatch):
    tokens = iter(["first", "second"])
    monkeypatch.setenv("ICEBERG_SESSION_TOKEN_URL", "http://gateway/token")
    monkeypatch.setattr(semantic, "access_token", lambda: next(tokens))
    seen = []

    def wire(request):
        seen.append(request.headers["Authorization"])
        return httpx.Response(200, json={"identifiers": []})

    client = httpx.Client(transport=httpx.MockTransport(wire), auth=NotebookAuth("startup-token"))
    models = SemanticModels(client, BASE)
    models.names()
    models.names()
    assert seen == ["Bearer first", "Bearer second"]


def test_password_mode_notebooks_keep_their_client_credentials_token(monkeypatch):
    monkeypatch.delenv("ICEBERG_SESSION_TOKEN_URL", raising=False)
    monkeypatch.setattr(semantic, "access_token", lambda: pytest.fail("no session endpoint to ask"))
    seen = []
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: seen.append(r.headers["Authorization"]) or httpx.Response(200, json={"identifiers": []})),
        auth=NotebookAuth("client-token"),
    )
    with SemanticModels(client, BASE) as models:
        models.names()
    assert seen == ["Bearer client-token"] and client.is_closed


@pytest.mark.parametrize(
    ("store", "message"),
    [
        (Store(role="reader"), "Your role may not"),
        (Store(enabled=False), "switched off"),
    ],
)
def test_errors_explain_the_cause_without_echoing_the_response(store, message):
    with pytest.raises(SemanticModelError, match=message) as error:
        models_for(store).create("sales", MODEL)
    assert "secret-details" not in str(error.value) and "Feature not enabled" not in str(error.value)


def test_a_reader_can_read_but_not_drop():
    store = Store()
    writer = models_for(store)
    writer.create("sales", MODEL)
    store.role = "reader"
    assert writer.names() == ["sales"] and writer.load("sales")[1] == "1"
    with pytest.raises(SemanticModelError, match="Your role"):
        writer.drop("sales")


def test_drop_of_a_missing_model_is_quiet():
    models_for(Store()).drop("nothing")
