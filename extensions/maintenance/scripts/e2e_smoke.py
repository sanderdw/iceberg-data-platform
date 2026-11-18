"""Live end-to-end check of the Maintenance extension against a running platform, through MCP as an agent would.

Needs three linked Keycloak accounts of one team: an administrator, a writer and a reader
(E2E_{ADMIN,WRITER,READER}_{USER,PASSWORD}; the platform's demo accounts by default).

It writes a table with small files into the team's development database as the team's automation
principal (a Bridge write token), checks its health, sets a policy, dry-runs it, maintains the
database, and reads the table back: every row is still there, in fewer files, behind a `replace`
snapshot of the run. The table is dropped at the end.
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
    """The team's database as the team's automation principal, with a write token from the Bridge."""
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
                       json={"access": "write", "purpose": "maintenance e2e check"}).json()["accessToken"]
    return load_catalog("e2e", type="rest", uri=platform.get("POLARIS_URL", "http://localhost:8181") + "/api/catalog",
                        warehouse=warehouse, token=token,
                        **{"header.X-Iceberg-Access-Delegation": "vended-credentials"})


async def run(agent, database, mode, **arguments):
    result = await agent.call("run_maintenance", database=database, mode=mode, wait=600, **arguments)
    if result["status"] != "success":
        logs = await agent.call("get_run_logs", run=result["id"], tail=80)
        raise AssertionError(f"Run failed: {result['error']}\n" + "\n".join(logs["lines"]))
    return await agent.call("get_run", run=result["id"])


async def main():
    import pyarrow as pa

    env = {**read_env(".env.bridge"), **os.environ}
    origin = env.get("EXTENSION_ORIGIN", "http://localhost:3013")
    issuer = env["OIDC_ISSUER"]
    redirect = f"http://localhost:{env.get('MCP_CALLBACK_PORT', '3010')}/callback"
    platform = read_env(env.get("PLATFORM_ENV", "../../.env"))
    accounts = {}
    for role, default in (("admin", "demo-admin"), ("writer", "demo-writer"), ("reader", "demo-reader")):
        user = env.get(f"E2E_{role.upper()}_USER", default)
        password = env.get(f"E2E_{role.upper()}_PASSWORD") or platform[f"DEMO_{role.upper()}_PASSWORD"]
        accounts[role] = sign_in(issuer, "ext-maintenance-mcp", redirect, user, password)
    namespace, name = "e2e_maintenance", f"readings_{int(time.time())}"
    async with Agent(origin + "/mcp", accounts["admin"]) as admin, \
            Agent(origin + "/mcp", accounts["writer"]) as writer, \
            Agent(origin + "/mcp", accounts["reader"]) as reader:
        me = await writer.call("whoami")
        team = next(m for m in me["memberships"] if m["role"] == "writer")["team"]
        await writer.refused("enable_environment", "forbidden_role", team=team, environment="development")
        await admin.call("enable_environment", team=team, environment="development")
        database = next(d for d in me["databases"] if d["team"] == team and d["environment"] == "development")

        tables = catalog(env, platform, team, database["id"])
        if (namespace,) not in tables.list_namespaces():
            tables.create_namespace(namespace)
        data = pa.table({"id": pa.array(range(10), pa.int64()), "value": [f"v{i}" for i in range(10)]})
        table = tables.create_table((namespace, name), schema=data.schema)
        for i in range(8):
            table.append(pa.table({"id": pa.array(range(i * 10, i * 10 + 10), pa.int64()),
                                   "value": [f"v{j}" for j in range(10)]}))
        full = f"{namespace}.{name}"
        try:
            await reader.refused("run_maintenance", "forbidden_role", database=database["id"])
            await run(writer, database["id"], "inspect")
            health = await reader.call("get_table", database=database["id"], table=full)
            assert health["health"]["dataFiles"] == 8 and health["health"]["snapshots"] == 8, health["health"]
            assert health["state"] == "no-policy", health
            print("health", full, health["reasons"])

            await writer.refused("run_maintenance", "no_policy", database=database["id"], mode="maintain")
            await writer.refused("set_policy", "forbidden_role", database=database["id"], settings={})
            await admin.call("set_policy", database=database["id"],
                             settings={"compaction": {"minInputFiles": 2}})
            dry = await run(writer, database["id"], "dry-run", tables=[full])
            planned = {a["operation"]: a["status"] for a in dry["results"][0]["actions"]}
            assert planned["compaction"] == "planned", dry["results"]
            assert len(tables.load_table((namespace, name)).snapshots()) == 8, "A dry run changed the table"

            done = await run(writer, database["id"], "maintain", tables=[full])
            actions = {a["operation"]: a for a in done["results"][0]["actions"]}
            assert actions["compaction"]["status"] == "done", actions
            reloaded = tables.load_table((namespace, name))
            rows = reloaded.scan().to_arrow()
            assert rows.num_rows == 80 and len(set(rows["id"].to_pylist())) == 80, rows.num_rows
            assert reloaded.inspect.files().num_rows == 1, reloaded.inspect.files().num_rows
            summary = reloaded.current_snapshot().summary
            assert summary.operation.value == "replace", summary
            assert summary.additional_properties["iceberg-data-platform.maintenance.run-id"] == done["id"]
            print("maintained", full, actions["compaction"]["detail"])
            await admin.call("delete_policy", database=database["id"])
        finally:
            tables.drop_table((namespace, name))


if __name__ == "__main__":
    asyncio.run(main())
