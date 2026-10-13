"""Live end-to-end check of the Ingestion extension against a running platform, through MCP as an agent would.

Needs three linked Keycloak accounts of one team: an administrator, a writer and a reader
(E2E_{ADMIN,WRITER,READER}_{USER,PASSWORD}; the platform's demo accounts by default), and the
extension started with ING_ALLOW_PRIVATE_SOURCES=true, so runs reach the sources this script
serves on the host as host.docker.internal:

- a paginated JSON API with a bearer token (started here, E2E_API_PORT, 8765 by default);
- a PostgreSQL database, if E2E_POSTGRES_PORT is set (a test-only container published on the host,
  user and database `e2e`, password `e2e-password`).

It loads both twice, to prove that incremental state carries over and merge does not duplicate
rows, reads the tables back as the team's automation principal (a Bridge read token), checks the
Produced-by properties, and checks that a run cannot resolve the platform's internal services.
The pipelines are removed at the end; their tables stay.
"""

import asyncio
import base64
import hashlib
import html
import json
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import httpx
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

API_TOKEN = "e2e-" + secrets.token_hex(12)
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


# The source API: customers with an updated_at cursor, two per page, behind a bearer token.

CUSTOMERS = {i: {"id": i, "name": f"customer {i}", "updated_at": f"2026-10-0{i}T00:00:00Z",
                 "tags": [{"tag": "new"}]} for i in range(1, 6)}
REQUESTS = []


