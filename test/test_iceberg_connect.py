"""The downloadable helper signs users in with Keycloak's device login and never leaks their tokens."""

import base64
import json
import time
from io import StringIO
from urllib.parse import parse_qs

import httpx
import pytest

from scripts.setup import CLI_CLIENT_ID
from test.test_oidc import ISSUER, issuer  # noqa: F401 - fixture
from user_portal.client import iceberg_connect as helper


def token(exp):
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).rstrip(b"=").decode()
    return f"header.{payload}.signature"


def session(tmp_path, respond):
    calls = []

    def wire(request):
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        calls.append((request.url.path.rsplit("/", 1)[-1], form))
        return respond(calls[-1][0], form)

    http = httpx.Client(transport=httpx.MockTransport(wire))
    return helper.Session(ISSUER, CLI_CLIENT_ID, tmp_path / "platform" / "credentials.json", http), calls


def test_helper_uses_the_realm_client():
    assert helper.CLIENT_ID == CLI_CLIENT_ID


def test_device_login_polls_until_approved_and_stores_the_refresh_token_privately(tmp_path):
    replies = iter([
        httpx.Response(400, json={"error": "authorization_pending"}),
        httpx.Response(400, json={"error": "slow_down"}),
        httpx.Response(200, json={"access_token": token(time.time() + 3600), "refresh_token": "refresh-1"}),
    ])

    def respond(endpoint, form):
        if endpoint == "device":
            assert form == {"client_id": CLI_CLIENT_ID, "scope": "openid offline_access"}
            return httpx.Response(200, json={
                "device_code": "device-1", "user_code": "ABCD-EFGH", "interval": 5, "expires_in": 600,
                "verification_uri": ISSUER + "/device", "verification_uri_complete": ISSUER + "/device?user_code=ABCD-EFGH"})
        assert form == {"grant_type": helper.DEVICE_GRANT, "client_id": CLI_CLIENT_ID, "device_code": "device-1"}
        return next(replies)

    user, calls = session(tmp_path, respond)
    out, waits, opened = StringIO(), [], []
    user.login(out=out, sleep=waits.append, open_browser=opened.append)
    assert waits == [5, 5, 10]
    assert opened == [ISSUER + "/device?user_code=ABCD-EFGH"]
    assert "ABCD-EFGH" in out.getvalue() and "refresh-1" not in out.getvalue()
    assert json.loads(user.path.read_text()) == {"issuer": ISSUER, "refresh_token": "refresh-1"}
    assert user.path.stat().st_mode & 0o777 == 0o600
    assert user.path.parent.stat().st_mode & 0o777 == 0o700
    assert [endpoint for endpoint, _ in calls] == ["device", "token", "token", "token"]


def test_denied_device_login_stores_nothing(tmp_path):
    def respond(endpoint, form):
        if endpoint == "device":
            return httpx.Response(200, json={"device_code": "d", "user_code": "U", "verification_uri": "v"})
        return httpx.Response(400, json={"error": "access_denied"})

    user, _ = session(tmp_path, respond)
    with pytest.raises(helper.LoginRequired, match="denied"):
        user.login(out=StringIO(), sleep=lambda _: None, open_browser=lambda _: None)
    assert not user.path.exists()


def test_access_token_refreshes_before_expiry_and_rotates_the_refresh_token(tmp_path):
    issued = [token(time.time() + 30), token(time.time() + 3600)]

    def respond(endpoint, form):
        assert endpoint == "token" and form["grant_type"] == "refresh_token"
        refresh = form["refresh_token"]
        return httpx.Response(200, json={"access_token": issued.pop(0), "refresh_token": refresh + "+"})

    user, calls = session(tmp_path, respond)
    helper._write_private(user.path, {"issuer": ISSUER, "refresh_token": "refresh-1"})
    first = user.access_token()
    # Thirty seconds left is too little, so the next call refreshes again.
    second = user.access_token()
    assert first != second and user.access_token() == second
    assert [form["refresh_token"] for _, form in calls] == ["refresh-1", "refresh-1+"]
    assert json.loads(user.path.read_text())["refresh_token"] == "refresh-1++"


def test_missing_or_revoked_sign_in_asks_to_log_in_again(tmp_path):
    user, _ = session(tmp_path, lambda endpoint, form: httpx.Response(400, json={"error": "invalid_grant"}))
    with pytest.raises(helper.LoginRequired, match="not signed in"):
        user.access_token()
    helper._write_private(user.path, {"issuer": ISSUER, "refresh_token": "revoked"})
    with pytest.raises(helper.LoginRequired, match="expired or was revoked"):
        user.access_token()


