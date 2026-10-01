"""Clean setup creates only the administrator and always requires Keycloak."""

import json
import shutil
from unittest.mock import Mock

import pytest

from scripts.setup import ROOT, read_env, setup
from server import entrypoints


@pytest.fixture
def config_root(tmp_path):
    shutil.copyfile(ROOT / ".env.example", tmp_path / ".env.example")
    return tmp_path


def test_keycloak_setup_is_repeatable_and_does_not_seed_demo_data(config_root):
    setup(config_root)
    config = config_root / ".env"
    original = config.read_bytes()
    values = read_env(config)
    document = json.loads((config_root / values["KEYCLOAK_IMPORT_FILE"]).read_text())
    assert values["OIDC_ISSUER"] == "http://localhost:8080/realms/iceberg"
    assert [u["username"] for u in document["users"]] == ["platform-admin"]
    assert document["users"][0]["credentials"][0]["temporary"] is True
    assert "UPDATE_PASSWORD" in document["users"][0]["requiredActions"]
    assert document["users"][0]["realmRoles"] == ["default-roles-iceberg"]
    assert values["PLATFORM_ADMIN_PASSWORD"] not in json.dumps(document)
    assert document["clients"][0]["redirectUris"] == ["http://localhost:3000/auth/callback"]
    mcp = next(c for c in document["clients"] if c["clientId"] == "iceberg-mcp")
    assert mcp["publicClient"] and "secret" not in mcp and not mcp["directAccessGrantsEnabled"]
    assert mcp["redirectUris"] == ["http://localhost:3010/callback", "http://localhost/*", "http://127.0.0.1/*"]
    assert mcp["optionalClientScopes"] == ["offline_access"]
    assert [m["name"] for m in mcp["protocolMappers"]] == [m["name"] for m in document["clients"][0]["protocolMappers"]]
    cli = next(c for c in document["clients"] if c["clientId"] == "iceberg-cli")
    assert cli["publicClient"] and "secret" not in cli and not cli["directAccessGrantsEnabled"]
    assert not cli["standardFlowEnabled"] and cli["redirectUris"] == []
    assert cli["attributes"] == {"oauth2.device.authorization.grant.enabled": "true", "access.token.lifespan": "3600"}
    assert cli["optionalClientScopes"] == ["offline_access"]
    assert cli["protocolMappers"] == mcp["protocolMappers"]
    assert not any(key.startswith("DEMO_") for key in values)
    setup(config_root)
    assert config.read_bytes() == original
    assert config.stat().st_mode & 0o777 == 0o600


def test_demo_credentials_are_explicit_and_preserve_admin_password(config_root):
    setup(config_root)
    password = read_env(config_root / ".env")["PLATFORM_ADMIN_PASSWORD"]
    setup(config_root, demo=True)
    values = read_env(config_root / ".env")
    assert values["PLATFORM_ADMIN_PASSWORD"] == password
    assert values["DEMO_ADMIN_PASSWORD"] != password


def test_keycloak_without_a_secret_does_not_start(monkeypatch):
    monkeypatch.delenv("OIDC_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("OIDC_PORTAL_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="secret"):
        entrypoints.identity("admin")


def test_standard_entrypoints_always_enable_keycloak_and_identity_management(monkeypatch):
    from server import app, identity
    from user_portal import app as users_app

    oidc = Mock(secure=True)
    manager = Mock()
    monkeypatch.setattr(entrypoints, "identity", lambda portal: oidc)
    monkeypatch.setattr(identity, "UserManagement", lambda: manager)
    admin = Mock()
    users = Mock()
    monkeypatch.setattr(app, "create_app", admin)
    monkeypatch.setattr(users_app, "create_app", users)
    entrypoints.admin()
    entrypoints.users()
    admin.assert_called_once_with(oidc=oidc, secure_cookie=True,
                                  session_cookie="iceberg_admin", user_management=manager)
    users.assert_called_once_with(oidc=oidc, session_cookie="iceberg_user")


