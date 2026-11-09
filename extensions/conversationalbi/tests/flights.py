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
    views = ({d.name: d.table for d in model().datasets} if bindings is None
             else {b["view"]: b["table"] for b in bindings})
    for view, table in views.items():
        connection.execute(f"CREATE VIEW \"{view}\" AS SELECT * FROM '{FIXTURES / (table + '.parquet')}'")
    return connection
