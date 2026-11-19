"""Live end-to-end check of the Replication extension against a running platform, through MCP as an agent would.

Needs three linked Keycloak accounts of one team: an administrator, a writer and a reader
(E2E_{ADMIN,WRITER,READER}_{USER,PASSWORD}; the platform's demo accounts by default), the extension
started with REP_ALLOW_PRIVATE_SOURCES=true, and a test-only PostgreSQL with logical replication on
the host (E2E_POSTGRES_PORT, user and database `e2e`, password `e2e-password`, started with
`-c wal_level=logical`). Runs reach it as host.docker.internal.

It checks the source, chooses tables, copies them, changes rows (update, delete, insert) and runs
again; it then reads the tables' snapshots and properties from the catalog as the team's automation
principal (a Bridge read token), and checks that the password never reaches a log. The replication
is removed at the end; its tables stay.
"""

import asyncio
import base64
import hashlib
import html
import json
import os
import re
import secrets
import time
from urllib.parse import parse_qs, urlsplit

import httpx
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

PRODUCER = "iceberg-data-platform.producer"


def read_env(path):
    values = {}
    if os.path.exists(path):
        for line in open(path).read().splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.startswith("#"):
                values[key.strip()] = value.strip()
    return values


def sign_in(issuer, client_id, redirect_uri, username, password):
    """Authorization code with PKCE by scripting Keycloak's login form; returns the access token."""
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    with httpx.Client(timeout=30, follow_redirects=False) as browser:
        page = browser.get(f"{issuer}/protocol/openid-connect/auth", params={
            "client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri, "scope": "openid profile",
            "state": secrets.token_urlsafe(16), "code_challenge": challenge, "code_challenge_method": "S256"})
        page.raise_for_status()
        form = re.search(r'<form[^>]*id="kc-form-login"[^>]*action="([^"]+)"', page.text)
        assert form, "Keycloak login form not found"
        cookies = "; ".join(f"{c.name}={c.value}" for c in browser.cookies.jar)
        response = browser.post(html.unescape(form.group(1)), headers={"Cookie": cookies},
                                data={"username": username, "password": password})
        assert response.status_code == 302, f"Sign-in failed for {username}"
        code = parse_qs(urlsplit(response.headers["location"]).query)["code"][0]
        issued = browser.post(f"{issuer}/protocol/openid-connect/token", data={
            "grant_type": "authorization_code", "client_id": client_id, "code": code,
            "redirect_uri": redirect_uri, "code_verifier": verifier})
        issued.raise_for_status()
        return issued.json()["access_token"]


class Agent:
    def __init__(self, url, token):
        self.url, self.token = url, token

    async def __aenter__(self):
        self.http = httpx.AsyncClient(headers={"Authorization": f"Bearer {self.token}"},
                                      timeout=httpx.Timeout(30, read=700))
        self.session = Client(streamable_http_client(self.url, http_client=self.http))
        await self.session.__aenter__()
        return self

    async def __aexit__(self, *exc):
        await self.session.__aexit__(*exc)
        await self.http.aclose()

    async def call(self, tool, **arguments):
        result = await self.session.call_tool(tool, arguments)
        text = "".join(getattr(block, "text", "") for block in result.content)
        if result.is_error:
            raise RuntimeError(f"{tool}: {text}")
        return json.loads(text)

    async def refused(self, tool, code, **arguments):
        try:
            await self.call(tool, **arguments)
        except RuntimeError as exc:
            assert f"[{code}]" in str(exc), exc
            return
        raise AssertionError(f"{tool} should be refused with {code}")



