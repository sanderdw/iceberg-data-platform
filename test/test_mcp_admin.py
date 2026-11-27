"""Administration MCP endpoint: platform-admin tokens, resource metadata and management tools."""

import asyncio
import time
from types import SimpleNamespace

import httpx2
import pytest
from fastapi.testclient import TestClient

pytest.importorskip("mcp")
pytest.importorskip("authlib")
from joserfc import jwt
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

from server.app import create_app
from server.identity import UserManagement
from server.mcp_auth import KeycloakVerifier, protected_resource
from server.models import ServiceError, ShareInput
from test.conftest import PASSWORD, MemoryPolaris
from test.test_identity_management import MemoryKeycloak
from test.test_oidc import ISSUER, issuer  # noqa: F401 - fixture

ORIGIN = "http://localhost:13000"
TOOLS = {
    "get_overview", "list_teams", "create_team", "update_team", "delete_team",
    "list_databases", "create_database", "rename_database", "move_database", "delete_database", "get_database_connection",
    "browse_catalog", "list_users", "find_accounts", "create_user", "link_user", "update_user_access",
    "retry_user_setup", "delete_user", "list_shares", "delete_share",
    "list_automation_principals", "delete_automation_principal",
}
READS = {
    "get_overview", "list_teams", "list_databases", "get_database_connection", "browse_catalog",
    "list_users", "find_accounts", "list_shares", "list_automation_principals",
}
# Hints as (readOnly, destructive, idempotent); openWorld is false for every tool.
CREATES = {"create_team", "create_database", "create_user", "link_user", "retry_user_setup"}
UPDATES = {"update_team", "rename_database"}
# Replace what was there: a repeat changes nothing further, but what is left out or left behind loses access.
REPLACES = {"move_database", "update_user_access"}
DELETES = {"delete_team", "delete_database", "delete_user", "delete_share", "delete_automation_principal"}
HINTS = {
    **dict.fromkeys(READS, (True, False, True)), **dict.fromkeys(CREATES, (False, False, False)),
    **dict.fromkeys(UPDATES, (False, False, True)), **dict.fromkeys(REPLACES, (False, True, True)),
    **dict.fromkeys(DELETES, (False, True, False)),
}


def sign(state, *, admin=True, **changes):
    now = int(time.time())
    claims = {
        "iss": ISSUER, "sub": "admin-subject", "exp": now + 900, "iat": now,
        "aud": ["polaris", "iceberg-admin", "account"], "azp": "iceberg-mcp", "scope": "openid profile",
        **({"resource_access": {"iceberg-admin": {"roles": ["platform-admin"]}}} if admin else {}),
        **changes,
    }
    return jwt.encode({"alg": "RS256", "kid": "test"}, claims, state["signing_key"])


@pytest.fixture
def admin(issuer):  # noqa: F811 - pytest fixture
    oidc, state = issuer
    provider, kc = MemoryPolaris(), MemoryKeycloak()
    app = create_app(provider, PASSWORD, oidc=oidc, user_management=UserManagement(kc))
    return SimpleNamespace(
        app=app, oidc=oidc, state=state, provider=provider, kc=kc, token=sign(state), reader=sign(state, admin=False)
    )


def verify(stack, token):
    return asyncio.run(KeycloakVerifier(stack.oidc).verify_token(token))


def call(stack, token, name, arguments):
    async def run():
        access = await KeycloakVerifier(stack.oidc).verify_token(token)
        auth_context_var.set(AuthenticatedUser(access))
        async with Client(stack.app.state.mcp) as client:
            return await client.call_tool(name, arguments)

    return asyncio.run(run())


def text(result):
    return "".join(getattr(block, "text", "") for block in result.content)


def ok(stack, name, arguments=None):
    result = call(stack, stack.token, name, arguments or {})
    assert not result.is_error, text(result)
    return result.structured_content


def refused(stack, name, arguments, message, token=None):
    result = call(stack, token or stack.token, name, arguments)
    assert result.is_error and message in text(result), (name, text(result))
    return text(result)


