"""Authorization boundaries of the standalone user gateway."""

import time
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server.models import DatabaseInput, ServiceError, TeamInput, UserInput
from test.conftest import MemoryPolaris, members
from user_portal.app import COOKIE, create_app
from user_portal.directory import UserDirectory
from user_portal.runtime import Workspace, filespace_key

HEADERS = {"X-Portal-Request": "1"}

SNAPSHOT = "9007199254740993"
TABLE_METADATA = {
    "format-version": 2,
    "table-uuid": "table-uuid",
    "location": "s3://bucket/events",
    "current-schema-id": 0,
    "schemas": [
        {
            "schema-id": 0,
            "fields": [
                {"id": 1, "name": "id", "type": "long", "required": True, "doc": "Event ID"},
                {"id": 2, "name": "event", "type": "string", "required": False},
            ],
        }
    ],
    "current-snapshot-id": int(SNAPSHOT),
    "last-updated-ms": 1700000000000,
    "snapshots": [
        {
            "snapshot-id": int(SNAPSHOT),
            "schema-id": 0,
            "timestamp-ms": 1700000000000,
            "summary": {"operation": "append", "total-records": "3", "token": "hidden-token"},
        }
    ],
    "snapshot-log": [{"snapshot-id": int(SNAPSHOT), "timestamp-ms": 1700000000000}],
    "refs": {"main": {"snapshot-id": int(SNAPSHOT), "type": "branch"}},
    "properties": {"owner": "analytics", "s3.access-key-id": "hidden-key", "client-secret": "hidden-secret"},
    "partition-specs": [{"spec-id": 0, "fields": []}],
    "default-spec-id": 0,
    "sort-orders": [{"order-id": 0, "fields": []}],
    "default-sort-order-id": 0,
}


class FakeRuntime:
    def __init__(self):
        self.workspaces = {}
        self.recovered = False

    def recover(self):
        self.recovered = True

    def start(self, session, database, namespace, table, *, database_name=None, shared_objects=None):
        self.shared_objects = shared_objects
        id = f"notebook-{len(self.workspaces)}"
        w = Workspace(
            id,
            session.id,
            session.user_id,
            session.team,
            database,
            filespace_key(session.team, session.environment),
            None,
            None,
            "http://notebook:2718",
            "hidden-runtime-token",
            session.environment,
        )
        self.workspaces[id] = w
        return w

    def get(self, id, session):
        w = self.workspaces.get(id)
        if (
            not w
            or w.session_id != session.id
            or w.team != session.team
            or w.environment != session.environment
        ):
            raise ServiceError(404, "Not found")
        return w

    def stop(self, id):
        del self.workspaces[id]

    def stop_session(self, id):
        for w in list(self.workspaces.values()):
            if w.session_id == id:
                self.stop(w.id)

    def close(self):
        self.workspaces.clear()


