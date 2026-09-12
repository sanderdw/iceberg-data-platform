"""Exercise real Polaris and RustFS permissions; clean only unique test resources."""

import os
from contextlib import contextmanager
from uuid import uuid4

import boto3
import httpx
from botocore.config import Config
from botocore.exceptions import ClientError
from dotenv import load_dotenv

from server.models import DatabaseInput, ServiceError, TeamInput, UserInput
from server.polaris import PolarisProvider
from server.storage import RustFSStorage


@contextmanager
def stack_resources():
    load_dotenv()
    provider = PolarisProvider(os.environ, RustFSStorage(os.environ))
    teams, databases, users = [], [], []
    suffix = uuid4().hex[:12]
    try:
        yield provider, teams, databases, users, suffix
    finally:
        try:
            for id in reversed(users):
                provider.delete_user(id)
            for id in reversed(databases):
                provider.delete_database(id)
            for id in reversed(teams):
                provider.delete_team(id)
        finally:
            provider.close()


def token(provider, credentials):
    response = httpx.post(
        f"{provider.url}/api/catalog/v1/oauth/tokens",
        data={
            "grant_type": "client_credentials",
            "scope": "PRINCIPAL_ROLE:ALL",
            "client_id": credentials["clientId"],
            "client_secret": credentials["clientSecret"],
        },
        timeout=15,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def catalog_request(provider, credentials, database):
    return httpx.get(
        f"{provider.url}/api/catalog/v1/{database}/namespaces",
        headers={"Authorization": f"Bearer {token(provider, credentials)}"},
        timeout=15,
    )


def s3_client(credentials):
    return boto3.client(
        "s3",
        endpoint_url=os.environ["S3_ENDPOINT"],
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
        aws_access_key_id=credentials["accessKeyId"],
        aws_secret_access_key=credentials["secretAccessKey"],
        config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 0}),
    )


def assert_denied(action):
    try:
        action()
    except ClientError as exc:
        assert exc.response["ResponseMetadata"]["HTTPStatusCode"] in (403, 404)
    else:
        raise AssertionError("Expected access to be denied")


def main():
    with stack_resources() as (p, teams, databases, users, suffix):
        assert p.health()["status"] == "online"
        for name in ("source", "target"):
            teams.append(p.save_team(TeamInput(name=f"{name}-{suffix}"))["id"])
        accounts = []
        for idx in range(2):
            result = p.create_user(
                UserInput(name=f"user-{idx}-{suffix}", teams=[teams[idx]], role="bucket-admin")
            )
            users.append(result["user"]["id"])
            accounts.append(result)
        db = p.create_database(DatabaseInput(name=f"smoke_{suffix}", team=teams[0]))
        databases.append(db["id"])
        other = p.create_database(DatabaseInput(name=f"other_{suffix}", team=teams[0]))
        databases.append(other["id"])
        source, target = (s3_client(a["bucketCredentials"]) for a in accounts)
        source.put_object(Bucket=db["bucket"], Key="evidence.txt", Body=b"team access")
        assert_denied(lambda: target.get_object(Bucket=db["bucket"], Key="evidence.txt"))
        assert catalog_request(p, accounts[0]["credentials"], db["id"]).status_code == 200
        assert catalog_request(p, accounts[1]["credentials"], db["id"]).status_code in (403, 404)
        print("PASS: existing team members receive new database and S3 access")
        moved = p.move_database(db["id"], teams[1])
        assert moved["bucket"] == db["bucket"]
        assert_denied(lambda: source.get_object(Bucket=db["bucket"], Key="evidence.txt"))
        assert target.get_object(Bucket=db["bucket"], Key="evidence.txt")["Body"].read() == b"team access"
        assert catalog_request(p, accounts[0]["credentials"], db["id"]).status_code in (403, 404)
        assert catalog_request(p, accounts[1]["credentials"], db["id"]).status_code == 200
        assert catalog_request(p, accounts[0]["credentials"], other["id"]).status_code == 200
        assert_denied(lambda: target.list_objects_v2(Bucket=other["bucket"]))
        print("PASS: moving a database changes actual catalog and S3 permissions; other database is isolated")
        p.update_memberships(users[0], teams)
        assert source.get_object(Bucket=db["bucket"], Key="evidence.txt")["Body"].read() == b"team access"
        p.update_memberships(users[0], [teams[0]])
        assert_denied(lambda: source.get_object(Bucket=db["bucket"], Key="evidence.txt"))
        print("PASS: adding/removing memberships updates existing credentials")
        p.save_team(TeamInput(name=f"renamed-{suffix}"), teams[1])
        assert p.connection(db["id"])["bucket"] == db["bucket"]
        p.delete_database(db["id"])
        databases.remove(db["id"])
        assert len([u for u in p.list_users() if u["id"] in users]) == 2
        try:
            p.storage.call("head_bucket", Bucket=db["bucket"])
        except ServiceError as exc:
            assert getattr(exc, "status", None) == 404
        else:
            raise AssertionError("Deleted bucket still exists")
        print("PASS: database deletion removes nonempty bucket and preserves team users")
        source.close()
        target.close()
    print("PASS: test resources cleaned up")


if __name__ == "__main__":
    main()
