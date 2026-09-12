"""Live standalone gateway test; creates and cleans only its own data and notebooks."""

import json
import os
import subprocess
from hashlib import sha256

import docker
import httpx
import pyarrow as pa
from pyiceberg.catalog import load_catalog
from pyiceberg.view import ViewVersion

from scripts.smoke import stack_resources
from server.models import DatabaseInput, TeamInput, UserInput

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
            for team, db in zip(teams[:2], databases[:2], strict=True):
                key = sha256(json.dumps([users[0], team, db]).encode()).hexdigest()[:32]
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
                    if os.environ.get("USER_BROWSER_TEST") == "1":
                        subprocess.run(
                            ["node", "scripts/users-browser.mjs"],
                            input=json.dumps(
                                {
                                    "baseURL": str(c.base_url),
                                    "cookie": c.cookies.get("iceberg_user_session"),
                                    "notebook": notebook,
                                    "database": databases[0],
                                    "team": teams[1],
                                }
                            ),
                            text=True,
                            check=True,
                        )
                    if os.environ.get("USER_EXAMPLE_TEST") == "1":
                        subprocess.run(
                            ["node", "scripts/examples-browser.mjs"],
                            input=json.dumps(
                                {
                                    "baseURL": str(c.base_url),
                                    "cookie": c.cookies.get("iceberg_user_session"),
                                    "database": databases[0],
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
                        "PASS: team switch stops container, reopening preserves personal notebook storage",
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