def test_logout_revokes_the_refresh_token_and_forgets_it(tmp_path):
    user, calls = session(tmp_path, lambda endpoint, form: httpx.Response(200))
    helper._write_private(user.path, {"issuer": ISSUER, "refresh_token": "refresh-1"})
    user.logout()
    assert calls == [("revoke", {"client_id": CLI_CLIENT_ID, "token": "refresh-1", "token_type_hint": "refresh_token"})]
    assert not user.path.exists()
    user.logout()
    assert len(calls) == 1


def test_credentials_are_kept_per_platform(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    first, second = helper.credentials_path(ISSUER), helper.credentials_path("https://other.example/realms/iceberg")
    assert first.parent == second.parent == tmp_path / "iceberg-platform"
    assert first != second


def test_duckdb_sql_escapes_values_and_attaches_read_only_by_default():
    sql = helper.duckdb_sql("db-'x", "to'ken", catalog_uri="http://polaris/api/catalog").splitlines()
    assert "CREATE OR REPLACE SECRET lakehouse_token (TYPE iceberg, TOKEN 'to''ken');" in sql
    assert sql[-2] == "DETACH DATABASE IF EXISTS lakehouse;"
    assert sql[-1].startswith("ATTACH 'db-''x' AS lakehouse (TYPE iceberg, ENDPOINT 'http://polaris/api/catalog'")
    assert "ACCESS_DELEGATION_MODE 'vended_credentials'" in sql[-1] and sql[-1].endswith(", READ_ONLY);")
    writable = helper.duckdb_sql("db-1", "t", alias="sales", write=True).splitlines()
    assert "READ_ONLY" not in writable[-1] and " AS sales " in writable[-1]
    with pytest.raises(ValueError, match="identifier"):
        helper.duckdb_sql("db-1", "t", alias="x; DROP")


def test_duckdb_errors_never_repeat_the_token(monkeypatch):
    monkeypatch.setattr(helper, "session", lambda: type("S", (), {"access_token": lambda self: "secret-token"})())
    monkeypatch.setattr(helper, "duckdb_sql", lambda warehouse, token, alias, write: f"SELECT * FROM '{token}';")
    with pytest.raises(RuntimeError) as error:
        helper.duckdb_connection("db-1")
    assert "secret-token" not in str(error.value) and error.value.__cause__ is None


def test_pyiceberg_catalog_uses_the_refreshing_session(monkeypatch):
    from pyiceberg.catalog.rest.auth import AuthManagerFactory

    loaded = {}
    monkeypatch.setattr("pyiceberg.catalog.load_catalog", lambda name, **props: loaded.update(props, name=name))
    helper.catalog("db-1")
    assert loaded["name"] == loaded["warehouse"] == "db-1"
    assert loaded["uri"] == helper.CATALOG_URI
    assert loaded["header.X-Iceberg-Access-Delegation"] == "vended-credentials"
    monkeypatch.setattr(helper, "session", lambda: type("S", (), {"access_token": lambda self: "fresh"})())
    assert AuthManagerFactory.create(loaded["auth"]["type"], {}).auth_header() == "Bearer fresh"


def test_cli_prints_duckdb_sql_and_explains_a_missing_sign_in(monkeypatch, capsys):
    monkeypatch.setattr(helper, "session", lambda: type("S", (), {"access_token": lambda self: "t"})())
    helper.main(["duckdb", "db-1", "--as", "sales"])
    assert "ATTACH 'db-1' AS sales" in capsys.readouterr().out

    def signed_out():
        raise helper.LoginRequired("You are not signed in. " + helper.LOGIN_AGAIN)

    monkeypatch.setattr(helper, "session", lambda: type("S", (), {"access_token": lambda self: signed_out()})())
    with pytest.raises(SystemExit, match="login"):
        helper.main(["token"])


def test_portal_serves_the_helper_with_its_own_issuer_and_catalog(issuer):  # noqa: F811 - pytest fixture
    from fastapi.testclient import TestClient

    from test.test_user_portal import FakeRuntime
    from user_portal.app import create_app
    from user_portal.directory import UserDirectory

    oidc, _ = issuer
    directory = UserDirectory({"POLARIS_PUBLIC_URL": "http://catalog.example:8181"})
    with TestClient(create_app(directory, FakeRuntime(), oidc=oidc)) as client:
        response = client.get("/iceberg_connect.py")
    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="iceberg_connect.py"'
    assert f"\nISSUER = {ISSUER!r}\n" in response.text
    assert "\nCATALOG_URI = 'http://catalog.example:8181/api/catalog'\n" in response.text
    compile(response.text, "iceberg_connect.py", "exec")
    with TestClient(create_app(directory, FakeRuntime())) as client:
        assert client.get("/iceberg_connect.py").status_code == 404
