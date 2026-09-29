"""Live end-to-end check of the dbt stack against a running platform, through MCP as an agent would.

Needs three linked Keycloak accounts of one team: an administrator, a writer and a reader
(E2E_{ADMIN,WRITER,READER}_{USER,PASSWORD}; the platform's demo accounts by default). It enables
development for the team, creates a project, builds it on a branch, checks the tables through the
REST API and the pipeline, and removes the project again. Tables it built stay.
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


async def main():
    env = {**read_env(".env.bridge"), **os.environ}
    origin = env.get("EXTENSION_ORIGIN", "http://localhost:3004")
    issuer = env["OIDC_ISSUER"]
    redirect = f"http://localhost:{env.get('MCP_CALLBACK_PORT', '3010')}/callback"
    platform = read_env(env.get("PLATFORM_ENV", "../../.env"))
    accounts = {}
    for role, default in (("admin", "demo-admin"), ("writer", "demo-writer"), ("reader", "demo-reader")):
        user = env.get(f"E2E_{role.upper()}_USER", default)
        password = env.get(f"E2E_{role.upper()}_PASSWORD") or platform[f"DEMO_{role.upper()}_PASSWORD"]
        accounts[role] = sign_in(issuer, "ext-dbt-mcp", redirect, user, password)
    name = "e2e_" + str(int(time.time()))
    async with Agent(origin + "/mcp", accounts["admin"]) as admin, \
            Agent(origin + "/mcp", accounts["writer"]) as writer, \
            Agent(origin + "/mcp", accounts["reader"]) as reader:
        me = await writer.call("whoami")
        team = next(m for m in me["memberships"] if m["role"] == "writer")["team"]
        await writer.refused("enable_environment", "forbidden_role", team=team, environment="development")
        await admin.call("enable_environment", team=team, environment="development")
        database = next(d["name"] for d in me["databases"] if d["team"] == team and d["environment"] == "development")
        project = await writer.call("create_project", team=team, name=name, default_database=database)
        id = project["id"]
        print("project", id, "in", database)

        await writer.refused("write_files", "protected_branch", project=id, branch="main",
                             files={"x.sql": "select 1"}, message="x")
        await writer.call("create_branch", project=id, name="feature/orders")
        await writer.call("write_files", project=id, branch="feature/orders", message="Add an orders mart", files={
            "models/marts/country_orders.sql": "select country_code, regions * 10 as orders\n"
                                               "from {{ ref('regions_per_country') }}\n",
            "models/marts/_orders.yml": "models:\n  - name: country_orders\n    description: Orders per country.\n"
                                        "    columns:\n      - name: orders\n        description: Order count.\n"
                                        "        data_tests: [not_null]\n",
        })
        compiled = await reader.call("compile", project=id, ref="feature/orders", select="country_orders")
        assert compiled["status"] == "success", compiled
        assert any("regions_per_country" in sql for sql in compiled["compiled"].values()), compiled
        await reader.refused("run", "forbidden_role", project=id, command="build", ref="feature/orders")

        run = await writer.call("run", project=id, command="build", ref="feature/orders", wait=600)
        if run["status"] != "success":
            logs = await writer.call("get_run_logs", run=run["id"], tail=80)
            raise AssertionError(f"Build failed: {run['error']}\n" + "\n".join(logs["lines"]))
        detail = await reader.call("get_run", run=run["id"])
        statuses = {r["node"]: r["status"] for r in detail["results"]}
        assert statuses[f"model.{name}.country_orders"] == "success", statuses
        assert detail["summary"]["published"]["errors"] == [], detail["summary"]["published"]
        published = detail["summary"]["published"]["tables"]
        print("build", run["id"], detail["summary"]["counts"], "published", len(published))

        preview = await reader.call("preview_model", project=id, model="country_orders", ref="feature/orders", limit=5)
        assert preview["rows"] and "orders" in preview["rows"][0], preview

        pipeline = await reader.call("get_pipeline", project=id)
        nodes = {n["id"]: n for n in pipeline["nodes"]}
        orders = nodes[f"model.{name}.country_orders"]
        assert orders["status"] == "success" and orders["tests"]["pass"] >= 1, orders
        lineage = await reader.call("get_lineage", project=id, node=f"model.{name}.country_orders")
        assert f"seed.{name}.regions" in {n["id"] for n in lineage["upstream"]}

        await writer.refused("merge_to_main", "forbidden_role", project=id, branch="feature/orders")
        await admin.call("merge_to_main", project=id, branch="feature/orders")
        await writer.refused("set_schedule", "forbidden_role", project=id, environment="production",
                             cron="0 6 * * *")
        schedule = await writer.call("set_schedule", project=id, environment="development", cron="0 6 * * *")
        await writer.call("delete_schedule", schedule=schedule["id"])

        # The REST API gives the same answers.
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {accounts['reader']}"}) as rest:
            node = (await rest.get(f"{origin}/api/v1/projects/{id}/nodes/model.{name}.country_orders")).json()
            assert node["columns"] and node["compiledSql"], node

        await admin.call("delete_project", project=id, confirm_name=name)
    print("dbt e2e passed: roles, branches, compile, build, publish, preview, pipeline, lineage, merge, schedules.")


if __name__ == "__main__":
    asyncio.run(main())
