"""Live Bridge check against the demo stack with a registered `dbt` extension.

Requires `scripts.setup --demo --extension dbt --origin http://localhost:3004 --handshake <file>`
(or another registered extension through BRIDGE_SMOKE_EXTENSION and BRIDGE_SMOKE_ORIGIN)
and `keycloak-bootstrap --demo`. It creates an automation principal for demo-team in development,
writes and reads one table through it with DuckDB, reads a table and semantic model that a partner
team shared with demo-team (capability `shared-data`), and revokes everything again. No token or
secret is printed.
"""

import json
import os
import time
from uuid import uuid4

import duckdb
import httpx
from dotenv import load_dotenv

from scripts.mcp_smoke import sign_in
from scripts.setup import parse_pairs
from scripts.smoke import token as principal_token
from server.models import DatabaseInput, ShareInput, TeamInput, UserInput
from server.polaris import PolarisProvider
from server.storage import RustFSStorage


def sql(value):
    return "'" + str(value).replace("'", "''") + "'"


def shared_data(provider, recipient, principal, bridge, as_service, token):
    """A team share reaches the read role of the recipient team's automation principal, and nothing else."""
    import pyarrow as pa
    from pyiceberg.catalog import load_catalog

    suffix = uuid4().hex[:8]
    team = provider.save_team(TeamInput(name=f"bridge-partner-{suffix}"))["id"]
    database = None
    admin = None
    try:
        database = provider.create_database(DatabaseInput(name=f"partner_{suffix}", team=team))["id"]
        admin = provider.create_user(UserInput(name=f"partner-admin-{suffix}",
                                               memberships=[{"team": team, "role": "admin"}]))
        credentials = admin["credentials"]
        owner = load_catalog(database, type="rest", uri=provider.url + "/api/catalog", warehouse=database,
                             credential=f"{credentials['clientId']}:{credentials['clientSecret']}",
                             scope="PRINCIPAL_ROLE:ALL",
                             **{"header.X-Iceberg-Access-Delegation": "vended-credentials"})
        owner.create_namespace("ops")
        rows = pa.table({"id": [1, 2]})
        for name in ("shared", "private"):
            owner.create_table(("ops", name), schema=rows.schema).append(rows)
        models = f"{provider.url}/api/catalog/polaris/v1/{database}/namespaces/ops/semantic-models"
        document = {"version": "0.2.0", "semantic_model": json.dumps({"version": "0.2.0", "semantic_model": [
            {"name": "Ops", "datasets": [{"name": "SHARED", "source": "lakehouse.ops.shared", "primary_key": ["id"],
                                          "fields": []}]}]})}
        as_owner = {"Authorization": f"Bearer {principal_token(provider, credentials)}"}
        httpx.post(models, json={"name": "ops", "document": document}, headers=as_owner,
                   timeout=15).raise_for_status()
        share = provider.create_share(ShareInput.model_validate({
            "database": database, "name": "bridge-smoke", "external": False, "recipientTeam": recipient,
            "objects": [{"kind": "table", "namespace": ["ops"], "name": "shared"},
                        {"kind": "semantic-model", "namespace": ["ops"], "name": "ops"}],
        }), {"id": admin["user"]["id"], "name": "bridge-smoke"})["share"]

        scope = httpx.get(f"{bridge}/automation-principals/{principal}", headers=as_service,
                          timeout=15).raise_for_status().json()
        [received] = [d for d in scope["sharedDatabases"] if d["id"] == database]
        assert {(o["kind"], o["name"]) for o in received["sharedObjects"]} == {
            ("table", "shared"), ("semantic-model", "ops")}
        read = {"Authorization": f"Bearer {token('read')}"}
        tables = f"{provider.url}/api/catalog/v1/{database}/namespaces/ops/tables"
        vended = {**read, "X-Iceberg-Access-Delegation": "vended-credentials"}
        statuses = {
            "shared table": httpx.get(f"{tables}/shared", headers=vended, timeout=15).status_code,
            "shared model": httpx.get(f"{models}/ops", headers=read, timeout=15).status_code,
            "private table": httpx.get(f"{tables}/private", headers=vended, timeout=15).status_code,
            "list tables": httpx.get(tables, headers=read, timeout=15).status_code,
        }
        assert statuses == {"shared table": 200, "shared model": 200, "private table": 403, "list tables": 403}, (
            statuses)
        write = {"Authorization": f"Bearer {token('write')}"}
        assert httpx.get(f"{tables}/shared", headers=write, timeout=15).status_code == 403, (
            "A share must reach the read role only")
        provider.delete_share(share["id"])
        assert httpx.get(f"{tables}/shared", headers=read, timeout=15).status_code == 403
        assert all(d["id"] != database for d in httpx.get(
            f"{bridge}/automation-principals/{principal}", headers=as_service, timeout=15).json()["sharedDatabases"])
    finally:
        if admin:
            provider.delete_user(admin["user"]["id"])
        if database:
            provider.delete_database(database)
        provider.delete_team(team)


