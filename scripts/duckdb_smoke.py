"""Run the native DuckDB notebooks on temporary data in offline containers, as a reader and a writer."""

import argparse
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
import duckdb

path = '/app/user_portal/notebook/examples/03_duckdb_iceberg.py'
spec = importlib.util.spec_from_file_location('native_notebook', path)
notebook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notebook)
_, definitions = notebook.app.run(defs={'NAMESPACE': ['synthetic'], 'TABLE': 'neighborhood_electricity'})
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

V3_CODE = """
import importlib.util


def run(name):
    spec = importlib.util.spec_from_file_location('v3_notebook', '/app/user_portal/notebook/examples/' + name)
    notebook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(notebook)
    return notebook.app.run()[1]


for attempt in range(2):  # A full rerun recreates the table with the same result.
    written = run('04_duckdb_iceberg_v3_write.py')
    assert (written['inserted_rows'], written['updated_rows'], written['deleted_rows']) == (480, 40, 45)
    assert len(written['snapshots']) == 4
    written['lakehouse'].close()
read = run('05_duckdb_iceberg_v3_read.py')
types = dict(zip(read['columns']['column_name'], read['columns']['column_type']))
assert (types['payload'], types['measured_at'], types['location']) == ('VARIANT', 'TIMESTAMP_NS', 'GEOMETRY')
assert read['row_count'].iloc[0]['rows'] == 436
assert set(read['kinds']['kind']) == {'temperature', 'vibration', 'door'}
precision = read['precision'].iloc[0]
assert precision['distinct_at_microseconds'] < precision['distinct_at_nanoseconds'] == 436
assert dict(zip(read['firmware']['firmware'], read['firmware']['events'])) == {'v1.0': 399, 'v2.0': 37}
assert len(read['lineage']) == 3 and read['lineage']['first_row_id'].min() == 0
assert 'puffin' in set(read['files']['file_format'])
assert read['first_snapshot']['events'].sum() == 480
read['lakehouse'].close()

from user_portal.notebook.duckdb_connection import connect_duckdb, drop_example_table, table_reference

foreign = connect_duckdb(['iceberg_v3'], 'foreign_events', read_only=False, missing_ok=True)
foreign.execute(f"CREATE TABLE {table_reference(['iceberg_v3'], 'foreign_events')} (id BIGINT)")
try:
    drop_example_table(foreign, ['iceberg_v3'], 'foreign_events', '04_duckdb_iceberg_v3_write')
except RuntimeError:
    assert foreign.execute(f"SELECT count(*) FROM {table_reference(['iceberg_v3'], 'foreign_events')}").fetchone() == (0,)
else:
    raise AssertionError('Expected the example to keep a table it did not create')
foreign.close()
print('PASS: DuckDB writes an Iceberg v3 table (variant, timestamp_ns, geometry, default, deletion vectors) and reads it back with row lineage and time travel')
"""
V3_READER_CODE = """
import importlib.util

import duckdb

from user_portal.notebook.duckdb_connection import connect_duckdb

spec = importlib.util.spec_from_file_location('v3_notebook', '/app/user_portal/notebook/examples/04_duckdb_iceberg_v3_write.py')
notebook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notebook)
try:
    notebook.app.run()
except duckdb.Error as error:
    # Only Polaris's refusal passes: a broken import, extension or connection raises something else.
    assert 'Forbidden' in str(error), error
else:
    raise AssertionError('Expected Polaris to refuse the write for a reader')
lakehouse = connect_duckdb(['synthetic'], 'neighborhood_electricity')
schemas = lakehouse.execute("SELECT schema_name FROM information_schema.schemata WHERE catalog_name = 'lakehouse'").fetchall()
assert ('synthetic',) in schemas and ('iceberg_v3',) not in schemas, schemas
lakehouse.close()
print('PASS: Polaris refuses the Iceberg v3 write notebook for a reader and nothing is created')
"""

