import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from server.bridge import CONTRACT, create_bridge_app
from server.models import DatabaseInput, ServiceError, ShareInput, ShareUpdate, TeamInput, UserInput
from test.conftest import SEMANTIC_MODEL, MemoryPolaris, members

ISSUER = "http://localhost:8080/realms/iceberg"
ENV = {"OIDC_ISSUER": ISSUER, "PLATFORM_EXTENSIONS": "dbt=http://localhost:3004,other=http://localhost:3009",
       "PLATFORM_LISTING_EXTENSIONS": "other"}


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
        def call(method, path, token=None, headers=None, **kwargs):
            headers = {**({"Authorization": f"Bearer {token}"} if token else {}), **(headers or {})}
            return client.request(method, "/bridge/v1" + path, headers=headers, **kwargs)

        yield SimpleNamespace(call=call, provider=provider, verifier=verifier, team=team, other=other, dev=dev,
                              foreign=foreign, admin=admin, writer=writer)


def test_discovery_is_public_and_complete(bridge):
    body = bridge.call("GET", "").json()
    assert body["contractVersion"] == "0.1.1"
    assert body["capabilities"] == ["user-context", "automation-principals", "automation-tokens", "shared-data",
                                    "listings"]
    assert body["oidc"] == {"issuer": ISSUER, "internalIssuer": ISSUER, "bridgeAudience": "iceberg-bridge"}
    assert body["catalog"]["internalUri"] == "http://polaris-control-plane:8181/api/catalog"
    assert body["network"]["name"] == "iceberg-platform_default"
    assert body["extensions"] == [{"id": "dbt", "origin": "http://localhost:3004", "listing": False},
                                  {"id": "other", "origin": "http://localhost:3009", "listing": True}]
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
    assert [(d["id"], d["sharedWithTeam"], d["recipientTeams"]) for d in shared] == [
        (bridge.foreign, bridge.team, [bridge.team])]
    assert shared[0]["sharedObjects"] == [{"kind": "table", "namespace": ["sales"], "name": "orders"}]
    assert shared[0]["ownerTeamName"] == "other-team"


def share_to(bridge, team, objects, name="partner"):
    return bridge.provider.create_share(ShareInput.model_validate({
        "database": bridge.foreign, "name": name, "external": False, "recipientTeam": team,
        "objects": objects,
    }), {"id": "portal-" + "c" * 32, "name": "carol"})["share"]


def test_me_combines_shares_received_by_several_teams(bridge):
    third = bridge.provider.save_team(TeamInput(name="third-team"))["id"]
    bridge.provider.update_memberships(bridge.admin["id"], {bridge.team: "admin", third: "reader"})
    model = {"kind": "semantic-model", "namespace": ["sales"], "name": "revenue"}
    orders = {"kind": "table", "namespace": ["sales"], "name": "orders"}
    share_to(bridge, third, [model, orders], name="to-third")
    share_to(bridge, bridge.team, [orders], name="to-data")
    [shared] = bridge.call("GET", "/me", "alice").json()["sharedDatabases"]
    assert shared["recipientTeams"] == [bridge.team, third] and shared["sharedWithTeam"] == bridge.team
    assert shared["sharedObjects"] == [orders, model]
    # A team member without the other membership sees only their own team's share.
    [only] = bridge.call("GET", "/me", "bob").json()["sharedDatabases"]
    assert only["recipientTeams"] == [bridge.team] and only["sharedObjects"] == [orders]


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


def test_scope_lists_received_shares_of_the_environment(bridge):
    id = bridge.call("POST", f"/teams/{bridge.team}/automation-principals", "alice",
                     json={"environment": "development"}).json()["id"]
    assert bridge.call("GET", f"/automation-principals/{id}", "service").json()["sharedDatabases"] == []
    model = {"kind": "semantic-model", "namespace": ["ops"], "name": "flights"}
    share = share_to(bridge, bridge.team, [model, {"kind": "table", "namespace": ["ops"], "name": "flights"}])
    scope = bridge.call("GET", f"/automation-principals/{id}", "service").json()
    [shared] = scope["sharedDatabases"]
    assert (shared["id"], shared["team"], shared["environment"]) == (bridge.foreign, bridge.other, "development")
    assert model in shared["sharedObjects"] and "storageLocation" not in shared
    assert f"/principal-roles/{id}-read/catalog-roles/{bridge.foreign}/{share['id']}" in bridge.provider.grants
    bridge.provider.delete_share(share["id"])
    assert bridge.call("GET", f"/automation-principals/{id}", "service").json()["sharedDatabases"] == []


def test_revocation(bridge):
    id = bridge.call("POST", f"/teams/{bridge.team}/automation-principals", "alice",
                     json={"environment": "development"}).json()["id"]
    assert bridge.call("DELETE", f"/automation-principals/{id}", "bob").json()["code"] == "forbidden_role"
    assert bridge.call("DELETE", f"/automation-principals/{id}", "other-service").status_code == 404
    assert bridge.call("DELETE", f"/automation-principals/{id}", "alice").json() == {"revoked": True}
    response = bridge.call("POST", f"/automation-principals/{id}/tokens", "service", json={"access": "read"})
    assert (response.status_code, response.json()["code"]) == (404, "not_found")
    assert bridge.provider.list_automation() == []