def test_verifier_records_roles_and_tools_require_platform_admin(admin, caplog):
    assert verify(admin, admin.token).claims["roles"] == ["platform-admin"]
    assert verify(admin, admin.reader).claims["roles"] == []
    for token in (
        sign(admin.state, aud="wrong"),
        sign(admin.state, azp="iceberg-admin"),
        sign(admin.state, exp=int(time.time()) - 10),
        admin.token[:-8] + "AAAAAAAA",
    ):
        assert verify(admin, token) is None
    for name, arguments in (("get_overview", {}), ("list_teams", {}), ("create_team", {"name": "data-team"})):
        refused(admin, name, arguments, "Platform administrator access is required.", token=admin.reader)
    assert admin.provider.list_teams() == []
    assert "eyJ" not in caplog.text


def test_metadata_routes_and_bearer_challenge(admin):
    metadata = protected_resource(admin.oidc, "Iceberg Workspace Administration")
    assert metadata["resource"] == ORIGIN + "/mcp" and metadata["authorization_servers"] == [ISSUER]
    with TestClient(admin.app) as c:
        for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
            response = c.get(path)
            assert response.status_code == 200 and response.json() == metadata
            assert response.headers["cache-control"] == "no-store"
        for headers in ({}, {"Authorization": "Bearer nonsense"}, {"Origin": "http://evil.example"}):
            response = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=headers)
            assert response.status_code == 401
            assert f'resource_metadata="{ORIGIN}/.well-known/oauth-protected-resource/mcp"' in response.headers["www-authenticate"]
        assert c.get("/api/teams").status_code == 401  # Portal routes keep their session checks.
        assert c.get("/missing").status_code == 404


def test_no_mcp_without_keycloak():
    with TestClient(create_app(MemoryPolaris(), PASSWORD)) as c:
        assert c.get("/.well-known/oauth-protected-resource").status_code == 404
        assert c.post("/mcp", json={}).status_code in (404, 405)


def test_admin_lifecycle_creates_links_edits_and_deletes(admin, caplog):
    overview = ok(admin, "get_overview")
    assert overview["health"]["status"] == "online" and overview["teams"] == []
    team = ok(admin, "create_team", {"name": "data-team", "description": "Analytics"})["id"]
    assert ok(admin, "update_team", {"team": team, "name": "data-team", "description": "Data"})["description"] == "Data"
    # What an update leaves out stays as it is.
    assert ok(admin, "update_team", {"team": team, "name": "data-renamed"})["description"] == "Data"
    cleared = ok(admin, "update_team", {"team": team, "description": ""})
    assert (cleared["name"], cleared["description"]) == ("data-renamed", "")
    assert ok(admin, "update_team", {"team": team, "name": "data-team"})["name"] == "data-team"
    refused(admin, "update_team", {"team": team}, "Give a name, a description or both")
    database = ok(admin, "create_database", {"name": "analytics", "team": team, "environment": "acceptance"})
    assert database["environment"] == "acceptance"
    renamed = ok(admin, "rename_database", {"database": database["id"], "name": "analytics_new"})
    assert renamed["id"] == database["id"] and renamed["bucket"] == database["bucket"]
    refused(admin, "rename_database", {"database": database["id"], "name": "Bad Name"}, "name")
    listed = ok(admin, "list_databases")["databases"]
    assert [d["id"] for d in listed] == [database["id"]]
    connection = ok(admin, "get_database_connection", {"database": database["id"]})
    assert connection["credential"] == "<client-id>:<client-secret>" and connection["warehouse"] == database["id"]
    assert "s3-secret" not in str(connection)
    assert database["id"] in [d["id"] for d in ok(admin, "browse_catalog")["databases"]]
    contents = ok(admin, "browse_catalog", {"database": database["id"], "namespace": ["analytics"]})
    assert contents == {"database": database["id"], "namespace": ["analytics"], "namespaces": [], "tables": [], "views": [], "semanticModels": []}

    admin.kc.accounts["ext"] = {"id": "ext", "username": "ext-name", "enabled": True, "email": "ext@example.test"}
    accounts = ok(admin, "find_accounts", {"username": "ext-name"})["accounts"]
    assert accounts == [{"id": "ext", "username": "ext-name", "email": "ext@example.test", "enabled": True, "linked": False}]
    linked = ok(admin, "link_user", {"username": "ext-name", "name": "bob", "memberships": [{"team": team, "role": "writer"}]})
    assert "temporaryPassword" not in linked["identity"] and linked["identity"]["status"] == "linked"
    bob = linked["user"]["id"]
    assert ok(admin, "find_accounts", {"username": "ext-name"})["accounts"][0]["linked"]
    refused(admin, "link_user", {"username": "ext-name", "name": "bob2", "memberships": [{"team": team, "role": "reader"}]}, "already linked")
    refused(admin, "link_user", {"username": "nobody", "name": "bob2", "memberships": [{"team": team, "role": "reader"}]}, "find_accounts")
    updated = ok(admin, "update_user_access", {"user": bob, "memberships": [{"team": team, "role": "reader"}]})
    assert updated["user"]["memberships"] == [{"team": team, "role": "reader"}]
    refused(admin, "update_user_access", {"user": bob, "memberships": [{"team": team, "role": "bucket-admin"}]}, "direct S3 accounts")

    created = ok(admin, "create_user", {
        "name": "carol", "memberships": [{"team": team, "role": "admin"}], "email": "carol@example.test",
        "first_name": "Carol", "last_name": "Curator",
    })
    password = created["identity"]["temporaryPassword"]
    assert len(password) >= 24 and password not in caplog.text and password not in str(admin.provider.resources)
    carol = created["user"]["id"]
    assert "temporaryPassword" not in ok(admin, "retry_user_setup", {"user": carol})["identity"]
    users = ok(admin, "list_users")["users"]
    assert {u["name"] for u in users} == {"bob", "carol"}
    subject = next(a["id"] for a in admin.kc.accounts.values() if a["username"] == "carol")
    admin.kc.admins.add(subject)
    refused(admin, "delete_user", {"user": carol}, "platform-administrator role")
    admin.kc.admins.discard(subject)
    assert ok(admin, "delete_user", {"user": carol}) == {"deleted": True, "user": carol}
    assert ok(admin, "delete_user", {"user": bob})["deleted"]
    refused(admin, "delete_user", {"user": bob}, "missing")  # MemoryPolaris's 404 text
    principal, _ = admin.provider.create_automation(team, "development", "dbt", {"id": bob, "name": "bob"})
    assert [p["id"] for p in ok(admin, "list_automation_principals")["automationPrincipals"]] == [principal["id"]]
    assert ok(admin, "delete_automation_principal", {"principal": principal["id"]}) == {
        "deleted": True, "principal": principal["id"]}
    refused(admin, "delete_automation_principal", {"principal": principal["id"]}, "no longer exists")
    assert admin.provider.list_automation() == []
    refused(admin, "delete_team", {"team": team}, "databases first")
    assert ok(admin, "delete_database", {"database": database["id"], "confirm_name": "analytics_new"})["deleted"]
    assert ok(admin, "delete_team", {"team": team}) == {"deleted": True, "team": team}
    assert ok(admin, "list_teams")["teams"] == []
    assert ok(admin, "list_shares")["shares"] == []


