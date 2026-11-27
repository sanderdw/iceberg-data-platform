"""The Keycloak bootstrap (server/keycloak_bootstrap.py) against an in-memory Keycloak Admin API."""

import json
import re
import sys

import httpx
import pytest

from scripts.setup import extension_client
from server import keycloak_bootstrap as bootstrap

PREFIX = "/admin/realms/iceberg"
SCOPES = ("basic", "profile", "roles", "offline_access", "kc-admin-api")


class FakeKeycloak:
    """The Admin API calls the bootstrap makes, over clients, users, roles and the user profile."""

    def __init__(self):
        self.clients = {}
        self.users = {}
        self.realm_roles = {}
        self.client_roles = {}
        self.master = {"realm": "master", "attributes": {"existing": "kept"}, "sslRequired": "external"}
        self.realm = None
        self.profile = {"attributes": [{"name": "username"}, {"name": "polaris_id", "permissions": {"edit": ["user"]}}]}
        self.calls = []
        for name in ("admin-cli", "realm-management", "iceberg-admin", "iceberg-users"):
            self.add_client(name, directAccessGrantsEnabled=name == "admin-cli")

    def add_client(self, client_id, **fields):
        id = f"c{len(self.clients) + 1}"
        self.clients[id] = {"id": id, "clientId": client_id, "enabled": True, **fields,
                            "mappers": [], "optional": set(), "default": set()}
        return self.clients[id]

    def add_user(self, username, **fields):
        id = f"u{len(self.users) + 1}"
        self.users[id] = {"id": id, "username": username, "attributes": {}, **fields,
                          "realmRoles": [], "clientRoles": {}}
        return self.users[id]

    def by_client_id(self, client_id):
        return next((c for c in self.clients.values() if c["clientId"] == client_id), None)

    @staticmethod
    def public(client):
        return {k: v for k, v in client.items() if k not in ("mappers", "optional", "default")}

    def handler(self, request):
        method, path = request.method, request.url.path
        self.calls.append((method, path))
        if path == "/realms/master/protocol/openid-connect/token":
            return httpx.Response(200, json={"access_token": "admin-token"})
        body = json.loads(request.content) if request.content else None
        assert request.headers["authorization"] == "Bearer admin-token"
        if path == "/admin/realms/master":
            if method == "PUT":
                self.master = body
            return httpx.Response(200 if method == "GET" else 204, json=self.master if method == "GET" else None)
        if path == PREFIX and method == "PUT":
            self.realm = body
            return httpx.Response(204)
        rest = path.removeprefix(PREFIX)
        if rest == "/clients":
            if method == "POST":
                mappers = body.pop("protocolMappers", [])
                self.add_client(body["clientId"], **body)["mappers"] = list(mappers)
                return httpx.Response(201)
            wanted = request.url.params.get("clientId")
            return httpx.Response(200, json=[self.public(c) for c in self.clients.values()
                                             if wanted is None or c["clientId"] == wanted])
        if rest == "/client-scopes":
            return httpx.Response(200, json=[{"id": f"s-{name}", "name": name} for name in SCOPES])
        if match := re.fullmatch(r"/clients/(\w+)", rest):
            client = self.clients[match[1]]
            assert method == "PUT" and "protocolMappers" not in body
            client.update({k: v for k, v in body.items() if k != "id"})
            return httpx.Response(204)
        if match := re.fullmatch(r"/clients/(\w+)/protocol-mappers/models", rest):
            client = self.clients[match[1]]
            if method == "POST":
                client["mappers"].append(body)
                return httpx.Response(201)
            return httpx.Response(200, json=client["mappers"])
        if match := re.fullmatch(r"/clients/(\w+)/(optional|default)-client-scopes/s-([\w-]+)", rest):
            self.clients[match[1]][match[2]].add(match[3])
            return httpx.Response(204)
        if match := re.fullmatch(r"/clients/(\w+)/service-account-user", rest):
            client = self.clients[match[1]]
            name = "service-account-" + client["clientId"]
            user = next((u for u in self.users.values() if u["username"] == name), None) or self.add_user(name)
            return httpx.Response(200, json={"id": user["id"], "username": name})
        if match := re.fullmatch(r"/clients/(\w+)/roles/([\w-]+)", rest):
            return httpx.Response(200, json={"id": f"r-{match[2]}", "name": match[2], "containerId": match[1]})
        if match := re.fullmatch(r"/roles/([\w-]+)", rest):
            return httpx.Response(200, json={"id": f"r-{match[1]}", "name": match[1]})
        if rest == "/users/profile":
            if method == "PUT":
                self.profile = body
                return httpx.Response(200, json=body)
            return httpx.Response(200, json=self.profile)
        if rest == "/users":
            if method == "POST":
                body.pop("credentials", None)
                self.add_user(body.pop("username"), **body)
                return httpx.Response(201)
            name = request.url.params["username"]
            return httpx.Response(200, json=[{k: v for k, v in u.items() if k not in ("realmRoles", "clientRoles")}
                                             for u in self.users.values() if u["username"] == name])
        if match := re.fullmatch(r"/users/(\w+)", rest):
            self.users[match[1]].update({k: v for k, v in body.items() if k != "id"})
            return httpx.Response(204)
        if match := re.fullmatch(r"/users/(\w+)/role-mappings/realm", rest):
            self.users[match[1]]["realmRoles"] += [r["name"] for r in body]
            return httpx.Response(204)
        if match := re.fullmatch(r"/users/(\w+)/role-mappings/clients/(\w+)", rest):
            roles = self.users[match[1]]["clientRoles"].setdefault(self.clients[match[2]]["clientId"], [])
            roles += [r["name"] for r in body]
            return httpx.Response(204)
        raise AssertionError(f"Unexpected {method} {path}")


