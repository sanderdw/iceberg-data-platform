"""The flights semantic model as notebook 06 stores it, its table schemas and a local copy of its data."""

import json
from pathlib import Path

import duckdb

from conversationalbi.semantic import ossie

FIXTURES = Path(__file__).parent / "fixtures"
ENVELOPE = json.loads((FIXTURES / "flights.semantic.json").read_text())
TABLE_SCHEMAS = json.loads((FIXTURES / "flights.schemas.json").read_text())


def model():
    return ossie.parse(ENVELOPE, ["ai_flights"])


def schemas(parsed=None):
    parsed = parsed or model()
    return {d.name: {c["name"]: c["type"] for c in TABLE_SCHEMAS[d.table]} for d in parsed.datasets}


def local(bindings=None):
    """DuckDB with one view per dataset over the fixture Parquet, named like the dataset."""
    connection = duckdb.connect()
    for d in model().datasets:
        if bindings is None or d.name in {b["view"] for b in bindings}:
            connection.execute(f"CREATE VIEW \"{d.name}\" AS SELECT * FROM '{FIXTURES / (d.table + '.parquet')}'")
    return connection
