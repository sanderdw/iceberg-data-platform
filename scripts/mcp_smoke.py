"""Live MCP check against the demo stack: Keycloak sign-in, resource metadata and user-scoped tools.

Requires `scripts.setup --demo` and `keycloak-bootstrap --demo`. Creates and removes only its
own namespace. No token is printed or passed on a command line.
"""

import asyncio
import base64
import hashlib
import html
import os
import re
import secrets
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import httpx2
import pyarrow as pa
from dotenv import load_dotenv
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from pyiceberg.catalog import load_catalog


def sign_in(issuer, client_id, redirect_uri, username, password, client_secret=None, offline=True):
    """Authorization code with PKCE by scripting Keycloak's login form; returns the access token."""
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    with httpx.Client(timeout=30, follow_redirects=False) as browser:
        response = browser.get(f"{issuer}/protocol/openid-connect/auth", params={
            "client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri,
            "scope": "openid profile" + (" offline_access" if offline else ""), "state": secrets.token_urlsafe(16),
            "code_challenge": challenge, "code_challenge_method": "S256",
        })
        response.raise_for_status()
        match = re.search(r'<form[^>]*id="kc-form-login"[^>]*action="([^"]+)"', response.text)
        assert match, "Keycloak login form not found"
        # Python's cookie jar does not replay cookies for localhost with a port; send them by hand.
        cookies = "; ".join(f"{c.name}={c.value}" for c in browser.cookies.jar)
        response = browser.post(html.unescape(match.group(1)), headers={"Cookie": cookies},
                                data={"username": username, "password": password})
        assert response.status_code == 302, f"Keycloak sign-in failed for {username}: {response.status_code}"
        location = response.headers["location"]
        assert location.startswith(redirect_uri), "Sign-in did not return to the registered redirect"
        code = parse_qs(urlsplit(location).query)["code"][0]
        response = browser.post(f"{issuer}/protocol/openid-connect/token", data={
            "grant_type": "authorization_code", "client_id": client_id, "code": code,
            "redirect_uri": redirect_uri, "code_verifier": verifier,
            **({"client_secret": client_secret} if client_secret else {}),
        })
        response.raise_for_status()
        issued = response.json()
        assert "refresh_token" in issued, "Sign-in must issue a refresh token"
        return issued["access_token"]


def text(result):
    return "".join(getattr(block, "text", "") for block in result.content)


async def tools_as(url, token, calls):
    headers = {"Authorization": f"Bearer {token}"}
    async with (
        httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(30, read=120)) as http,
        Client(streamable_http_client(url, http_client=http)) as client,
    ):
        listed = await client.list_tools()
        results = [await client.call_tool(name, arguments) for name, arguments in calls]
        return getattr(listed, "tools", listed), results


def main():
    load_dotenv()
    issuer, users_url = os.environ["OIDC_ISSUER"].rstrip("/"), os.environ["USER_ORIGIN"].rstrip("/")
    port = os.environ.get("MCP_CALLBACK_PORT", "3010")
    redirect = f"http://localhost:{port}/callback"
    passwords = {a: os.environ[f"DEMO_{a.upper()}_PASSWORD"] for a in ("writer", "reader", "outsider")}
    namespace = "mcp_smoke_" + str(int(time.time()))

    metadata = httpx.get(f"{users_url}/.well-known/oauth-protected-resource/mcp", timeout=15)
    assert metadata.status_code == 200 and metadata.json()["authorization_servers"] == [issuer], metadata.text
    assert metadata.json()["resource"] == users_url + "/mcp"
    discovery = httpx.get(f"{issuer}/.well-known/openid-configuration", timeout=15).json()
    assert "S256" in discovery["code_challenge_methods_supported"]
    challenge = httpx.post(f"{users_url}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, timeout=15)
    assert challenge.status_code == 401 and "resource_metadata=" in challenge.headers.get("www-authenticate", "")
    print("PASS: resource metadata, Keycloak discovery and bearer challenge")

    writer = sign_in(issuer, "iceberg-mcp", redirect, "demo-writer", passwords["writer"])
    listed, (databases,) = asyncio.run(tools_as(users_url + "/mcp", writer, [("list_databases", {})]))
    assert not databases.is_error, text(databases)
    database = next(d["id"] for d in databases.structured_content["databases"] if d["name"] == "demo-demo")
    catalog = load_catalog(
        database, type="rest", uri=f"{os.environ['POLARIS_PUBLIC_URL']}/api/catalog", warehouse=database,
        token=writer, **{"header.X-Iceberg-Access-Delegation": "vended-credentials"},
    )
    catalog.create_namespace(namespace)
    try:
        rows = pa.table({"id": [1, 2, 3], "event": ["deploy", "build", "release"]})
        catalog.create_table((namespace, "events"), schema=rows.schema).append(rows)
        print("PASS: writer signed in through the MCP client and published a table")

        reader = sign_in(issuer, "iceberg-mcp", redirect, "demo-reader", passwords["reader"])
        listed, results = asyncio.run(tools_as(users_url + "/mcp", reader, [
            ("list_databases", {}),
            ("list_namespaces", {"database": database}),
            ("list_tables", {"database": database, "namespace": [namespace]}),
            ("describe_table", {"database": database, "namespace": [namespace], "table": "events"}),
            ("preview_rows", {"database": database, "namespace": [namespace], "table": "events", "limit": 3}),
            ("list_tables", {"database": "db-" + "0" * 32, "namespace": [namespace]}),
        ]))
        assert sorted(t.name for t in listed) == [
            "create_database", "delete_database", "describe_table", "describe_view", "list_databases",
            "list_namespaces", "list_tables", "preview_rows", "rename_database",
        ]
        assert all(t.annotations.read_only_hint for t in listed if t.name in {
            "describe_table", "describe_view", "list_databases", "list_namespaces", "list_tables", "preview_rows",
        })
        assert next(t for t in listed if t.name == "delete_database").annotations.destructive_hint
        databases, namespaces, tables, described, preview, denied = results
        assert [d["name"] for d in databases.structured_content["databases"]] == ["demo-demo"], text(databases)
        assert [namespace] in namespaces.structured_content["namespaces"], text(namespaces)
        assert [t["name"] for t in tables.structured_content["tables"]] == ["events"], text(tables)
        assert [c["name"] for c in described.structured_content["columns"]] == ["id", "event"], text(described)
        assert len(preview.structured_content["rows"]) == 3, text(preview)
        assert denied.is_error and "not available" in text(denied)
        assert all(reader not in text(r) and writer not in text(r) for r in results)
        print("PASS: reader lists, describes and previews with the MCP tools; other databases are refused")

        outsider = sign_in(issuer, "iceberg-mcp", redirect, "demo-outsider", passwords["outsider"])
        _, (result,) = asyncio.run(tools_as(users_url + "/mcp", outsider, [("list_databases", {})]))
        assert result.is_error and "not linked" in text(result), text(result)
        print("PASS: an unlinked Keycloak account gets a readable tool error")

        portal = sign_in(issuer, "iceberg-users", users_url + "/auth/callback", "demo-reader", passwords["reader"],
                         client_secret=os.environ["OIDC_USERS_SECRET"], offline=False)
        response = httpx.post(f"{users_url}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                              headers={"Authorization": f"Bearer {portal}"}, timeout=15)
        assert response.status_code == 401, response.status_code
        print("PASS: a user-portal token is refused by the MCP endpoint")
    finally:
        try:
            catalog.drop_table((namespace, "events"))
        finally:
            catalog.drop_namespace(namespace)


if __name__ == "__main__":
    main()