def test_delete_database_requires_the_display_name(admin):
    team = ok(admin, "create_team", {"name": "data-team"})["id"]
    database = ok(admin, "create_database", {"name": "analytics", "team": team})["id"]
    refused(admin, "delete_database", {"database": database, "confirm_name": "analytic"}, "Nothing was deleted")
    catalog = admin.provider.resources["catalogs"][database]
    assert "portal.deleting" not in catalog["properties"]
    refused(admin, "delete_database", {"database": "db-" + "0" * 32, "confirm_name": "analytics"}, "Nothing was deleted")
    # The provider rechecks the name itself: the user portal may rename after the check above.
    with pytest.raises(ServiceError, match="renamed") as raised:
        admin.provider.delete_database(database, expected_name="analytic")
    assert raised.value.status == 409 and "portal.deleting" not in catalog["properties"]
    assert ok(admin, "delete_database", {"database": database, "confirm_name": "analytics"})["name"] == "analytics"
    assert database not in admin.provider.resources["catalogs"]


def test_delete_share_requires_the_share_name(admin):
    team = ok(admin, "create_team", {"name": "data-team"})["id"]
    database = ok(admin, "create_database", {"name": "analytics", "team": team})["id"]
    share = admin.provider.create_share(ShareInput.model_validate({
        "database": database, "name": "partner", "recipient": "Partner BV",
        "objects": [{"kind": "table", "namespace": ["analytics"], "name": "orders"}],
    }), {"id": "portal-" + "c" * 32, "name": "carol"})["share"]["id"]
    assert [s["name"] for s in ok(admin, "list_shares")["shares"]] == ["partner"]
    refused(admin, "delete_share", {"share": share, "confirm_name": "Partner"}, "Nothing was deleted")
    refused(admin, "delete_share", {"share": share, "confirm_name": ""}, "Nothing was deleted")
    assert call(admin, admin.token, "delete_share", {"share": share}).is_error  # confirm_name is required
    assert [s["id"] for s in ok(admin, "list_shares")["shares"]] == [share]
    refused(admin, "delete_share", {"share": "share-" + "a" * 32, "confirm_name": "partner"},
            "Share not found. Nothing was deleted.")
    assert ok(admin, "delete_share", {"share": share, "confirm_name": "partner"}) == {
        "deleted": True, "share": share, "name": "partner"}
    assert ok(admin, "list_shares")["shares"] == []
    refused(admin, "delete_share", {"share": share, "confirm_name": "partner"}, "Share not found")
    # The old names are gone, not kept beside the new ones.
    for old in ("revoke_share", "revoke_automation_principal"):
        assert call(admin, admin.token, old, {}).is_error


