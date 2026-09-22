"""SQL implementation of Ossie's flights example, not a general ontology compiler."""

import hashlib
from pathlib import Path

import yaml

from user_portal.notebook.duckdb_connection import (
    EXAMPLE_PROPERTY,
    drop_example_table,
    refresh_table_credentials,
    schema_reference,
    sql_literal,
    table_reference,
)

EXAMPLE = "06_duckdb_flights_write"


def identifier(value):
    return '"' + value.replace('"', '""') + '"'


def load_semantics(directory):
    """Read the copies beside the notebook, including the user's local edits."""
    directory = Path(directory)
    model_text = (directory / "flights.yaml").read_text()
    contract_text = (directory / "flights.product.yaml").read_text()
    return yaml.safe_load(model_text), yaml.safe_load(contract_text), {
        "ontology_sha256": hashlib.sha256(model_text.encode()).hexdigest(),
        "contract_sha256": hashlib.sha256(contract_text.encode()).hexdigest(),
    }


def datasets(model):
    return model["ontology_mappings"][0]["semantic_model"]["datasets"]


def table_name(dataset):
    return dataset["source"].split(".")[-1].lower()


def generate_flights(connection, model, row_count=12000):
    """Deterministic, native DuckDB generation; all six mapped schemas come from YAML.

    Physical values and rules are implemented explicitly in SQL. The YAML selects
    the published fields and binds conceptual dataset names to physical tables.
    No network, random state, pandas, Arrow or PyIceberg is used for generation.
    """
    if not isinstance(row_count, int) or isinstance(row_count, bool) or row_count < 10000:
        raise ValueError("The flights demonstration requires at least 10,000 flight records.")
    connection.execute("""
        CREATE OR REPLACE TEMP TABLE generated_airports AS
        SELECT code, name, city_nm, state_code, state_nm, city_nm AS market_nm,
               latitude::DOUBLE AS latitude, longitude::DOUBLE AS longitude,
               DATE '1980-01-01' AS opened, true AS synthetic
        FROM (VALUES
            ('QAA', 'Example Atlantic Airport', 'Example Atlantic', 'NY', 'New York', 40.6, -73.8),
            ('QAB', 'Example Pacific Airport', 'Example Pacific', 'CA', 'California', 33.9, -118.4),
            ('QAC', 'Example Lakes Airport', 'Example Lakes', 'IL', 'Illinois', 42.0, -87.9),
            ('QAD', 'Example Mountain Airport', 'Example Mountain', 'CO', 'Colorado', 39.8, -104.7),
            ('QAE', 'Example Gulf Airport', 'Example Gulf', 'TX', 'Texas', 30.0, -95.3),
            ('QAF', 'Example Northwest Airport', 'Example Northwest', 'WA', 'Washington', 47.4, -122.3),
            ('QAG', 'Example Desert Airport', 'Example Desert', 'AZ', 'Arizona', 33.4, -112.0),
            ('QAH', 'Example Southeast Airport', 'Example Southeast', 'GA', 'Georgia', 33.6, -84.4)
        ) AS a(code, name, city_nm, state_code, state_nm, latitude, longitude);

        CREATE OR REPLACE TEMP TABLE generated_carriers AS
        SELECT *, true AS synthetic FROM (VALUES
            ('XA', 'Example Aurora Air'), ('XB', 'Example Blue Skies'), ('XC', 'Example Cloudline')
        ) AS c(code, name);

        CREATE OR REPLACE TEMP TABLE generated_aircraft AS
        SELECT 'DEMO-SERIAL-' || c.code || '-' || n::VARCHAR AS serial_nr,
               'Example aircraft ' || c.code || '-' || n::VARCHAR AS name,
               (150 + n % 4 * 20)::INTEGER AS nr_seats,
               'DEMO-' || c.code || '-' || lpad(n::VARCHAR, 3, '0') AS tail_nr,
               c.code AS carrier_code, 'Example Aerospace' AS manufacturer,
               'DemoJet-' || (n % 4)::VARCHAR AS model,
               (2005 + n % 20)::VARCHAR AS year, (40000 + n % 4 * 5000)::BIGINT AS capacity,
               true AS synthetic
        FROM generated_carriers c CROSS JOIN range(40) AS aircraft(n);

        CREATE OR REPLACE TEMP TABLE generated_routes AS
        WITH distances AS (
            SELECT o.code AS orig_airport_code, d.code AS dest_airport_code,
                   round(3958.7613 * 2 * asin(sqrt(least(1.0,
                       pow(sin(radians(d.latitude - o.latitude) / 2), 2)
                       + cos(radians(o.latitude)) * cos(radians(d.latitude))
                       * pow(sin(radians(d.longitude - o.longitude) / 2), 2)))), 1) AS distance,
                   o.code || ' -> ' || d.code AS id, o.name || ' to ' || d.name AS name
            FROM generated_airports o CROSS JOIN generated_airports d WHERE o.code <> d.code
        )
        SELECT *, least(10, greatest(1, ceil(distance / 300)))::INTEGER AS dist_grp,
               true AS synthetic FROM distances;

        CREATE OR REPLACE TEMP TABLE generated_runways AS
        SELECT code AS airport_code, (8000 + n * 1000)::DOUBLE AS length,
               to_json([[latitude, longitude], [latitude + 0.01, longitude],
                        [latitude + 0.01, longitude + 0.001], [latitude, longitude + 0.001],
                        [latitude, longitude]])::VARCHAR AS shape,
               CASE n WHEN 0 THEN '09/27' ELSE '18/36' END AS designator, true AS synthetic
        FROM generated_airports CROSS JOIN range(2) AS runway(n);
    """)
    connection.execute("""
        CREATE OR REPLACE TEMP TABLE generated_flights AS
        WITH routes AS (
            SELECT *, row_number() OVER (ORDER BY id) - 1 AS route_index FROM generated_routes
        ), schedule AS (
            SELECT i, r.id AS route_id, r.distance AS route_distance,
                   ['XA', 'XB', 'XC'][1 + i % 3] AS carrier_code,
                   TIMESTAMP '2026-01-01 00:00:00' + (i // 400) * INTERVAL '1 day'
                       + (i % 400) * INTERVAL '3 minutes' AS scheduled_departure,
                   (ceil(r.distance / 7.5) + 40)::DOUBLE AS scheduled_duration,
                   i % 41 = 0 AS cancelled, i % 97 = 0 AND i % 41 <> 0 AS diverted,
                   CASE WHEN i % 11 = 0 THEN 40 + (i * 7) % 120 ELSE (i * 13) % 36 - 10 END
                       AS departure_offset
            FROM range(?) AS instances(i) JOIN routes r ON r.route_index = (i * 17) % 56
        ), observed AS (
            SELECT *, scheduled_departure + scheduled_duration * INTERVAL '1 minute' AS scheduled_arrival,
                   CASE WHEN NOT cancelled THEN departure_offset::DOUBLE END AS dep_delay,
                   CASE WHEN NOT cancelled THEN
                       (departure_offset + (i * 19) % 21 - 10 + CASE WHEN diverted THEN 65 ELSE 0 END)::DOUBLE
                   END AS arr_delay
            FROM schedule
        ), actual AS (
            SELECT *, scheduled_departure + dep_delay * INTERVAL '1 minute' AS departure,
                   scheduled_arrival + arr_delay * INTERVAL '1 minute' AS arrival
            FROM observed
        )
        SELECT 'DEMO-' || lpad((i + 1)::VARCHAR, 6, '0') AS id,
               carrier_code || (100 + i % 300)::VARCHAR AS nr, carrier_code, route_id,
               'DEMO-' || carrier_code || '-' || lpad(((i // 3) % 40)::VARCHAR, 3, '0') AS tail_nr,
               scheduled_departure::DATE AS date, scheduled_departure, scheduled_arrival, scheduled_duration,
               departure, arrival, dep_delay, arr_delay,
               departure + INTERVAL '12 minutes' AS wheels_off,
               arrival - INTERVAL '8 minutes' AS wheels_on,
               date_diff('minute', departure, arrival)::DOUBLE AS duration,
               (date_diff('minute', departure, arrival) - 20)::DOUBLE AS air_time,
               CASE WHEN NOT cancelled THEN round(route_distance * CASE WHEN diverted THEN 1.15 ELSE 1.02 END, 1)
                   END AS distance,
               cancelled, diverted,
               CASE WHEN cancelled THEN ['A', 'B', 'C', 'D'][1 + (i // 41) % 4] END AS cancel_code,
               true AS synthetic
        FROM actual
    """, [row_count])
    expected = {"airports", "carriers", "aircraft", "routes", "runways", "flights"}
    if {table_name(d) for d in datasets(model)} != expected:
        raise ValueError("This SQL generator implements the six datasets in Ossie's flights example.")
    for dataset in datasets(model):
        # Bind the upstream conceptual names (FLIGHT, AIRPORT, ...) to SQL views.
        columns = ", ".join(identifier(f["name"]) for f in dataset["fields"])
        connection.execute(
            f"CREATE OR REPLACE TEMP VIEW {identifier(dataset['name'])} AS "
            f"SELECT {columns}, synthetic FROM {identifier('generated_' + table_name(dataset))}"
        )


