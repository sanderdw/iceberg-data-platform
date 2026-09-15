"""Live standalone gateway test; creates and cleans only its own data and notebooks."""

import json
import os
import subprocess

import docker
import httpx
import pyarrow as pa
from pyiceberg.catalog import load_catalog
from pyiceberg.view import ViewVersion

from scripts.smoke import stack_resources
from server.models import DatabaseInput, TeamInput, UserInput
from user_portal.runtime import filespace_key

HEADERS = {"X-Portal-Request": "1"}


def main():
    # Docker Desktop's host socket follows the selected CLI context, while the
    # gateway itself uses the standard mounted daemon socket.
    context = json.loads(subprocess.check_output(["docker", "context", "inspect"]))[0]
    daemon = docker.DockerClient(base_url=context["Endpoints"]["docker"]["Host"])
    volume_names = []
    try:
        with stack_resources() as (p, teams, databases, users, suffix):
            for i in range(3):
                teams.append(p.save_team(TeamInput(name=f"workspace-{i}-{suffix}"))["id"])
                db = p.create_database(DatabaseInput(name=f"workspace_{i}_{suffix}", team=teams[-1]))
                databases.append(db["id"])
            account = p.create_user(UserInput(name=f"analyst-{suffix}", teams=teams[:2], role="writer"))
            users.append(account["user"]["id"])
            other = p.create_user(UserInput(name=f"other-{suffix}", teams=[teams[2]], role="reader"))
            users.append(other["user"]["id"])
            teammate = p.create_user(UserInput(name=f"alex-{suffix}", teams=[teams[0]], role="reader"))
            users.append(teammate["user"]["id"])
            production = p.create_database(
                DatabaseInput(name=f"workspace_0_{suffix}", team=teams[0], environment="production")
            )
            databases.append(production["id"])
            credentials = account["credentials"]
            catalog = load_catalog(
                databases[0],
                type="rest",
                uri=f"{p.public_url}/api/catalog",
                warehouse=databases[0],
                credential=f"{credentials['clientId']}:{credentials['clientSecret']}",
                scope="PRINCIPAL_ROLE:ALL",
                **{
                    "oauth2-server-uri": f"{p.public_url}/api/catalog/v1/oauth/tokens",
                    "header.X-Iceberg-Access-Delegation": "vended-credentials",
                },
            )
            catalog.create_namespace("analytics")
            catalog.create_namespace(("analytics", "nested"))
            records = pa.table({"id": [1, 2, 3], "event": ["deploy", "build", "release"]})
            table = catalog.create_table(("analytics", "nested", "events"), schema=records.schema)
            table.append(records)
            catalog.create_view(
                ("analytics", "nested", "event_report"),
                schema=records.schema,
                view_version=ViewVersion(
                    schema_id=0,
                    default_namespace=("analytics", "nested"),
                    representations=[{"type": "sql", "sql": "SELECT * FROM events", "dialect": "spark"}],
                ),
            )
            for team, env in [(teams[0], "development"), (teams[1], "development"), (teams[0], "production")]:
                key = filespace_key(team, env)
                volume_names.append(f"iceberg-workspaces-work-{key}")
            with httpx.Client(
                base_url=os.environ.get("USER_PORTAL_URL", "http://localhost:3002"), timeout=90
            ) as c:
                try:
                    assert c.get("/api/workspace").status_code == 401
                    response = c.post(
                        "/api/session",
                        json={"username": account["user"]["name"], "secret": credentials["clientSecret"]},
                        headers=HEADERS,
                    )
                    assert response.status_code == 200, (response.status_code, response.text)
                    state = c.get("/api/workspace").json()
                    assert len(state["teams"]) == 2
                    assert len(state["filespaces"]) == 2
                    assert {d["id"] for d in state["databases"]} == {databases[0]}
                    assert c.get("/api/contents", params={"database": databases[2]}).status_code == 403
                    content = c.get(
                        "/api/contents",
                        params=[
                            ("database", databases[0]),
                            ("namespace", "analytics"),
                            ("namespace", "nested"),
                        ],
                    )
                    assert content.status_code == 200, content.text
                    assert content.json()["tables"][0]["name"] == "events"
                    assert content.json()["views"][0]["name"] == "event_report"
                    detail_params = [
                        ("database", databases[0]),
                        ("namespace", "analytics"),
                        ("namespace", "nested"),
                        ("kind", "table"),
                        ("name", "events"),
                    ]
                    details = c.get("/api/details", params=detail_params)
                    assert details.status_code == 200, details.text
                    snapshot = details.json()["currentSnapshotId"]
                    assert isinstance(snapshot, str) and details.json()["columns"][0]["name"] == "id"
                    preview_payload = {
                        "database": databases[0],
                        "namespace": ["analytics", "nested"],
                        "table": "events",
                        "snapshot_id": snapshot,
                        "limit": 100,
                    }
                    preview = c.post("/api/preview", headers=HEADERS, json=preview_payload)
                    assert preview.status_code == 200, preview.text
                    assert preview.json()["rows"] == [["1", "deploy"], ["2", "build"], ["3", "release"]]
                    assert credentials["clientSecret"] not in details.text + preview.text
                    print(
                        "PASS: username/client-secret, team catalog, nested namespaces, tables, views, denied other team",
                        flush=True,
                    )
                    result = c.post(
                        "/api/notebooks",
                        headers=HEADERS,
                        json={
                            "database": databases[0],
                            "namespace": ["analytics", "nested"],
                            "table": "events",
                        },
                    )
                    assert result.status_code == 201, (result.status_code, result.text)
                    notebook = result.json()
                    assert c.get(notebook["url"]).status_code == 200
                    print("PASS: isolated marimo editor starts behind authenticated gateway", flush=True)
                    runtime = daemon.containers.get("iceberg-workspaces-marimo-" + notebook["id"])
                    config = runtime.attrs
                    assert config["HostConfig"]["ReadonlyRootfs"] is True
                    assert config["HostConfig"]["CapDrop"] == ["ALL"]
                    assert config["Config"]["User"] == "10001:10001"
                    assert not config["HostConfig"]["PortBindings"]
                    assert not any(m["Destination"] == "/var/run/docker.sock" for m in config["Mounts"])
                    assert not any(
                        e.startswith(("POLARIS_CLIENT_SECRET=", "PORTAL_PASSWORD=", "AWS_SECRET_ACCESS_KEY="))
                        for e in config["Config"]["Env"]
                    )
                    code = "from user_portal.notebook.connection import connect; c=connect(); t=c.load_table(('analytics','nested','events')); assert t.scan().to_arrow().num_rows==3; from pathlib import Path; Path('/work/persistence-proof.txt').write_text('saved'); print('PASS: notebook reads real Parquet with user credentials')"
                    executed = runtime.exec_run(["/app/.venv/bin/python", "-c", code])
                    assert executed.exit_code == 0, executed.output.decode()
                    print(executed.output.decode().strip(), flush=True)
                    with httpx.Client(base_url=str(c.base_url), timeout=90) as peer:
                        try:
                            peer.post(
                                "/api/session",
                                headers=HEADERS,
                                json={
                                    "username": teammate["user"]["name"],
                                    "secret": teammate["credentials"]["clientSecret"],
                                },
                            ).raise_for_status()
                            peer_preview = peer.post("/api/preview", headers=HEADERS, json=preview_payload)
                            assert peer_preview.status_code == 200, peer_preview.text
                            assert peer_preview.json()["rows"] == preview.json()["rows"]
                            shared_response = peer.post(
                                "/api/notebooks", headers=HEADERS, json={"database": databases[0]}
                            )
                            shared_response.raise_for_status()
                            shared = shared_response.json()
                            assert shared["filespace"] == notebook["filespace"]
                            assert shared["id"] != notebook["id"]
                            peer_runtime = daemon.containers.get("iceberg-workspaces-marimo-" + shared["id"])
                            proof = peer_runtime.exec_run(["cat", "/work/persistence-proof.txt"])
                            assert proof.exit_code == 0 and proof.output == b"saved"
                            peer_write = peer_runtime.exec_run(
                                [
                                    "/app/.venv/bin/python",
                                    "-c",
                                    "from pathlib import Path; Path('/work/alex.txt').write_text('shared with sander')",
                                ]
                            )
                            assert peer_write.exit_code == 0
                            proof = runtime.exec_run(["cat", "/work/alex.txt"])
                            assert proof.exit_code == 0 and proof.output == b"shared with sander"
                            assert peer.get(notebook["url"]).status_code == 404
                            assert peer.get(shared["filesUrl"]).status_code == 200
                            peer.patch(
                                "/api/environment", headers=HEADERS, json={"environment": "production"}
                            ).raise_for_status()
                            prod_response = peer.post(
                                "/api/notebooks", headers=HEADERS, json={"database": production["id"]}
                            )
                            prod_response.raise_for_status()
                            prod_notebook = prod_response.json()
                            assert prod_notebook["filespace"] != shared["filespace"]
                            prod_runtime = daemon.containers.get(
                                "iceberg-workspaces-marimo-" + prod_notebook["id"]
                            )
                            proof = prod_runtime.exec_run(
                                [
                                    "/app/.venv/bin/python",
                                    "-c",
                                    "from pathlib import Path; assert not Path('/work/alex.txt').exists(); assert not Path('/work/persistence-proof.txt').exists()",
                                ]
                            )
                            assert proof.exit_code == 0, proof.output.decode()
                        finally:
                            peer.request(
                                "DELETE", "/api/session", headers=HEADERS, json={}
                            ).raise_for_status()
                        assert c.get(notebook["url"]).status_code == 200
                    print(
                        "PASS: two members share saved files; Production is separate; teammate sign-out preserves execution",
                        flush=True,
                    )
                    if os.environ.get("USER_BROWSER_TEST") == "1":
                        subprocess.run(
                            ["node", "scripts/users-browser.mjs"],
                            input=json.dumps(
                                {
                                    "baseURL": str(c.base_url),
                                    "cookie": c.cookies.get("iceberg_user_session"),
                                    "notebook": notebook,
                                    "database": databases[0],
                                    "databaseName": f"workspace_0_{suffix}",
                                    "team": teams[1],
                                }
                            ),
                            text=True,
                            check=True,
                        )
                    if os.environ.get("USER_EXAMPLE_TEST") == "1":
                        # Environment switching above stops the original runtime.
                        # Create its replacement before Chromium starts: new Docker
                        # interfaces can abort in-flight assets with ERR_NETWORK_CHANGED
                        # when the browser runs on the same Linux host as Docker.
                        example_response = c.post(
                            "/api/notebooks", headers=HEADERS, json={"database": databases[0]}
                        )
                        example_response.raise_for_status()
                        example_notebook = example_response.json()
                        c.get(example_notebook["url"]).raise_for_status()
                        subprocess.run(
                            ["node", "scripts/examples-browser.mjs"],
                            input=json.dumps(
                                {
                                    "baseURL": str(c.base_url),
                                    "cookie": c.cookies.get("iceberg_user_session"),
                                    "notebook": example_notebook,
                                    "database": databases[0],
                                    "databaseName": f"workspace_0_{suffix}",
                                }
                            ),
                            text=True,
                            check=True,
                        )
                        energy = catalog.load_table(("synthetic", "neighborhood_electricity"))
                        assert energy.scan().count() == 26880
                    response = c.patch("/api/team", headers=HEADERS, json={"team": teams[1]})
                    assert response.status_code == 200
                    assert response.json()["databases"][0]["id"] == databases[1]
                    assert c.get(notebook["url"]).status_code == 404
                    assert not daemon.containers.list(all=True, filters={"name": runtime.name})
                    c.patch("/api/team", headers=HEADERS, json={"team": teams[0]}).raise_for_status()
                    reopened = c.post(
                        "/api/notebooks", headers=HEADERS, json={"database": databases[0]}
                    ).json()
                    second = daemon.containers.get("iceberg-workspaces-marimo-" + reopened["id"])
                    proof = second.exec_run(["cat", "/work/persistence-proof.txt"])
                    assert proof.exit_code == 0 and proof.output == b"saved"
                    if os.environ.get("USER_BROWSER_TEST") == "1":
                        saved = second.exec_run(
                            [
                                "/app/.venv/bin/python",
                                "-c",
                                "from pathlib import Path; assert 'portal-browser-save-proof' in Path('/work/workspace.py').read_text()",
                            ]
                        )
                        assert saved.exit_code == 0, saved.output.decode()
                    print(
                        "PASS: team switch stops container, reopening preserves shared team/environment storage",
                        flush=True,
                    )
                    with httpx.Client(base_url=str(c.base_url)) as outsider:
                        outsider.post(
                            "/api/session",
                            headers=HEADERS,
                            json={
                                "username": other["user"]["name"],
                                "secret": other["credentials"]["clientSecret"],
                            },
                        ).raise_for_status()
                        assert outsider.get(reopened["url"]).status_code == 404
                        outsider.request(
                            "DELETE", "/api/session", headers=HEADERS, json={}
                        ).raise_for_status()
                    print("PASS: another user cannot access private notebook URL", flush=True)
                finally:
                    c.request("DELETE", "/api/session", headers=HEADERS, json={}).raise_for_status()
            assert not daemon.containers.list(
                all=True, filters={"name": "iceberg-workspaces-marimo-" + reopened["id"]}
            )
            print("PASS: logout removes runtime and its private network", flush=True)
    finally:
        for name in volume_names:
            try:
                daemon.volumes.get(name).remove()
            except docker.errors.NotFound:
                pass
        daemon.close()


if __name__ == "__main__":
    main()
