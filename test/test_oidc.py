"""Exercise real OIDC signature/claim validation with a simulated token endpoint."""

import asyncio
import base64
import hashlib
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import httpx2
import pytest
from fastapi.testclient import TestClient

pytest.importorskip("authlib")
from joserfc import jwt
from joserfc.jwk import RSAKey

from server.app import create_app
from server.models import DatabaseInput, ServiceError, TeamInput, UserInput
from server.oidc import OIDC, PENDING_PER_CLIENT
from test.conftest import HEADERS, PASSWORD, MemoryPolaris, members
from test.test_user_portal import FakeRuntime
from user_portal.app import create_app as create_users
from user_portal.directory import UserDirectory

ISSUER = "http://localhost:18080/realms/iceberg"


@pytest.fixture
def issuer():
    oidc = OIDC({"OIDC_ISSUER": ISSUER, "OIDC_ORIGIN": "http://localhost:13000",
                 "OIDC_CLIENT_ID": "iceberg-admin", "OIDC_CLIENT_SECRET": "test-client-secret"})
    key = RSAKey.generate_key(2048, parameters={"kid": "test"})
    state = {"id_changes": {}, "access_changes": {}, "calls": 0, "signing_key": key}

    def wire(request):
        if request.url.path.endswith("/certs"):
            return httpx2.Response(200, json={"keys": [key.as_dict(private=False)]})
        assert request.url.path.endswith("/token")
        state["calls"] += 1
        form = parse_qs(request.content.decode())
        state["form"] = form
        if form["grant_type"] == ["refresh_token"]:
            assert form["refresh_token"] == [state["refresh_token"]]
            if state.get("unavailable"):
                raise httpx2.ConnectError("private endpoint details", request=request)
            if state.get("revoked"):
                return httpx2.Response(400, json={"error": "invalid_grant", "error_description": "private token details"})
        else:
            assert form["grant_type"] == ["authorization_code"]
            assert form["redirect_uri"] == ["http://localhost:13000/auth/callback"]
            challenge = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"][0].encode()).digest())
            assert challenge.rstrip(b"=").decode() == state["query"]["code_challenge"][0]
        common = {"iss": ISSUER, "sub": "subject-123", "exp": int(time.time()) + 900,
                  "iat": int(time.time())}
        identity = {**common, "aud": "iceberg-admin", "nonce": state["query"]["nonce"][0],
                    **state["id_changes"]}
        access = {**common, "aud": ["polaris", "account"],
                  "resource_access": {"iceberg-admin": {"roles": ["platform-admin"]}},
                  **state["access_changes"]}
        sign = lambda claims: jwt.encode({"alg": "RS256", "kid": "test"}, claims, state["signing_key"])
        state["refresh_token"] = f"private-refresh-{state['calls']}"
        return httpx2.Response(200, json={"token_type": "Bearer", "expires_in": 900,
                                        "refresh_token": state["refresh_token"], "refresh_expires_in": 1800,
                                        "id_token": sign(identity), "access_token": sign(access)})

    oidc.client.client_kwargs["transport"] = httpx2.MockTransport(wire)
    create_session = oidc.session
    state["sessions"] = []

    def capture_session(*args):
        session = create_session(*args)
        state["sessions"].append(session)
        return session

    oidc.session = capture_session
    return oidc, state


