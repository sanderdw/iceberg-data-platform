"""Resumable identity provisioning, explicit linking and platform-only revocation."""

import copy
import time
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.identity import CreateIdentity, KeycloakAdmin, LinkIdentity, UserManagement
from server.models import DatabaseInput, ServiceError, TeamInput, UserInput
from server.polaris import PolarisProvider
from test.conftest import HEADERS, PASSWORD, MemoryPolaris


@pytest.mark.parametrize("error_type,success", [("GRANT_NOT_FOUND", True), ("INVALID_REQUEST", False)])
def test_polaris_removal_accepts_only_the_explicit_absent_grant_error(error_type, success):
    provider = PolarisProvider({}, Mock())
    provider.http.close()
    provider.http = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
        400, json={"error": {"type": error_type, "code": 400}},
    )))
    provider.token, provider.token_until = "test-token", time.monotonic() + 60
    try:
        if success:
            provider.remove("/principals/test/principal-roles/test")
        else:
            with pytest.raises(ServiceError):
                provider.remove("/principals/test/principal-roles/test")
    finally:
        provider.close()


class MemoryKeycloak:
    issuer = "http://localhost:18080/realms/iceberg"
    binding = staticmethod(KeycloakAdmin.binding)

    def __init__(self):
        self.accounts = {}
        self.passwords = {}
        self.admins = set()
        self.fail = None
        self.events = []

    def close(self):
        pass

    def account(self, subject, missing_ok=False):
        return self.request("/users/" + subject, missing_ok=missing_ok)

    def administrator(self, subject):
        return subject in self.admins

    def find(self, username):
        return [copy.deepcopy(a) for a in self.accounts.values() if a["username"] == username]

    def request(self, path, method="GET", body=None, params=None, *, missing_ok=False):
        self.events.append((path, method, copy.deepcopy(body)))
        if self.fail and self.fail(path, method, body):
            raise ServiceError(503, "Simulated Keycloak outage")
        if path == "/users" and method == "POST":
            if self.find(body["username"]):
                raise ServiceError(409, "Duplicate account")
            subject = "subject-" + str(len(self.accounts))
            self.accounts[subject] = {"id": subject, **copy.deepcopy(body)}
            return None
        parts = path.strip("/").split("/")
        subject = parts[1]
        if subject not in self.accounts:
            if missing_ok:
                return None
            raise ServiceError(404, "Missing Keycloak account")
        if path.endswith("/reset-password"):
            self.passwords[subject] = body.copy()
        elif method == "GET":
            return copy.deepcopy(self.accounts[subject])
        elif method == "PUT":
            self.accounts[subject].update(copy.deepcopy(body))
        else:
            raise AssertionError("Unexpected Keycloak operation")


@pytest.fixture
def identities():
    provider = MemoryPolaris()
    team = provider.save_team(TeamInput(name="demo-team"))["id"]
    provider.create_database(DatabaseInput(name="demo", team=team))
    kc = MemoryKeycloak()
    manager = UserManagement(kc)
    data = CreateIdentity(name="alice", role="writer", teams=[team], email="alice@example.test",
                          first_name="Alice", last_name="Analyst")
    return SimpleNamespace(provider=provider, kc=kc, manager=manager, data=data)


def test_create_returns_only_temporary_keycloak_password(identities):
    s = identities
    result = s.manager.create(s.provider, s.data, "new")
    user = result["user"]
    account, = s.kc.accounts.values()
    assert account["enabled"]
    assert account["attributes"]["polaris_name"] == [user["id"]]
    assert s.kc.passwords[account["id"]]["temporary"] is True
    password = result["identity"]["temporaryPassword"]
    assert len(password) >= 24
    assert password not in str(s.provider.resources)
    assert "credentials" not in result
    assert s.manager.users(s.provider)[0]["identity"]["status"] == "linked"
    assert "temporaryPassword" not in s.manager.resume(s.provider, user["id"])["identity"]