class FakeProvider:
    """The Polaris calls of --demo: teams, databases and users, with principals' properties."""

    def __init__(self, env=None, storage=None):
        self.teams, self.databases, self.users, self.properties = [], [], [], {}

    def list_teams(self):
        return self.teams

    def save_team(self, data):
        team = {"id": "team-" + f"{len(self.teams):x}".rjust(32, "0"), "name": data.name}
        self.teams.append(team)
        return team

    def list_databases(self):
        return self.databases

    def create_database(self, data):
        self.databases.append({"name": data.name, "team": data.team})

    def list_users(self):
        return self.users

    def create_user(self, data):
        user = {"id": data.name, "name": data.name}
        self.users.append(user)
        self.properties[f"/principals/{data.name}"] = {}
        return {"user": user}

    def require(self, path):
        return {"name": path.rsplit("/", 1)[1], "properties": self.properties[path]}

    def update_properties(self, path, properties):
        self.properties[path] = properties

    def close(self):
        pass


ENV = {
    "OIDC_ISSUER": "http://localhost:8080/realms/iceberg",
    "OIDC_INTERNAL_ISSUER": "http://keycloak:8080/realms/iceberg",
    "KEYCLOAK_ADMIN_PASSWORD": "admin-password",
    "OIDC_MANAGEMENT_SECRET": "management-secret-value",
    "PLATFORM_EXTENSIONS": "dbt=http://localhost:3004",
    "PLATFORM_EXTENSION_SECRETS": "dbt=dbt-client-secret-value",
    **{f"DEMO_{a}_PASSWORD": f"demo-{a.lower()}-password" for a in ("ADMIN", "WRITER", "READER", "OUTSIDER")},
}


@pytest.fixture
def keycloak():
    return FakeKeycloak()


@pytest.fixture
def kc(keycloak):
    with httpx.Client(base_url="http://keycloak:8080", transport=httpx.MockTransport(keycloak.handler)) as client:
        client.headers["Authorization"] = "Bearer admin-token"
        yield client


@pytest.fixture
def run(keycloak, monkeypatch):
    """Run main() with the given arguments and environment against the fake Keycloak and Polaris."""
    provider = FakeProvider()
    transport = httpx.MockTransport(keycloak.handler)
    real_client = httpx.Client
    monkeypatch.setattr(bootstrap.httpx, "Client", lambda **kwargs: real_client(
        base_url=kwargs["base_url"], transport=transport))
    monkeypatch.setattr(bootstrap, "PolarisProvider", lambda env, storage: provider)
    monkeypatch.setattr(bootstrap, "RustFSStorage", lambda env: None)

    def main(*args, **env):
        for key, value in {**ENV, **env}.items():
            if value is None:
                monkeypatch.delenv(key, raising=False)
            else:
                monkeypatch.setenv(key, value)
        monkeypatch.setattr(sys, "argv", ["keycloak_bootstrap", *args])
        bootstrap.main()
        return provider

    return main