def catalog(env, platform, team, warehouse):
    """The team's catalog as the team's automation principal, with a read token from the Bridge."""
    bridge = env.get("E2E_BRIDGE_URL", "http://localhost:3005") + "/bridge/v1"
    issued = httpx.post(f"{env['OIDC_ISSUER']}/protocol/openid-connect/token", data={
        "grant_type": "client_credentials", "client_id": env["BRIDGE_CLIENT_ID"],
        "client_secret": env["BRIDGE_CLIENT_SECRET"]}, timeout=30)
    issued.raise_for_status()
    service = {"Authorization": f"Bearer {issued.json()['access_token']}"}
    principals = httpx.get(f"{bridge}/automation-principals", headers=service, timeout=30).json()
    principal = next(p for p in principals["automationPrincipals"]
                     if p["team"] == team and p["environment"] == "development")
    token = httpx.post(f"{bridge}/automation-principals/{principal['id']}/tokens", headers=service, timeout=30,
                       json={"access": "read", "purpose": "replication e2e check"}).json()["accessToken"]
    uri = platform.get("POLARIS_URL", "http://localhost:8181") + "/api/catalog"
    http = httpx.Client(base_url=uri, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    prefix = http.get("/v1/config", params={"warehouse": warehouse}).json().get("overrides", {}).get("prefix", "")

    def table(namespace, name):
        response = http.get(f"/v1/{prefix}/namespaces/{namespace}/tables/{name}")
        response.raise_for_status()
        return response.json()["metadata"]

    return table


def live_rows(metadata):
    """Rows not replaced by a positional delete, from the current snapshot's summary."""
    current = next(s for s in metadata["snapshots"] if s["snapshot-id"] == metadata["current-snapshot-id"])
    summary = current["summary"]
    return int(summary.get("total-records", 0)) - int(summary.get("total-position-deletes", 0))


async def run(agent, replication, **arguments):
    result = await agent.call("run_replication", replication=replication, wait=600, **arguments)
    if result["status"] != "success":
        logs = await agent.call("get_run_logs", run=result["id"], tail=120)
        raise AssertionError(f"Run {result['status']}: {result['error']}\n" + "\n".join(logs["lines"]))
    return result


async def main():
    import pg8000.native

    env = {**read_env(".env.bridge"), **os.environ}
    origin = env.get("EXTENSION_ORIGIN", "http://localhost:3014")
    issuer = env["OIDC_ISSUER"]
    redirect = f"http://localhost:{env.get('MCP_CALLBACK_PORT', '3010')}/callback"
    platform = read_env(env.get("PLATFORM_ENV", "../../.env"))
    accounts = {}
    for role, default in (("admin", "demo-admin"), ("writer", "demo-writer"), ("reader", "demo-reader")):
        user = env.get(f"E2E_{role.upper()}_USER", default)
        password = env.get(f"E2E_{role.upper()}_PASSWORD") or platform[f"DEMO_{role.upper()}_PASSWORD"]
        accounts[role] = sign_in(issuer, "ext-replication-mcp", redirect, user, password)
    port = int(env["E2E_POSTGRES_PORT"])
    suffix = str(int(time.time()))
    namespace, slot = f"e2e_{suffix}", f"e2e_{suffix}"
    db = pg8000.native.Connection("e2e", password="e2e-password", database="e2e", host="localhost", port=port)
    db.run("DROP TABLE IF EXISTS orders, audit_log")
    db.run("CREATE TABLE orders (id integer primary key, status text, amount numeric(10,2))")
    db.run("INSERT INTO orders SELECT g, 'new', g * 1.5 FROM generate_series(1, 200) g")
    db.run("CREATE TABLE audit_log (at timestamptz default now(), message text)")
    db.run("INSERT INTO audit_log (message) VALUES ('one'), ('two')")
    db.run("ALTER TABLE audit_log REPLICA IDENTITY FULL")
    created = None
    async with Agent(origin + "/mcp", accounts["admin"]) as admin, \
            Agent(origin + "/mcp", accounts["writer"]) as writer, \
            Agent(origin + "/mcp", accounts["reader"]) as reader:
        me = await writer.call("whoami")
        assert me["allowPrivateSources"], "Start the extension with REP_ALLOW_PRIVATE_SOURCES=true for this check."
        team = next(m for m in me["memberships"] if m["role"] == "writer")["team"]
        await writer.refused("enable_environment", "forbidden_role", team=team, environment="development")
        await admin.call("enable_environment", team=team, environment="development")
        database = next(d for d in me["databases"] if d["team"] == team and d["environment"] == "development")
        templates = await reader.call("list_templates")
        assert {t["id"] for t in templates["templates"]} >= {"postgres-cdc", "postgres-incremental"}

        source = {"host": "host.docker.internal", "port": port, "database": "e2e", "username": "e2e",
                  "password": "$secret:password", "tls": "disable", "slot": slot, "publication": slot}
        await writer.refused("create_replication", "forbidden_role", team=team, name="crm",
                             database=database["id"], namespace=namespace, source_type="postgres", source=source)
        created = await admin.call("create_replication", team=team, name=f"{namespace} crm", database=database["id"],
                                   namespace=namespace, source_type="postgres", source=source)
        await writer.refused("check_source", "secrets_missing", replication=created["id"])
        await writer.refused("set_replication_secrets", "forbidden_role", replication=created["id"],
                             secrets={"password": "e2e-password"})
        await admin.call("set_replication_secrets", replication=created["id"], secrets={"password": "e2e-password"})

        # Without slot and publication the check fails on them; the setup SQL creates them.
        check = await writer.call("check_source", replication=created["id"])
        failed = {c["name"] for c in check["checks"] if c["required"] and not c["passed"]}
        assert check["connected"] and {"replication_slot", "publication"} <= failed, check
        db.run(f"CREATE PUBLICATION {slot} FOR TABLE orders, audit_log")
        db.run(f"SELECT pg_create_logical_replication_slot('{slot}', 'pgoutput')")
        check = await writer.call("check_source", replication=created["id"])
        assert check["ready"], check
        tables = {t["name"]: t for t in check["tables"]}
        assert tables["public.orders"]["primaryKey"] == ["id"] and tables["public.audit_log"]["primaryKey"] == []
        await reader.refused("check_source", "forbidden_role", replication=created["id"])

        chosen = {"public.orders": {"mode": "cdc"}, "public.audit_log": {"mode": "cdc"}}
        await admin.call("update_replication", replication=created["id"], tables=chosen)
        await reader.refused("run_replication", "forbidden_role", replication=created["id"])
        first = await run(writer, created["id"])
        written = {t["name"]: t["records"] for t in first["summary"]["tables"]}
        assert written == {"public.orders": 200, "public.audit_log": 2}, first["summary"]
        assert first["summary"]["published"]["errors"] == [] and first["summary"]["readUpTo"], first["summary"]
        print("first copy", first["id"], written)

        db.run("UPDATE orders SET status = 'paid' WHERE id <= 10")
        db.run("DELETE FROM orders WHERE id > 195")
        db.run("INSERT INTO orders VALUES (201, 'new', 1), (202, 'new', 2)")
        second = await run(writer, created["id"])
        counts = {t["name"]: t for t in second["summary"]["tables"]}
        # 10 updates, 5 deletes and 2 inserts are written; the 15 rows they replace get positional deletes.
        assert counts["public.orders"]["records"] == 17 and counts["public.orders"]["replaced"] == 15, counts
        assert second["summary"]["readUpTo"] != first["summary"]["readUpTo"]
        print("changes", second["id"], counts["public.orders"])

        table = catalog(env, platform, team, database["id"])
        orders = table(namespace, "orders")
        # 197 rows in the source, plus the 5 kept with _op_type = 'd'.
        assert live_rows(orders) == 197 + 5, orders["snapshots"][-1]["summary"]
        properties = orders["properties"]
        assert properties[PRODUCER] == "olake" and properties[f"{PRODUCER}.url"] == created["url"], properties
        assert properties[f"{PRODUCER}.source-revision"] == f"LSN {second['summary']['readUpTo']}", properties
        assert "write.delete.mode" not in properties or properties["write.delete.mode"] != "equality"
        for run_id in (first["id"], second["id"]):
            lines = "\n".join((await reader.call("get_run_logs", run=run_id, tail=5000))["lines"])
            assert "e2e-password" not in lines, "A run log shows the source password"

        schedule = await admin.call("set_schedule", replication=created["id"], cron="*/15 * * * *")
        assert schedule["enabled"]
        await writer.refused("delete_schedule", "forbidden_role", replication=created["id"])
        await admin.call("delete_schedule", replication=created["id"])

        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {accounts['reader']}"}) as rest:
            detail = (await rest.get(f"{origin}/api/v1/replications/{created['id']}")).json()
            assert detail["secretsSet"] == ["password"] and "e2e-password" not in json.dumps(detail), detail

        deleted = await admin.call("delete_replication", replication=created["id"], confirm_name=created["name"])
        assert deleted["dropSlotSql"] == f"SELECT pg_drop_replication_slot('{slot}');", deleted
    db.run(f"SELECT pg_drop_replication_slot('{slot}')")
    db.run(f"DROP PUBLICATION {slot}")
    db.close()
    print("Replication e2e passed: roles, write-only password, source checks, first copy, updates, deletes and "
          "inserts as positional deletes, table properties and the change log position.")


if __name__ == "__main__":
    asyncio.run(main())
