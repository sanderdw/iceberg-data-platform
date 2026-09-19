"""Notebook token delivery uses runtime authorization, never portal cookies or refresh tokens."""

from unittest.mock import Mock

import httpx
import pytest

from user_portal.notebook import credentials


def test_notebook_reads_current_token_each_time(monkeypatch):
    endpoint = "http://workspace-gateway:3002/internal/notebooks/notebook-1/token"
    monkeypatch.setenv("ICEBERG_SESSION_TOKEN_URL", endpoint)
    monkeypatch.setenv("MARIMO_GATEWAY_TOKEN", "runtime-secret")
    monkeypatch.setenv("ICEBERG_ACCESS_TOKEN", "old-token")
    request = httpx.Request("GET", endpoint)
    get = Mock(side_effect=[
        httpx.Response(200, json={"access_token": "fresh-token"}, request=request),
        httpx.Response(200, json={"access_token": "renewed-token"}, request=request),
        httpx.Response(401, json={"error": "private details"}, request=request),
    ])
    monkeypatch.setattr(credentials.httpx, "get", get)
    assert credentials.access_token() == "fresh-token"
    assert credentials.access_token() == "renewed-token"
    with pytest.raises(RuntimeError, match="Could not renew notebook access") as exc:
        credentials.access_token()
    assert "private" not in str(exc.value) and "runtime-secret" not in str(exc.value)
    for call in get.call_args_list:
        assert call.args == (endpoint,)
        assert call.kwargs["headers"] == {"Authorization": "Bearer runtime-secret"}
        assert call.kwargs["trust_env"] is False


def test_existing_pyiceberg_catalog_uses_fresh_session_auth(monkeypatch):
    pytest.importorskip("pyiceberg")
    from user_portal.notebook import connection

    token = Mock(side_effect=["first-token", "second-token"])
    monkeypatch.setattr(connection, "access_token", token)
    manager = connection.SessionAuthManager()
    assert manager.auth_header() == "Bearer first-token"
    assert manager.auth_header() == "Bearer second-token"
