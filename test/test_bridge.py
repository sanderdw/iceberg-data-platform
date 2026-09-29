import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from server.bridge import CONTRACT, create_bridge_app
from server.models import DatabaseInput, ServiceError, ShareInput, TeamInput, UserInput
from test.conftest import MemoryPolaris, members

ISSUER = "http://localhost:8080/realms/iceberg"
ENV = {"OIDC_ISSUER": ISSUER, "PLATFORM_EXTENSIONS": "dbt=http://localhost:3004,other=http://localhost:3009"}


class FakeVerifier:
    """Token string → claims; anything else is an invalid token."""

    def __init__(self):
        self.tokens = {}

    async def claims(self, token):
        if token not in self.tokens:
            raise ValueError("invalid")
        return self.tokens[token]


class TokenPolaris(MemoryPolaris):
    def __init__(self):
        super().__init__()
        self.issued, self.resets, self.valid = [], 0, set()

    def reset_automation_secret(self, id):
        self.resets += 1
        secret = f"secret-{self.resets}"
        self.valid = {secret}
        return {"clientId": id, "clientSecret": secret}

    def principal_token(self, client_id, secret, role):
        if secret not in self.valid:
            raise ServiceError(401, "refused")
        self.issued.append(role)
        return {"access_token": f"polaris-token-for-{role}", "expires_in": 3600}

    def close(self):
        pass


@pytest.fixture
def bridge():
    provider, verifier = TokenPolaris(), FakeVerifier()
    team = provider.save_team(TeamInput(name="data-team"))["id"]
    other = provider.save_team(TeamInput(name="other-team"))["id"]
    dev = provider.create_database(DatabaseInput(name="sales", team=team))["id"]
    provider.create_database(DatabaseInput(name="sales", team=team, environment="production"))
    foreign = provider.create_database(DatabaseInput(name="partner", team=other))["id"]

    def person(name, role, teams=(team,)):
        user = provider.create_user(UserInput(name=name, memberships=members(list(teams), role)))["user"]
        path = f"/principals/{user['id']}"
        properties = provider.require(path)["properties"]
        provider.update_properties(path, {**properties, "portal.oidc-subject": "sub-" + name,
                                          "portal.oidc-issuer": ISSUER})
        verifier.tokens[name] = {"iss": ISSUER, "sub": "sub-" + name, "azp": "ext-dbt-mcp",
                                 "polaris": {"principal_name": user["id"], "principal_id": 0}}
        return user

    admin, writer = person("alice", "admin"), person("bob", "writer")
    person("carol", "reader", (other,))
    verifier.tokens["service"] = {"iss": ISSUER, "sub": "svc", "azp": "ext-dbt",
                                  "preferred_username": "service-account-ext-dbt"}
    verifier.tokens["other-service"] = {"iss": ISSUER, "sub": "svc2", "azp": "ext-other",
                                        "preferred_username": "service-account-ext-other"}
    verifier.tokens["stranger"] = {"iss": ISSUER, "sub": "x", "azp": "iceberg-users",
                                   "polaris": {"principal_name": admin["id"], "principal_id": 0}}
    with TestClient(create_bridge_app(provider, verifier, ENV)) as client:
        def call(method, path, token=None, **kwargs):
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            return client.request(method, "/bridge/v1" + path, headers=headers, **kwargs)

        yield SimpleNamespace(call=call, provider=provider, verifier=verifier, team=team, other=other, dev=dev,
                              foreign=foreign, admin=admin, writer=writer)


def test_discovery_is_public_and_complete(bridge):
    body = bridge.call("GET", "").json()
    assert body["contractVersion"] == "1.0.0"
    assert body["oidc"] == {"issuer": ISSUER, "internalIssuer": ISSUER, "bridgeAudience": "iceberg-bridge"}
    assert body["catalog"]["internalUri"] == "http://polaris-control-plane:8181/api/catalog"
    assert body["network"]["name"] == "iceberg-platform_default"
    assert body["extensions"] == [{"id": "dbt", "origin": "http://localhost:3004"},
                                  {"id": "other", "origin": "http://localhost:3009"}]
    assert bridge.call("GET", "/health").json() == {"status": "ok"}


@pytest.mark.parametrize(("token", "status", "code"), [
    (None, 401, "unauthorized"),
    ("forged", 401, "unauthorized"),
    ("stranger", 403, "unknown_extension"),
    ("service", 403, "user_required"),
])
def test_user_endpoints_require_a_linked_user_of_an_extension(bridge, token, status, code):
    response = bridge.call("GET", "/me", token)
    assert (response.status_code, response.json()["code"]) == (status, code)


def test_me_returns_teams_and_databases(bridge):
    body = bridge.call("GET", "/me", "alice").json()
    assert body["user"] == {"id": bridge.admin["id"], "name": "alice"}
    assert body["memberships"] == [{"team": bridge.team, "teamName": "data-team", "role": "admin"}]
    assert [(d["name"], d["environment"]) for d in body["databases"]] == [
        ("sales", "development"), ("sales", "production")]
    assert body["sharedDatabases"] == []
    # A team share makes the database visible, not writable.
    bridge.provider.create_share(ShareInput.model_validate({
        "database": bridge.foreign, "name": "partner", "external": False, "recipientTeam": bridge.team,
        "objects": [{"kind": "table", "namespace": ["sales"], "name": "orders"}],
    }), {"id": "portal-" + "c" * 32, "name": "carol"})
    shared = bridge.call("GET", "/me", "alice").json()["sharedDatabases"]
    assert [(d["id"], d["sharedWithTeam"]) for d in shared] == [(bridge.foreign, bridge.team)]


