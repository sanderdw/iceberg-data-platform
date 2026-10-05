"""The flights semantic model as notebook 06 stores it, with local data, for the governed-query tests.

Mirrors `extensions/conversationalbi/tests/flights.py`, but builds everything from the notebook code
instead of fixture files: the model, its table schemas and one DuckDB view per dataset.
"""

import duckdb

from test.test_flights_notebooks import EXAMPLES
from user_portal.notebook.flights import generate_flights, load_semantics, semantic_model
from user_portal.semantic import ossie
from user_portal.semantic.rules import document

ICEBERG_TYPES = {"BIGINT": "long", "INTEGER": "int", "VARCHAR": "string", "DOUBLE": "double", "DATE": "date",
                 "TIMESTAMP": "timestamp", "BOOLEAN": "boolean", "FLOAT": "float"}


def stored(flagged=False):
    """The model as Polaris returns it; without dimension flags by default, like the extension's fixture."""
    found, contract = load_semantics(EXAMPLES)
    model = semantic_model(found, contract, ["ai_flights"])
    if not flagged:
        for dataset in model["datasets"]:
            for field in dataset["fields"]:
                field.pop("dimension", None)
    return {"document": document(model), "entity-version": 1}


ENVELOPE = stored()


def model():
    return ossie.parse(ENVELOPE, ["ai_flights"])


def local(bindings=None):
    """DuckDB with one view per dataset over generated flights data, named like the dataset."""
    connection = duckdb.connect()
    found, _ = load_semantics(EXAMPLES)
    generate_flights(connection, found)
    return connection


def schemas(parsed=None):
    parsed = parsed or model()
    connection = local()
    return {d.name: {name: ICEBERG_TYPES.get(kind.split("(")[0], kind.lower())
                     for name, kind, *_ in connection.execute(f'DESCRIBE "{d.name}"').fetchall()}
            for d in parsed.datasets}

