from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.models import ServiceError
from test.conftest import HEADERS, PASSWORD, MemoryPolaris


@pytest.mark.parametrize(
    "path", ["/api/admin/explorer/databases", "/api/admin/explorer/contents?database=analytics"]
)
def test_explorer_requires_portal_session_not_database_role(path):
    provider = MemoryPolaris()
    provider.explorer_databases = Mock(return_value=[])
    provider.explorer_contents = Mock(return_value={})
    with TestClient(create_app(provider, PASSWORD)) as client:
        for headers in ({}, {"Authorization": "Bearer database-admin-token"}, {"X-Portal-Admin": "true"}):
            assert client.get(path, headers=headers).status_code == 401
        provider.explorer_databases.assert_not_called()
        provider.explorer_contents.assert_not_called()
        client.post("/api/session", json={"password": PASSWORD}, headers=HEADERS).raise_for_status()
        assert client.get(path).status_code == 200
        client.delete("/api/session", headers=HEADERS)
        assert client.get(path).status_code == 401


def test_explorer_lists_unmanaged_catalogs_without_exposing_properties(portal):
    portal.provider.resources["catalogs"]["external"] = {
        "name": "external",
        "type": "EXTERNAL",
        "properties": {"secret": "never-return-this"},
    }
    result = portal.client.get("/api/admin/explorer/databases")
    assert result.status_code == 200
    assert result.json()["databases"][0]["name"] == "external"
    assert result.json()["databases"][0]["managed"] is False
    assert "never-return-this" not in result.text


def test_nested_namespace_pagination_and_temporary_read_only_role(portal):
    p = portal.provider
    p.resources["catalogs"]["external"] = {"name": "external", "properties": {}}
    paths = []

    def request(path, method="GET", body=None, retry=True):
        assert method == "GET"
        paths.append(path)
        url = urlsplit(path)
        query = parse_qs(url.query)
        if url.path.endswith("/namespaces"):
            assert query["parent"] == ["analytics\x1fnested.dot"]
            return {"namespaces": [["analytics", "nested.dot", "child"]]}
        assert "/analytics%1Fnested.dot/" in url.path
        if url.path.endswith("/tables"):
            if "pageToken" not in query:
                return {
                    "identifiers": [
                        {"name": "first", "namespace": ["analytics", "nested.dot"], "secret": "hidden"}
                    ],
                    "next-page-token": "next +/&",
                }
            assert query["pageToken"] == ["next +/&"]
            return {"identifiers": [{"name": "second", "namespace": ["analytics", "nested.dot"]}]}
        return {"identifiers": [{"name": "report", "namespace": ["analytics", "nested.dot"]}]}

    p.request = request
    result = portal.client.get(
        "/api/admin/explorer/contents",
        params=[("database", "external"), ("namespace", "analytics"), ("namespace", "nested.dot")],
    )
    assert result.status_code == 200, result.text
    assert [t["name"] for t in result.json()["tables"]] == ["first", "second"]
    assert result.json()["views"][0]["name"] == "report"
    assert len(paths) == 4
    assert "hidden" not in result.text
    grants = [body["grant"]["privilege"] for _, method, body in p.events if body and "grant" in body]
    assert set(grants) == {"CATALOG_READ_PROPERTIES", "NAMESPACE_LIST", "TABLE_LIST", "VIEW_LIST"}
    assert any(
        path.startswith("/principal-roles/service_admin/") and method == "PUT" for path, method, _ in p.events
    )
    assert p.events[-1][1] == "DELETE" and "/catalog-roles/portal-explorer-" in p.events[-1][0]


def test_explorer_failure_removes_its_role(portal):
    p = portal.provider
    p.resources["catalogs"]["external"] = {"name": "external", "properties": {}}
    p.request = Mock(side_effect=ServiceError(503, "Provider offline"))
    result = portal.client.get("/api/admin/explorer/contents?database=external")
    assert result.status_code == 503
    assert p.events[-1][1] == "DELETE"


def test_explorer_reports_cleanup_failure(portal):
    p = portal.provider
    p.resources["catalogs"]["external"] = {"name": "external", "properties": {}}
    p.fail = lambda path, method, body: method == "DELETE"
    result = portal.client.get("/api/admin/explorer/contents?database=external")
    assert result.status_code == 502
    assert "read role" in result.json()["error"]


@pytest.mark.parametrize(
    "query",
    [
        "database=..",
        "database=x&namespace=",
        "database=x&namespace=..",
        "database=x&namespace=a%1Fb",
        "database=x&namespace=a%00b",
    ],
)
def test_invalid_namespace_is_rejected_before_provider_calls(portal, query):
    portal.provider.events.clear()
    assert portal.client.get("/api/admin/explorer/contents?" + query).status_code == 422
    assert portal.provider.events == []


def test_deleting_database_is_not_browsable(portal):
    portal.provider.resources["catalogs"]["deleting"] = {
        "name": "deleting",
        "properties": {"portal.deleting": "true"},
    }
    assert portal.client.get("/api/admin/explorer/contents?database=deleting").status_code == 409
    assert not any(method == "POST" for _, method, _ in portal.provider.events)