def test_validation_errors_are_readable_and_never_echo_values(admin):
    team = ok(admin, "create_team", {"name": "data-team"})["id"]
    cases = [
        ("create_team", {"name": "Bad Name"}, "name"),
        ("update_user_access", {"user": "portal-" + "1" * 32, "memberships": [{"team": team, "role": "reader"}, {"team": team, "role": "writer"}]}, "only once"),
        ("create_user", {"name": "dave", "memberships": [{"team": team, "role": "reader"}], "email": "nope", "first_name": "D", "last_name": "E"}, "mail"),
        ("browse_catalog", {"database": "..", "namespace": []}, "Invalid database or namespace."),
        ("browse_catalog", {"database": team, "namespace": ["a\x1fb"]}, "Invalid database or namespace."),
        ("move_database", {"database": "db-" + "0" * 32, "team": "team-x"}, "team"),
        ("delete_team", {"team": "not-a-team"}, "team"),
    ]
    for name, arguments, message in cases:
        message_text = refused(admin, name, arguments, message)
        assert "Bad Name" not in message_text and "nope" not in message_text
    assert admin.provider.list_users() == [] and len(admin.provider.list_teams()) == 1


def test_offline_provider_and_provider_errors(admin):
    ok(admin, "create_team", {"name": "data-team"})
    admin.provider.fail = lambda path, method, body: path == "/catalogs"
    # Not the empty lists the portal page shows while it waits: an agent would read an empty platform.
    refused(admin, "get_overview", {}, "data provider is unavailable")
    admin.provider.fail = lambda path, method, body: path == "/principal-roles"
    refused(admin, "list_teams", {}, "injected failure")


def test_streamable_http_round_trip_lists_annotated_tools(admin):
    async def run(token, name):
        async with admin.app.router.lifespan_context(admin.app):
            transport = httpx2.ASGITransport(app=admin.app)
            headers = {"Authorization": "Bearer " + token}
            async with (
                httpx2.AsyncClient(transport=transport, base_url=ORIGIN, headers=headers) as http,
                Client(streamable_http_client(ORIGIN + "/mcp", http_client=http)) as client,
            ):
                tools = await client.list_tools()
                return getattr(tools, "tools", tools), await client.call_tool(name, {})

    tools, result = asyncio.run(run(admin.token, "list_teams"))
    assert {t.name for t in tools} == TOOLS == set(HINTS)
    for tool in tools:
        hints = tool.annotations
        assert (hints.read_only_hint, hints.destructive_hint, hints.idempotent_hint) == HINTS[tool.name], tool.name
        assert hints.open_world_hint is False, tool.name
    # Leaving a team out removes it, and a retry may hand out another one-time password.
    assert HINTS["update_user_access"] == (False, True, True) and HINTS["retry_user_setup"] == (False, False, False)
    schemas = {t.name: t.input_schema for t in tools}
    assert schemas["delete_share"]["required"] == ["share", "confirm_name"]
    assert schemas["delete_automation_principal"]["required"] == ["principal"]
    assert not result.is_error and result.structured_content == {"teams": []}
    tools, result = asyncio.run(run(admin.reader, "list_teams"))
    assert len(tools) == len(TOOLS)
    assert result.is_error and "Platform administrator access is required." in text(result)
    assert admin.token not in text(result) and admin.reader not in text(result)