def test_upsert_creates_a_missing_client_with_its_mappers_and_optional_scopes(keycloak, kc):
    config = extension_client("dbt", "http://localhost:3004", "secret")
    config["optionalClientScopes"] = ["offline_access"]
    bootstrap.upsert_client(kc, PREFIX, config)
    client = keycloak.by_client_id("ext-dbt")
    assert [m["name"] for m in client["mappers"]] == [m["name"] for m in config["protocolMappers"]]
    assert client["optional"] == {"offline_access"}
    assert "protocolMappers" not in client


def test_upsert_updates_an_existing_client_and_adds_only_its_missing_mappers(keycloak, kc):
    config = extension_client("dbt", "http://localhost:3004", "new-secret")
    existing = keycloak.add_client("ext-dbt", secret="old-secret", redirectUris=["http://old/auth/callback"])
    existing["mappers"] = [config["protocolMappers"][0]]
    bootstrap.upsert_client(kc, PREFIX, config)
    assert keycloak.by_client_id("ext-dbt") is existing
    assert existing["secret"] == "new-secret" and existing["redirectUris"] == config["redirectUris"]
    names = [m["name"] for m in existing["mappers"]]
    assert sorted(names) == sorted(m["name"] for m in config["protocolMappers"])
    assert len(names) == len(set(names))
    assert sum(1 for method, path in keycloak.calls if method == "POST" and path == PREFIX + "/clients") == 0


def test_an_extension_without_its_client_secret_is_refused(kc):
    env = {"PLATFORM_EXTENSIONS": "dbt=http://localhost:3004,ingestion=http://localhost:3008",
           "PLATFORM_EXTENSION_SECRETS": "dbt=dbt-client-secret-value"}
    with pytest.raises(RuntimeError, match="No client secret for extension ingestion"):
        bootstrap.reconcile_extensions(kc, PREFIX, env)


def test_clients_of_removed_extensions_are_disabled_and_others_left_alone(keycloak, kc):
    removed = keycloak.add_client("ext-ingestion")
    removed_mcp = keycloak.add_client("ext-ingestion-mcp")
    already_off = keycloak.add_client("ext-old", enabled=False, secret="kept")
    unrelated = keycloak.add_client("my-app")
    wanted = bootstrap.reconcile_extensions(kc, PREFIX, ENV)
    assert wanted == {"ext-dbt", "ext-dbt-mcp"}
    assert keycloak.by_client_id("ext-dbt")["enabled"] and keycloak.by_client_id("ext-dbt-mcp")["enabled"]
    assert not removed["enabled"] and not removed_mcp["enabled"]
    assert already_off["secret"] == "kept" and unrelated["enabled"]
    disabled = [path for method, path in keycloak.calls if method == "PUT"]
    assert PREFIX + "/clients/" + already_off["id"] not in disabled


def test_the_provisioner_can_use_the_admin_api_and_manage_users(keycloak, run):
    admin = keycloak.add_user("platform-admin")
    run()
    provisioner = keycloak.by_client_id("iceberg-provisioner")
    assert provisioner["secret"] == ENV["OIDC_MANAGEMENT_SECRET"] and provisioner["serviceAccountsEnabled"]
    assert not provisioner["standardFlowEnabled"] and not provisioner["directAccessGrantsEnabled"]
    # Keycloak 26.8.2 accepts Admin API tokens only with this scope.
    assert "kc-admin-api" in provisioner["default"]
    service = next(u for u in keycloak.users.values() if u["username"] == "service-account-iceberg-provisioner")
    assert service["clientRoles"]["realm-management"] == ["manage-users", "view-users", "query-users", "view-clients"]
    assert not keycloak.by_client_id("admin-cli")["directAccessGrantsEnabled"]
    assert admin["realmRoles"] == ["default-roles-iceberg"]
    for name in ("iceberg-admin", "iceberg-users", "iceberg-mcp", "iceberg-cli", "ext-dbt", "ext-dbt-mcp"):
        assert "basic" in keycloak.by_client_id(name)["default"], name
    assert keycloak.realm == bootstrap.REALM_SECURITY
    binding = {a["name"]: a for a in keycloak.profile["attributes"]}
    for name in ("polaris_id", "polaris_name", "iceberg_provisioning_id"):
        assert binding[name]["permissions"] == {"view": ["admin"], "edit": ["admin"]}
    assert [a["name"] for a in keycloak.profile["attributes"]].count("polaris_id") == 1
    # Without KEYCLOAK_ADMIN_ORIGIN the master realm is not touched.
    assert ("PUT", "/admin/realms/master") not in keycloak.calls


