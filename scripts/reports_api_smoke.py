"""Live reporting API checks, invoked by reporting_smoke with temporary resources."""

import os
import time
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from user_portal.app import create_app
from user_portal.directory import UserDirectory
from user_portal.reporting.runtime import ReportRuntime
from user_portal.reporting.store import ReportStore

HEADERS = {"Origin": "http://testserver", "Content-Type": "application/json", "X-Portal-Request": "1"}


class NoNotebooks:
    """Keep this reporting fixture independent of existing notebook containers."""

    def __init__(self):
        self.workspaces = {}

    def recover(self):
        pass

    def stop_session(self, _identifier):
        pass

    def close(self):
        pass


def verify_reports_api(provider, database, account, outside_team):
    directory = UserDirectory(
        {**os.environ, "POLARIS_URL": provider.url, "USER_S3_ENDPOINT": os.environ["S3_ENDPOINT"]}
    )
    runtime = ReportRuntime({**os.environ, "USER_STACK_NAME": "reporting-api-smoke"})
    with TemporaryDirectory() as tmp:
        store = ReportStore(f"{tmp}/reports.sqlite")
        app = create_app(directory, NoNotebooks(), report_store=store, report_runner=runtime)
        with TestClient(app) as client:
            response = client.post(
                "/api/session",
                json={"username": account["user"]["name"], "secret": account["credentials"]["clientSecret"]},
                headers=HEADERS,
            )
            response.raise_for_status()

            def post(path, body):
                response = client.post("/api/" + path, json=body, headers=HEADERS)
                response.raise_for_status()
                return response.json()

            def run(body):
                started = post("report-jobs", body)
                deadline = time.monotonic() + 100
                while time.monotonic() < deadline:
                    response = client.get(f"/api/report-jobs/{started['id']}")
                    response.raise_for_status()
                    result = response.json()
                    if result["status"] != "running":
                        assert result["status"] == "done", result
                        for card in result["cards"]:
                            assert "result" in card, card
                        return result
                    time.sleep(0.2)
                raise AssertionError("Reporting API timed out")

            definition = {
                "name": "Live energy count",
                "source": {
                    "database": database["id"],
                    "namespace": ["synthetic"],
                    "table": "neighborhood_electricity",
                },
                "visualization": {"kind": "kpi"},
            }
            saved = post("reports", {"definition": definition})
            first = run({"report_id": saved["id"]})
            result = first["cards"][0]["result"]
            assert result["data"]["rows"] == [[26881]] and "<svg" in result["svg"]
            assert not result["cached"]
            assert run({"report_id": saved["id"]})["cards"][0]["result"]["cached"]
            dashboard = post(
                "dashboards",
                {"definition": {
                    "name": "Live dashboard",
                    "cards": [
                        {"report_id": saved["id"], "filter_column": "street"},
                        {"report_id": saved["id"], "filter_column": "street"},
                    ],
                }},
            )
            filtered = run({"dashboard_id": dashboard["id"], "filter_value": "Example Solar Street"})
            cards = [card["result"] for card in filtered["cards"]]
            assert cards[0]["data"]["rows"][0][0] in (6720, 6721)
            assert cards[0]["data"]["snapshotId"] == cards[1]["data"]["snapshotId"]
            assert cards[1]["cached"]
            sql = {
                **definition,
                "mode": "sql",
                "sql": "WITH selected AS (SELECT * FROM source WHERE street = $street) SELECT count(*) AS value FROM selected",
                "parameters": {"street": "Example Solar Street"},
                "visualization": {"kind": "table"},
            }
            assert run({"definition": sql})["cards"][0]["result"]["data"]["rows"] == cards[0]["data"]["rows"]
            literal = {
                **sql,
                "name": "{{ 7 * 7 }}",
                "sql": "SELECT '{{ 7 * 7 }}' AS dimension, count(*) AS value FROM source",
                "parameters": {},
                "visualization": {"kind": "bar"},
            }
            rendered = run({"definition": literal})["cards"][0]["result"]
            assert "{{ 7 * 7 }}" in rendered["svg"]
            v3 = {
                **definition,
                "source": {"database": database["id"], "namespace": ["iceberg_v3"], "table": "sensor_events"},
                "visualization": {"kind": "table"},
            }
            assert run({"definition": v3})["cards"][0]["result"]["data"]["rows"] == [[436]]
            forbidden = client.post(
                "/api/report-jobs",
                json={"definition": {**sql, "sql": "SELECT * FROM read_csv('/etc/passwd')", "parameters": {}}},
                headers=HEADERS,
            )
            assert forbidden.status_code == 422
            # Revoke the actual Polaris membership after a result has been cached.
            provider.update_memberships(account["user"]["id"], {outside_team: "reader"})
            assert client.get(f"/api/report-jobs/{first['id']}").status_code == 403
            assert client.post("/api/report-jobs", json={"report_id": saved["id"]}, headers=HEADERS).status_code == 403
    print("PASS: live reporting API, saved definitions, KPI, dashboard filters, snapshot pinning, cache, parameterized SQL, Iceberg v3 and revoked access", flush=True)