def test_extension_registration_writes_a_private_handshake(config_root):
    from scripts.setup import register_extension, remove_extension

    setup(config_root)
    handshake = config_root / "extensions" / "dbt" / ".env.bridge"
    register_extension(config_root, "dbt", "http://localhost:3004/", handshake)
    values = read_env(config_root / ".env")
    assert values["PLATFORM_EXTENSIONS"] == "dbt=http://localhost:3004"
    secret = values["PLATFORM_EXTENSION_SECRETS"].removeprefix("dbt=")
    assert len(secret) == 48
    bridge = read_env(handshake)
    assert handshake.stat().st_mode & 0o777 == 0o600
    assert bridge == {
        "BRIDGE_CONTRACT": "1", "BRIDGE_URL": "http://bridge:3005", "BRIDGE_EXTENSION_ID": "dbt",
        "BRIDGE_CLIENT_ID": "ext-dbt", "BRIDGE_CLIENT_SECRET": secret, "BRIDGE_MCP_CLIENT_ID": "ext-dbt-mcp",
        "OIDC_ISSUER": "http://localhost:8080/realms/iceberg",
        "OIDC_INTERNAL_ISSUER": "http://keycloak:8080/realms/iceberg",
        "EXTENSION_ORIGIN": "http://localhost:3004", "MCP_CALLBACK_PORT": "3010",
        "PLATFORM_NETWORK": "iceberg-platform_default",
    }
    # Registering again keeps the secret; a second extension is appended; setup leaves both alone.
    register_extension(config_root, "dbt", "http://localhost:3004", handshake)
    register_extension(config_root, "other", "http://localhost:3009", config_root / "other.env")
    setup(config_root)
    values = read_env(config_root / ".env")
    assert values["PLATFORM_EXTENSIONS"] == "dbt=http://localhost:3004,other=http://localhost:3009"
    assert values["PLATFORM_EXTENSION_SECRETS"].startswith(f"dbt={secret},other=")
    remove_extension(config_root, "dbt")
    assert read_env(config_root / ".env")["PLATFORM_EXTENSIONS"] == "other=http://localhost:3009"
    for bad_id, origin in (("DBT", "http://localhost:3004"), ("dbt", "http://localhost:3004/path")):
        with pytest.raises(SystemExit):
            register_extension(config_root, bad_id, origin, handshake)


def test_extension_clients_never_carry_the_polaris_audience():
    from scripts.setup import BRIDGE_AUDIENCE, extension_client, extension_mcp_client

    confidential = extension_client("dbt", "http://localhost:3004", "s" * 48)
    public = extension_mcp_client("dbt", 3010)
    assert confidential["serviceAccountsEnabled"] and not confidential["publicClient"]
    assert confidential["redirectUris"] == ["http://localhost:3004/auth/callback"]
    assert public["publicClient"] and "secret" not in public and public["clientId"] == "ext-dbt-mcp"
    for client in (confidential, public):
        audiences = [m["config"]["included.custom.audience"] for m in client["protocolMappers"]
                     if m["protocolMapper"] == "oidc-audience-mapper"]
        assert audiences == [BRIDGE_AUDIENCE]
        assert "polaris-roles" not in {m["name"] for m in client["protocolMappers"]}


def test_bootstrap_reconciles_and_disables_extension_clients():
    from server.keycloak_bootstrap import reconcile_extensions

    clients = [{"id": "old", "clientId": "ext-legacy", "enabled": True}]
    kc = Mock()

    def get(path, params=None):
        response = Mock()
        if path.endswith("/clients") and params:
            response.json.return_value = [c for c in clients if c["clientId"] == params["clientId"]]
        elif path.endswith("/clients"):
            response.json.return_value = clients
        elif path.endswith("/client-scopes"):
            response.json.return_value = [{"name": "offline_access", "id": "scope-offline"}]
        else:
            response.json.return_value = []
        return response

    def post(path, json=None):
        if path.endswith("/clients"):
            clients.append({"id": json["clientId"], **json})
        return Mock()

    kc.get.side_effect, kc.post.side_effect = get, post
    env = {"PLATFORM_EXTENSIONS": "dbt=http://localhost:3004", "PLATFORM_EXTENSION_SECRETS": "dbt=" + "s" * 48}
    assert reconcile_extensions(kc, "/admin/realms/iceberg", env) == {"ext-dbt", "ext-dbt-mcp"}
    assert {c["clientId"] for c in clients} == {"ext-legacy", "ext-dbt", "ext-dbt-mcp"}
    disabled = [c.kwargs["json"] for c in kc.put.call_args_list if c.args[0].endswith("/clients/old")]
    assert disabled == [{"id": "old", "clientId": "ext-legacy", "enabled": False}]
    with pytest.raises(RuntimeError):
        reconcile_extensions(kc, "/admin/realms/iceberg", {"PLATFORM_EXTENSIONS": "dbt=http://localhost:3004"})