def test_a_second_run_updates_the_provisioner_instead_of_creating_another(keycloak, run):
    run()
    run(OIDC_MANAGEMENT_SECRET="rotated-management-secret")
    provisioners = [c for c in keycloak.clients.values() if c["clientId"] == "iceberg-provisioner"]
    assert len(provisioners) == 1 and provisioners[0]["secret"] == "rotated-management-secret"


def test_hosted_mcp_callbacks_reach_every_mcp_client_on_each_run(keycloak, run):
    claude = "https://claude.ai/api/mcp/auth_callback"
    run(MCP_REDIRECT_URIS=claude)
    for name in ("iceberg-mcp", "ext-dbt-mcp"):
        assert claude in keycloak.by_client_id(name)["redirectUris"], name
    assert claude not in keycloak.by_client_id("ext-dbt")["redirectUris"]
    # Taking the setting away removes the callback again on the next run.
    run(MCP_REDIRECT_URIS=None)
    for name in ("iceberg-mcp", "ext-dbt-mcp"):
        assert claude not in keycloak.by_client_id(name)["redirectUris"], name


@pytest.mark.parametrize("origin,ssl", [("http://localhost:8080", "none"), ("https://kc.example.com", "external")])
def test_the_admin_origin_becomes_the_master_realms_front_end(keycloak, run, origin, ssl):
    run(KEYCLOAK_ADMIN_ORIGIN=origin)
    assert keycloak.master["attributes"] == {"existing": "kept", "frontendUrl": origin}
    assert keycloak.master["sslRequired"] == ssl


def test_demo_data_needs_the_demo_passwords(run):
    with pytest.raises(SystemExit):
        run("--demo", DEMO_READER_PASSWORD=None)


def test_demo_accounts_are_created_and_linked_to_their_principals(keycloak, run):
    provider = run("--demo")
    users = {u["username"]: u for u in keycloak.users.values()}
    assert {"demo-admin", "demo-writer", "demo-reader", "demo-outsider"} <= set(users)
    assert users["demo-admin"]["clientRoles"] == {"iceberg-admin": ["platform-admin"]}
    assert {t["name"] for t in provider.teams} == {"demo-team", "demo-private"}
    assert {d["name"] for d in provider.databases} == {"demo-demo", "demo-private"}
    for name in ("demo-admin", "demo-writer", "demo-reader"):
        assert provider.properties[f"/principals/{name}"]["portal.oidc-subject"] == users[name]["id"]
        assert users[name]["attributes"]["polaris_name"] == [name]
    # Running it again changes nothing and refuses nothing.
    run("--demo")
    assert len([u for u in keycloak.users.values() if u["username"] == "demo-writer"]) == 1


def test_demo_refuses_to_rebind_a_principal_linked_to_another_account(keycloak, run, monkeypatch):
    provider = FakeProvider()
    provider.create_user(type("User", (), {"name": "demo-admin"}))
    provider.properties["/principals/demo-admin"] = {"portal.oidc-subject": "someone-else"}
    monkeypatch.setattr(bootstrap, "PolarisProvider", lambda env, storage: provider)
    with pytest.raises(RuntimeError, match="refusing to rebind"):
        run("--demo")
    assert provider.properties["/principals/demo-admin"] == {"portal.oidc-subject": "someone-else"}
