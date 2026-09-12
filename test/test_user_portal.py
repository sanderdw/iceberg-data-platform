"""Authorization boundaries of the standalone user gateway."""

import time
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server.models import DatabaseInput, ServiceError, TeamInput, UserInput
from test.conftest import MemoryPolaris
from user_portal.app import COOKIE, create_app
from user_portal.directory import UserDirectory
from user_portal.runtime import Workspace

HEADERS = {"X-Portal-Request": "1"}


class FakeRuntime:
    def __init__(self):
        self.workspaces = {}
        self.recovered = False

    def recover(self):
        self.recovered = True

    def start(self, session, database, namespace, table):
        id = f"notebook-{len(self.workspaces)}"
        w = Workspace(
            id,
            session.id,
            session.user_id,
            session.team,
            database,
            id,
            None,
            None,
            "http://notebook:2718",
            "hidden-runtime-token",
        )
        self.workspaces[id] = w
        return w

    def get(self, id, session):
        w = self.workspaces.get(id)
        if not w or w.session_id != session.id or w.team != session.team:
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
    for i, team in enumerate(teams):
        p.create_database(DatabaseInput(name=f"data_{i}", team=team))
    account = p.create_user(UserInput(name="alice", teams=teams[:2], role="reader"))["user"]
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
    assert [d["id"] for d in state["databases"]] == ["data_0"]
    for forbidden in ("clientSecret", "correct-secret", "actual-user-token", "clientId"):
        assert forbidden not in str(state)
    assert c.get("/api/state").status_code == 404


def test_team_switch_and_database_access(users):
    c = users.client
    login(c)
    assert c.get("/api/contents", params={"database": "data_1"}).status_code == 403
    assert c.patch("/api/team", json={"team": users.teams[2]}, headers=HEADERS).status_code == 403
    state = c.patch("/api/team", json={"team": users.teams[1]}, headers=HEADERS).json()
    assert [d["id"] for d in state["databases"]] == ["data_1"]
    assert c.get("/api/contents", params={"database": "data_0"}).status_code == 403
    result = c.get("/api/contents", params={"database": "data_1", "namespace": "analytics"})
    assert result.status_code == 200
    assert result.json()["tables"][0]["name"] == "events"
    assert result.json()["views"][0]["name"] == "report"
    assert len(users.calls) == 4  # OAuth, namespaces, tables, views; all as user.


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
    assert c.post("/api/notebooks", json={"database": "data_2"}, headers=HEADERS).status_code == 403
    assert c.get("/api/contents", params={"database": "data_0", "namespace": ".."}).status_code == 422
    assert (
        c.post(
            "/api/notebooks",
            json={"database": "data_0", "namespace": ["analytics"], "table": "missing"},
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
    result = c.post("/api/notebooks", json={"database": "data_0"}, headers=HEADERS)
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
    assert c.post("/api/notebooks", json={"database": "data_0"}, headers=HEADERS).status_code == 201
    users.provider.update_memberships(users.account["id"], [users.teams[1]])
    state = c.get("/api/workspace").json()
    assert state["activeTeam"] == users.teams[1]
    assert not users.runtime.workspaces
    assert c.get("/api/contents", params={"database": "data_0"}).status_code == 403


def test_logout_removes_runtime_and_cookie(users):
    c = users.client
    login(c)
    c.post("/api/notebooks", json={"database": "data_0"}, headers=HEADERS)
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
    workspace = c.post("/api/notebooks", json={"database": "data_0"}, headers=HEADERS).json()
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
    directory.contents(session, "data_0", [])
    assert sum(r.url.path.endswith("/oauth/tokens") for r in users.calls) == 2


def test_database_move_and_deleted_user_revoke_live_access(users):
    c = users.client
    login(c)
    notebook = c.post("/api/notebooks", json={"database": "data_0"}, headers=HEADERS).json()
    users.provider.move_database("data_0", users.teams[2])
    assert c.get(notebook["url"]).status_code == 403
    assert c.get("/api/workspace").json()["databases"] == []
    assert not users.runtime.workspaces
    users.provider.delete_user(users.account["id"])
    assert c.get("/api/workspace").status_code == 401
