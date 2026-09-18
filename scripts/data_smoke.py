"""Write and read real Parquet using team credentials, then delete a nonempty catalog."""

import pyarrow as pa
from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import ForbiddenError
from pyiceberg.view import ViewVersion

from scripts.smoke import stack_resources
from server.models import DatabaseInput, TeamInput, UserInput


def main():
    with stack_resources() as (p, teams, databases, users, suffix):
        owner = p.save_team(TeamInput(name=f"data-team-{suffix}"))["id"]
        teams.append(owner)
        db = p.create_database(DatabaseInput(name=f"data_{suffix}", team=owner))
        databases.append(db["id"])
        catalogs = {}
        for role in ("writer", "reader"):
            result = p.create_user(UserInput(name=f"{role}-{suffix}", memberships=[{"team": owner, "role": role}]))
            users.append(result["user"]["id"])
            credentials = result["credentials"]
            catalogs[role] = load_catalog(
                db["id"],
                type="rest",
                uri=f"{p.public_url}/api/catalog",
                warehouse=db["id"],
                credential=f"{credentials['clientId']}:{credentials['clientSecret']}",
                scope="PRINCIPAL_ROLE:ALL",
                **{
                    "oauth2-server-uri": f"{p.public_url}/api/catalog/v1/oauth/tokens",
                    "header.X-Iceberg-Access-Delegation": "vended-credentials",
                },
            )
        writer, reader = catalogs["writer"], catalogs["reader"]
        writer.create_namespace("analytics")
        writer.create_namespace(("analytics", "nested"))
        records = pa.table({"id": [1, 2, 3], "event": ["deploy", "build", "release"]})
        table = writer.create_table(("analytics", "nested", "events"), schema=records.schema)
        table.append(records)
        result = reader.load_table(("analytics", "nested", "events")).scan().to_arrow()
        assert result.sort_by("id").to_pylist() == records.to_pylist()
        print("PASS: writer appended Parquet records; team reader scanned them from RustFS")
        try:
            reader.create_namespace("forbidden")
        except ForbiddenError:
            pass
        else:
            raise AssertionError("Reader must not create namespaces")
        print("PASS: reader cannot write")
        p.update_memberships(users[1], {owner: "writer"})
        reader.create_namespace("promoted")
        p.update_memberships(users[1], {owner: "reader"})
        try:
            reader.create_namespace("demoted")
        except ForbiddenError:
            pass
        else:
            raise AssertionError("Demoted reader still has write access")
        print("PASS: role promotion and demotion update an existing catalog client's permissions")
        writer.drop_namespace("promoted")
        writer.create_view(
            ("analytics", "nested", "event_report"),
            schema=records.schema,
            view_version=ViewVersion(
                schema_id=0,
                default_namespace=("analytics", "nested"),
                representations=[{"type": "sql", "sql": "SELECT * FROM events", "dialect": "spark"}],
            ),
        )
        assert any(d["id"] == db["id"] for d in p.explorer_databases())
        root = p.explorer_contents(db["id"], [])
        assert root["namespaces"] == [["analytics"]]
        parent = p.explorer_contents(db["id"], ["analytics"])
        assert parent["namespaces"] == [["analytics", "nested"]]
        content = p.explorer_contents(db["id"], ["analytics", "nested"])
        assert [t["name"] for t in content["tables"]] == ["events"]
        assert [v["name"] for v in content["views"]] == ["event_report"]
        roles = p.management(f"/catalogs/{db['id']}/catalog-roles")["roles"]
        assert not any(r["name"].startswith("portal-explorer-") for r in roles)
        print("PASS: portal explorer lists nested namespaces, tables and views; temporary roles removed")
        p.delete_database(db["id"])
        databases.remove(db["id"])
        assert not any(d["id"] == db["id"] for d in p.list_databases())
        print("PASS: database deletion removed nested namespaces, table and data bucket")


if __name__ == "__main__":
    main()