def test_existing_account_link_is_explicit_and_preserves_credentials(identities):
    s = identities
    s.kc.accounts["external-sub"] = {"id": "external-sub", "username": "external-name",
                                     "enabled": True, "attributes": {"other_app": ["keep"]}}
    data = LinkIdentity(name="local-name", role="reader", teams=s.data.teams, subject="external-sub")
    result = s.manager.create(s.provider, data, "link")
    assert result["identity"]["username"] == "external-name"
    assert "temporaryPassword" not in result["identity"]
    assert not s.kc.passwords
    assert s.kc.accounts["external-sub"]["attributes"]["other_app"] == ["keep"]
    with pytest.raises(ServiceError, match="already linked"):
        s.manager.create(s.provider, data.model_copy(update={"name": "another-name"}), "link")


def test_link_existing_polaris_user_without_changing_team_or_role(identities):
    s = identities
    user = s.provider.create_user(UserInput(name="legacy", role="reader", teams=s.data.teams))["user"]
    s.kc.accounts["legacy-sub"] = {"id": "legacy-sub", "username": "legacy-kc", "enabled": True}
    result = s.manager.link(s.provider, user["id"], "legacy-sub")
    assert result["user"]["teams"] == user["teams"]
    assert result["user"]["role"] == "reader"
    assert len(s.provider.list_users()) == 1


def test_create_never_claims_a_same_name_existing_account(identities):
    s = identities
    s.kc.accounts["someone"] = {"id": "someone", "username": "alice", "enabled": True}
    with pytest.raises(ServiceError, match="already exists"):
        s.manager.create(s.provider, s.data, "new")
    assert not s.provider.list_users()
    assert not s.kc.events


@pytest.mark.parametrize("failure", ["create", "password", "activate", "checkpoint"])
def test_provisioning_failure_is_visible_and_retry_reuses_the_same_accounts(identities, failure):
    s = identities
    if failure == "checkpoint":
        s.provider.fail = lambda path, method, body: method == "PUT" and body and body.get("properties", {}).get("portal.identity-status") == "linked"
    else:
        s.kc.fail = lambda path, method, body: method in ("POST", "PUT") and (
            (failure == "create" and path == "/users")
            or (failure == "password" and path.endswith("reset-password"))
            or (failure == "activate" and body == {"enabled": True})
        )
    with pytest.raises(ServiceError, match="Setup is incomplete"):
        s.manager.create(s.provider, s.data, "new")
    user, = s.manager.users(s.provider)
    assert user["identity"]["status"] == "pending"
    mutations = [event for event in s.provider.events if event[1] != "GET"]
    assert mutations[-1][0] == f"/principals/{user['id']}/principal-roles/{user['id']}"
    s.kc.fail = s.provider.fail = None
    s.manager.resume(s.provider, user["id"])
    assert len(s.provider.list_users()) == len(s.kc.accounts) == 1
    assert s.manager.users(s.provider)[0]["identity"]["status"] == "linked"


def test_lost_keycloak_create_response_recovers_using_ownership_marker(identities):
    s = identities
    original = s.kc.request

    def lost_response(path, method="GET", body=None, **kwargs):
        result = original(path, method, body, **kwargs)
        if path == "/users" and method == "POST":
            raise ServiceError(503, "Response lost")
        return result

    s.kc.request = lost_response
    with pytest.raises(ServiceError, match="Setup is incomplete"):
        s.manager.create(s.provider, s.data, "new")
    s.kc.request = original
    user, = s.provider.list_users()
    s.manager.resume(s.provider, user["id"])
    assert len(s.kc.accounts) == 1


def test_lost_final_checkpoint_response_preserves_completed_setup(identities):
    s = identities
    original = s.provider.update_properties

    def lost_response(path, properties):
        result = original(path, properties)
        if properties.get("portal.identity-status") == "linked":
            raise ServiceError(503, "Response lost")
        return result

    s.provider.update_properties = lost_response
    result = s.manager.create(s.provider, s.data, "new")
    assert result["identity"]["temporaryPassword"]
    assert s.manager.users(s.provider)[0]["identity"]["status"] == "linked"
    mutations = [event for event in s.provider.events if event[1] != "GET"]
    assert mutations[-1][1] == "PUT"