def start(client, state, target="/"):
    response = client.get("/auth/login", params={"return_to": target}, follow_redirects=False)
    assert response.status_code == 302
    state["query"] = parse_qs(urlsplit(response.headers["location"]).query)
    assert state["query"]["code_challenge_method"] == ["S256"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
    return "/auth/callback?code=one-use-code&state=" + state["query"]["state"][0]


@pytest.mark.parametrize("target,expected", [
    ("/#users?search=alice", "/#users?search=alice"),
    ("https://evil.test", "/"), ("//evil.test", "/"), ("/\\evil.test", "/"),
])
def test_sign_in_restores_only_local_portal_location(issuer, target, expected):
    oidc, state = issuer
    with TestClient(create_app(MemoryPolaris(), PASSWORD, oidc=oidc)) as client:
        response = client.get(start(client, state, target), follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == expected


def test_admin_login_pkce_single_use_and_logout(issuer):
    oidc, state = issuer
    with TestClient(create_app(MemoryPolaris(), PASSWORD, oidc=oidc)) as client:
        assert client.get("/api/session").json()["loginUrl"] == "/auth/login"
        assert client.post("/api/session", json={"password": PASSWORD}, headers=HEADERS).status_code == 403
        callback = start(client, state)
        response = client.get(callback, follow_redirects=False)
        assert response.status_code == 303, state.get("form")
        assert client.get("/api/teams").status_code == 200
        assert all("eyJ" not in value for value in client.cookies.values())
        assert client.get(callback, follow_redirects=False).status_code == 403
        assert state["calls"] == 1
        assert client.delete("/api/session").status_code == 403  # Logout remains CSRF protected.
        response = client.delete("/api/session", headers=HEADERS)
        assert response.json()["logoutUrl"].startswith(ISSUER)
        assert client.get("/api/teams").status_code == 401


def test_admin_renews_rotating_tokens_without_replacing_session(issuer):
    oidc, state = issuer
    with TestClient(create_app(MemoryPolaris(), PASSWORD, oidc=oidc)) as client:
        client.get(start(client, state), follow_redirects=False)
        cookie = client.cookies.get("portal_session")
        grant = state["sessions"][0]
        for expected_calls in (2, 3):
            previous_refresh = state["refresh_token"]
            grant.access_until = 0
            assert client.get("/api/teams").status_code == 200
            assert state["form"]["refresh_token"] == [previous_refresh]
            assert state["calls"] == expected_calls
            assert grant.refresh_token == state["refresh_token"] != previous_refresh
            assert client.cookies.get("portal_session") == cookie
            response = client.get("/api/session")
            assert response.json()["authenticated"]
            assert "private-refresh" not in response.text and "eyJ" not in response.text
            assert state["calls"] == expected_calls
        grant.expires = 0
        assert client.get("/api/teams").status_code == 401


@pytest.mark.parametrize("changes", [
    {"sub": "different-user"}, {"iss": "https://wrong.test"}, {"aud": "wrong-resource"},
    {"resource_access": {}}, {"exp": 1},
])
def test_invalid_refreshed_claims_end_admin_session(issuer, changes):
    oidc, state = issuer
    with TestClient(create_app(MemoryPolaris(), PASSWORD, oidc=oidc)) as client:
        client.get(start(client, state), follow_redirects=False)
        state["access_changes"] = changes
        state["sessions"][0].access_until = 0
        assert client.get("/api/teams").status_code == 401
        assert not client.get("/api/session").json()["authenticated"]


def test_renewal_retries_transient_failure_but_rejects_revocation(issuer):
    oidc, state = issuer
    with TestClient(create_app(MemoryPolaris(), PASSWORD, oidc=oidc)) as client:
        client.get(start(client, state), follow_redirects=False)
        state["sessions"][0].access_until = 0
        state["unavailable"] = True
        response = client.get("/api/teams")
        assert response.status_code == 503 and "private" not in response.text
        state["unavailable"] = False
        assert client.get("/api/teams").status_code == 200
        state["sessions"][0].access_until = 0
        state["revoked"] = True
        response = client.get("/api/teams")
        assert response.status_code == 401 and "private" not in response.text
        assert not client.get("/api/session").json()["authenticated"]


def test_concurrent_requests_refresh_only_once(issuer, monkeypatch):
    oidc, state = issuer
    with TestClient(create_app(MemoryPolaris(), PASSWORD, oidc=oidc)) as client:
        client.get(start(client, state), follow_redirects=False)
        grant = state["sessions"][0]
        grant.access_until = 0
        fetch = oidc.client.fetch_access_token

        async def delayed(**kwargs):
            await asyncio.sleep(0.01)
            return await fetch(**kwargs)

        monkeypatch.setattr(oidc.client, "fetch_access_token", delayed)

        async def renew():
            await asyncio.gather(*(oidc.renew(grant) for _ in range(5)))

        asyncio.run(renew())
        assert state["calls"] == 2


@pytest.mark.parametrize("kind,changes", [
    ("id_changes", {"iss": "https://wrong.test"}),
    ("id_changes", {"aud": "another-client"}),
    ("id_changes", {"nonce": "wrong-nonce"}),
    ("id_changes", {"exp": 1}),
    ("access_changes", {"iss": "https://wrong.test"}),
    ("access_changes", {"aud": "another-resource"}),
    ("access_changes", {"sub": "another-user"}),
    ("access_changes", {"exp": 1}),
    ("access_changes", {"resource_access": {"iceberg-users": {"roles": ["platform-admin"]}}}),
])
def test_reject_invalid_identity_and_admin_role(issuer, kind, changes):
    oidc, state = issuer
    state[kind] = changes
    with TestClient(create_app(MemoryPolaris(), PASSWORD, oidc=oidc)) as client:
        response = client.get(start(client, state), follow_redirects=False)
        assert response.status_code == 403
        assert client.get("/api/teams").status_code == 401


def test_reject_forged_signature_and_unbound_callback(issuer):
    oidc, state = issuer
    state["signing_key"] = RSAKey.generate_key(2048)
    with TestClient(create_app(MemoryPolaris(), PASSWORD, oidc=oidc)) as client:
        assert client.get("/auth/callback?state=forged&code=x").status_code == 403
        assert state["calls"] == 0
        assert client.get(start(client, state), follow_redirects=False).status_code == 403
        assert client.get("/api/teams").status_code == 401


def test_expired_or_wrong_state_never_exchanges_code(issuer):
    oidc, state = issuer
    with TestClient(create_app(MemoryPolaris(), PASSWORD, oidc=oidc)) as client:
        callback = start(client, state)
        transaction = next(iter(oidc.pending))
        oidc.pending[transaction] = (0, *oidc.pending[transaction][1:])
        assert client.get(callback).status_code == 403
        start(client, state)
        assert client.get("/auth/callback?state=wrong&code=x").status_code == 403
        assert state["calls"] == 0


def test_login_flood_cannot_block_another_client(issuer, monkeypatch):
    oidc, state = issuer
    app = create_app(MemoryPolaris(), PASSWORD, oidc=oidc)
    with (
        TestClient(app, client=("10.0.0.1", 50000)) as victim,
        TestClient(app, client=("10.0.0.2", 50000)) as attacker,
    ):
        callback = start(victim, state)
        for _ in range(PENDING_PER_CLIENT + 5):
            assert attacker.get("/auth/login", follow_redirects=False).status_code == 302
        assert [entry[2] for entry in oidc.pending.values()].count("10.0.0.2") == PENDING_PER_CLIENT
        assert victim.get(callback, follow_redirects=False).status_code == 303
        # A full table drops its oldest sign-in, so many clients together cannot refuse a login either.
        monkeypatch.setattr("server.oidc.PENDING_TOTAL", 3)
        for host in range(3, 8):
            with TestClient(app, client=(f"10.0.0.{host}", 50000)) as other:
                assert other.get("/auth/login", follow_redirects=False).status_code == 302
            assert len(oidc.pending) <= 3
        callback = start(victim, state)
        assert victim.get(callback, follow_redirects=False).status_code == 303


def test_user_link_identity_permissions_and_token_expiry(issuer):
    oidc, state = issuer
    provider = MemoryPolaris()
    team = provider.save_team(TeamInput(name="demo-team"))["id"]
    provider.create_database(DatabaseInput(name="demo-db", team=team))
    account = provider.create_user(UserInput(name="alice", memberships=members([team])))["user"]
    principal = provider.resources["principals"][account["id"]]
    principal["properties"].update({"portal.oidc-subject": "subject-123", "portal.oidc-issuer": ISSUER})
    state["access_changes"] = {"polaris": {"principal_id": 0, "principal_name": account["id"]}}
    directory = UserDirectory({})
    directory.metadata.http.close()
    directory.metadata = provider
    provider.http = httpx.Client()
    calls = []

    def wire(request):
        calls.append(request)
        assert request.headers["authorization"].startswith("Bearer eyJ")
        assert not request.url.path.endswith("/oauth/tokens")
        return httpx.Response(200, json={})

    directory.http.close()
    directory.http = httpx.Client(transport=httpx.MockTransport(wire))
    runtime = FakeRuntime()
    with TestClient(create_users(directory, runtime, oidc=oidc)) as client:
        assert client.get(start(client, state), follow_redirects=False).status_code == 303
        workspace = client.get("/api/workspace").json()
        assert workspace["user"]["id"] == account["id"]
        assert workspace["activeRole"] == "reader"
        assert [t["id"] for t in workspace["teams"]] == [team]
        assert len(calls) == 1
        # Renewal preserves the team, browser cookie and running notebook identity.
        cookie = client.cookies.get("iceberg_user_session")
        notebook = client.post("/api/notebooks", headers=HEADERS, json={"database": workspace["databases"][0]["id"]}).json()
        token_path = f"/internal/notebooks/{notebook['id']}/token"
        assert client.get(token_path).status_code == 401
        assert client.get(token_path, headers={"Authorization": "Bearer wrong"}).status_code == 401
        state["sessions"][0].access_until = 0
        response = client.get(token_path, headers={"Authorization": "Bearer hidden-runtime-token"})
        assert response.status_code == 200
        assert list(response.json()) == ["access_token"]
        assert response.headers["cache-control"] == "no-store"
        assert state["calls"] == 2
        assert client.cookies.get("iceberg_user_session") == cookie
        refreshed = client.get("/api/workspace").json()
        assert refreshed["activeTeam"] == team and refreshed["notebooks"][0]["id"] == notebook["id"]
        session = directory.login_oidc(
            {"sub": "subject-123", "iss": ISSUER, **state["access_changes"]}, "eyJ-token", 30, "test-session"
        )
        assert session.secret == session.client_id == ""
        session.token_until = 0
        with pytest.raises(ServiceError, match="expired"):
            directory.request(session, "/api/catalog/v1/config")
        principal["properties"]["portal.oidc-subject"] = "different-subject"
        assert client.get(token_path, headers={"Authorization": "Bearer hidden-runtime-token"}).status_code == 403
        assert client.get("/api/workspace").status_code == 403
        assert client.get(start(client, state), follow_redirects=False).status_code == 403
