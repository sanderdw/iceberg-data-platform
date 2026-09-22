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
    assert values["PLATFORM_ADMIN_PASSWORD"] not in json.dumps(document)
    assert document["clients"][0]["redirectUris"] == ["http://localhost:3000/auth/callback"]
    mcp = next(c for c in document["clients"] if c["clientId"] == "iceberg-mcp")
    assert mcp["publicClient"] and "secret" not in mcp and not mcp["directAccessGrantsEnabled"]
    assert mcp["redirectUris"] == ["http://localhost:3010/callback"]
    assert mcp["optionalClientScopes"] == ["offline_access"]
    assert [m["name"] for m in mcp["protocolMappers"]] == [m["name"] for m in document["clients"][0]["protocolMappers"]]
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