ORDERS = {"kind": "table", "namespace": ["sales"], "name": "orders"}
REPORT = {"kind": "view", "namespace": ["sales"], "name": "report"}
REVENUE = {"kind": "semantic-model", "namespace": ["sales"], "name": "revenue"}


def listed(bridge, objects=(ORDERS, REPORT, REVENUE), name="catalog", **extra):
    """A listing of the data team's development database, with its definitions in the catalog."""
    db = bridge.dev
    base = f"/api/catalog/v1/{db}/namespaces/sales"
    bridge.provider.catalog.update({
        f"{base}/tables/orders": {"metadata": {
            "table-uuid": "u-orders", "location": "s3://bucket/sales/orders", "current-schema-id": 0,
            "schemas": [{"schema-id": 0, "fields": [
                {"id": 1, "name": "id", "type": "long", "required": True, "doc": "Order number"},
                {"id": 2, "name": "customer", "required": False, "type": {"type": "struct", "fields": [
                    {"id": 3, "name": "name", "type": "string", "required": False}]}},
                {"id": 4, "name": "ordered_at", "type": "timestamptz", "required": False},
            ]}],
            "default-spec-id": 0,
            "partition-specs": [{"spec-id": 0, "fields": [
                {"name": "ordered_at_day", "source-id": 4, "field-id": 1000, "transform": "day"}]}],
            "current-snapshot-id": 7,
            "snapshots": [{"snapshot-id": 7, "timestamp-ms": 1, "summary": {"operation": "append", "total-records": "42"}}],
            "last-updated-ms": 1700000000000,
            "properties": {
                "comment": "All orders", "write.format.default": "parquet",
                "iceberg-data-platform.producer": "dbt",
                "iceberg-data-platform.lineage.inputs": json.dumps([
                    {"database": db, "namespace": ["sales"], "name": "report"},
                    {"database": db, "namespace": ["raw"], "name": "unlisted"}]),
            },
        }},
        f"{base}/views/report": {"metadata": {
            "view-uuid": "u-report", "current-version-id": 1, "properties": {"comment": "Monthly"},
            "versions": [{"version-id": 1, "schema-id": 0, "timestamp-ms": 5, "representations": [
                {"type": "sql", "dialect": "spark", "sql": "SELECT * FROM raw.unlisted WHERE secret = 1"}]}],
            "schemas": [{"schema-id": 0, "fields": [{"id": 1, "name": "total", "type": "decimal(10,2)",
                                                     "required": False}]}],
        }},
        f"/api/catalog/polaris/v1/{db}/namespaces/sales/semantic-models/revenue": SEMANTIC_MODEL,
    })
    return bridge.provider.create_share(ShareInput.model_validate({
        "database": db, "name": name, "external": False, "listed": True, "contact": "data@example.test",
        "objects": list(objects), **extra,
    }), {"id": "portal-" + "c" * 32, "name": "alice"})["share"]


@pytest.mark.parametrize(("token", "code"), [
    ("service", "listing_not_enabled"), ("alice", "service_required"), (None, "unauthorized")])
def test_only_a_listing_extension_reads_listings(bridge, token, code):
    share = listed(bridge)
    for path in ("/listings", f"/listings/{share['id']}"):
        assert bridge.call("GET", path, token).json()["code"] == code


def test_listings_name_only_listed_shares(bridge):
    share = listed(bridge)
    share_to(bridge, bridge.team, [ORDERS], name="team-only")
    body = bridge.call("GET", "/listings", "other-service").json()
    assert [(item["id"], item["databaseId"], item["environment"]) for item in body["listings"]] == [
        (share["id"], bridge.dev, "development")]
    version = body["listings"][0]["version"]
    bridge.provider.update_share(share["id"], ShareUpdate.model_validate({"description": "Orders per day"}))
    assert bridge.call("GET", "/listings", "other-service").json()["listings"][0]["version"] != version
    # Unlisting or revoking takes it out of the catalog.
    both = listed(bridge, name="both", recipientTeam=bridge.other)
    bridge.provider.update_share(both["id"], ShareUpdate.model_validate({"listed": False}))
    bridge.provider.delete_share(share["id"])
    assert bridge.call("GET", "/listings", "other-service").json()["listings"] == []