@pytest.fixture
def users():
    p = MemoryPolaris()
    p.http = Mock()
    teams = [p.save_team(TeamInput(name=f"team-{i}"))["id"] for i in range(3)]
    databases = [
        p.create_database(DatabaseInput(name=f"data_{i}", team=team))["id"] for i, team in enumerate(teams)
    ]
    roles = {teams[0]: "reader", teams[1]: "writer"}
    account = p.create_user(UserInput(name="alice", memberships=members(teams[:2], roles)))["user"]
    p.resources["principals"][account["id"]]["clientId"] = account["id"]
    directory = UserDirectory({})
    directory.metadata.http.close()
    directory.metadata = p
    calls = []

    def wire(request):
        calls.append(request)
        if request.url.path.endswith("/oauth/tokens"):
            if b"client_secret=correct-secret" not in request.content:
                return httpx.Response(401)
            return httpx.Response(200, json={"access_token": "actual-user-token", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer actual-user-token"
        if request.url.path.endswith("/tables/events"):
            return httpx.Response(
                200,
                json={
                    "metadata": TABLE_METADATA,
                    "config": {"s3.secret-access-key": "hidden-storage-secret"},
                },
            )
        if request.url.path.endswith("/views/report"):
            return httpx.Response(
                200,
                json={
                    "metadata": {
                        "format-version": 1,
                        "view-uuid": "view-uuid",
                        "location": "s3://bucket/view",
                        "schemas": TABLE_METADATA["schemas"],
                        "current-version-id": 1,
                        "versions": [
                            {
                                "version-id": 1,
                                "schema-id": 0,
                                "timestamp-ms": 1700000000000,
                                "default-namespace": ["analytics"],
                                "representations": [
                                    {"type": "sql", "dialect": "spark", "sql": "SELECT * FROM events"}
                                ],
                            }
                        ],
                    }
                },
            )
        if request.url.path.endswith("/namespaces/analytics"):
            return httpx.Response(
                200,
                json={
                    "namespace": ["analytics"],
                    "properties": {"owner": "analytics", "password": "hidden-password"},
                },
            )
        if request.url.path.endswith("/tables"):
            return httpx.Response(200, json={"identifiers": [{"namespace": ["analytics"], "name": "events"}]})
        if request.url.path.endswith("/views"):
            return httpx.Response(200, json={"identifiers": [{"namespace": ["analytics"], "name": "report"}]})
        return httpx.Response(200, json={"namespaces": [["analytics"]]})

    directory.http.close()
    directory.http = httpx.Client(transport=httpx.MockTransport(wire))
    runtime = FakeRuntime()
    app = create_app(directory, runtime)
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            app=app,
            runtime=runtime,
            directory=directory,
            provider=p,
            teams=teams,
            databases=databases,
            account=account,
            calls=calls,
        )


def login(client):
    response = client.post(
        "/api/session", json={"username": "alice", "secret": "correct-secret"}, headers=HEADERS
    )
    assert response.status_code == 200
    assert (
        "HttpOnly" in response.headers["set-cookie"] and "SameSite=strict" in response.headers["set-cookie"]
    )
    return response


def test_catalog_details_use_user_identity_and_project_metadata(users):
    c = users.client
    assert (
        c.get("/api/details", params={"database": users.databases[0], "kind": "database"}).status_code == 401
    )
    login(c)
    params = {"database": users.databases[0], "namespace": "analytics"}
    assert (
        c.get("/api/details", params={**params, "kind": "database"}).json()["database"]["id"]
        == users.databases[0]
    )
    ns = c.get("/api/details", params={**params, "kind": "namespace"}).json()
    assert ns["properties"] == {"owner": "analytics"}
    result = c.get("/api/details", params={**params, "kind": "table", "name": "events"})
    assert result.status_code == 200
    table = result.json()
    assert table["currentSnapshotId"] == table["snapshots"][0]["id"] == SNAPSHOT
    assert table["history"][0]["snapshotId"] == table["refs"][0]["snapshotId"] == SNAPSHOT
    assert table["columns"][0]["doc"] == "Event ID"
    assert table["properties"] == {"owner": "analytics"}
    assert "hidden-" not in result.text
    assert "actual-user-token" not in result.text
    view = c.get("/api/details", params={**params, "kind": "view", "name": "report"}).json()
    assert view["columns"][0]["name"] == "id"
    assert view["versions"][0]["representations"] == [{"dialect": "spark", "sql": "SELECT * FROM events"}]
    assert "snapshots" not in view
    assert c.get("/catalog.js").status_code == 200
    assert c.get("/guide.js").status_code == 200


def test_catalog_denies_other_team_environment_and_provider_forbidden(users, monkeypatch):
    c = users.client
    login(c)
    params = {"database": users.databases[1], "namespace": "analytics", "kind": "table", "name": "events"}
    before = len(users.calls)
    assert c.get("/api/details", params=params).status_code == 403
    assert len(users.calls) == before
    params["database"] = users.databases[0]
    c.patch("/api/environment", json={"environment": "production"}, headers=HEADERS)
    assert c.get("/api/details", params=params).status_code == 403
    c.patch("/api/environment", json={"environment": "development"}, headers=HEADERS)
    monkeypatch.setattr(
        users.directory.http, "get", lambda *a, **kw: httpx.Response(403, text="hidden-secret")
    )
    denied = c.get("/api/details", params=params)
    assert denied.status_code == 403 and "hidden-secret" not in denied.text


def test_preview_authorization_limits_snapshot_and_revocation(users, monkeypatch):
    c = users.client
    login(c)
    payload = {"database": users.databases[0], "namespace": ["analytics"], "table": "events"}
    run = Mock(return_value={"columns": ["id"], "rows": [["1"]], "snapshotId": SNAPSHOT, "limit": 100})
    monkeypatch.setattr("user_portal.app.run_preview", run)
    assert c.post("/api/preview", json=payload).status_code == 403
    for override in (
        {"database": users.databases[1]},
        {"limit": 101},
        {"snapshot_id": "123"},
        {"snapshot_id": "1; DROP TABLE"},
        {"namespace": []},
        {"table": ".."},
    ):
        assert c.post("/api/preview", json={**payload, **override}, headers=HEADERS).status_code in (
            403,
            404,
            422,
        )
    run.assert_not_called()
    result = c.post("/api/preview", json=payload, headers=HEADERS)
    assert result.status_code == 200 and result.json()["snapshotId"] == SNAPSHOT
    prepared = run.call_args.args[0]
    assert prepared["token"] == "actual-user-token" and prepared["snapshotId"] == SNAPSHOT
    assert "secret" not in prepared and "client_id" not in prepared
    assert "actual-user-token" not in result.text

    def revoke(_):
        users.provider.update_memberships(users.account["id"], {users.teams[1]: "reader"})
        return {"rows": [["must not be returned"]]}

    run.side_effect = revoke
    result = c.post("/api/preview", json=payload, headers=HEADERS)
    assert result.status_code == 403 and "must not be returned" not in result.text


def test_existing_name_secret_and_admin_cookie_is_not_user_session(users):
    c = users.client
    c.cookies.set("portal_session", "admin-session")
    assert c.get("/api/workspace").status_code == 401
    assert (
        c.post("/api/session", json={"username": "alice", "secret": "wrong"}, headers=HEADERS).status_code
        == 401
    )
    login(c)
    state = c.get("/api/workspace").json()
    assert {t["id"] for t in state["teams"]} == set(users.teams[:2])
    assert [d["id"] for d in state["databases"]] == [users.databases[0]]
    for forbidden in ("clientSecret", "correct-secret", "actual-user-token", "clientId"):
        assert forbidden not in str(state)
    assert c.get("/api/state").status_code == 404


@pytest.mark.parametrize("role", ["reader", "writer", "admin", "bucket-admin"])
def test_team_overview_members_are_scoped_and_have_no_credentials(users, role):
    c, p = users.client, users.provider
    assert c.get("/api/team").status_code == 401
    p.update_memberships(users.account["id"], {users.teams[0]: role, users.teams[1]: "writer"})
    bob = p.create_user(UserInput(name="bob", memberships=members(users.teams[:2], {
        users.teams[0]: "admin", users.teams[1]: "reader",
    })))["user"]
    p.create_user(UserInput(name="outsider", memberships=members([users.teams[2]])))
    login(c)
    result = c.get("/api/team").json()
    assert result == {"team": users.teams[0], "members": [
        {"id": users.account["id"], "name": "alice", "role": role},
        {"id": bob["id"], "name": "bob", "role": "admin"},
    ]}
    c.patch("/api/team", json={"team": users.teams[1]}, headers=HEADERS)
    assert c.get("/api/team").json() == {"team": users.teams[1], "members": [
        {"id": users.account["id"], "name": "alice", "role": "writer"},
        {"id": bob["id"], "name": "bob", "role": "reader"},
    ]}
    p.update_memberships(bob["id"], {users.teams[0]: "admin"})
    assert len(c.get("/api/team").json()["members"]) == 1
    p.update_memberships(users.account["id"], {users.teams[0]: role})
    assert c.get("/api/team").status_code == 403


def test_team_switch_and_database_access(users):
    c = users.client
    login(c)
    assert c.get("/api/contents", params={"database": users.databases[1]}).status_code == 403
    assert c.patch("/api/team", json={"team": users.teams[2]}, headers=HEADERS).status_code == 403
    assert c.get("/api/workspace").json()["activeRole"] == "reader"
    state = c.patch("/api/team", json={"team": users.teams[1]}, headers=HEADERS).json()
    assert [d["id"] for d in state["databases"]] == [users.databases[1]]
    assert state["activeRole"] == "writer"  # The role follows the active team.
    assert [t["role"] for t in state["teams"]] == ["reader", "writer"]
    assert c.get("/api/contents", params={"database": users.databases[0]}).status_code == 403
    result = c.get("/api/contents", params={"database": users.databases[1], "namespace": "analytics"})
    assert result.status_code == 200
    assert result.json()["tables"][0]["name"] == "events"
    assert result.json()["views"][0]["name"] == "report"
    assert len(users.calls) == 4  # OAuth, namespaces, tables, views; all as user.


@pytest.mark.parametrize("role", ["reader", "writer"])
def test_database_management_refuses_non_administrators(users, role):
    c, p = users.client, users.provider
    p.update_memberships(users.account["id"], {users.teams[0]: role, users.teams[1]: "writer"})
    login(c)
    assert c.post("/api/databases", json={"name": "new_data"}, headers=HEADERS).status_code == 403
    assert c.patch(f"/api/databases/{users.databases[0]}", json={"name": "renamed"}, headers=HEADERS).status_code == 403
    assert c.request("DELETE", f"/api/databases/{users.databases[0]}", json={"confirm_name": "data_0"}, headers=HEADERS).status_code == 403


@pytest.mark.parametrize("role", ["admin", "bucket-admin"])
def test_team_administrator_manages_own_database_in_active_environment(users, role):
    c, p = users.client, users.provider
    p.update_memberships(users.account["id"], {users.teams[0]: role, users.teams[1]: "writer"})
    login(c)
    assert c.post("/api/databases", json={"name": "wrong", "team": users.teams[2]}, headers=HEADERS).status_code == 422
    created = c.post("/api/databases", json={"name": "team_data", "description": "Owned by us"}, headers=HEADERS)
    assert created.status_code == 201
    db = created.json()
    assert (db["team"], db["environment"], db["description"]) == (users.teams[0], "development", "Owned by us")
    assert c.patch(f"/api/databases/{db['id']}", json={"name": "data_0"}, headers=HEADERS).status_code == 409
    renamed = c.patch(f"/api/databases/{db['id']}", json={"name": "renamed_data"}, headers=HEADERS)
    assert renamed.status_code == 200
    assert (renamed.json()["id"], renamed.json()["bucket"]) == (db["id"], db["bucket"])
    assert c.get("/api/workspace").json()["databases"][-1]["name"] == "renamed_data"
    assert c.patch(f"/api/databases/{users.databases[1]}", json={"name": "other"}, headers=HEADERS).status_code == 403
    assert c.request("DELETE", f"/api/databases/{db['id']}", json={"confirm_name": "team_data"}, headers=HEADERS).status_code == 422
    assert c.request("DELETE", f"/api/databases/{db['id']}", json={"confirm_name": "renamed_data"}, headers=HEADERS).status_code == 200
    assert db["id"] not in {d["id"] for d in p.list_databases()}


def test_database_deletion_can_be_resumed_only_by_current_team_administrator(users):
    c, p = users.client, users.provider
    p.update_memberships(users.account["id"], {users.teams[0]: "admin", users.teams[1]: "writer"})
    login(c)
    db = c.post("/api/databases", json={"name": "production_data"}, headers=HEADERS).json()
    marker = f"/catalogs/{db['id']}/catalog-roles/catalog_admin/grants"
    p.fail = lambda path, method, body: path == marker and method == "PUT"
    path = f"/api/databases/{db['id']}"
    assert c.request("DELETE", path, json={"confirm_name": db["name"]}, headers=HEADERS).status_code == 502
    state = c.get("/api/workspace").json()
    assert db["id"] in {d["id"] for d in state["deletingDatabases"]}
    p.update_memberships(users.account["id"], {users.teams[0]: "reader", users.teams[1]: "writer"})
    assert c.request("DELETE", path, json={"confirm_name": db["name"]}, headers=HEADERS).status_code == 403
    p.update_memberships(users.account["id"], {users.teams[0]: "admin", users.teams[1]: "writer"})
    p.fail = None
    assert c.request("DELETE", path, json={"confirm_name": db["name"]}, headers=HEADERS).status_code == 200


def test_database_management_is_limited_to_selected_environment(users):
    c, p = users.client, users.provider
    p.update_memberships(users.account["id"], {users.teams[0]: "admin", users.teams[1]: "writer"})
    login(c)
    production = p.create_database(DatabaseInput(name="prod_data", team=users.teams[0], environment="production"))
    assert c.patch(f"/api/databases/{production['id']}", json={"name": "other"}, headers=HEADERS).status_code == 403
    assert c.request("DELETE", f"/api/databases/{production['id']}", json={"confirm_name": "prod_data"}, headers=HEADERS).status_code == 403
    c.patch("/api/environment", json={"environment": "production"}, headers=HEADERS)
    created = c.post("/api/databases", json={"name": "fresh_prod"}, headers=HEADERS).json()
    assert created["environment"] == "production"
    assert c.request("DELETE", f"/api/databases/{created['id']}", json={"confirm_name": "fresh_prod"}, headers=HEADERS).status_code == 200


def test_database_management_refuses_a_move_in_progress(users):
    c, p = users.client, users.provider
    p.update_memberships(users.account["id"], {users.teams[0]: "admin", users.teams[1]: "writer"})
    login(c)
    db = users.databases[0]
    p.resources["catalogs"][db]["properties"]["portal.moving"] = users.teams[2]
    assert c.patch(f"/api/databases/{db}", json={"name": "other"}, headers=HEADERS).status_code == 409
    assert c.request("DELETE", f"/api/databases/{db}", json={"confirm_name": "data_0"}, headers=HEADERS).status_code == 409
    assert p.resources["catalogs"][db]["properties"].get("portal.deleting") is None


def test_csrf_and_invalid_inputs(users):
    c = users.client
    assert c.post("/api/session", json={"username": "alice", "secret": "correct-secret"}).status_code == 403
    assert (
        c.post(
            "/api/session",
            json={"username": "alice", "secret": "correct-secret"},
            headers={**HEADERS, "Origin": "http://evil.example"},
        ).status_code
        == 403
    )
    login(c)
    assert c.post("/api/notebooks", json={"database": users.databases[2]}, headers=HEADERS).status_code == 403
    assert (
        c.get("/api/contents", params={"database": users.databases[0], "namespace": ".."}).status_code == 422
    )
    assert (
        c.post(
            "/api/notebooks",
            json={"database": users.databases[0], "namespace": ["analytics"], "table": "missing"},
            headers=HEADERS,
        ).status_code
        == 404
    )
    assert (
        c.post(
            "/api/notebooks", content="a" * 8193, headers={**HEADERS, "Content-Type": "application/json"}
        ).status_code
        == 413
    )


def test_notebooks_are_owned_by_session_and_stop_on_team_switch(users):
    c = users.client
    login(c)
    result = c.post("/api/notebooks", json={"database": users.databases[0]}, headers=HEADERS)
    assert result.status_code == 201
    workspace = result.json()
    assert "hidden-runtime-token" not in str(workspace)
    assert "upstream" not in workspace
    owner_cookie = c.cookies.get(COOKIE)
    c.cookies.clear()
    assert c.get(workspace["url"]).status_code == 401
    login(c)  # Same user, different session still cannot take over runtime.
    assert (
        c.request("DELETE", "/api/notebooks/" + workspace["id"], json={}, headers=HEADERS).status_code == 404
    )
    assert c.get(workspace["url"]).status_code == 404
    c.cookies.clear()
    c.cookies.set(COOKIE, owner_cookie)
    assert c.patch("/api/team", json={"team": users.teams[1]}, headers=HEADERS).status_code == 200
    assert not users.runtime.workspaces
    assert c.get(workspace["url"]).status_code == 404


def test_revoked_membership_stops_runtime(users):
    c = users.client
    login(c)
    assert c.post("/api/notebooks", json={"database": users.databases[0]}, headers=HEADERS).status_code == 201
    users.provider.update_memberships(users.account["id"], {users.teams[1]: "reader"})
    state = c.get("/api/workspace").json()
    assert state["activeTeam"] == users.teams[1]
    assert not users.runtime.workspaces
    assert c.get("/api/contents", params={"database": users.databases[0]}).status_code == 403


def test_logout_removes_runtime_and_cookie(users):
    c = users.client
    login(c)
    c.post("/api/notebooks", json={"database": users.databases[0]}, headers=HEADERS)
    assert c.request("DELETE", "/api/session", json={}, headers=HEADERS).status_code == 200
    assert not users.runtime.workspaces
    assert c.get("/api/workspace").status_code == 401


def test_websocket_requires_cookie_owner_and_same_origin(users):
    c = users.client
    for headers in ({}, {"Origin": "http://testserver"}):
        with (
            pytest.raises(WebSocketDisconnect),
            c.websocket_connect("/workspaces/unknown/ws", headers=headers),
        ):
            pytest.fail("Anonymous websocket accepted")
    login(c)
    with (
        pytest.raises(WebSocketDisconnect),
        c.websocket_connect("/workspaces/unknown/ws", headers={"Origin": "http://evil.example"}),
    ):
        pytest.fail("Cross-origin websocket accepted")


def test_proxy_strips_both_portals_cookies_and_injects_runtime_token(users):
    c = users.client
    login(c)
    workspace = c.post("/api/notebooks", json={"database": users.databases[0]}, headers=HEADERS).json()
    c.cookies.set("portal_session", "administrator-cookie")
    captured = []

    def upstream(request):
        captured.append(request)
        return httpx.Response(200, content=b"notebook html", headers={"Set-Cookie": "untrusted=bad"})

    # Replace proxy transport; no real runtime required for this HTTP boundary test.
    users.app.state.proxy_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    response = c.get(workspace["url"], headers={"Authorization": "Bearer incoming-user-value"})
    assert response.status_code == 200
    assert captured[0].headers.get("cookie") is None
    assert captured[0].headers["authorization"] == "Bearer hidden-runtime-token"
    assert "set-cookie" not in response.headers
    assert c.post(workspace["url"], json={}).status_code == 403


def test_expired_token_refreshes_using_users_own_secret(users):
    directory = users.directory
    session = directory.login("alice", "correct-secret", "session")
    session.token_until = time.monotonic() - 1
    directory.contents(session, users.databases[0], [])
    assert sum(r.url.path.endswith("/oauth/tokens") for r in users.calls) == 2


def test_database_move_and_deleted_user_revoke_live_access(users):
    c = users.client
    login(c)
    notebook = c.post("/api/notebooks", json={"database": users.databases[0]}, headers=HEADERS).json()
    users.provider.move_database(users.databases[0], users.teams[2])
    assert c.get(notebook["url"]).status_code == 403
    assert c.get("/api/workspace").json()["databases"] == []
    assert not users.runtime.workspaces
    users.provider.delete_user(users.account["id"])
    assert c.get("/api/workspace").status_code == 401


def test_team_environment_filespaces_are_shared_and_execution_stays_independent(users):
    p, c = users.provider, users.client
    owner = users.teams[0]
    development = p.create_database(DatabaseInput(name="db1", team=owner))
    production = p.create_database(DatabaseInput(name="db1", team=owner, environment="production"))
    for name, role in [("sander", "writer"), ("alex", "reader")]:
        account = p.create_user(UserInput(name=name, memberships=members([owner], role)))["user"]
        p.resources["principals"][account["id"]]["clientId"] = account["id"]
    cookies, notebooks = {}, {}
    for name in ("sander", "alex"):
        c.cookies.clear()
        assert (
            c.post(
                "/api/session", json={"username": name, "secret": "correct-secret"}, headers=HEADERS
            ).status_code
            == 200
        )
        cookies[name] = c.cookies.get(COOKIE)
        state = c.get("/api/workspace").json()
        assert state["environments"] == ["development", "acceptance", "production"]
        assert len(state["filespaces"]) == 2  # data_0 adds no extra Development filespace.
        assert {f["environment"] for f in state["filespaces"]} == {"development", "production"}
        assert (
            c.post("/api/notebooks", json={"database": production["id"]}, headers=HEADERS).status_code == 403
        )
        notebooks[name] = c.post(
            "/api/notebooks", json={"database": development["id"]}, headers=HEADERS
        ).json()
    assert notebooks["sander"]["filespace"] == notebooks["alex"]["filespace"]
    assert notebooks["sander"]["id"] != notebooks["alex"]["id"]
    assert c.get(notebooks["sander"]["url"]).status_code == 404
    assert c.patch("/api/environment", json={"environment": "staging"}, headers=HEADERS).status_code == 422
    switched = c.patch("/api/environment", json={"environment": "production"}, headers=HEADERS)
    assert switched.status_code == 200
    assert [d["id"] for d in switched.json()["databases"]] == [production["id"]]
    assert notebooks["sander"]["id"] in users.runtime.workspaces
    assert notebooks["alex"]["id"] not in users.runtime.workspaces
    prod = c.post("/api/notebooks", json={"database": production["id"]}, headers=HEADERS).json()
    assert prod["filespace"] != notebooks["sander"]["filespace"]
    assert c.get("/api/contents", params={"database": development["id"]}).status_code == 403
    assert c.request("DELETE", "/api/session", json={}, headers=HEADERS).status_code == 200
    assert notebooks["sander"]["id"] in users.runtime.workspaces
    c.cookies.clear()
    c.cookies.set(COOKIE, cookies["sander"])
    assert c.get("/api/workspace").json()["notebooks"][0]["id"] == notebooks["sander"]["id"]
    empty = c.patch("/api/environment", json={"environment": "acceptance"}, headers=HEADERS).json()
    assert empty["activeEnvironment"] == "acceptance" and empty["databases"] == []


JSON = {**HEADERS, "Content-Type": "application/json"}


def share_body(database, **extra):
    objects = [
        {"kind": "table", "namespace": ["analytics"], "name": "events"},
        {"kind": "view", "namespace": ["analytics"], "name": "report"},
    ]
    return {"database": database, "name": "partner", "objects": objects, **extra}


def promote(users, roles):
    users.provider.update_memberships(users.account["id"], roles)


def test_share_creation_rechecks_team_after_gateway_authorization(users, monkeypatch):
    c, provider = users.client, users.provider
    first, second, _ = users.teams
    db = users.databases[0]
    promote(users, {first: "admin", second: "reader"})
    login(c)
    create = provider.create_share

    def move_then_create(data, user, **kwargs):
        provider.move_database(db, second)
        return create(data, user, **kwargs)

    monkeypatch.setattr(provider, "create_share", move_then_create)
    response = c.post("/api/shares", json=share_body(db), headers=JSON)
    assert response.status_code == 409
    assert provider.list_shares() == []


def test_only_current_team_admins_manage_shares(users):
    c, (first, second, _), (db, other_db, foreign_db) = users.client, users.teams, users.databases
    assert c.get("/api/shares", params={"database": db}).status_code == 401
    login(c)
    # Readers and writers can inspect shares, but cannot manage them.
    assert c.get("/api/shares", params={"database": db}).status_code == 200
    assert c.post("/api/shares", json=share_body(db), headers=JSON).status_code == 403
    assert c.patch("/api/team", json={"team": second}, headers=JSON).status_code == 200
    assert c.post("/api/shares", json=share_body(other_db), headers=JSON).status_code == 403
    assert users.provider.list_shares() == []
    promote(users, {first: "reader", second: "admin"})
    created = c.post("/api/shares", json=share_body(other_db, recipient="Partner BV"), headers=JSON)
    assert created.status_code == 201, created.text
    body = created.json()
    id = body["share"]["id"]
    assert body["credentials"]["clientSecret"] and body["share"]["createdBy"] == "alice"
    assert body["connection"]["identifiers"][0] == {"kind": "table", "identifier": "analytics.events"}
    listed = c.get("/api/shares", params={"database": other_db}).json()
    assert [s["id"] for s in listed["shares"]] == [id] and listed["limits"] == {"shares": 20, "objects": 50}
    assert "clientSecret" not in str(listed) and all(o["granted"] for o in listed["shares"][0]["objects"])
    # Admin of one team is nothing in another: not a member, or a member without the role.
    assert c.get("/api/shares", params={"database": foreign_db}).status_code == 403
    assert c.post("/api/shares", json=share_body(foreign_db), headers=JSON).status_code == 403
    assert c.post("/api/shares", json=share_body(db), headers=JSON).status_code == 403
    assert c.get("/api/shares", params={"database": db}).status_code == 403
    # The share belongs to the active team and environment only.
    assert c.patch("/api/environment", json={"environment": "production"}, headers=JSON).status_code == 200
    assert c.delete(f"/api/shares/{id}", headers=JSON).status_code == 403
    assert c.get("/api/shares", params={"database": other_db}).status_code == 403
    assert c.patch("/api/environment", json={"environment": "development"}, headers=JSON).status_code == 200
    assert c.patch("/api/team", json={"team": first}, headers=JSON).status_code == 200
    assert c.post(f"/api/shares/{id}/rotate", json={}, headers=JSON).status_code == 403
    assert c.patch("/api/team", json={"team": second}, headers=JSON).status_code == 200
    rotated = c.post(f"/api/shares/{id}/rotate", json={}, headers=JSON)
    assert rotated.status_code == 200 and rotated.json()["credentials"]["clientSecret"] == "rotated-secret"
    # Demotion between two requests takes effect at once; the share itself survives its creator.
    promote(users, {first: "reader", second: "writer"})
    for response in (
        c.patch(f"/api/shares/{id}", json={"recipient": "x"}, headers=JSON),
        c.post(f"/api/shares/{id}/rotate", json={}, headers=JSON),
        c.delete(f"/api/shares/{id}", headers=JSON),
    ):
        assert response.status_code == 403
    assert [s["id"] for s in users.provider.list_shares()] == [id]
    for role in ("reader", "writer"):
        promote(users, {first: "reader", second: role})
        response = c.get("/api/shares", params={"database": other_db})
        assert response.status_code == 200
        assert response.json() == listed
        assert "clientSecret" not in response.text
    promote(users, {first: "reader", second: "bucket-admin"})
    edited = c.patch(f"/api/shares/{id}", json={"objects": share_body(db)["objects"][:1]}, headers=JSON)
    assert edited.status_code == 200 and [o["name"] for o in edited.json()["share"]["objects"]] == ["events"]
    assert c.delete(f"/api/shares/{id}", headers=JSON).status_code == 200
    assert users.provider.list_shares() == []


def test_share_requests_are_validated_and_csrf_protected(users):
    c, second, db = users.client, users.teams[1], users.databases[1]
    promote(users, {users.teams[0]: "reader", second: "admin"})
    login(c)
    c.patch("/api/team", json={"team": second}, headers=JSON)
    assert c.post("/api/shares", json=share_body(db)).status_code == 403
    assert c.post("/api/shares", json=share_body(db), headers={**JSON, "Origin": "http://evil.test"}).status_code == 403
    view_only = c.post("/api/shares", json={**share_body(db), "objects": share_body(db)["objects"][1:]}, headers=JSON)
    assert (view_only.status_code, view_only.json()["error"]) == (422, "Select the tables a shared view reads.")
    invalid_name = c.post("/api/shares", json=share_body(db, name="Sensor Events", expiresAt=None), headers=JSON)
    assert (invalid_name.status_code, invalid_name.json()["error"]) == (
        422, "Share name must use 3–48 lowercase letters, digits, hyphens or underscores, starting with a letter."
    )
    for values, message in (
        ({"recipient": "x" * 121}, "Recipient must be at most 120 characters."),
        ({"description": "x" * 281}, "Description must be at most 280 characters."),
        ({"objects": []}, "Select between 1 and 50 tables and views, including at least one table."),
        ({"expiresAt": "not-a-date"}, "Choose a valid future expiry with a time zone, or leave it empty."),
    ):
        response = c.post("/api/shares", json=share_body(db, **values), headers=JSON)
        assert (response.status_code, response.json()["error"]) == (422, message)
    assert users.provider.list_shares() == []
    for body in (
        share_body("../etc"),
        share_body(db, expiresAt="2020-01-01T00:00:00Z"),
        share_body(db, team=second),
        {**share_body(db), "objects": [{"kind": "table", "namespace": [".."], "name": "events"}]},
        {**share_body(db), "objects": [{"kind": "table", "namespace": ["a"], "name": f"t{n}"} for n in range(51)]},
    ):
        assert c.post("/api/shares", json=body, headers=JSON).status_code in (413, 422)
    assert c.get("/api/shares", params={"database": "db-x"}).status_code == 422
    assert c.delete("/api/shares/portal-" + "a" * 32, headers=JSON).status_code == 422
    assert c.delete("/api/shares/share-" + "a" * 32, headers=JSON).status_code == 404
    # A platform user is not a share, and a share is not a login.
    assert c.delete(f"/api/shares/{users.account['id'].replace('portal-', 'share-')}", headers=JSON).status_code == 404
    id = c.post("/api/shares", json=share_body(db), headers=JSON).json()["share"]["id"]
    users.provider.resources["principals"][id]["clientId"] = id
    c.delete("/api/session", headers=JSON)
    for username in ("partner", id):
        attempt = c.post("/api/session", json={"username": username, "secret": "correct-secret"}, headers=HEADERS)
        assert attempt.status_code == 401


def test_user_api_docs_require_session_and_describe_authenticated_operations(users):
    client = users.client
    for path in ("/docs", "/openapi.json"):
        assert client.get(path).status_code == 401
    login(client)
    docs = client.get("/docs")
    assert docs.status_code == 200
    assert '/docs.js' in docs.text
    assert "https://cdn.jsdelivr.net" in docs.headers["content-security-policy"]
    assert "https://cdn.jsdelivr.net" not in client.get("/").headers["content-security-policy"]
    schema = client.get("/openapi.json").json()
    assert all(path.startswith("/api/") for path in schema["paths"])
    assert schema["components"]["securitySchemes"]["UserSession"]["name"] == COOKIE
    assert schema["paths"]["/api/workspace"]["get"]["security"] == [{"UserSession": []}]
    create = schema["paths"]["/api/databases"]["post"]
    assert any(p["name"] == "X-Portal-Request" and p["schema"]["default"] == "1"
               for p in create["parameters"])
    assert client.post("/api/databases", json={"name": "forbidden"}, headers=HEADERS).status_code == 403
    assert client.patch("/api/environment", json={"environment": "production"}).status_code == 403
    assert client.patch("/api/environment", json={"environment": "production"}, headers=HEADERS).status_code == 200
    client.request("DELETE", "/api/session", json={}, headers=HEADERS)
    assert client.get("/openapi.json").status_code == 401


def test_internal_shares_are_visible_only_to_recipient_team_and_environment(users):
    c = users.client
    first, second, third = users.teams
    db = users.databases[0]
    assert c.get('/api/received-shares').status_code == 401
    login(c)
    assert c.get('/api/share-teams').status_code == 403
    promote(users, {first: 'admin', second: 'reader'})
    assert {t['id'] for t in c.get('/api/share-teams').json()} == {second, third}
    response = c.post('/api/shares', json=share_body(db, external=False, recipientTeam=second), headers=JSON)
    assert response.status_code == 201, response.text
    assert set(response.json()) == {'share'}
    id = response.json()['share']['id']
    assert c.get('/api/received-shares').json() == []
    c.patch('/api/team', json={'team': second}, headers=JSON)
    received = c.get('/api/received-shares').json()
    assert [s['id'] for s in received] == [id]
    assert received[0]['database'] == db
    assert received[0]['ownerTeam'] == first
    assert received[0]['ownerTeamName'] == next(t['name'] for t in users.provider.list_teams() if t['id'] == first)
    assert 'clientId' not in received[0]
    assert c.delete(f'/api/shares/{id}', headers=JSON).status_code == 403
    c.patch('/api/environment', json={'environment': 'production'}, headers=JSON)
    assert c.get('/api/received-shares').json() == []
    c.patch('/api/environment', json={'environment': 'development'}, headers=JSON)
    users.provider.resources['principals'][id]['properties']['portal.expires-at'] = '2020-01-01T00:00:00+00:00'
    assert c.get('/api/received-shares').json() == []
    assert not any(id in grant for grant in users.provider.grants)


def test_shared_catalog_and_notebooks_without_owned_databases(users):
    from server.models import ShareInput

    c, provider = users.client, users.provider
    owner, _, recipient = users.teams
    db = users.databases[0]
    provider.delete_database(users.databases[2])
    share = provider.create_share(
        ShareInput.model_validate(share_body(db, external=False, recipientTeam=recipient)), users.account,
    )['share']
    promote(users, {recipient: 'reader'})
    login(c)
    workspace = c.get('/api/workspace').json()
    assert len(workspace['databases']) == 1
    assert workspace['databases'][0]['id'] == db
    assert workspace['databases'][0]['shared'] is True
    assert workspace['databases'][0]['team'] == owner
    owner_team = next(t for t in provider.list_teams() if t['id'] == owner)
    assert workspace['databases'][0]['ownerTeamName'] == owner_team['name']
    assert owner not in {t['id'] for t in workspace['teams']}
    detail = c.get('/api/details', params={'database': db, 'kind': 'database'}).json()
    assert detail['database']['ownerTeamName'] == owner_team['name']
    assert len(workspace['filespaces']) == 1
    root = c.get('/api/contents', params={'database': db}).json()
    assert root['namespaces'] == [['analytics']]
    contents = c.get('/api/contents', params={'database': db, 'namespace': 'analytics'}).json()
    assert contents['tables'] == [{'name': 'events', 'namespace': ['analytics']}]
    assert contents['views'] == [{'name': 'report', 'namespace': ['analytics']}]
    assert c.get('/api/details', params={'database': db, 'kind': 'namespace', 'namespace': 'analytics'}).status_code == 200
    assert c.get('/api/details', params={'database': db, 'kind': 'table', 'namespace': 'analytics', 'name': 'events'}).status_code == 200
    assert c.get('/api/details', params={'database': db, 'kind': 'table', 'namespace': 'analytics', 'name': 'private'}).status_code == 403
    opened = c.post('/api/notebooks', json={'database': db, 'namespace': ['analytics'], 'table': 'events'}, headers=JSON)
    assert opened.status_code == 201, opened.text
    assert opened.json()['team'] == recipient
    assert users.runtime.shared_objects[0]['name'] == 'events'
    # Recipient administrators still cannot manage the source database or outgoing shares.
    promote(users, {recipient: 'admin'})
    assert c.get('/api/shares', params={'database': db}).status_code == 403
    assert c.post('/api/shares', json=share_body(db, name='reshare'), headers=JSON).status_code == 403
    assert c.patch(f'/api/databases/{db}', json={'name': 'renamed'}, headers=JSON).status_code == 403
    c.patch('/api/environment', json={'environment': 'production'}, headers=JSON)
    assert c.get('/api/workspace').json()['databases'] == []
    c.patch('/api/environment', json={'environment': 'development'}, headers=JSON)
    c.post('/api/notebooks', json={'database': db}, headers=JSON)
    provider.delete_share(share['id'])
    workspace = c.get('/api/workspace').json()
    assert workspace['databases'] == [] and workspace['notebooks'] == []
    assert c.get('/api/contents', params={'database': db}).status_code == 403
    assert c.post('/api/notebooks', json={'database': db}, headers=JSON).status_code == 403