def quality_report(connection, contract):
    """Execute the reviewed SQL checks in the local demo contract, not Ossie expressions."""
    return [
        {"check": check["name"], "violations": connection.execute(check["sql"]).fetchone()[0]}
        for check in contract["quality_checks"]
    ]


def require_quality(report):
    failed = [row["check"] for row in report if row["violations"] != 0]
    if failed:
        raise ValueError("Flights product quality checks failed: " + "; ".join(failed))


def publish_flights(connection, model, namespace, fingerprints):
    """Publish validated local views using native DuckDB CREATE/INSERT statements.

    Preflight ownership across all tables before replacing any. Each table is a
    separate Iceberg commit; rerun after an interrupted publication before reading.
    """
    for dataset in datasets(model):
        name = table_name(dataset)
        exists = connection.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_catalog = 'lakehouse' AND table_schema = ? AND table_name = ?",
            [".".join(namespace), name],
        ).fetchone()[0]
        if exists:
            owner = connection.execute(
                f"SELECT value FROM iceberg_table_properties({table_reference(namespace, name)}) WHERE key = ?",
                [EXAMPLE_PROPERTY],
            ).fetchone()
            if not owner or owner[0] != EXAMPLE:
                raise RuntimeError(f"{name} already exists and is not owned by this example. Choose another NAMESPACE.")
    connection.execute(f"CREATE SCHEMA IF NOT EXISTS {schema_reference(namespace)}")
    results = []
    for dataset in datasets(model):
        name = table_name(dataset)
        reference = table_reference(namespace, name)
        source = identifier(dataset["name"])
        columns = connection.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()
        ddl = ", ".join(f"{identifier(c[0])} {c[1]}" for c in columns)
        properties = {
            "format-version": "2", EXAMPLE_PROPERTY: EXAMPLE,
            "flights.ontology-sha256": fingerprints["ontology_sha256"],
            "flights.contract-sha256": fingerprints["contract_sha256"],
            "comment": "Synthetic Ossie flights demo. UTC timestamps; distances in miles; delays in minutes.",
        }
        options = ", ".join(f"{sql_literal(k)} = {sql_literal(v)}" for k, v in properties.items())
        drop_example_table(connection, namespace, name, EXAMPLE)
        connection.execute(f"CREATE TABLE {reference} ({ddl}) WITH ({options})")
        refresh_table_credentials(connection, namespace, name, secret_name=f"flights_{name}")
        count = connection.execute(f"INSERT INTO {reference} SELECT * FROM {source}").fetchone()[0]
        results.append({"dataset": dataset["name"], "table": reference, "rows": count})
    return results


def bind_flights(connection, model, namespace, fingerprints):
    """Read the same six Iceberg tables directly through views; retain table-scoped secrets."""
    for dataset in datasets(model):
        name = table_name(dataset)
        reference = table_reference(namespace, name)
        properties = dict(connection.execute(f"SELECT key, value FROM iceberg_table_properties({reference})").fetchall())
        if any(properties.get(f"flights.{key.replace('_', '-')}") != value for key, value in fingerprints.items()):
            raise ValueError("Local semantics differ from the published product. Run notebook 06 with these YAML files.")
        refresh_table_credentials(connection, namespace, name, secret_name=f"flights_{name}")
        connection.execute(f"CREATE OR REPLACE TEMP VIEW {identifier(dataset['name'])} AS SELECT * FROM {reference}")