def main():
    load_dotenv()
    issuer = os.environ["OIDC_ISSUER"].rstrip("/")
    bridge = os.environ.get("BRIDGE_URL", f"http://localhost:{os.environ.get('BRIDGE_PORT', '3005')}") + "/bridge/v1"
    extension = os.environ.get("BRIDGE_SMOKE_EXTENSION", "dbt")
    origin = os.environ.get("BRIDGE_SMOKE_ORIGIN", "http://localhost:3004")
    secret = parse_pairs(os.environ["PLATFORM_EXTENSION_SECRETS"])[extension]
    port = os.environ.get("MCP_CALLBACK_PORT", "3010")
    namespace = "bridge_smoke_" + str(int(time.time()))

    discovery = httpx.get(bridge, timeout=15).raise_for_status().json()
    assert discovery["contractVersion"].startswith("0.1.") and "automation-tokens" in discovery["capabilities"]
    assert {"id": extension, "origin": origin} in discovery["extensions"]

    service = httpx.post(f"{issuer}/protocol/openid-connect/token", timeout=15, data={
        "grant_type": "client_credentials", "client_id": f"ext-{extension}", "client_secret": secret,
    }).raise_for_status().json()["access_token"]
    as_service = {"Authorization": f"Bearer {service}"}
    writer = sign_in(issuer, f"ext-{extension}-mcp", f"http://localhost:{port}/callback", "demo-writer",
                     os.environ["DEMO_WRITER_PASSWORD"])
    as_writer = {"Authorization": f"Bearer {writer}"}

    # Extension tokens are for the bridge only: Polaris refuses them.
    catalog = httpx.get(os.environ.get("POLARIS_URL", "http://localhost:8181") + "/api/catalog/v1/config",
                        headers=as_writer, timeout=15)
    assert catalog.status_code == 401, "Polaris must refuse extension tokens"

    me = httpx.get(f"{bridge}/me", headers=as_writer, timeout=15).raise_for_status().json()
    team = next(m for m in me["memberships"] if m["teamName"] == "demo-team")
    assert team["role"] == "writer"
    denied = httpx.post(f"{bridge}/teams/{team['team']}/automation-principals", headers=as_writer,
                        json={"environment": "development"}, timeout=15)
    assert denied.json()["code"] == "forbidden_role", "Only team administrators enable an extension"
    assert httpx.get(f"{bridge}/me", headers=as_service, timeout=15).json()["code"] == "user_required"

    # The demo has no team administrator; the platform creates the principal as a team admin would.
    provider = PolarisProvider({**os.environ, "POLARIS_URL": os.environ.get("POLARIS_URL", "http://localhost:8181")},
                               RustFSStorage(os.environ))
    try:
        item, _ = provider.create_automation(team["team"], "development", extension,
                                             {"id": me["user"]["id"], "name": "bridge-smoke"})
    finally:
        provider.close()
    try:
        scope = httpx.get(f"{bridge}/automation-principals/{item['id']}", headers=as_service,
                          timeout=15).raise_for_status().json()
        database = next(d for d in scope["databases"] if d["name"] == "demo-demo")
        assert all(d["environment"] == "development" for d in scope["databases"])

        def token(access):
            return httpx.post(f"{bridge}/automation-principals/{item['id']}/tokens", headers=as_service,
                              json={"access": access, "purpose": "bridge smoke"}, timeout=30,
                              ).raise_for_status().json()["accessToken"]

        def attach(access_token):
            connection = duckdb.connect()
            connection.execute("INSTALL httpfs; INSTALL iceberg; LOAD httpfs; LOAD iceberg")
            connection.execute(f"CREATE SECRET polaris (TYPE iceberg, TOKEN {sql(access_token)})")
            connection.execute(
                f"ATTACH {sql(database['id'])} AS lake (TYPE iceberg, ENDPOINT {sql(discovery['catalog']['uri'])}, "
                "SECRET polaris, ACCESS_DELEGATION_MODE 'vended_credentials', SUPPORT_NESTED_NAMESPACES true)"
            )
            return connection

        writable = attach(token("write"))
        writable.execute(f'CREATE SCHEMA lake."{namespace}"')
        writable.execute(f'CREATE TABLE lake."{namespace}".checks AS SELECT 1 AS id')
        readable = attach(token("read"))
        assert readable.execute(f'SELECT count(*) FROM lake."{namespace}".checks').fetchone() == (1,)
        try:
            readable.execute(f'INSERT INTO lake."{namespace}".checks VALUES (2)')
        except duckdb.Error:
            pass
        else:
            raise AssertionError("A read token must not write")
        writable.execute(f'DROP TABLE lake."{namespace}".checks')
        writable.execute(f'DROP SCHEMA lake."{namespace}"')
        if "shared-data" in discovery["capabilities"]:
            provider = PolarisProvider(
                {**os.environ, "POLARIS_URL": os.environ.get("POLARIS_URL", "http://localhost:8181")},
                RustFSStorage(os.environ))
            try:
                shared_data(provider, team["team"], item["id"], bridge, as_service, token)
            finally:
                provider.close()
    finally:
        httpx.delete(f"{bridge}/automation-principals/{item['id']}", headers=as_service, timeout=30)
    revoked = httpx.post(f"{bridge}/automation-principals/{item['id']}/tokens", headers=as_service,
                         json={"access": "read"}, timeout=15)
    assert revoked.status_code == 404
    print("Bridge smoke passed: discovery, user context, roles, scoped tokens, vended storage, shared data "
          "and revocation.")


if __name__ == "__main__":
    main()
