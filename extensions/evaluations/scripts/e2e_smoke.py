"""Live end-to-end check of the Evaluations extension against a running platform and Conversational BI.

Prepare the platform as for Conversational BI's smoke test: `scripts.flights_seed --own demo-demo`
(demo-team has its own copy of the flights product) and demo-admin an administrator of demo-team,
and run Conversational BI with the scripted model (`LLM_MODEL=test:flights`, `BI_ALLOW_TEST_MODEL=1`).
That model answers every question with the average departure delay by carrier, so exactly one case
of the flights example passes: the check proves the scoring, not an LLM. Uses demo-admin and
demo-reader (E2E_{ADMIN,READER}_{USER,PASSWORD} override them).
"""

import base64
import hashlib
import html
import os
import re
import secrets
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

ROOT = Path(__file__).resolve().parent.parent


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


class Api:
    def __init__(self, origin, token):
        self.http = httpx.Client(base_url=origin + "/api/v1", headers={"Authorization": f"Bearer {token}"}, timeout=60)

    def __call__(self, method, path, expect=200, **kwargs):
        response = self.http.request(method, path, **kwargs)
        assert response.status_code == expect, f"{method} {path}: {response.status_code} {response.text[:300]}"
        return response.json()


def wait(api, run_id, seconds=300):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        run = api("GET", f"/runs/{run_id}")
        if run["status"] not in ("queued", "running"):
            return run
        time.sleep(2)
    raise AssertionError(f"Run {run_id} did not finish in {seconds} s")


def main():
    env = {**read_env(ROOT / ".env.bridge"), **os.environ}
    platform = read_env(env.get("PLATFORM_ENV", str(ROOT.parent.parent / ".env")))
    origin, issuer = env.get("EXTENSION_ORIGIN", "http://localhost:3012"), env["OIDC_ISSUER"]
    redirect = f"http://localhost:{env.get('MCP_CALLBACK_PORT', '3010')}/callback"
    cbi_origin = dict(p.split("=", 1) for p in platform.get("PLATFORM_EXTENSIONS", "").split(",") if "=" in p).get(
        "conversationalbi", "http://localhost:3007")
    users = {role: (env.get(f"E2E_{role.upper()}_USER", f"demo-{role}"),
                    env.get(f"E2E_{role.upper()}_PASSWORD") or platform[f"DEMO_{role.upper()}_PASSWORD"])
             for role in ("admin", "reader")}
    admin = Api(origin, sign_in(issuer, "ext-evaluations-mcp", redirect, *users["admin"]))
    reader = Api(origin, sign_in(issuer, "ext-evaluations-mcp", redirect, *users["reader"]))
    cbi = Api(cbi_origin, sign_in(issuer, "ext-conversationalbi-mcp", redirect, *users["admin"]))

    me = admin("GET", "/me")
    team = next(m["team"] for m in me["memberships"] if m["teamName"] == env.get("E2E_TEAM", "demo-team"))
    # Both extensions, enabled by a team administrator: the consent for asking the agent for the team.
    cbi("POST", f"/teams/{team}/environments/development")
    reader("POST", f"/teams/{team}/environments/development", expect=403)
    admin("POST", f"/teams/{team}/environments/development")

    models = admin("GET", f"/teams/{team}/environments/development/agent-models")
    own = next(m for m in models["models"] if m["name"] == "flights" and not m["shared"])
    database = own["model"]["database"]
    name = f"flights_smoke_{uuid.uuid4().hex[:8]}"
    example = (ROOT / "examples" / "flights.yaml").read_text()
    body = {"team": team, "environment": "development", "name": name, "model": own["model"], "yaml": example,
            "target_database": database}
    reader("POST", "/question-sets", expect=403, json=body)
    created = admin("POST", "/question-sets", json=body)
    try:
        assert created["version"] == 1 and "delay_by_carrier" in created["yaml"]
        first = wait(admin, admin("POST", f"/question-sets/{created['id']}/runs", json={})["id"])
        assert first["status"] == "done", first["error"]
        summary = first["summary"]
        cases = {c["name"]: c for c in first["cases"]}
        # The scripted model answers only the carrier delay question right, whatever it is asked.
        assert (summary["cases"], summary["passed"]) == (25, 1), summary
        assert cases["delay_by_carrier"]["passed"], cases["delay_by_carrier"]["scores"]
        assert not cases["delay_by_runway_length"]["scores"]["refused"]["value"]
        assert not cases["on_time_per_airport"]["scores"]["clarified"]["value"]
        assert cases["departure_delay_overall"]["scores"]["right_metrics"]["value"]
        assert first["published"].endswith(".evaluations.case_results"), first["published"]
        print(f"run {first['id']}: {summary['passed']}/{summary['cases']} passed, results in {first['published']}")

        second = wait(admin, admin("POST", f"/question-sets/{created['id']}/runs", json={})["id"])
        compared = admin("GET", f"/runs/{first['id']}/compare/{second['id']}")
        assert compared["change"] == {"fixed": 0, "broken": 0, "same": 25, "no_answer": 0}, compared["change"]
        assert compared["reliable"] and first["summary"]["reliable"]
        assert reader("GET", f"/runs/{second['id']}")["summary"]["passed"] == 1

        if models.get("schemaOnly"):
            pair = admin("POST", f"/question-sets/{created['id']}/measure-semantic-model", json={})
            control, semantic = wait(admin, pair["control"]["id"]), wait(admin, pair["semantic"]["id"])
            assert control["arm"] == "control" and control["summary"]["passed"] == 1, control["summary"]
            measured = admin("GET", f"/runs/{control['id']}/compare/{semantic['id']}")["uplift"]
            # The scripted model answers the same one question right in both arms.
            assert (measured["control"], measured["semantic"], measured["points"]) == (0.04, 0.04, 0.0), measured
            assert set(measured["byKind"]) == {"query", "refusal", "clarify", "decline"}
            print(f"control arm {control['id']}: uplift {measured['points']} points")
        schedule = admin("POST", f"/question-sets/{created['id']}/schedules", json={"cron": "0 3 * * 1"})
        reader("POST", f"/question-sets/{created['id']}/schedules", expect=403, json={"cron": "0 3 * * 1"})
        admin("DELETE", f"/question-sets/{created['id']}/schedules/{schedule['id']}")

        live = admin("PUT", f"/teams/{team}/environments/development/live",
                     json={"enabled": True, "sample_pct": 100, "cron": "0 * * * *"})
        assert live["enabled"]
        admin("PUT", f"/teams/{team}/environments/development/live", json={"enabled": False})
        print("Evaluations e2e passed: roles, question sets, scored runs, comparison, Iceberg results, schedules, "
              "live.")
    finally:
        admin("DELETE", f"/question-sets/{created['id']}")


if __name__ == "__main__":
    main()
