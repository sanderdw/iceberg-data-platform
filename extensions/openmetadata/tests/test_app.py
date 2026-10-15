from fastapi.testclient import TestClient

from openmetadata_sync.app import create_app
from tests.conftest import TOKEN, settings


def test_without_openmetadata_settings_nothing_runs():
    with TestClient(create_app(settings(om_token=""))) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        assert client.get("/api/status").json() == {"version": "0.1.1", "state": "not_configured"}


def test_status_shows_counts_never_secrets():
    with TestClient(create_app(settings(), start=False)) as client:
        body = client.get("/api/status").json()
    assert body["state"] == "starting" and body["listings"] == 0
    assert TOKEN not in str(body)


def test_the_portal_link_opens_openmetadata_or_the_status():
    import pytest

    from openmetadata_sync.config import web_url

    with TestClient(create_app(settings(ui_url="http://localhost:8585"), start=False)) as client:
        response = client.get("/", follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (302, "http://localhost:8585")
    with TestClient(create_app(settings(), start=False)) as client:
        assert client.get("/", follow_redirects=False).headers["location"] == "/api/status"
    assert web_url(" https://metadata.example.com/ ") == "https://metadata.example.com"
    with pytest.raises(ValueError):
        web_url("javascript:alert(1)")
