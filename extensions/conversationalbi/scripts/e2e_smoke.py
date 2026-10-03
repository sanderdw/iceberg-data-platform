"""Live end-to-end check of Conversational BI against a running platform, through MCP as an agent would.

Prepare the platform with `scripts.flights_seed --own <database>` (from the platform directory): a
partner team shares the flights product with demo-team, and demo-team has its own copy. Notebook 06's
generator is deterministic, so both copies must give the same answers. Needs two linked Keycloak
accounts of demo-team, an administrator and a reader (E2E_{ADMIN,READER}_{USER,PASSWORD}; the
platform's demo accounts by default).
"""

import asyncio
import base64
import hashlib
import html
import json
import os
import re
import secrets
from urllib.parse import parse_qs, urlsplit

import httpx
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

QUESTION = {"metrics": ["average_departure_delay", "cancellation_pct"], "dimensions": [{"field": "CARRIER.name"}],
            "order_by": [{"name": "average_departure_delay", "desc": True}]}


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
    origin = env.get("EXTENSION_ORIGIN", "http://localhost:3007")
    issuer = env["OIDC_ISSUER"]
    redirect = f"http://localhost:{env.get('MCP_CALLBACK_PORT', '3010')}/callback"
    platform = read_env(env.get("PLATFORM_ENV", "../../.env"))
    tokens = {}
    for role, default in (("admin", "demo-admin"), ("reader", "demo-reader")):
        user = env.get(f"E2E_{role.upper()}_USER", default)
        password = env.get(f"E2E_{role.upper()}_PASSWORD") or platform[f"DEMO_{role.upper()}_PASSWORD"]
        tokens[role] = sign_in(issuer, "ext-conversationalbi-mcp", redirect, user, password)
    async with Agent(origin + "/mcp", tokens["admin"]) as admin, Agent(origin + "/mcp", tokens["reader"]) as reader:
        me = await reader.call("whoami")
        team = next(m for m in me["memberships"] if m["teamName"] == env.get("E2E_TEAM", "demo-team"))["team"]
        await reader.refused("enable_environment", "forbidden_role", team=team, environment="development")
        await admin.call("enable_environment", team=team, environment="development")

        listed = (await reader.call("list_models"))["models"]
        flights = [m for m in listed if m["name"] == "flights" and m["environment"] == "development"]
        own = next(m for m in flights if not m["shared"])
        shared = next((m for m in flights if m["shared"]), None)
        if me["sharedData"]:
            assert shared, f"The shared flights model is missing: {listed}"
        print("models:", [(m["databaseName"], m["shared"]) for m in flights])

        described = await reader.call("describe_model", **own["model"])
        assert {m["name"] for m in described["metrics"]} >= {"average_departure_delay", "on_time_arrival_pct"}
        answers = {}
        for entry in [own, shared] if shared else [own]:
            ref = entry["model"]
            result = await reader.call("query", database=ref["database"], namespace=ref["namespace"],
                                       model=ref["name"], **QUESTION)
            assert result["rowCount"] == 3 and result["joinPaths"] == {"CARRIER.name": ["flight_carrier"]}, result
            answers[entry["shared"]] = result["rows"]
            print("query", "shared" if entry["shared"] else "own", result["resultId"], result["rows"][0])
        if shared:
            assert answers[True] == answers[False], answers

        ref = (shared or own)["model"]
        where = {"database": ref["database"], "namespace": ref["namespace"], "model": ref["name"]}
        await reader.refused("query", "ambiguous_join", **where, metrics=["average_departure_delay"],
                             dimensions=[{"field": "AIRPORT.name"}])
        await reader.refused("query", "fan_out", **where, metrics=["average_departure_delay"],
                             dimensions=[{"field": "RUNWAY.length"}])
        weekly = await reader.call("query", **where, metrics=["on_time_arrival_pct"],
                                   dimensions=[{"field": "FLIGHT.date", "grain": "week"},
                                               {"field": "AIRPORT.code", "via": ["route_departure_airport"]}],
                                   filters=[{"field": "FLIGHT.date", "op": "between",
                                             "value": ["2026-01-01", "2026-01-14"]}], limit=500)
        assert weekly["rowCount"] > 0 and not weekly["truncated"], weekly
        assert weekly["joinPaths"]["AIRPORT.code"] == ["flight_route", "route_departure_airport"], weekly

        # Results are the asker's: the REST API returns every row to them, and nothing to anyone else.
        async with httpx.AsyncClient(timeout=30) as rest:
            url = f"{origin}/api/v1/results/{weekly['resultId']}"
            mine = (await rest.get(url, headers={"Authorization": f"Bearer {tokens['reader']}"})).json()
            assert mine["rowCount"] == weekly["rowCount"] and len(mine["rows"]) == weekly["rowCount"], mine
            other = (await rest.get(url, headers={"Authorization": f"Bearer {tokens['admin']}"})).json()
            assert other["code"] == "unknown_result", other
    print("Conversational BI e2e passed: roles, own and shared models, governed queries, join rules, results.")


if __name__ == "__main__":
    asyncio.run(main())
