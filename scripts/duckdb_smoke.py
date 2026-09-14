"""Run the native notebook as a reader on temporary data in an offline container."""

import json
import os
import subprocess
from contextlib import closing
from pathlib import Path

import docker
from pyiceberg.catalog import load_catalog

from scripts.smoke import stack_resources
from server.models import DatabaseInput, TeamInput, UserInput
from user_portal.notebook.synthetic import generate_energy_data

CODE = """
import importlib.util
from types import SimpleNamespace
import duckdb

path = '/app/user_portal/notebook/examples/03_duckdb_iceberg.py'
spec = importlib.util.spec_from_file_location('native_notebook', path)
notebook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notebook)
_, definitions = notebook.app.run(defs={'table_settings': SimpleNamespace(value={
    'namespace': 'synthetic', 'table': 'neighborhood_electricity'
})})
assert definitions['row_count'].iloc[0]['rows'] == 26880
assert len(definitions['preview']) == 100
assert len(definitions['snapshots']) == 1
assert definitions['street_totals']['homes'].sum() == 40
connection = definitions['lakehouse']
try:
    connection.execute('DELETE FROM lakehouse.synthetic.neighborhood_electricity')
except duckdb.Error:
    pass
else:
    raise AssertionError('Expected read-only attachment to reject writes')
connection.close()
print('PASS: native notebook reads 26,880 Iceberg rows, previews 100, aggregates 40 homes, inspects snapshots and rejects writes')
"""


def main():
    context = json.loads(subprocess.check_output(["docker", "context", "inspect"]))[0]
    with (
        closing(docker.DockerClient(base_url=context["Endpoints"]["docker"]["Host"])) as daemon,
        stack_resources() as (provider, teams, databases, users, suffix),
    ):
        teams.append(provider.save_team(TeamInput(name=f"native-{suffix}"))["id"])
        database = provider.create_database(DatabaseInput(name=f"native_{suffix}", team=teams[0]))
        databases.append(database["id"])
        accounts = []
        for role in ("writer", "reader"):
            account = provider.create_user(UserInput(name=f"native-{role}-{suffix}", teams=teams, role=role))
            users.append(account["user"]["id"])
            accounts.append(account["credentials"])
        writer, reader = accounts
        catalog = load_catalog(
            database["id"],
            type="rest",
            uri=f"{provider.public_url}/api/catalog",
            warehouse=database["id"],
            credential=f"{writer['clientId']}:{writer['clientSecret']}",
            scope="PRINCIPAL_ROLE:ALL",
            **{"header.X-Iceberg-Access-Delegation": "vended-credentials"},
        )
        data = generate_energy_data()
        catalog.create_namespace("synthetic")
        catalog.create_table(("synthetic", "neighborhood_electricity"), schema=data.schema).append(data)
        network = daemon.networks.create(f"native-duckdb-test-{suffix}", internal=True)
        container = None
        try:
            network.connect(
                os.environ.get("POLARIS_CONTAINER", "iceberg-platform-polaris-1"), aliases=["polaris"]
            )
            network.connect(
                os.environ.get("RUSTFS_CONTAINER", "iceberg-platform-rustfs-1"), aliases=["rustfs"]
            )
            container = daemon.containers.run(
                "iceberg-user-notebook:0.1.0",
                ["/app/.venv/bin/python", "-c", CODE],
                detach=True,
                network=network.name,
                read_only=True,
                user="10001:10001",
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                mem_limit="1g",
                tmpfs={"/tmp": "rw,nosuid,nodev,size=256m,mode=1777"},
                volumes={
                    str(Path("user_portal/notebook").resolve()): {
                        "bind": "/app/user_portal/notebook",
                        "mode": "ro",
                    }
                },
                environment={
                    "ICEBERG_DATABASE": database["id"],
                    "ICEBERG_CLIENT_ID": reader["clientId"],
                    "ICEBERG_CLIENT_SECRET": reader["clientSecret"],
                    "ICEBERG_CATALOG_URI": "http://polaris:8181/api/catalog",
                    "ICEBERG_TOKEN_URI": "http://polaris:8181/api/catalog/v1/oauth/tokens",
                    "ICEBERG_S3_ENDPOINT": "http://rustfs:9000",
                    "HOME": "/tmp",
                    "MARIMO_SKIP_UPDATE_CHECK": "1",
                },
            )
            status = container.wait(timeout=120)
            output = container.logs().decode()
            assert status["StatusCode"] == 0, output
            print(output.strip())
        finally:
            if container:
                container.remove(force=True)
            network.reload()
            for container_id in network.attrs.get("Containers", {}):
                network.disconnect(container_id, force=True)
            network.remove()


if __name__ == "__main__":
    main()
