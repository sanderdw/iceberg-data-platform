"""Live administration MCP check against the demo stack: platform-admin tools end to end.

Requires `scripts.setup --demo` and `keycloak-bootstrap --demo`. Creates a team, database
and user named after the run and removes them again, including the Keycloak account.
No token or password is printed or passed on a command line.
"""

import asyncio
import os
import time

import httpx
from dotenv import load_dotenv

from scripts.mcp_smoke import sign_in, text, tools_as

TOOL_COUNT = 21


def delete_keycloak_account(username):
    """Remove the disposable account, as the lifecycle smoke test does; revoke keeps accounts."""
    base = os.environ["OIDC_INTERNAL_ISSUER"].rsplit("/realms/", 1)[0]
    with httpx.Client(base_url=base, timeout=30) as kc:
        response = kc.post("/realms/master/protocol/openid-connect/token", data={
            "grant_type": "password", "client_id": "admin-cli", "username": "admin",
            "password": os.environ["KEYCLOAK_ADMIN_PASSWORD"],
        })
        response.raise_for_status()
        kc.headers["Authorization"] = "Bearer " + response.json()["access_token"]
        response = kc.get("/admin/realms/iceberg/users", params={"username": username, "exact": "true"})
        response.raise_for_status()
        for account in response.json():
            if account["username"] == username:
                kc.delete("/admin/realms/iceberg/users/" + account["id"]).raise_for_status()


def main():
    load_dotenv()
    issuer = os.environ["OIDC_ISSUER"].rstrip("/")
    admin_url, users_url = os.environ["PORTAL_ORIGIN"].rstrip("/"), os.environ["USER_ORIGIN"].rstrip("/")
    redirect = f"http://localhost:{os.environ.get('MCP_CALLBACK_PORT', '3010')}/callback"
    name = f"mcp-smoke-{int(time.time())}"
    secrets = set()

    def say(line):
        assert not any(secret in line for secret in secrets), "Output must not contain credentials"
        print(line)

    def run(token, calls, url=admin_url + "/mcp"):
        listed, results = asyncio.run(tools_as(url, token, calls))
        for result in results:
            assert all(secret not in text(result) for secret in secrets), "Tool output must not contain tokens"
        return listed, results

    def ok(token, tool, arguments, url=admin_url + "/mcp"):
        _, (result,) = run(token, [(tool, arguments)], url=url)
        assert not result.is_error, f"{tool}: {text(result)}"
        return result.structured_content

    metadata = httpx.get(f"{admin_url}/.well-known/oauth-protected-resource/mcp", timeout=15)
    assert metadata.status_code == 200 and metadata.json()["resource"] == admin_url + "/mcp", metadata.text
    assert metadata.json()["authorization_servers"] == [issuer]
    challenge = httpx.post(f"{admin_url}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, timeout=15)
    assert challenge.status_code == 401 and "resource_metadata=" in challenge.headers.get("www-authenticate", "")
    say("PASS: administration resource metadata and bearer challenge")

    admin = sign_in(issuer, "iceberg-mcp", redirect, "demo-admin", os.environ["DEMO_ADMIN_PASSWORD"])
    secrets.add(admin)
    listed, (overview,) = run(admin, [("get_overview", {})])
    assert len(listed) == TOOL_COUNT, sorted(t.name for t in listed)
    assert not overview.is_error, text(overview)
    assert overview.structured_content["health"]["status"] == "online"
    assert "demo-team" in {t["name"] for t in overview.structured_content["teams"]}
    say("PASS: platform administrator signed in through the MCP client and read the overview")

    team = database = user = self_service = None
    database_name = "mcp_smoke"
    self_service_name = "mcp_self_service"
    demo_user = next(u for u in ok(admin, "list_users", {})["users"] if u["name"] == "demo-admin")
    original_memberships = demo_user["memberships"]
    promoted = False
    try:
        team = ok(admin, "create_team", {"name": name, "description": "MCP smoke"})["id"]
        ok(admin, "update_user_access", {"user": demo_user["id"],
                                          "memberships": [*original_memberships, {"team": team, "role": "admin"}]})
        promoted = True
        self_service_url = users_url + "/mcp"
        self_service = ok(admin, "create_database", {"team": team, "name": self_service_name},
                          url=self_service_url)["id"]
        assert ok(admin, "rename_database", {"database": self_service, "name": "mcp_self_service_renamed"},
                  url=self_service_url)["id"] == self_service
        self_service_name = "mcp_self_service_renamed"
        _, (refused,) = run(admin, [("delete_database", {"database": self_service,
                                                            "confirm_name": "wrong-name"})], url=self_service_url)
        assert refused.is_error, text(refused)
        assert ok(admin, "delete_database", {"database": self_service, "confirm_name": self_service_name},
                  url=self_service_url)["deleted"]
        self_service = None
        say("PASS: team admin creates, renames and permanently deletes a database through user MCP")
        database = ok(admin, "create_database", {"name": "mcp_smoke", "team": team, "environment": "acceptance"})["id"]
        assert ok(admin, "rename_database", {"database": database, "name": "mcp_smoke_renamed"})["id"] == database
        database_name = "mcp_smoke_renamed"
        connection = ok(admin, "get_database_connection", {"database": database})
        assert connection["credential"] == "<client-id>:<client-secret>" and connection["warehouse"] == database
        contents = ok(admin, "browse_catalog", {"database": database, "namespace": []})
        assert contents["namespaces"] == [] and contents["tables"] == []
        say("PASS: team and database created, connection and catalog browsing work")

        created = ok(admin, "create_user", {
            "name": name, "memberships": [{"team": team, "role": "writer"}],
            "email": f"{name}@example.test", "first_name": "Mcp", "last_name": "Smoke",
        })
        user = created["user"]["id"]
        password = created["identity"]["temporaryPassword"]
        assert len(password) >= 24
        secrets.add(password)
        accounts = ok(admin, "find_accounts", {"username": name})["accounts"]
        assert len(accounts) == 1 and accounts[0]["linked"], accounts
        updated = ok(admin, "update_user_access", {"user": user, "memberships": [{"team": team, "role": "reader"}]})
        assert updated["user"]["memberships"] == [{"team": team, "role": "reader"}]
        assert name in {u["name"] for u in ok(admin, "list_users", {})["users"]}
        say("PASS: Keycloak account created with a one-time password, found, and its access updated")

        reader = sign_in(issuer, "iceberg-mcp", redirect, "demo-reader", os.environ["DEMO_READER_PASSWORD"])
        secrets.add(reader)
        listed, (refused,) = run(reader, [("get_overview", {})])
        assert len(listed) == TOOL_COUNT
        assert refused.is_error and "Platform administrator access is required." in text(refused), text(refused)
        say("PASS: a user without the platform-admin role is refused")

        databases = ok(admin, "list_databases", {}, )
        assert any(d["id"] == database for d in databases["databases"])
        _, (own,) = run(admin, [("list_databases", {})], url=users_url + "/mcp")
        assert not own.is_error, text(own)
        say("PASS: the same sign-in also serves the user portal's MCP endpoint")
    finally:
        if user:
            ok(admin, "delete_user", {"user": user})
        if self_service:
            ok(admin, "delete_database", {"database": self_service, "confirm_name": self_service_name})
        if database:
            ok(admin, "delete_database", {"database": database, "confirm_name": database_name})
        if promoted:
            ok(admin, "update_user_access", {"user": demo_user["id"], "memberships": original_memberships})
        if team:
            ok(admin, "delete_team", {"team": team})
        delete_keycloak_account(name)
        say("PASS: smoke resources removed")


if __name__ == "__main__":
    main()
