"""Prove live dbt Charts reporting on temporary Iceberg data in isolated containers.

Run after building the reporting image and starting Polaris/RustFS. Generated
artifacts contain only the fictional data created by this test, never credentials.
"""

import json
import os
import subprocess
from contextlib import closing, suppress
from pathlib import Path
from uuid import uuid4

import docker
from docker.errors import NotFound
from pyiceberg.catalog import load_catalog

from scripts.duckdb_smoke import V3_CODE, run_notebook
from scripts.reports_api_smoke import verify_reports_api
from scripts.smoke import stack_resources
from server.models import DatabaseInput, ServiceError, TeamInput, UserInput
from user_portal.directory import UserDirectory
from user_portal.notebook.synthetic import generate_energy_data
from user_portal.reporting.service import ReportingProof

IMAGE = "iceberg-user-reporting:0.1.0"
QUERY = {"measure": "consumption_kwh", "timestamp": "timestamp_utc", "category": "street", "grain": "day"}


class ContainerRunner:
    def __init__(self, daemon, network):
        self.daemon, self.network = daemon, network
        self.calls = 0

    def __call__(self, request):
        self.calls += 1
        name = f"report-proof-{uuid4().hex}"
        source = {
            **request["source"],
            "uri": "http://polaris:8181/api/catalog",
            "s3Endpoint": "http://rustfs:9000",
        }
        try:
            completed = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-i",
                    "--name",
                    name,
                    "--network",
                    self.network.name,
                    "--read-only",
                    "--user",
                    "10001:10001",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges:true",
                    "--memory",
                    "1g",
                    "--cpus",
                    "2",
                    "--pids-limit",
                    "128",
                    "--tmpfs",
                    "/tmp:rw,nosuid,nodev,size=128m,mode=1777",
                    IMAGE,
                ],
                input=json.dumps({**request, "source": source}),
                text=True,
                capture_output=True,
                timeout=45,
                check=False,
            )
            if completed.returncode or len(completed.stdout.encode()) > 4_000_000:
                # Never print docker/provider stderr: it may contain credentials.
                raise ServiceError(502, "Reporting worker failed.")
            return json.loads(completed.stdout)
        finally:
            with suppress(NotFound):
                self.daemon.containers.get(name).remove(force=True)


