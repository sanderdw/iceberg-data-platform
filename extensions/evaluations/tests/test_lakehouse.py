"""Results land in Iceberg tables of the team's database, with the semantic model next to them."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pyiceberg.catalog.sql import SqlCatalog

from evaluations import lakehouse
from evaluations.app import create_app
from tests.conftest import DB, MODEL, TEAM, YAML, FakeAgent, FakeBridge, FakeVerifier, settings


@pytest.fixture
def catalog(tmp_path):
    return SqlCatalog("local", uri=f"sqlite:///{tmp_path}/catalog.db", warehouse=f"file://{tmp_path}/warehouse")


def polaris(models):
    """Polaris' semantic model endpoints for one database: GET, POST and PUT."""

    def handle(request):
        name = request.url.path.rsplit("/", 1)[-1]
        if request.method == "GET":
            found = models.get(name)
            return httpx.Response(200, json=found) if found else httpx.Response(404, json={})
        body = json.loads(request.content)
        if request.method == "POST":
            models[body["name"]] = {"document": body["document"], "entity-version": 1}
            return httpx.Response(200, json={})
        models[name] = {"document": body["document"], "entity-version": body["entity-version"] + 1}
        return httpx.Response(200, json={})

    return httpx.Client(transport=httpx.MockTransport(handle))


def test_a_run_appends_rows_and_publishes_the_model(tmp_path, catalog):
    bridge, models = FakeBridge(), {}
    store = lakehouse.Lakehouse(settings(tmp_path), bridge, catalog=catalog, http=polaris(models))
    app = create_app(settings(tmp_path), bridge=bridge, verifier=FakeVerifier(), agent=FakeAgent(), lakehouse=store)
    with TestClient(app) as client:
        def call(method, path, **kwargs):
            return client.request(method, "/api/v1" + path, headers={"Authorization": "Bearer alice"}, **kwargs)

        call("POST", f"/teams/{TEAM}/environments/development")
        id = call("POST", "/question-sets", json={"team": TEAM, "name": "flights_checks", "model": MODEL,
                                                  "yaml": YAML, "target_database": DB}).json()["id"]
        runs = []
        for _ in range(2):
            run = call("POST", f"/question-sets/{id}/runs", json={}).json()
            while (found := call("GET", f"/runs/{run['id']}").json())["status"] in ("queued", "running"):
                pass
            runs.append(found)
    assert runs[0]["published"] == "sales.evaluations.case_results"
    cases = catalog.load_table(("evaluations", "case_results")).scan().to_arrow().to_pylist()
    assert len(cases) == 6 and {c["run_id"] for c in cases} == {r["id"] for r in runs}
    delay = next(c for c in cases if c["case_name"] == "delay_by_carrier")
    assert delay["passed"] and delay["result_matches"] and delay["refused"] is None
    assert json.loads(delay["reasons"])["grounded"] == "Every figure comes from a result."
    table = catalog.load_table(("evaluations", "runs"))
    assert table.scan().to_arrow().num_rows == 2
    assert table.properties["iceberg-data-platform.producer"] == "evaluations"
    assert table.properties["iceberg-data-platform.producer.run-id"] == runs[1]["id"]
    # A write token of Evaluations' principal for the team, once per run.
    assert [access for _, access, _ in bridge.tokens] == ["write", "write"]
    stored = json.loads(models["evaluation_results"]["document"]["semantic_model"])
    [model] = stored["semantic_model"]
    assert model["datasets"][0]["source"] == "lakehouse.evaluations.case_results"
    assert {m["name"] for m in model["metrics"]} >= {"pass_rate", "grounded_rate", "average_tokens"}
    # Unchanged the second time.
    assert models["evaluation_results"]["entity-version"] == 1


def test_an_unreliable_run_stays_out_of_the_teams_tables(tmp_path, catalog):
    import asyncio

    store = lakehouse.Lakehouse(settings(tmp_path), FakeBridge(), catalog=catalog, http=polaris({}))
    run = {"id": "run-" + "1" * 24, "summary": {"reliable": False, "errors": 9, "cases": 10}}
    published = asyncio.run(store.publish(run, {"target_database": DB}, []))
    assert published.startswith("not published: 9 of 10 questions got no answer")
    assert catalog.list_namespaces() == []


def test_an_unanswered_question_is_no_row(tmp_path):
    run = {"id": "r", "version": 1, "llm_model": "", "triggered_by": "a", "finished_at": 1.0}
    case = {"name": "a", "question": "q", "kind": "query", "passed": False, "scores": {}}
    rows = lakehouse.case_rows(run, {"name": "s"}, [case, {**case, "name": "b", "error": "rate limited"}])
    assert [r["case_name"] for r in rows] == ["a"]


def test_a_database_of_another_environment_is_refused(tmp_path, catalog):
    bridge = FakeBridge()
    bridge.automation_.append({"id": "svc-" + "0" * 32, "team": TEAM, "environment": "production",
                               "extension": "evaluations", "status": "active", "createdBy": "alice"})
    store = lakehouse.Lakehouse(settings(tmp_path), bridge, catalog=catalog, http=polaris({}))
    run = {"id": "run-" + "1" * 24, "team": TEAM, "environment": "production", "llm_model": "", "version": 1,
           "triggered_by": "alice", "summary": {"reliable": True}}
    with pytest.raises(lakehouse.EvalError) as found:
        import asyncio

        asyncio.run(store.publish(run, {"id": "qs", "name": "x", "agent": "conversationalbi", "target_database": DB},
                                  []))
    assert found.value.code == "unknown_database"


def test_a_new_column_joins_an_existing_table(catalog):
    import pyarrow as pa

    catalog.create_namespace(("evaluations",))
    old = pa.schema([field for field in lakehouse.CASES if field.name != "declined"])
    catalog.create_table(("evaluations", "case_results"), schema=old)
    lakehouse.append(catalog, "case_results", lakehouse.CASES, [], {})
    names = {f.name for f in catalog.load_table(("evaluations", "case_results")).schema().fields}
    assert "declined" in names
