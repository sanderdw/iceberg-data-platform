"""Live Bridge check against the demo stack with a registered `dbt` extension.

Requires `scripts.setup --demo --extension dbt --origin http://localhost:3004 --handshake <file>`
and `keycloak-bootstrap --demo`. It creates an automation principal for demo-team in development,
writes and reads one table through it with DuckDB, and revokes it again. No token or secret is printed.
"""

import os
import time

import duckdb
import httpx
from dotenv import load_dotenv

from scripts.mcp_smoke import sign_in
from scripts.setup import parse_pairs
from server.polaris import PolarisProvider
from server.storage import RustFSStorage


def sql(value):
    return "'" + str(value).replace("'", "''") + "'"


def main():
    load_dotenv()
    issuer = os.environ["OIDC_ISSUER"].rstrip("/")
    bridge = os.environ.get("BRIDGE_URL", f"http://localhost:{os.environ.get('BRIDGE_PORT', '3005')}") + "/bridge/v1"
    secret = parse_pairs(os.environ["PLATFORM_EXTENSION_SECRETS"])["dbt"]
    port = os.environ.get("MCP_CALLBACK_PORT", "3010")
    namespace = "bridge_smoke_" + str(int(time.time()))

    discovery = httpx.get(bridge, timeout=15).raise_for_status().json()
    assert discovery["contractVersion"].startswith("1.") and "automation-tokens" in discovery["capabilities"]
    assert {"id": "dbt", "origin": "http://localhost:3004"} in discovery["extensions"]

    service = httpx.post(f"{issuer}/protocol/openid-connect/token", timeout=15, data={
        "grant_type": "client_credentials", "client_id": "ext-dbt", "client_secret": secret,
    }).raise_for_status().json()["access_token"]
    as_service = {"Authorization": f"Bearer {service}"}
    writer = sign_in(issuer, "ext-dbt-mcp", f"http://localhost:{port}/callback", "demo-writer",
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
        item, _ = provider.create_automation(team["team"], "development", "dbt",
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
    finally:
        httpx.delete(f"{bridge}/automation-principals/{item['id']}", headers=as_service, timeout=30)
    revoked = httpx.post(f"{bridge}/automation-principals/{item['id']}/tokens", headers=as_service,
                         json={"access": "read"}, timeout=15)
    assert revoked.status_code == 404
    print("Bridge smoke passed: discovery, user context, roles, scoped tokens, vended storage and revocation.")


if __name__ == "__main__":
    main()