def test_a_relinked_identity_is_refused(bridge):
    path = f"/principals/{bridge.admin['id']}"
    properties = bridge.provider.require(path)["properties"]
    bridge.provider.update_properties(path, {**properties, "portal.oidc-subject": "someone-else"})
    response = bridge.call("GET", "/me", "alice")
    assert (response.status_code, response.json()["code"]) == (403, "not_linked")


def test_team_admins_enable_an_extension_idempotently(bridge):
    path = f"/teams/{bridge.team}/automation-principals"
    denied = bridge.call("POST", path, "bob", json={"environment": "development"})
    assert (denied.status_code, denied.json()["code"]) == (403, "forbidden_role")
    outsider = bridge.call("POST", f"/teams/{bridge.other}/automation-principals", "alice",
                           json={"environment": "development"})
    assert (outsider.status_code, outsider.json()["code"]) == (403, "not_a_member")
    created = bridge.call("POST", path, "alice", json={"environment": "development"})
    assert created.status_code == 201
    item = created.json()
    assert (item["extension"], item["environment"], item["createdBy"]) == ("dbt", "development", "alice")
    assert bridge.call("POST", path, "alice", json={"environment": "development"}).status_code == 200
    assert bridge.call("POST", path, "alice", json={"environment": "staging"}).status_code == 422
    # Members see this extension's principals of the team; the extension sees all of its own.
    assert [i["id"] for i in bridge.call("GET", path, "bob").json()["automationPrincipals"]] == [item["id"]]
    assert [i["id"] for i in bridge.call("GET", "/automation-principals", "service").json()[
        "automationPrincipals"]] == [item["id"]]
    assert bridge.call("GET", "/automation-principals", "other-service").json()["automationPrincipals"] == []


def test_scope_and_tokens_for_the_extension_only(bridge):
    item = bridge.call("POST", f"/teams/{bridge.team}/automation-principals", "alice",
                       json={"environment": "development"}).json()
    id = item["id"]
    scope = bridge.call("GET", f"/automation-principals/{id}", "service").json()
    assert [(d["id"], d["name"]) for d in scope["databases"]] == [(bridge.dev, "sales")]
    assert bridge.call("GET", f"/automation-principals/{id}", "alice").json()["code"] == "service_required"
    assert bridge.call("GET", f"/automation-principals/{id}", "other-service").status_code == 404

    read = bridge.call("POST", f"/automation-principals/{id}/tokens", "service",
                       json={"access": "read", "purpose": "compile"}).json()
    write = bridge.call("POST", f"/automation-principals/{id}/tokens", "service", json={"access": "write"}).json()
    assert read["accessToken"] == f"polaris-token-for-{id}-read" and read["access"] == "read"
    assert write["accessToken"] == f"polaris-token-for-{id}" and write["expiresIn"] == 3600
    assert write["catalog"] == {"internalUri": "http://polaris-control-plane:8181/api/catalog"}
    # The secret is reset once and kept in memory; a refused secret is reset once more.
    assert bridge.provider.resets == 1
    bridge.provider.valid = set()
    assert bridge.call("POST", f"/automation-principals/{id}/tokens", "service",
                       json={"access": "read"}).status_code == 200
    assert bridge.provider.resets == 2
    assert bridge.call("POST", f"/automation-principals/{id}/tokens", "other-service",
                       json={"access": "write"}).status_code == 404
    assert bridge.call("POST", f"/automation-principals/{id}/tokens", "service",
                       json={"access": "admin"}).status_code == 422


def test_revocation(bridge):
    id = bridge.call("POST", f"/teams/{bridge.team}/automation-principals", "alice",
                     json={"environment": "development"}).json()["id"]
    assert bridge.call("DELETE", f"/automation-principals/{id}", "bob").json()["code"] == "forbidden_role"
    assert bridge.call("DELETE", f"/automation-principals/{id}", "other-service").status_code == 404
    assert bridge.call("DELETE", f"/automation-principals/{id}", "alice").json() == {"revoked": True}
    response = bridge.call("POST", f"/automation-principals/{id}/tokens", "service", json={"access": "read"})
    assert (response.status_code, response.json()["code"]) == (404, "not_found")
    assert bridge.provider.list_automation() == []


def test_committed_contract_matches_the_implementation(bridge):
    app = create_bridge_app(TokenPolaris(), FakeVerifier(), ENV)
    assert json.loads(CONTRACT.read_text()) == json.loads(json.dumps(app.openapi())), (
        "Regenerate contracts/bridge/v1/openapi.yaml with `python -m scripts.bridge_contract` "
        "and record the change in contracts/bridge/CHANGELOG.md"
    )