def write_artifacts(results):
    root = Path("test-results/reporting")
    root.mkdir(parents=True, exist_ok=True)
    summary = {}
    for label, result in results.items():
        (root / f"{label}.svg").write_text(result["svg"])
        # JSON is valid YAML, and this standalone board is directly renderable by dct.
        (root / f"{label}.yml").write_text(json.dumps(result["board"], indent=2) + "\n")
        (root / f"{label}.json").write_text(json.dumps(result["data"], indent=2) + "\n")
        summary[label] = {
            **result["timing"],
            "records": result["data"]["records"],
            "cached": result["cached"],
        }
    (root / "dbt_charts.yml").write_text("{}\n")
    (root / "timings.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"Artifacts: {root.resolve()}", flush=True)


def main():
    context = json.loads(subprocess.check_output(["docker", "context", "inspect"]))[0]
    with (
        closing(docker.DockerClient(base_url=context["Endpoints"]["docker"]["Host"])) as daemon,
        stack_resources() as (provider, teams, databases, users, suffix),
    ):
        teams.append(provider.save_team(TeamInput(name=f"report-{suffix}"))["id"])
        teams.append(provider.save_team(TeamInput(name=f"outside-{suffix}"))["id"])
        database = provider.create_database(DatabaseInput(name=f"report_{suffix}", team=teams[0]))
        databases.append(database["id"])
        accounts = []
        for index, (role, team) in enumerate(
            (("writer", teams[0]), ("reader", teams[0]), ("reader", teams[0]), ("reader", teams[1]))
        ):
            account = provider.create_user(
                UserInput(name=f"report-{index}-{suffix}", memberships=[{"team": team, "role": role}])
            )
            users.append(account["user"]["id"])
            accounts.append(account)
        writer, reader = accounts[:2]
        credentials = writer["credentials"]
        catalog = load_catalog(
            database["id"],
            type="rest",
            uri=f"{provider.public_url}/api/catalog",
            warehouse=database["id"],
            credential=f"{credentials['clientId']}:{credentials['clientSecret']}",
            scope="PRINCIPAL_ROLE:ALL",
            **{
                "header.X-Iceberg-Access-Delegation": "vended-credentials",
                "oauth2-server-uri": f"{provider.public_url}/api/catalog/v1/oauth/tokens",
            },
        )
        catalog.create_namespace("synthetic")
        data = generate_energy_data()
        table = catalog.create_table(("synthetic", "neighborhood_electricity"), schema=data.schema)
        table.append(data)
        network = daemon.networks.create(f"reporting-proof-{suffix}", internal=True)
        directory = UserDirectory(
            {**os.environ, "POLARIS_URL": provider.url, "USER_S3_ENDPOINT": os.environ["S3_ENDPOINT"]}
        )
        results = {}
        try:
            network.connect(
                os.environ.get("POLARIS_CONTAINER", "iceberg-platform-polaris-1"), aliases=["polaris"]
            )
            network.connect(
                os.environ.get("RUSTFS_CONTAINER", "iceberg-platform-rustfs-1"), aliases=["rustfs"]
            )
            sessions = [
                directory.login(a["user"]["name"], a["credentials"]["clientSecret"], f"{suffix}-{i}")
                for i, a in enumerate(accounts)
            ]
            runner = ContainerRunner(daemon, network)
            proof = ReportingProof(directory, runner)

            def run(session=sessions[1], query=None, **kwargs):
                return proof.run(
                    session,
                    database["id"],
                    ["synthetic"],
                    "neighborhood_electricity",
                    query or QUERY,
                    **kwargs,
                )

            results["initial"] = first = run()
            assert first["data"]["records"] == 26_880
            assert "<svg" in first["svg"] and len(first["data"]["rows"]) == 32  # UTC spans eight dates.
            results["cached"] = run()
            assert results["cached"]["cached"] and runner.calls == 1
            results["other-viewer"] = run(sessions[2])
            assert not results["other-viewer"]["cached"] and runner.calls == 2
            results["filtered"] = run(query={**QUERY, "category_value": "Example Solar Street"})
            assert results["filtered"]["data"]["records"] == 6720
            assert all(row[1] == "Example Solar Street" for row in results["filtered"]["data"]["rows"])
            assert catalog.list_tables("synthetic") == [("synthetic", "neighborhood_electricity")]
            print(
                "PASS: dbt Charts renders live aggregates; filter reruns, per-viewer caching and no reporting tables",
                flush=True,
            )

            table.append(data.slice(0, 1))
            results["after-append"] = latest = run()
            assert latest["data"]["records"] == 26_881 and not latest["cached"]
            assert latest["data"]["snapshotId"] != first["data"]["snapshotId"]
            results["forced-refresh"] = run(refresh=True)
            assert not results["forced-refresh"]["cached"]
            print(
                "PASS: a new Iceberg commit bypasses the old cache; explicit refresh executes again",
                flush=True,
            )

            before = runner.calls
            try:
                run(sessions[3])
            except ServiceError as exc:
                assert exc.status == 403
            else:
                raise AssertionError("Outsider accessed team data")
            assert runner.calls == before
            # Also exercise the worker's actual Polaris boundary with the denied token.
            prepared = directory.preview_request(
                sessions[1], database["id"], ["synthetic"], "neighborhood_electricity", None, 100
            )
            details = directory.details(
                sessions[1], database["id"], ["synthetic"], "table", "neighborhood_electricity"
            )
            prepared.update(tableUuid=details["uuid"], schemaId=details["schemaId"], token=sessions[3].token)
            try:
                runner({"source": prepared, "query": QUERY})
            except ServiceError:
                pass
            else:
                raise AssertionError("Worker accepted an unauthorized catalog token")
            provider.update_memberships(reader["user"]["id"], {teams[1]: "reader"})
            try:
                run()
            except ServiceError as exc:
                assert exc.status in (401, 403)
            else:
                raise AssertionError("Revoked viewer received cached data")
            print("PASS: outsider and revoked viewer cannot read fresh or cached reports", flush=True)

            # Reuse the platform's existing v3 write/read fixture, which creates
            # variant, geometry, nanosecond timestamps and deletion vectors.
            run_notebook(daemon, network, database, credentials, V3_CODE)
            results["iceberg-v3"] = v3 = proof.run(
                sessions[2],
                database["id"],
                ["iceberg_v3"],
                "sensor_events",
                {"measure": "event_id", "timestamp": "measured_at", "category": "site", "grain": "day"},
            )
            assert v3["data"]["records"] == 436
            print("PASS: reports read Iceberg v3 with updates/deletion vectors applied", flush=True)
            write_artifacts(results)
            verify_reports_api(provider, database, accounts[2], teams[1])
        finally:
            directory.close()
            network.reload()
            for container_id in network.attrs.get("Containers", {}):
                network.disconnect(container_id, force=True)
            network.remove()


if __name__ == "__main__":
    main()
