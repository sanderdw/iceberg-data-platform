"""Run the example notebooks on temporary data in offline containers, as a reader and a writer."""

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
    assert foreign.execute(f"SELECT count(*) FROM {table_reference(['iceberg_v3'], 'foreign_events')}").fetchall() == [(0,)]
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

for expected in ('created', 'updated'):
    written = run('06_duckdb_flights_write.py')
    counts = {r['dataset']: r['rows'] for r in written['published']}
    assert counts == {'FLIGHT': 12000, 'AIRPORT': 8, 'CARRIER': 3, 'AIRCRAFT': 120, 'ROUTE': 56, 'RUNWAY': 16}
    assert written['model_action'] == expected, written['model_action']
    written['lakehouse'].close()
print('PASS: native DuckDB publishes all six Ossie datasets and their semantic model twice without duplicate flights')
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
assert len(read['checks']) == 13 and all(c['violations'] == 0 for c in read['checks'])
try:
    read['lakehouse'].execute('DELETE FROM lakehouse.ai_flights.flights')
except duckdb.Error:
    pass
else:
    raise AssertionError('Expected the read-only attachment to reject writes')
read['lakehouse'].close()
print('PASS: reader takes the semantic model from Polaris, checks keys and joins, and answers with its metrics')
"""

FLIGHTS_SEMANTIC_READ_CODE = """
import json

from user_portal.notebook.semantic import SemanticModelError, SemanticModels

models = SemanticModels.connect(['ai_flights'])
assert models.names() == ['flights']
stored, version = models.load('flights')
raw = models.client.get(models.base + '/flights').json()['document']
ossie = json.loads(raw['semantic_model'])
assert raw['version'] == ossie['version'] == '0.2.0.dev0' and ossie['semantic_model'] == [stored], raw['version']
assert [d['name'] for d in stored['datasets']] == ['RUNWAY', 'AIRCRAFT', 'AIRPORT', 'FLIGHT', 'CARRIER', 'ROUTE']
assert models.load('missing') is None
for change in (lambda: models.create('other', stored), lambda: models.update('flights', stored, version), lambda: models.drop('flights')):
    try:
        change()
    except SemanticModelError as error:
        assert 'role' in str(error), error
    else:
        raise AssertionError('Expected Polaris to refuse a reader changing a semantic model')
assert models.names() == ['flights']
print('PASS: a reader lists and reads the semantic model but Polaris refuses every change')
"""


GRID_WRITE_CODE = """
import importlib.util
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx

from user_portal.notebook.connection import SessionRestCatalog, connect

# Sign in as a portal notebook does: every request fetches the owner's current token from a session URL.
token = httpx.post(os.environ['ICEBERG_TOKEN_URI'], data={
    'grant_type': 'client_credentials', 'client_id': os.environ['ICEBERG_CLIENT_ID'],
    'client_secret': os.environ['ICEBERG_CLIENT_SECRET'], 'scope': 'PRINCIPAL_ROLE:ALL'}).json()['access_token']

class Session(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({'access_token': token}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass

server = HTTPServer(('127.0.0.1', 0), Session)
threading.Thread(target=server.serve_forever, daemon=True).start()
os.environ.update(ICEBERG_SESSION_TOKEN_URL=f'http://127.0.0.1:{server.server_port}/token', MARIMO_GATEWAY_TOKEN='smoke')
assert isinstance(connect(), SessionRestCatalog)

def run(name):
    spec = importlib.util.spec_from_file_location('grid_notebook', '/app/user_portal/notebook/examples/' + name)
    notebook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(notebook)
    return notebook.app.run()[1]

identities = []
for expected in ('created', 'updated'):
    written = run('09_polars_grid_forecast_write.py')
    counts = {r['dataset']: r['rows'] for r in written['published']}
    assert counts['LOAD_FORECAST'] == 8640 and counts['SPREADING_CONTRIBUTION'] == 21600, counts
    assert written['model_action'] == expected, written['model_action']
    catalog = connect()
    tables = [catalog.load_table(('ai_grid_forecast', r['table'].split('.')[-1])) for r in written['published']]
    identities.append([str(t.metadata.table_uuid) for t in tables])
    assert [t.scan().count() for t in tables] == list(counts.values())
assert identities[0] == identities[1]
print('PASS: Polars writes all eight grid forecast tables with its Iceberg sink in a portal session, twice, keeping their identity and row counts')
"""

GRID_READ_CODE = """
import importlib.util

from user_portal.notebook.connection import connect

def run(name):
    spec = importlib.util.spec_from_file_location('grid_notebook', '/app/user_portal/notebook/examples/' + name)
    notebook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(notebook)
    return notebook.app.run()[1]

read = run('10_polars_grid_forecast_read.py')
assert len(read['checks']) == 15 and all(c['violations'] == 0 for c in read['checks'])
assert read['congestion_overview']['congestion_points'].sum() == 162
assert read['engines_agree']['agree'].all()
forecast = connect().load_table(('ai_grid_forecast', 'load_forecast'))
before = forecast.current_snapshot().snapshot_id
written = run('09_polars_grid_forecast_write.py')
assert 'published' not in written, 'Expected Polaris to refuse a reader publishing the grid forecast'
assert connect().load_table(('ai_grid_forecast', 'load_forecast')).current_snapshot().snapshot_id == before
print('PASS: Polars scans the grid forecast with the semantic model from Polaris; a reader cannot publish it')
"""


def main(*, flights_only=False, grid_only=False):
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
            checks = [] if flights_only or grid_only else [(CODE, reader), (V3_READER_CODE, reader), (V3_CODE, writer)]
            if not grid_only:
                checks += [(FLIGHTS_WRITE_CODE, writer), (FLIGHTS_READ_CODE, reader), (FLIGHTS_SEMANTIC_READ_CODE, reader)]
            if not flights_only:
                checks += [(GRID_WRITE_CODE, writer), (GRID_READ_CODE, reader)]
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
    parser.add_argument("--grid-only", action="store_true", help="Run only the Polars grid forecast write/read pair")
    arguments = parser.parse_args()
    main(flights_only=arguments.flights_only, grid_only=arguments.grid_only)
