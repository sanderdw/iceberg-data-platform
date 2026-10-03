"""Publish the flights data product in a partner team and share it with a recipient team.

For demos of the Conversational BI extension and its CI. It runs notebook 06's steps on the host:
generate 12,000 synthetic flights with DuckDB, check them, write six Iceberg tables to the
`ai_flights` namespace and store the `flights` semantic model in Polaris. A team share then gives
the recipient team (by default `demo-team`) the model and exactly the tables it reads.

    uv run --all-groups python -m scripts.flights_seed                  # partner team `flights-partner`
    uv run --all-groups python -m scripts.flights_seed --own demo-demo  # also into a recipient database
    uv run --all-groups python -m scripts.flights_seed --remove         # share, database and team

It writes as a temporary administrator of the partner team, removed at the end. Reruns replace
the example's tables and model. No token or secret is printed.
"""

import argparse
import os
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

from server.models import DatabaseInput, ShareInput, TeamInput, UserInput
from server.polaris import PolarisProvider
from server.storage import RustFSStorage
from user_portal import duckdb_extensions

NAMESPACE = ["ai_flights"]
MODEL = "flights"
EXAMPLES = Path(__file__).resolve().parent.parent / "user_portal" / "notebook" / "examples"
SEEDER = {"id": "portal-" + "0" * 32, "name": "flights-seed"}


def team_named(provider, name, *, create=False):
    team = next((t for t in provider.list_teams() if t["name"] == name), None)
    if team is None and create:
        team = provider.save_team(TeamInput(name=name))
    if team is None:
        raise SystemExit(f"Team {name!r} does not exist.")
    return team["id"]


def database_named(provider, team, name, environment, *, create=False):
    found = next((d for d in provider.list_databases() if (d["team"], d["name"], d["environment"]) == (
        team, name, environment) and d["status"] == "ready"), None)
    if found is None and create:
        found = provider.create_database(DatabaseInput(name=name, team=team, environment=environment))
    if found is None:
        raise SystemExit(f"Database {name!r} ({environment}) does not exist in that team.")
    return found["id"]


@contextmanager
def writer_for(provider, team, database):
    """A temporary team administrator whose client credentials the notebook helpers use."""
    created = provider.create_user(UserInput(name=f"flights-seed-{uuid4().hex[:8]}",
                                             memberships=[{"team": team, "role": "admin"}]))
    credentials = created["credentials"]
    keys = {
        "ICEBERG_CATALOG_URI": provider.url + "/api/catalog",
        "ICEBERG_TOKEN_URI": provider.url + "/api/catalog/v1/oauth/tokens",
        "ICEBERG_DATABASE": database,
        "ICEBERG_CLIENT_ID": credentials["clientId"],
        "ICEBERG_CLIENT_SECRET": credentials["clientSecret"],
        "ICEBERG_S3_ENDPOINT": os.environ.get("S3_ENDPOINT", "http://localhost:9000"),
    }
    previous = {k: os.environ.get(k) for k in [*keys, "ICEBERG_SESSION_TOKEN_URL"]}
    os.environ.update(keys)
    os.environ.pop("ICEBERG_SESSION_TOKEN_URL", None)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        provider.delete_user(created["user"]["id"])


def publish(provider, team, database):
    """Notebook 06, without the notebook: data, quality evidence and the semantic model."""
    from user_portal.notebook.duckdb_connection import connect_duckdb
    from user_portal.notebook.flights import (
        generate_flights,
        load_semantics,
        publish_flights,
        quality_report,
        require_quality,
        semantic_model,
    )
    from user_portal.notebook.semantic import SemanticModels

    ontology, product = load_semantics(EXAMPLES)
    with writer_for(provider, team, database):
        connection = connect_duckdb(NAMESPACE, "flights", read_only=False, missing_ok=True)
        try:
            generate_flights(connection, ontology)
            require_quality(quality_report(connection, product))
            published = publish_flights(connection, ontology, NAMESPACE)
        finally:
            connection.close()
        model = semantic_model(ontology, product, NAMESPACE)
        with SemanticModels.connect(NAMESPACE, ontology["version"]) as models:
            action, _ = models.publish(MODEL, model)
    tables = sorted(row["table"].rsplit(".", 1)[-1].strip('"') for row in published)
    print(f"Published {len(tables)} tables and the semantic model ({action}) in {database}.")
    return tables


def share(provider, database, recipient, tables):
    objects = [{"kind": "semantic-model", "namespace": NAMESPACE, "name": MODEL}] + [
        {"kind": "table", "namespace": NAMESPACE, "name": t} for t in tables]
    for existing in provider.list_shares(database):
        if existing["name"] == MODEL:
            provider.delete_share(existing["id"])
    result = provider.create_share(ShareInput.model_validate({
        "database": database, "name": MODEL, "external": False, "recipientTeam": recipient,
        "description": "Synthetic flights product with its semantic model", "objects": objects,
    }), SEEDER)
    print(f"Shared the model and {len(tables)} tables with the recipient team ({result['share']['id']}).")


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--partner", default="flights-partner", help="team that owns the product")
    parser.add_argument("--database", default="flights", help="the partner team's database")
    parser.add_argument("--recipient", default="demo-team", help="team that receives the share")
    parser.add_argument("--environment", default="development")
    parser.add_argument("--own", metavar="DATABASE", help="also publish into this database of the recipient team")
    parser.add_argument("--remove", action="store_true", help="remove the share, database and partner team")
    args = parser.parse_args()
    provider = PolarisProvider({**os.environ, "POLARIS_URL": os.environ.get("POLARIS_URL", "http://localhost:8181")},
                               RustFSStorage(os.environ))
    try:
        if args.remove:
            partner = team_named(provider, args.partner)
            for database in provider.list_databases():
                if database["team"] == partner:
                    provider.delete_database(database["id"])
            provider.delete_team(partner)
            print(f"Removed team {args.partner!r} with its databases and shares.")
            return
        # DuckDB here never downloads extensions on demand. Install them the way the notebook image
        # does, so a clean machine or CI runner works too.
        os.environ.setdefault("DUCKDB_EXTENSION_DIRECTORY", str(Path.home() / ".duckdb" / "extensions"))
        Path(os.environ["DUCKDB_EXTENSION_DIRECTORY"]).mkdir(parents=True, exist_ok=True)
        duckdb_extensions.main()
        partner = team_named(provider, args.partner, create=True)
        recipient = team_named(provider, args.recipient)
        database = database_named(provider, partner, args.database, args.environment, create=True)
        share(provider, database, recipient, publish(provider, partner, database))
        if args.own:
            publish(provider, recipient, database_named(provider, recipient, args.own, args.environment))
    finally:
        provider.close()


if __name__ == "__main__":
    main()
