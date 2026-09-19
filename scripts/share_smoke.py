"""Read a shared table with only the share credential; everything else must stay closed."""

import os

import boto3
import httpx
import pyarrow as pa
from botocore.config import Config
from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import ForbiddenError
from pyiceberg.view import ViewVersion

from scripts.smoke import assert_denied, stack_resources, token
from server.models import DatabaseInput, ServiceError, ShareInput, ShareUpdate, TeamInput, UserInput


def connect(connection):
    return load_catalog(
        connection["warehouse"],
        type="rest",
        uri=connection["uri"],
        warehouse=connection["warehouse"],
        credential=connection["credential"],
        scope=connection["scope"],
        **{
            "oauth2-server-uri": connection["oauth2ServerUri"],
            "header.X-Iceberg-Access-Delegation": connection["accessDelegation"],
        },
    )


def forbidden(action, message):
    try:
        action()
    except ForbiddenError:
        return
    raise AssertionError(message)


def split(location):
    bucket, _, key = location.removeprefix("s3://").partition("/")
    return bucket, key


def main():
    with stack_resources() as (p, teams, databases, users, suffix):
        owner = p.save_team(TeamInput(name=f"share-team-{suffix}"))["id"]
        teams.append(owner)
        db = p.create_database(DatabaseInput(name=f"share_{suffix}", team=owner))["id"]
        other = p.create_database(DatabaseInput(name=f"share_other_{suffix}", team=owner))["id"]
        databases.extend([db, other])
        admin = p.create_user(UserInput(name=f"admin-{suffix}", memberships=[{"team": owner, "role": "admin"}]))
        users.append(admin["user"]["id"])
        credential = f"{admin['credentials']['clientId']}:{admin['credentials']['clientSecret']}"
        team_connection = {**p.connection(db), "credential": credential, "accessDelegation": "vended-credentials"}
        writer = connect(team_connection)
        elsewhere = connect({**team_connection, "warehouse": other})
        records = pa.table({"id": [1, 2, 3]})
        writer.create_namespace("sales")
        elsewhere.create_namespace("sales")
        tables = {}
        for catalog, name in ((writer, "orders"), (writer, "internal"), (elsewhere, "elsewhere")):
            tables[name] = catalog.create_table(("sales", name), schema=records.schema)
            tables[name].append(records)
        writer.create_view(
            ("sales", "report"),
            schema=records.schema,
            view_version=ViewVersion(
                schema_id=0,
                default_namespace=("sales",),
                representations=[{"type": "sql", "sql": "SELECT * FROM orders", "dialect": "spark"}],
            ),
        )

        objects = [
            {"kind": "table", "namespace": ["sales"], "name": "orders"},
            {"kind": "view", "namespace": ["sales"], "name": "report"},
        ]
        issued = p.create_share(
            ShareInput.model_validate({"database": db, "name": f"partner-{suffix}", "objects": objects}),
            {"id": admin["user"]["id"], "name": admin["user"]["name"]},
        )
        id = issued["share"]["id"]
        assert not any(u["id"] == id for u in p.list_users())
        external = connect(issued["connection"])
        assert external.load_table("sales.orders").scan().to_arrow().num_rows == 3
        assert external.view_exists("sales.report")
        print("PASS: the share credential reads the shared table and loads the shared view")
        forbidden(lambda: external.load_table("sales.internal"), "A table outside the share is readable")
        forbidden(external.list_namespaces, "The share credential lists namespaces")
        forbidden(lambda: external.list_tables("sales"), "The share credential lists tables")
        forbidden(lambda: external.create_namespace("forbidden"), "The share credential writes")
        bearer = {"Authorization": f"Bearer {token(p, issued['credentials'])}"}
        foreign = httpx.get(f"{p.url}/api/catalog/v1/{other}/namespaces/sales/tables/elsewhere", headers=bearer)
        assert foreign.status_code == 403, foreign.status_code
        print("PASS: other tables, listings, writes and other databases are forbidden")

        loaded = httpx.get(
            f"{p.url}/api/catalog/v1/{db}/namespaces/sales/tables/orders",
            headers={**bearer, "X-Iceberg-Access-Delegation": "vended-credentials"},
        ).json()["config"]
        s3 = boto3.client(
            "s3",
            endpoint_url=os.environ["S3_ENDPOINT"],
            region_name=os.environ.get("AWS_REGION", "us-east-1"),
            aws_access_key_id=loaded["s3.access-key-id"],
            aws_secret_access_key=loaded["s3.secret-access-key"],
            aws_session_token=loaded["s3.session-token"],
            config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 0}),
        )
        bucket, prefix = split(tables["orders"].location())
        assert s3.list_objects_v2(Bucket=bucket, Prefix=prefix + "/")["KeyCount"] > 0
        internal_bucket, internal_key = split(next(iter(tables["internal"].scan().plan_files())).file.file_path)
        other_bucket, other_prefix = split(tables["elsewhere"].location())
        assert_denied(lambda: s3.get_object(Bucket=internal_bucket, Key=internal_key))
        assert_denied(lambda: s3.list_objects_v2(Bucket=bucket))
        assert_denied(lambda: s3.list_objects_v2(Bucket=other_bucket, Prefix=other_prefix + "/"))
        assert_denied(lambda: s3.put_object(Bucket=bucket, Key=prefix + "/data/intruder", Body=b"x"))
        print("PASS: vended storage credentials are read-only and confined to the shared table")

        p.update_share(id, ShareUpdate.model_validate({"objects": objects[:1] + [{**objects[0], "name": "internal"}]}))
        assert external.load_table("sales.internal").scan().to_arrow().num_rows == 3
        forbidden(lambda: external.view_exists("sales.report"), "A view removed from the share is still visible")
        p.update_share(id, ShareUpdate.model_validate({"objects": objects[:1]}))
        forbidden(lambda: external.load_table("sales.internal"), "A table removed from the share is still readable")
        writer.rename_table("sales.orders", "sales.orders_v2")
        drifted = p.list_shares(db, drift=True)[0]
        assert [o["granted"] for o in drifted["objects"]] == [False]
        assert [e["name"] for e in drifted["extraGrants"]] == ["orders_v2"]
        writer.rename_table("sales.orders_v2", "sales.orders")
        print("PASS: editing the selection grants and revokes live; a rename shows up as drift")

        try:
            p.move_database(db, p.save_team(TeamInput(name=f"share-next-{suffix}"))["id"])
        except ServiceError as exc:
            assert exc.status == 409
        else:
            raise AssertionError("A shared database changed owner")
        finally:
            teams.extend(t["id"] for t in p.list_teams() if t["name"] == f"share-next-{suffix}")

        rotated = p.rotate_share(id)
        assert rotated["credentials"]["clientId"] == issued["credentials"]["clientId"]
        try:
            token(p, issued["credentials"])
        except httpx.HTTPStatusError as exc:
            assert exc.response.status_code == 401
        else:
            raise AssertionError("The previous secret still works")
        assert connect(rotated["connection"]).load_table("sales.orders").scan().to_arrow().num_rows == 3
        print("PASS: a new secret keeps the client ID and ends the previous secret")

        live = {"Authorization": f"Bearer {token(p, rotated['credentials'])}"}
        p.delete_share(id)
        gone = httpx.get(f"{p.url}/api/catalog/v1/{db}/namespaces/sales/tables/orders", headers=live)
        assert gone.status_code in (401, 403), gone.status_code
        assert p.list_shares(db) == []
        print("PASS: revocation ends an already issued token at once")

        p.create_share(
            ShareInput.model_validate({"database": db, "name": f"leftover-{suffix}", "objects": objects}),
            {"id": admin["user"]["id"], "name": admin["user"]["name"]},
        )
        p.delete_database(db)
        databases.remove(db)
        assert all(s["database"] != db for s in p.list_shares())
        print("PASS: deleting a database revokes its shares and drops the catalog")


if __name__ == "__main__":
    main()
