"""Exercise real OIDC signature/claim validation with a simulated token endpoint."""

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
from server.oidc import OIDC
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
        return httpx2.Response(200, json={"token_type": "Bearer", "expires_in": 900,
                                        "id_token": sign(identity), "access_token": sign(access)})

    oidc.client.client_kwargs["transport"] = httpx2.MockTransport(wire)
    return oidc, state


def start(client, state):
    response = client.get("/auth/login", follow_redirects=False)
    assert response.status_code == 302
    state["query"] = parse_qs(urlsplit(response.headers["location"]).query)
    assert state["query"]["code_challenge_method"] == ["S256"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
    return "/auth/callback?code=one-use-code&state=" + state["query"]["state"][0]


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
        oidc.pending[transaction] = (0, oidc.pending[transaction][1])
        assert client.get(callback).status_code == 403
        start(client, state)
        assert client.get("/auth/callback?state=wrong&code=x").status_code == 403
        assert state["calls"] == 0


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
        session = directory.login_oidc(
            {"sub": "subject-123", "iss": ISSUER, **state["access_changes"]}, "eyJ-token", 30, "test-session"
        )
        assert session.secret == session.client_id == ""
        session.token_until = 0
        with pytest.raises(ServiceError, match="expired"):
            directory.request(session, "/api/catalog/v1/config")
        principal["properties"]["portal.oidc-subject"] = "different-subject"
        assert client.get("/api/workspace").status_code == 403
        assert client.get(start(client, state), follow_redirects=False).status_code == 403