def test_a_listing_carries_metadata_never_data(bridge):
    share = listed(bridge)
    provider = bridge.provider
    provider.events.clear()
    response = bridge.call("GET", f"/listings/{share['id']}", "other-service")
    assert response.status_code == 200
    body = response.json()
    assert (body["name"], body["contact"], body["owner"]) == (
        "catalog", "data@example.test", {"team": bridge.team, "teamName": "data-team"})
    assert body["database"] == {"id": bridge.dev, "name": "sales", "environment": "development"}
    table, view, model = sorted(body["objects"], key=lambda o: o["kind"] == "semantic-model" and 2 or o["kind"] == "view")
    assert table["columns"][1] == {"id": 2, "name": "customer", "type": "struct<name: string>", "required": False,
                                   "doc": "", "children": [{"id": 3, "name": "name", "type": "string",
                                                            "required": False, "doc": "", "children": []}]}
    assert table["partitionFields"] == [{"name": "ordered_at_day", "sourceColumn": "ordered_at", "transform": "day"}]
    assert (table["comment"], table["rowCount"]) == ("All orders", 42)
    # Lineage keeps only what the same listing names.
    assert table["producer"]["inputs"] == [{"database": bridge.dev, "namespace": ["sales"], "name": "report"}]
    assert view["columns"][0]["type"] == "decimal(10,2)" and view["comment"] == "Monthly"
    assert [m["name"] for m in model["models"][0]["metrics"]] == ["event_count"]
    text = json.dumps(body)
    for secret in ("SELECT", "s3://", "write.format", "unlisted", "snapshots", "definition"):
        assert secret not in text
    assert body["unavailable"] == []
    # Read with a temporary role that holds no data privilege and is gone afterwards.
    grants = [b["grant"] for path, method, b in provider.events if path.endswith("/grants") and method == "PUT"]
    assert {g["privilege"] for g in grants} == {"TABLE_READ_PROPERTIES", "VIEW_READ_PROPERTIES", "SEMANTIC_MODEL_READ"}
    role = next(b["catalogRole"]["name"] for path, method, b in provider.events
                if path == f"/catalogs/{bridge.dev}/catalog-roles" and method == "POST")
    assert role.startswith("portal-listing-") and role not in provider.catalog_roles[bridge.dev]
    assert (f"/principal-roles/service_admin/catalog-roles/{bridge.dev}", "PUT", {"catalogRole": {"name": role}}) \
        in provider.events
    # Catalog reads ask for no storage credentials.
    assert all(method == "GET" for path, method, _ in provider.events if path.startswith("/api/catalog/"))


def test_a_listing_is_conditional_and_reports_dropped_objects(bridge):
    share = listed(bridge)
    first = bridge.call("GET", f"/listings/{share['id']}", "other-service")
    etag = first.headers["etag"]
    assert bridge.call("GET", f"/listings/{share['id']}", "other-service",
                       headers={"If-None-Match": etag}).status_code == 304
    bridge.provider.catalog[f"/api/catalog/v1/{bridge.dev}/namespaces/sales/views/report"] = None
    changed = bridge.call("GET", f"/listings/{share['id']}", "other-service", headers={"If-None-Match": etag})
    assert changed.status_code == 200 and changed.json()["unavailable"] == [REPORT]
    assert bridge.call("GET", f"/listings/share-{'f' * 32}", "other-service").status_code == 404
    unlisted = share_to(bridge, bridge.team, [ORDERS], name="team-only")
    assert bridge.call("GET", f"/listings/{unlisted['id']}", "other-service").status_code == 404


def test_a_listing_without_semantic_models_carries_its_tables(bridge):
    share = listed(bridge)
    bridge.provider.fail = lambda path, method, body: (
        ServiceError(406, "This feature is not enabled.") if "/semantic-models/" in path else None)
    body = bridge.call("GET", f"/listings/{share['id']}", "other-service").json()
    assert [o["kind"] for o in body["objects"]] == ["table", "view"]
    assert [o["kind"] for o in body["unavailable"]] == ["semantic-model"]


def test_a_listing_reads_only_its_own_share_grants(bridge):
    share = listed(bridge)
    other = share_to(bridge, bridge.team, [ORDERS], name="team-only")
    bridge.provider.events.clear()
    assert bridge.call("GET", f"/listings/{share['id']}", "other-service").status_code == 200
    read = {path for path, method, _ in bridge.provider.events if method == "GET" and path.endswith("/grants")}
    assert any(share["id"] in path for path in read) and not any(other["id"] in path for path in read)


def test_a_failed_listing_read_removes_its_temporary_role(bridge):
    share = listed(bridge)
    bridge.provider.fail = lambda path, method, body: path.endswith("/views/report")
    assert bridge.call("GET", f"/listings/{share['id']}", "other-service").status_code == 502
    bridge.provider.fail = None
    assert not [r for r in bridge.provider.catalog_roles[bridge.dev] if r.startswith("portal-listing-")]
    # A role a stopped bridge left behind is swept.
    bridge.provider.management(f"/catalogs/{bridge.dev}/catalog-roles", "POST",
                               {"catalogRole": {"name": "portal-listing-left"}})
    assert bridge.provider.sweep_listing_roles() == ["portal-listing-left"]


def test_committed_contract_matches_the_implementation(bridge):
    app = create_bridge_app(TokenPolaris(), FakeVerifier(), ENV)
    assert json.loads(CONTRACT.read_text()) == json.loads(json.dumps(app.openapi())), (
        "Regenerate contracts/bridge/v1/openapi.yaml with `python -m scripts.bridge_contract` "
        "and record the change in contracts/bridge/CHANGELOG.md"
    )
