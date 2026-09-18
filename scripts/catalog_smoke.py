"""Verify user catalog details and disposable previews on temporary Polaris data."""

import os

import pyarrow as pa
from pyiceberg.catalog import load_catalog
from pyiceberg.view import ViewVersion

from scripts.smoke import stack_resources
from server.models import DatabaseInput, ServiceError, TeamInput, UserInput
from user_portal.directory import UserDirectory
from user_portal.preview import run_preview


def main():
    with stack_resources() as (provider, teams, databases, users, suffix):
        teams.append(provider.save_team(TeamInput(name=f"catalog-{suffix}"))["id"])
        db = provider.create_database(DatabaseInput(name=f"catalog_{suffix}", team=teams[0]))["id"]
        databases.append(db)
        writer = provider.create_user(UserInput(name=f"writer-{suffix}", memberships=[{"team": t, "role": "writer"} for t in teams]))
        users.append(writer["user"]["id"])
        reader = provider.create_user(UserInput(name=f"reader-{suffix}", memberships=[{"team": t, "role": "reader"} for t in teams]))
        users.append(reader["user"]["id"])
        credentials = writer["credentials"]
        catalog = load_catalog(
            db,
            type="rest",
            uri=f"{provider.public_url}/api/catalog",
            warehouse=db,
            credential=f"{credentials['clientId']}:{credentials['clientSecret']}",
            scope="PRINCIPAL_ROLE:ALL",
            **{
                "header.X-Iceberg-Access-Delegation": "vended-credentials",
                "oauth2-server-uri": f"{provider.public_url}/api/catalog/v1/oauth/tokens",
            },
        )
        catalog.create_namespace("analytics")
        catalog.create_namespace(("analytics", "nested"), properties={"owner": "catalog-test"})
        data = pa.table(
            {"id": list(range(150)), "event": ["test"] * 150, "details": [{"source": "sensor"}] * 150}
        )
        table = catalog.create_table(("analytics", "nested", "events"), schema=data.schema)
        table.append(data)
        old = str(table.current_snapshot().snapshot_id)
        table.overwrite(pa.table({"id": [999], "event": ["new"], "details": [{"source": "other"}]}))
        catalog.create_view(
            ("analytics", "nested", "report"),
            schema=data.schema,
            view_version=ViewVersion(
                schema_id=0,
                default_namespace=("analytics", "nested"),
                representations=[{"type": "sql", "dialect": "spark", "sql": "SELECT * FROM events"}],
            ),
        )
        directory = UserDirectory(
            {**os.environ, "POLARIS_URL": provider.url, "USER_S3_ENDPOINT": os.environ["S3_ENDPOINT"]}
        )
        try:
            session = directory.login(reader["user"]["name"], reader["credentials"]["clientSecret"], suffix)
            ns = ["analytics", "nested"]
            details = directory.details(session, db, ns, "table", "events")
            assert len(details["snapshots"]) >= 2 and old in {s["id"] for s in details["snapshots"]}
            assert details["columns"][-1]["name"] == "details.source"
            assert directory.details(session, db, ns, "namespace")["properties"]["owner"] == "catalog-test"
            assert (
                directory.details(session, db, ns, "view", "report")["versions"][0]["representations"][0][
                    "dialect"
                ]
                == "spark"
            )
            old_preview = run_preview(directory.preview_request(session, db, ns, "events", old, 100))
            assert len(old_preview["rows"]) == 100 and old_preview["snapshotId"] == old
            assert old_preview["columns"] == ["id", "event", "details"]
            latest = run_preview(directory.preview_request(session, db, ns, "events", None, 100))
            assert latest["rows"][0][:2] == ["999", "new"] and len(latest["rows"]) == 1
            print(
                "PASS: reader inspects nested namespaces, table/schema/snapshots, view SQL and real previews",
                flush=True,
            )
            print(
                "PASS: isolated process reads current and historical snapshots, nested values and 100-row limit",
                flush=True,
            )
            provider.delete_user(reader["user"]["id"])
            users.remove(reader["user"]["id"])
            try:
                directory.preview_request(session, db, ns, "events", None, 100)
            except ServiceError as exc:
                assert exc.status == 401
            else:
                raise AssertionError("Revoked reader retained portal access")
            print("PASS: deleted user cannot prepare another preview", flush=True)
        finally:
            directory.close()


if __name__ == "__main__":
    main()