class SourceApi(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        REQUESTS.append(self.path)
        if self.headers.get("Authorization") != f"Bearer {API_TOKEN}":
            return self.reply(401, {"error": "unauthorized"})
        if url.path != "/v1/customers":
            return self.reply(404, {"error": "not found"})
        since = query.get("since", [""])[0]
        rows = sorted((c for c in CUSTOMERS.values() if c["updated_at"] > since), key=lambda c: c["id"])
        page = int(query.get("page", ["1"])[0])
        chunk = rows[(page - 1) * 2:page * 2]
        next_url = None
        if page * 2 < len(rows):
            next_url = f"http://{self.headers['Host']}/v1/customers?page={page + 1}" + (f"&since={since}" if since
                                                                                        else "")
        self.reply(200, {"items": chunk, "next": next_url})

    def reply(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def serve(port):
    server = ThreadingHTTPServer(("0.0.0.0", port), SourceApi)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def catalog(env, platform, team, warehouse):
    """The team's database as the team's automation principal, with a read token from the Bridge."""
    from pyiceberg.catalog import load_catalog

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
                       json={"access": "read", "purpose": "ingestion e2e check"}).json()["accessToken"]
    return load_catalog("e2e", type="rest", uri=platform.get("POLARIS_URL", "http://localhost:8181") + "/api/catalog",
                        warehouse=warehouse, token=token,
                        **{"header.X-Iceberg-Access-Delegation": "vended-credentials"})


async def run(agent, pipeline, **arguments):
    result = await agent.call("run_pipeline", pipeline=pipeline, wait=600, **arguments)
    if result["status"] != "success":
        logs = await agent.call("get_run_logs", run=result["id"], tail=80)
        raise AssertionError(f"Run failed: {result['error']}\n" + "\n".join(logs["lines"]))
    return result


async def main():
    env = {**read_env(".env.bridge"), **os.environ}
    origin = env.get("EXTENSION_ORIGIN", "http://localhost:3008")
    issuer = env["OIDC_ISSUER"]
    redirect = f"http://localhost:{env.get('MCP_CALLBACK_PORT', '3010')}/callback"
    platform = read_env(env.get("PLATFORM_ENV", "../../.env"))
    accounts = {}
    for role, default in (("admin", "demo-admin"), ("writer", "demo-writer"), ("reader", "demo-reader")):
        user = env.get(f"E2E_{role.upper()}_USER", default)
        password = env.get(f"E2E_{role.upper()}_PASSWORD") or platform[f"DEMO_{role.upper()}_PASSWORD"]
        accounts[role] = sign_in(issuer, "ext-ingestion-mcp", redirect, user, password)
    port = int(env.get("E2E_API_PORT", "8765"))
    server = serve(port)
    namespace = "e2e_" + str(int(time.time()))
    created = []
    async with Agent(origin + "/mcp", accounts["admin"]) as admin, \
            Agent(origin + "/mcp", accounts["writer"]) as writer, \
            Agent(origin + "/mcp", accounts["reader"]) as reader:
        me = await writer.call("whoami")
        assert me["allowPrivateSources"], "Start the extension with ING_ALLOW_PRIVATE_SOURCES=true for this check."
        team = next(m for m in me["memberships"] if m["role"] == "writer")["team"]
        await writer.refused("enable_environment", "forbidden_role", team=team, environment="development")
        await admin.call("enable_environment", team=team, environment="development")
        database = next(d for d in me["databases"] if d["team"] == team and d["environment"] == "development")
        templates = await reader.call("list_templates")
        assert {t["id"] for t in templates["templates"]} >= {"cbs-statline", "postgres-incremental", "airports-csv"}

        source = {
            "client": {"base_url": f"http://host.docker.internal:{port}/v1/",
                       "auth": {"type": "bearer", "token": "$secret:token"},
                       "paginator": {"type": "json_link", "next_url_path": "next"}},
            "resources": [{"name": "customers", "endpoint": {
                "path": "customers", "data_selector": "items",
                "incremental": {"cursor_path": "updated_at", "start_param": "since"}}}],
        }
        resources = {"customers": {"writeDisposition": "merge", "primaryKey": ["id"]}}
        checked = await reader.call("validate_pipeline", source_type="rest_api", source=source, resources=resources)
        assert checked["valid"] and checked["secretsNeeded"] == ["token"], checked
        await writer.refused("create_pipeline", "forbidden_role", team=team, name="api", database=database["id"],
                             namespace=namespace, source_type="rest_api", source=source, resources=resources)
        api = await admin.call("create_pipeline", team=team, name=f"{namespace} api", database=database["id"],
                               namespace=namespace, source_type="rest_api", source=source, resources=resources)
        created.append(api)
        await writer.refused("run_pipeline", "secrets_missing", pipeline=api["id"])
        await writer.refused("set_pipeline_secrets", "forbidden_role", pipeline=api["id"],
                             secrets={"token": API_TOKEN})
        await admin.call("set_pipeline_secrets", pipeline=api["id"], secrets={"token": API_TOKEN})
        await reader.refused("run_pipeline", "forbidden_role", pipeline=api["id"])

        first = await run(writer, api["id"])
        rows = {t["name"]: t["rows"] for t in first["summary"]["tables"]}
        assert rows["customers"] == 5 and rows.get("customers__tags") == 5, rows
        assert first["summary"]["published"]["errors"] == [], first["summary"]["published"]
        logs = await reader.call("get_run_logs", run=first["id"], tail=2000)
        assert API_TOKEN not in "\n".join(logs["lines"]), "The run log shows the API token"
        print("rest api run 1", first["id"], rows)

        # The source changes one customer and adds one: only those come, and merge keeps one row per id.
        CUSTOMERS[2] = {**CUSTOMERS[2], "name": "customer 2, renamed", "updated_at": "2026-10-08T00:00:00Z"}
        CUSTOMERS[6] = {"id": 6, "name": "customer 6", "updated_at": "2026-10-09T00:00:00Z", "tags": []}
        REQUESTS.clear()
        second = await run(writer, api["id"])
        assert any("since=2026-10-05" in r for r in REQUESTS), REQUESTS
        assert {t["name"]: t["rows"] for t in second["summary"]["tables"]}["customers"] == 2, second["summary"]

        tables = catalog(env, platform, team, database["id"])
        listed = {name for _, name in tables.list_tables(namespace)}
        assert "customers" in listed and not any(n.startswith("_dlt") for n in listed), listed
        customers = tables.load_table((namespace, "customers"))
        data = customers.scan().to_arrow()
        assert data.num_rows == 6 and len(set(data["id"].to_pylist())) == 6, data.num_rows
        assert "customer 2, renamed" in data["name"].to_pylist()
        properties = customers.properties
        assert properties[PRODUCER] == "dlt" and properties[f"{PRODUCER}.url"] == api["url"], properties
        assert properties[f"{PRODUCER}.run-id"] == second["summary"]["loadId"], properties
        uuid = customers.metadata.table_uuid
        third = await run(writer, api["id"], full_refresh=True)
        assert {t["name"]: t["rows"] for t in third["summary"]["tables"]}["customers"] == 6
        reloaded = tables.load_table((namespace, "customers"))
        assert reloaded.metadata.table_uuid == uuid and reloaded.scan().to_arrow().num_rows == 6
        print("rest api: incremental, merge without duplicates, full refresh with the same table")

        if env.get("E2E_POSTGRES_PORT"):
            await postgres(env, admin, writer, team, database, namespace, tables, created)

        # A run reaches only the catalog, storage and the sources: never the platform's own services.
        for host in ("bridge:3005", "keycloak:8080", "postgres:5432"):
            probe = await admin.call("create_pipeline", team=team, name=f"{namespace} probe {host.split(':')[0]}",
                                     database=database["id"], namespace=namespace, source_type="rest_api",
                                     source={"client": {"base_url": f"http://{host}/"},
                                             "resources": [{"name": "probe", "endpoint": {"path": "x"}}]})
            created.append(probe)
            result = await writer.call("run_pipeline", pipeline=probe["id"], wait=600)
            assert result["status"] == "failed", result
            lines = "\n".join((await reader.call("get_run_logs", run=result["id"], tail=200))["lines"])
            assert re.search(r"(?i)resolve|name or service|getaddrinfo|refused", lines), lines[-2000:]
        print("runs cannot resolve bridge, keycloak or postgres")

        schedule = await admin.call("set_schedule", pipeline=api["id"], cron="0 5 * * *")
        assert schedule["enabled"]
        await writer.refused("delete_schedule", "forbidden_role", pipeline=api["id"])
        await admin.call("delete_schedule", pipeline=api["id"])

        # The REST API gives the same answers.
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {accounts['reader']}"}) as rest:
            detail = (await rest.get(f"{origin}/api/v1/pipelines/{api['id']}")).json()
            assert detail["secretsSet"] == ["token"] and API_TOKEN not in json.dumps(detail), detail

        for pipeline in created:
            await admin.call("delete_pipeline", pipeline=pipeline["id"], confirm_name=pipeline["name"])
    server.shutdown()
    print("Ingestion e2e passed: roles, secrets, REST API and PostgreSQL loads, incremental state, merge, full "
          "refresh, table properties and network isolation.")


async def postgres(env, admin, writer, team, database, namespace, tables, created):
    import pg8000.native

    port = int(env["E2E_POSTGRES_PORT"])
    db = pg8000.native.Connection("e2e", password="e2e-password", database="e2e", host="localhost", port=port)
    db.run("DROP TABLE IF EXISTS orders")
    db.run("CREATE TABLE orders (id integer primary key, amount numeric(10,2), updated_at timestamptz not null)")
    db.run("INSERT INTO orders VALUES (1, 10, '2026-10-01'), (2, 20, '2026-10-02'), (3, 30, '2026-10-03')")
    pipeline = await admin.call(
        "create_pipeline", team=team, name=f"{namespace} postgres", database=database["id"], namespace=namespace,
        source_type="sql_database",
        source={"dialect": "postgresql", "host": "host.docker.internal", "port": port, "database": "e2e",
                "username": "e2e", "password": "$secret:password", "tls": "disable",
                "tables": [{"name": "orders", "incremental": {"cursor": "updated_at"}}]},
        resources={"orders": {"writeDisposition": "merge", "primaryKey": ["id"]}})
    created.append(pipeline)
    await admin.call("set_pipeline_secrets", pipeline=pipeline["id"], secrets={"password": "e2e-password"})
    first = await run(writer, pipeline["id"])
    assert {t["name"]: t["rows"] for t in first["summary"]["tables"]}["orders"] == 3, first["summary"]
    db.run("UPDATE orders SET amount = 25, updated_at = '2026-10-05' WHERE id = 2")
    db.run("INSERT INTO orders VALUES (4, 40, '2026-10-06')")
    second = await run(writer, pipeline["id"])
    # The cursor's last row comes again (dlt deduplicates it), plus the changed and the new row.
    assert {t["name"]: t["rows"] for t in second["summary"]["tables"]}["orders"] <= 3, second["summary"]
    orders = tables.load_table((namespace, "orders")).scan().to_arrow()
    assert orders.num_rows == 4 and sorted(orders["id"].to_pylist()) == [1, 2, 3, 4], orders.num_rows
    db.close()
    print("postgres: incremental on updated_at, merged on id")


if __name__ == "__main__":
    asyncio.run(main())