FLIGHTS_WRITE_CODE = """
import importlib.util

def run(name):
    spec = importlib.util.spec_from_file_location('flights_notebook', '/app/user_portal/notebook/examples/' + name)
    notebook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(notebook)
    return notebook.app.run()[1]

for attempt in range(2):
    written = run('06_duckdb_flights_write.py')
    counts = {r['dataset']: r['rows'] for r in written['published']}
    assert counts == {'FLIGHT': 12000, 'AIRPORT': 8, 'CARRIER': 3, 'AIRCRAFT': 120, 'ROUTE': 56, 'RUNWAY': 16}
    written['lakehouse'].close()
print('PASS: native DuckDB publishes all six Ossie datasets twice without duplicate flights')
"""

FLIGHTS_READ_CODE = """
import importlib.util
import duckdb

spec = importlib.util.spec_from_file_location('flights_reader', '/app/user_portal/notebook/examples/07_duckdb_flights_read.py')
notebook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notebook)
_, read = notebook.app.run()
assert read['overview'].iloc[0]['scheduled_flights'] == 12000
assert read['airport_delays']['scheduled_flights'].sum() == 12000
assert read['carrier_performance']['eligible_arrivals'].sum() == 11587
assert read['join_cardinality'].iloc[0]['runway_join_rows'] == 24000
try:
    read['lakehouse'].execute('DELETE FROM lakehouse.ai_flights.flights')
except duckdb.Error:
    pass
else:
    raise AssertionError('Expected the read-only attachment to reject writes')
read['lakehouse'].close()

from user_portal.notebook.flights import bind_flights
from user_portal.notebook.duckdb_connection import connect_duckdb
connection = connect_duckdb(['ai_flights'], 'flights')
try:
    bind_flights(connection, read['model'], ['ai_flights'], {'ontology_sha256': 'changed'})
except ValueError as error:
    assert 'semantics differ' in str(error)
else:
    raise AssertionError('Expected semantic version mismatch to be detected')
connection.close()
print('PASS: reader queries all six Iceberg tables, verifies semantics and rejects a mismatched ontology')
"""


def main(*, flights_only=False):
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
            account = provider.create_user(UserInput(name=f"native-{role}-{suffix}", memberships=[{"team": t, "role": role} for t in teams]))
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
        try:
            network.connect(
                os.environ.get("POLARIS_CONTAINER", "iceberg-platform-polaris-1"), aliases=["polaris"]
            )
            network.connect(
                os.environ.get("RUSTFS_CONTAINER", "iceberg-platform-rustfs-1"), aliases=["rustfs"]
            )
            checks = [] if flights_only else [(CODE, reader), (V3_READER_CODE, reader), (V3_CODE, writer)]
            checks.extend([(FLIGHTS_WRITE_CODE, writer), (FLIGHTS_READ_CODE, reader)])
            for code, account in checks:
                run_notebook(daemon, network, database, account, code)
        finally:
            network.reload()
            for container_id in network.attrs.get("Containers", {}):
                network.disconnect(container_id, force=True)
            network.remove()


def run_notebook(daemon, network, database, account, code):
    container = daemon.containers.run(
        "iceberg-user-notebook:0.1.0",
        ["/app/.venv/bin/python", "-c", code],
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
            "ICEBERG_CLIENT_ID": account["clientId"],
            "ICEBERG_CLIENT_SECRET": account["clientSecret"],
            "ICEBERG_CATALOG_URI": "http://polaris:8181/api/catalog",
            "ICEBERG_TOKEN_URI": "http://polaris:8181/api/catalog/v1/oauth/tokens",
            "ICEBERG_S3_ENDPOINT": "http://rustfs:9000",
            "HOME": "/tmp",
            "MARIMO_SKIP_UPDATE_CHECK": "1",
        },
    )
    try:
        status = container.wait(timeout=180)
        output = container.logs().decode()
        assert status["StatusCode"] == 0, output
        print(output.strip())
    finally:
        container.remove(force=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flights-only", action="store_true", help="Run only the flights write/read pair")
    main(flights_only=parser.parse_args().flights_only)