def test_revocation_retries_cleanup_without_deleting_keycloak_account(identities):
    s = identities
    user = s.manager.create(s.provider, s.data, "new")["user"]
    account, = s.kc.accounts.values()
    account["attributes"]["another-app"] = ["keep"]
    s.kc.fail = lambda path, method, body: method == "PUT"
    with pytest.raises(ServiceError, match="Data access is revoked"):
        s.manager.revoke(s.provider, user["id"])
    assert user["id"] not in s.provider.resources["principal-roles"]
    assert s.manager.users(s.provider)[0]["identity"]["status"] == "revoking"
    s.kc.fail = None
    s.manager.revoke(s.provider, user["id"])
    assert not s.provider.list_users()
    assert account["enabled"]
    assert account["attributes"] == {"another-app": ["keep"]}
    assert not any(method == "DELETE" for _, method, _ in s.kc.events)


def test_reset_is_explicit_and_restricted_to_portal_created_accounts(identities):
    s = identities
    result = s.manager.create(s.provider, s.data, "new")
    reset = s.manager.reset_password(s.provider, result["user"]["id"])
    assert reset["identity"]["temporaryPassword"] != result["identity"]["temporaryPassword"]
    principal = s.provider.resources["principals"][result["user"]["id"]]
    principal["properties"]["portal.identity-mode"] = "link"
    with pytest.raises(ServiceError, match="only available"):
        s.manager.reset_password(s.provider, result["user"]["id"])


def test_administrator_revocation_requires_explicit_role_removal(identities):
    s = identities
    user = s.manager.create(s.provider, s.data, "new")["user"]
    s.kc.admins.update(s.kc.accounts)
    with pytest.raises(ServiceError, match="administrator"):
        s.manager.revoke(s.provider, user["id"])
    assert s.manager.users(s.provider)[0]["identity"]["status"] == "linked"


def test_identity_api_is_admin_and_csrf_protected_and_disables_legacy_creation(identities):
    s = identities
    with TestClient(create_app(s.provider, PASSWORD, user_management=s.manager)) as client:
        assert client.get("/api/identity/accounts?username=alice").status_code == 401
        assert client.post("/api/identity/users", json=s.data.model_dump(), headers=HEADERS).status_code == 401
        client.post("/api/session", json={"password": PASSWORD}, headers=HEADERS)
        assert client.post("/api/identity/users", json=s.data.model_dump()).status_code == 403
        assert client.post("/api/users", json={"name": "alice", "role": "reader", "teams": s.data.teams}, headers=HEADERS).status_code == 409
        result = client.post("/api/identity/users", json=s.data.model_dump(), headers=HEADERS)
        assert result.status_code == 201
        assert "clientSecret" not in result.text
        assert "temporaryPassword" not in client.get("/api/overview").text
        assert client.get("/api/session").json()["userManagement"] == "keycloak"


def test_keycloak_admin_uses_service_credentials_and_sanitizes_errors():
    kc = KeycloakAdmin({"OIDC_ISSUER": "http://localhost:18080/realms/iceberg",
                        "OIDC_INTERNAL_ISSUER": "http://keycloak:8080/realms/iceberg",
                        "OIDC_MANAGEMENT_SECRET": "private-service-secret"})
    calls = []

    def wire(request):
        calls.append(request)
        if request.url.path.endswith("/token"):
            assert b"grant_type=client_credentials" in request.content
            assert b"username=" not in request.content
            return httpx.Response(200, json={"access_token": "service-token", "expires_in": 60})
        assert request.headers["authorization"] == "Bearer service-token"
        return httpx.Response(500, text="private-service-secret service-token")

    kc.http.close()
    kc.http = httpx.Client(transport=httpx.MockTransport(wire))
    try:
        with pytest.raises(ServiceError) as error:
            kc.find("alice")
        assert error.value.status == 503
        assert "secret" not in str(error.value)
        assert len(calls) == 2
    finally:
        kc.close()
