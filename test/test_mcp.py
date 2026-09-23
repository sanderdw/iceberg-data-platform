"""MCP endpoint: Keycloak bearer tokens, catalog reads and team database management."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
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

from server.mcp_auth import MCP_MAX_BODY, KeycloakVerifier, protected_resource
from server.models import DatabaseInput, TeamInput, UserInput
from test.conftest import MemoryPolaris, members
from test.test_oidc import ISSUER, issuer  # noqa: F401 - fixture
from test.test_user_portal import SNAPSHOT, TABLE_METADATA, FakeRuntime
from user_portal.app import create_app
from user_portal.directory import UserDirectory

ORIGIN = "http://localhost:13000"
METADATA = {
    "resource": ORIGIN + "/mcp",
    "authorization_servers": [ISSUER],
    "bearer_methods_supported": ["header"],
    "scopes_supported": ["openid", "profile"],
    "resource_name": "Iceberg Workspaces",
}


def sign(state, account, **changes):
    now = int(time.time())
    claims = {
        "iss": ISSUER, "sub": "subject-123", "exp": now + 900, "iat": now, "aud": ["polaris", "account"],
        "azp": "iceberg-mcp", "scope": "openid profile",
        "polaris": {"principal_id": 0, "principal_name": account}, **changes,
    }
    return jwt.encode({"alg": "RS256", "kid": "test"}, claims, state["signing_key"])


@pytest.fixture
def stack(issuer):  # noqa: F811 - pytest fixture
    oidc, state = issuer
    p = MemoryPolaris()
    p.http = Mock()
    teams = [p.save_team(TeamInput(name=f"team-{i}"))["id"] for i in range(2)]
    databases = [p.create_database(DatabaseInput(name=f"data_{i}", team=t))["id"] for i, t in enumerate(teams)]
    account = p.create_user(UserInput(name="alice", memberships=members(teams[:1])))["user"]
    principal = p.resources["principals"][account["id"]]
    principal["properties"].update({"portal.oidc-subject": "subject-123", "portal.oidc-issuer": ISSUER})
    directory = UserDirectory({})
    directory.metadata.http.close()
    directory.metadata = p
    expected = {"token": None}
    calls = []

    def wire(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer " + expected["token"]
        if request.url.path.endswith("/tables/events"):
            return httpx.Response(200, json={"metadata": TABLE_METADATA, "config": {"s3.secret-access-key": "hidden-storage-secret"}})
        if request.url.path.endswith("/views/report"):
            return httpx.Response(200, json={"metadata": {
                "format-version": 1, "view-uuid": "view-uuid", "location": "s3://bucket/view",
                "schemas": TABLE_METADATA["schemas"], "current-version-id": 1,
                "versions": [{"version-id": 1, "schema-id": 0, "timestamp-ms": 1700000000000,
                              "default-namespace": ["analytics"],
                              "representations": [{"type": "sql", "dialect": "spark", "sql": "SELECT * FROM events"}]}],
            }})
        if request.url.path.endswith("/tables"):
            return httpx.Response(200, json={"identifiers": [{"namespace": ["analytics"], "name": "events"}]})
        if request.url.path.endswith("/views"):
            return httpx.Response(200, json={"identifiers": [{"namespace": ["analytics"], "name": "report"}]})
        return httpx.Response(200, json={"namespaces": [["analytics"]]})

    directory.http.close()
    directory.http = httpx.Client(transport=httpx.MockTransport(wire))
    app = create_app(directory, FakeRuntime(), oidc=oidc)
    token = sign(state, account["id"])
    expected["token"] = token
    return SimpleNamespace(
        app=app, oidc=oidc, state=state, provider=p, principal=principal, account=account,
        teams=teams, databases=databases, token=token, expected=expected, calls=calls,
    )


def verify(stack, token):
    return asyncio.run(KeycloakVerifier(stack.oidc).verify_token(token))


def test_verifier_accepts_only_mcp_client_tokens_for_platform_users(stack, caplog):
    access = verify(stack, stack.token)
    assert access.client_id == "iceberg-mcp" and access.subject == "subject-123"
    assert access.claims == {"iss": ISSUER, "polaris": {"principal_name": stack.account["id"]}, "roles": []}
    assert access.expires_at > time.time()
    state, account = stack.state, stack.account["id"]
    rejected = [
        sign(state, account, aud="wrong"),
        sign(state, account, azp="iceberg-users"),
        sign(state, account, exp=int(time.time()) - 10),
        sign(state, "portal-x", iss="http://localhost:18080/realms/other"),
        stack.token[:-8] + "AAAAAAAA",
        "not-a-token",
    ]
    for token in rejected:
        assert verify(stack, token) is None
    # A Keycloak account without a platform link is authenticated but has no principal.
    unlinked = [
        sign(state, account, polaris={}),
        sign(state, account, polaris={"principal_id": 1, "principal_name": account}),
        sign(state, account, polaris={"principal_id": 0, "principal_name": "alice"}),
    ]
    for token in unlinked:
        assert verify(stack, token).claims["polaris"]["principal_name"] == ""
        result = call(stack, token, "list_databases", {})
        assert result.is_error and "not linked" in text(result)
    assert "eyJ" not in caplog.text


def test_metadata_routes_and_bearer_challenge(stack):
    assert protected_resource(stack.oidc, "Iceberg Workspaces") == METADATA
    with TestClient(stack.app) as c:
        for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
            response = c.get(path)
            assert response.status_code == 200 and response.json() == METADATA
            assert response.headers["cache-control"] == "no-store"
        # No cookie, CSRF header or same-origin check applies to the bearer-token API.
        for headers in ({}, {"Authorization": "Bearer nonsense"}, {"Origin": "http://evil.example"}):
            response = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=headers)
            assert response.status_code == 401
            assert f'resource_metadata="{ORIGIN}/.well-known/oauth-protected-resource/mcp"' in response.headers["www-authenticate"]
            assert "eyJ" not in response.text
        assert c.get("/missing").status_code == 404  # The static catch-all still follows the MCP routes.


def test_mcp_request_body_is_bounded(stack):
    headers = {"Authorization": "Bearer " + stack.token, "Content-Type": "application/json"}
    with TestClient(stack.app) as c:
        declared = c.post("/mcp", content=b" " * (MCP_MAX_BODY + 1), headers=headers)
        assert declared.status_code == 413 and declared.json() == {"error": "Request is too large."}
        # Without Content-Length the body is counted as it streams in.
        streamed = c.post("/mcp", content=iter([b" " * MCP_MAX_BODY, b" "]), headers=headers)
        assert streamed.status_code == 413
        # A body within the limit reaches the transport unchanged.
        listed = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                        headers={**headers, "Accept": "application/json, text/event-stream"})
        assert listed.status_code == 200 and listed.json()["result"]["tools"]


def test_metadata_absent_without_keycloak():
    directory = UserDirectory({})
    directory.metadata.http.close()
    directory.metadata = MemoryPolaris()
    directory.metadata.http = Mock()
    with TestClient(create_app(directory, FakeRuntime())) as c:
        assert c.get("/.well-known/oauth-protected-resource").status_code == 404
        assert c.post("/mcp", json={}).status_code in (404, 405)


def call(stack, token, name, arguments):
    async def run():
        access = await KeycloakVerifier(stack.oidc).verify_token(token)
        auth_context_var.set(AuthenticatedUser(access))
        async with Client(stack.app.state.mcp) as client:
            return await client.call_tool(name, arguments)

    return asyncio.run(run())


def text(result):
    return "".join(getattr(block, "text", "") for block in result.content)


def test_tools_use_the_callers_grants(stack, monkeypatch):
    stack.expected["token"] = stack.token
    result = call(stack, stack.token, "list_databases", {})
    assert not result.is_error, text(result)
    listing = result.structured_content
    assert listing["user"]["id"] == stack.account["id"]
    assert listing["teams"] == [{"id": stack.teams[0], "name": "team-0", "role": "reader"}]
    assert [d["id"] for d in listing["databases"]] == stack.databases[:1]
    assert set(listing["databases"][0]) == {"id", "name", "team", "environment", "description", "status"}
    database = stack.databases[0]
    result = call(stack, stack.token, "list_namespaces", {"database": database})
    assert result.structured_content["namespaces"] == [["analytics"]]
    result = call(stack, stack.token, "list_tables", {"database": database, "namespace": ["analytics"]})
    assert result.structured_content["tables"] == [{"name": "events", "namespace": ["analytics"]}]
    assert result.structured_content["views"] == [{"name": "report", "namespace": ["analytics"]}]
    result = call(stack, stack.token, "describe_table", {"database": database, "namespace": ["analytics"], "table": "events"})
    table = result.structured_content
    assert table["columns"][0]["doc"] == "Event ID" and table["currentSnapshotId"] == SNAPSHOT
    assert table["snapshotCount"] == 1 and table["properties"] == {"owner": "analytics"}
    assert "hidden-" not in text(result)
    result = call(stack, stack.token, "describe_view", {"database": database, "namespace": ["analytics"], "view": "report"})
    assert result.structured_content["versions"][0]["representations"][0]["sql"] == "SELECT * FROM events"
    run = Mock(return_value={"columns": ["id"], "rows": [["1"]], "snapshotId": SNAPSHOT, "limit": 3})
    monkeypatch.setattr("user_portal.mcp_server.run_preview", run)
    result = call(stack, stack.token, "preview_rows", {"database": database, "namespace": ["analytics"], "table": "events", "limit": 3})
    assert result.structured_content["rows"] == [["1"]]
    prepared = run.call_args.args[0]
    assert prepared["token"] == stack.token and prepared["limit"] == 3 and prepared["snapshotId"] == SNAPSHOT
    assert stack.token not in text(result)


def test_tools_refuse_other_teams_invalid_input_and_unlinked_identity(stack, monkeypatch):
    database = stack.databases[0]
    denied = [
        ("list_tables", {"database": stack.databases[1], "namespace": ["analytics"]}, "not available to you"),
        ("list_tables", {"database": "db-" + "f" * 32, "namespace": ["analytics"]}, "not available to you"),
        ("list_tables", {"database": database, "namespace": []}, "Select a namespace"),
        ("list_tables", {"database": database, "namespace": [".."]}, "Invalid namespace"),
        ("describe_table", {"database": database, "namespace": ["analytics"], "table": "a\x1fb"}, "Invalid namespace"),
        ("preview_rows", {"database": database, "namespace": ["analytics"], "table": "events", "limit": 101}, "limit"),
        ("preview_rows", {"database": database, "namespace": ["analytics"], "table": "events", "snapshot_id": "1; DROP"}, "snapshot_id"),
        ("list_namespaces", {"database": "data_0"}, "database"),
    ]
    run = Mock(side_effect=AssertionError("must not run"))
    monkeypatch.setattr("user_portal.mcp_server.run_preview", run)
    for name, arguments, message in denied:
        result = call(stack, stack.token, name, arguments)
        assert result.is_error and message in text(result), (name, text(result))
    before = len(stack.calls)
    monkeypatch.setattr(stack.provider, "request", Mock(side_effect=AssertionError("no catalog call")))
    assert len(stack.calls) == before
    stack.principal["properties"]["portal.oidc-subject"] = "different-subject"
    result = call(stack, stack.token, "list_databases", {})
    assert result.is_error and "no longer linked" in text(result)
    stack.principal["properties"]["portal.oidc-subject"] = "subject-123"
    stack.principal["properties"]["portal.memberships"] = "{}"
    result = call(stack, stack.token, "list_databases", {})
    assert result.is_error and "teams" in text(result)


def test_database_management_tools_follow_current_team_role(stack):
    team, other = stack.teams
    refused = call(stack, stack.token, "create_database", {"team": team, "name": "new_data"})
    assert refused.is_error and "Only team administrators" in text(refused)
    stack.provider.update_memberships(stack.account["id"], {team: "writer"})
    refused = call(stack, stack.token, "rename_database", {"database": stack.databases[0], "name": "renamed"})
    assert refused.is_error and "Only team administrators" in text(refused)
    stack.provider.update_memberships(stack.account["id"], {team: "admin"})
    refused = call(stack, stack.token, "create_database", {"team": other, "name": "new_data"})
    assert refused.is_error and "Only team administrators" in text(refused)
    created = call(stack, stack.token, "create_database", {
        "team": team, "name": "new_data", "environment": "production", "description": "MCP database",
    })
    assert not created.is_error, text(created)
    db = created.structured_content
    assert (db["team"], db["environment"]) == (team, "production")
    renamed = call(stack, stack.token, "rename_database", {"database": db["id"], "name": "renamed_data"})
    assert not renamed.is_error, text(renamed)
    assert renamed.structured_content["id"] == db["id"] and renamed.structured_content["bucket"] == db["bucket"]
    refused = call(stack, stack.token, "delete_database", {"database": db["id"], "confirm_name": "new_data"})
    assert refused.is_error and "exactly" in text(refused)
    stack.provider.update_memberships(stack.account["id"], {team: "reader"})
    refused = call(stack, stack.token, "delete_database", {"database": db["id"], "confirm_name": "renamed_data"})
    assert refused.is_error and "Only team administrators" in text(refused)
    stack.provider.update_memberships(stack.account["id"], {team: "admin"})
    marker = f"/catalogs/{db['id']}/catalog-roles/catalog_admin/grants"
    stack.provider.fail = lambda path, method, body: path == marker and method == "PUT"
    interrupted = call(stack, stack.token, "delete_database", {"database": db["id"], "confirm_name": "renamed_data"})
    assert interrupted.is_error
    listing = call(stack, stack.token, "list_databases", {}).structured_content["databases"]
    assert next(d for d in listing if d["id"] == db["id"])["status"] == "deleting"
    stack.provider.fail = None
    deleted = call(stack, stack.token, "delete_database", {"database": db["id"], "confirm_name": "renamed_data"})
    assert not deleted.is_error, text(deleted)
    assert deleted.structured_content["deleted"] and db["id"] not in {d["id"] for d in stack.provider.list_databases()}


def test_delete_refuses_a_rename_after_confirmation(stack, monkeypatch):
    team = stack.teams[0]
    stack.provider.update_memberships(stack.account["id"], {team: "admin"})
    database = stack.databases[0]
    authorize = UserDirectory.manage_database

    def renamed_meanwhile(self, session, id, **kwargs):
        record = authorize(self, session, id, **kwargs)
        # The administration portal has its own lock and may rename in between.
        stack.provider.rename_database(id, "renamed_data")
        return record

    monkeypatch.setattr(UserDirectory, "manage_database", renamed_meanwhile)
    refused = call(stack, stack.token, "delete_database", {"database": database, "confirm_name": "data_0"})
    assert refused.is_error and "renamed" in text(refused)
    catalog = stack.provider.resources["catalogs"][database]["properties"]
    assert catalog["portal.name"] == "renamed_data" and "portal.deleting" not in catalog


def test_shared_databases_are_listed_and_read_only(stack):
    from test.test_shares import CREATOR, share_input

    team, owner = stack.teams
    database = stack.databases[1]
    events = {"kind": "table", "namespace": ["analytics"], "name": "events"}
    stack.provider.create_share(share_input(database, objects=[events], recipientTeam=team), CREATOR)
    listing = call(stack, stack.token, "list_databases", {}).structured_content
    assert [d["id"] for d in listing["databases"]] == stack.databases[:1]
    shared = listing["sharedDatabases"]
    assert [(d["id"], d["team"], d["sharedWithTeam"], d["ownerTeamName"]) for d in shared] == [
        (database, owner, team, "team-1")
    ]
    assert shared[0]["readOnly"] and shared[0]["shared"]
    result = call(stack, stack.token, "list_namespaces", {"database": database})
    assert not result.is_error, text(result)
    assert result.structured_content["namespaces"] == [["analytics"]]
    result = call(stack, stack.token, "list_tables", {"database": database, "namespace": ["analytics"]})
    assert result.structured_content["tables"] == [{"name": "events", "namespace": ["analytics"]}]
    assert result.structured_content["views"] == []
    result = call(stack, stack.token, "describe_table", {"database": database, "namespace": ["analytics"], "table": "events"})
    assert not result.is_error, text(result)
    result = call(stack, stack.token, "describe_view", {"database": database, "namespace": ["analytics"], "view": "report"})
    assert result.is_error and "not shared" in text(result)
    for name, arguments in [
        ("rename_database", {"database": database, "name": "renamed"}),
        ("delete_database", {"database": database, "confirm_name": "data_1"}),
    ]:
        refused = call(stack, stack.token, name, arguments)
        assert refused.is_error and "read-only" in text(refused), (name, text(refused))
    assert database in stack.provider.resources["catalogs"]


def test_renamed_shared_object_stays_visible_under_its_new_name(stack):
    from test.test_shares import CREATOR, share_input

    team, database = stack.teams[0], stack.databases[1]
    events = {"kind": "table", "namespace": ["analytics"], "name": "events"}
    share = stack.provider.create_share(share_input(database, objects=[events], recipientTeam=team), CREATOR)
    # Polaris keeps the grant on the renamed entity, so the recipient can still read it.
    stack.provider.catalog_roles[database][share["share"]["id"]][0]["tableName"] = "events_v2"
    result = call(stack, stack.token, "list_tables", {"database": database, "namespace": ["analytics"]})
    assert not result.is_error, text(result)
    assert result.structured_content["tables"] == [{"name": "events_v2", "namespace": ["analytics"]}]


def test_streamable_http_round_trip_authenticates_each_request(stack):
    async def run():
        async with stack.app.router.lifespan_context(stack.app):
            transport = httpx2.ASGITransport(app=stack.app)
            headers = {"Authorization": "Bearer " + stack.token}
            async with (
                httpx2.AsyncClient(transport=transport, base_url=ORIGIN, headers=headers) as http,
                Client(streamable_http_client(ORIGIN + "/mcp", http_client=http)) as client,
            ):
                tools = await client.list_tools()
                return tools, await client.call_tool("list_databases", {})

    tools, result = asyncio.run(run())
    tools = getattr(tools, "tools", tools)
    assert sorted(t.name for t in tools) == sorted(
        ["list_databases", "list_namespaces", "list_tables", "describe_table", "describe_view", "preview_rows",
         "create_database", "rename_database", "delete_database"]
    )
    assert all(t.annotations.read_only_hint for t in tools if t.name in {
        "list_databases", "list_namespaces", "list_tables", "describe_table", "describe_view", "preview_rows",
    })
    assert next(t for t in tools if t.name == "delete_database").annotations.destructive_hint
    assert not result.is_error, text(result)
    assert [d["id"] for d in result.structured_content["databases"]] == stack.databases[:1]
