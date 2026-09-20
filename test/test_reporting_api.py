"""Real gateway sessions with controlled worker execution, no Docker required."""

import threading
import time
from copy import deepcopy

import pytest

from test.test_user_portal import HEADERS, TABLE_METADATA, login
from test.test_user_portal import users as users  # noqa: PLC0414 - Expose the shared pytest fixture.
from user_portal.reporting.store import ReportStore


class Runner:
    def __init__(self):
        self.requests = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.release.set()

    def run(self, request, cancelled):
        self.requests.append(deepcopy(request))
        self.started.set()
        while not self.release.is_set() and not cancelled.is_set():
            time.sleep(0.01)
        source = request["source"]
        return {
            "data": {
                "columns": [{"name": "value", "type": "BIGINT"}],
                "rows": [[3]],
                "snapshotId": source["snapshotId"],
                "tableUuid": source["tableUuid"],
                "schemaId": source["schemaId"],
                "timezone": "UTC",
            },
            "svg": None,
        }

    def close(self):
        self.release.set()


@pytest.fixture
def reporting(users):
    runner = Runner()
    users.app.state.reports.runner = runner
    users.app.state.reports.store = ReportStore(":memory:")
    login(users.client)
    definition = {
        "name": "Event count",
        "source": {"database": users.databases[0], "namespace": ["analytics"], "table": "events"},
    }
    return users, runner, definition


def finished(client, identifier):
    for _ in range(100):
        response = client.get(f"/api/report-jobs/{identifier}")
        response.raise_for_status()
        result = response.json()
        if result["status"] != "running":
            return result
        time.sleep(0.01)
    raise AssertionError("Job did not complete")


def test_reader_can_save_run_duplicate_and_create_dashboard(reporting):
    users, runner, definition = reporting
    c = users.client
    saved = c.post("/api/reports", json={"definition": definition}, headers=HEADERS)
    assert saved.status_code == 201, saved.text
    item = saved.json()
    assert c.get("/api/reports").json()["items"][0]["id"] == item["id"]
    assert (
        c.put(
            f"/api/reports/{item['id']}", json={"definition": definition, "revision": 7}, headers=HEADERS
        ).status_code
        == 409
    )
    board = c.post(
        "/api/dashboards",
        json={
            "definition": {
                "name": "Team board",
                "cards": [{"report_id": item["id"]}, {"report_id": item["id"]}],
            }
        },
        headers=HEADERS,
    ).json()
    run = c.post("/api/report-jobs", json={"dashboard_id": board["id"]}, headers=HEADERS)
    assert run.status_code == 202, run.text
    result = finished(c, run.json()["id"])
    assert len(result["cards"]) == 2 and result["cards"][0]["result"]["data"]["rows"] == [[3]]
    assert result["cards"][1]["result"]["cached"]
    assert len(runner.requests) == 1
    assert "token" not in str(result) and "actual-user-token" not in str(result)
    assert runner.requests[0]["source"]["token"] == "actual-user-token"
    assert c.request("DELETE", f"/api/reports/{item['id']}", json={"revision": 1}, headers=HEADERS).status_code == 409


def test_jobs_cancel_and_never_cross_workspace_or_session(reporting):
    users, runner, definition = reporting
    c = users.client
    runner.release.clear()
    started = c.post("/api/report-jobs", json={"definition": definition}, headers=HEADERS).json()
    assert runner.started.wait(2)
    cancelled = c.request("DELETE", f"/api/report-jobs/{started['id']}", json={}, headers=HEADERS)
    assert cancelled.json()["status"] == "cancelled"
    assert finished(c, started["id"])["cards"] == []
    runner.started.clear()
    run = c.post("/api/report-jobs", json={"definition": definition}, headers=HEADERS).json()
    assert runner.started.wait(2)
    c.patch("/api/environment", json={"environment": "production"}, headers=HEADERS).raise_for_status()
    assert c.get(f"/api/report-jobs/{run['id']}").status_code == 404
    runner.release.set()
    c.patch("/api/environment", json={"environment": "development"}, headers=HEADERS).raise_for_status()
    assert finished(c, run["id"])["cards"] == []


def test_revoked_viewer_cannot_poll_completed_results_or_cache(reporting):
    users, runner, definition = reporting
    c = users.client
    run = c.post("/api/report-jobs", json={"definition": definition}, headers=HEADERS).json()
    assert finished(c, run["id"])["cards"][0]["result"]["data"]["rows"] == [[3]]
    users.provider.update_memberships(users.account["id"], {users.teams[1]: "reader"})
    assert c.get(f"/api/report-jobs/{run['id']}").status_code == 403
    assert c.post("/api/report-jobs", json={"definition": definition}, headers=HEADERS).status_code == 403
    assert len(runner.requests) == 1


def test_reports_are_scoped_and_sql_validation_precedes_execution(reporting):
    users, runner, definition = reporting
    c = users.client
    saved = c.post("/api/reports", json={"definition": definition}, headers=HEADERS).json()
    sql = c.post("/api/report-sql", json={"definition": definition}, headers=HEADERS)
    assert sql.status_code == 200 and "count(*)" in sql.json()["sql"]
    unsafe = {**definition, "mode": "sql", "sql": "SELECT * FROM duckdb_secrets()"}
    assert c.post("/api/reports", json={"definition": unsafe}, headers=HEADERS).status_code == 422
    assert c.post("/api/report-jobs", json={"definition": unsafe}, headers=HEADERS).status_code == 422
    assert not runner.requests
    c.patch("/api/team", json={"team": users.teams[1]}, headers=HEADERS).raise_for_status()
    assert c.get("/api/reports").json()["items"] == []
    assert c.get(f"/api/reports/{saved['id']}").status_code == 404


def test_schema_changes_discard_results_after_execution(reporting):
    users, runner, definition = reporting
    c = users.client
    runner.release.clear()
    run = c.post("/api/report-jobs", json={"definition": definition}, headers=HEADERS).json()
    assert runner.started.wait(2)
    old = TABLE_METADATA["table-uuid"]
    try:
        TABLE_METADATA["table-uuid"] = "replaced"
        runner.release.set()
        result = finished(c, run["id"])
        assert "error" in result["cards"][0] and "result" not in result["cards"][0]
    finally:
        TABLE_METADATA["table-uuid"] = old
