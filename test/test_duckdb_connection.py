"""Native catalog connection routing, identifier quoting and credential hygiene."""

from unittest.mock import Mock

import httpx
import pytest

pytest.importorskip("duckdb")
from user_portal.notebook import duckdb_connection as native


@pytest.fixture
def connection_setup(monkeypatch):
    for key, value in {
        "ICEBERG_CATALOG_URI": "http://polaris:8181/api/catalog",
        "ICEBERG_TOKEN_URI": "http://polaris:8181/api/catalog/v1/oauth/tokens",
        "ICEBERG_DATABASE": "team's warehouse",
        "ICEBERG_CLIENT_ID": "reader",
        "ICEBERG_CLIENT_SECRET": "private-client-secret",
        "ICEBERG_S3_ENDPOINT": "http://rustfs:9000",
    }.items():
        monkeypatch.setenv(key, value)
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/oauth/tokens"):
            assert b"client_id=reader" in request.content
            return httpx.Response(200, json={"access_token": "private'token"})
        assert request.headers["Authorization"] == "Bearer private'token"
        if request.url.path.endswith("/config"):
            return httpx.Response(
                200, json={"defaults": {"prefix": "ignored"}, "overrides": {"prefix": "db"}}
            )
        if request.url.path.endswith("/tables/missing"):
            return httpx.Response(404)
        return httpx.Response(
            200,
            json={
                "metadata": {"location": "s3://bucket/table"},
                "config": {
                    "s3.access-key-id": "temporary-key",
                    "s3.secret-access-key": "private-storage-secret",
                    "s3.session-token": "private-session-token",
                    "s3.endpoint": "http://localhost:9000",
                },
            },
        )

    # A fresh client per call: the helper closes each one after use.
    transport, real_client = httpx.MockTransport(handler), httpx.Client
    monkeypatch.setattr(native.httpx, "Client", lambda **kwargs: real_client(transport=transport))
    connection = Mock()
    monkeypatch.setattr(native.duckdb, "connect", lambda **kwargs: connection)
    return connection, requests


def test_native_connection_uses_scoped_vended_credentials_and_internal_endpoint(connection_setup):
    connection, requests = connection_setup
    assert native.connect_duckdb(("analytics", "nested"), "odd/table") is connection
    assert requests[-1].url.raw_path.endswith(b"/db/namespaces/analytics%1Fnested/tables/odd%2Ftable")
    assert requests[-1].headers["X-Iceberg-Access-Delegation"] == "vended-credentials"
    sql = "\n".join(call.args[0] for call in connection.execute.call_args_list)
    assert "ENDPOINT 'rustfs:9000'" in sql and "localhost:9000" not in sql
    assert "SCOPE 's3://bucket/table/'" in sql
    assert "TOKEN 'private''token'" in sql
    assert "ATTACH 'team''s warehouse'" in sql
    assert "READ_ONLY" in sql and "ACCESS_DELEGATION_MODE 'none'" in sql
    assert "PERSISTENT" not in sql


def test_writable_connection_to_a_new_table_waits_for_credentials(connection_setup):
    connection, requests = connection_setup
    assert native.connect_duckdb(("iceberg_v3",), "missing", read_only=False, missing_ok=True) is connection
    sql = "\n".join(call.args[0] for call in connection.execute.call_args_list)
    assert "READ_ONLY" not in sql and "TYPE s3" not in sql
    assert "ACCESS_DELEGATION_MODE 'none'" in sql
    connection.execute.reset_mock()
    native.refresh_table_credentials(connection, ("iceberg_v3",), "sensor_events")
    assert requests[-1].headers["X-Iceberg-Access-Delegation"] == "vended-credentials"
    sql = connection.execute.call_args.args[0]
    assert "CREATE OR REPLACE SECRET table_storage" in sql
    assert "ENDPOINT 'rustfs:9000'" in sql and "SCOPE 's3://bucket/table/'" in sql


@pytest.mark.parametrize("options", [{}, {"read_only": False}, {"missing_ok": True}])
def test_missing_table_needs_an_explicit_writable_opt_in(connection_setup, options):
    with pytest.raises(RuntimeError) as error:
        native.connect_duckdb(("iceberg_v3",), "missing", **options)
    assert ("writing" in str(error.value)) == (options.get("read_only") is False)


def test_failed_credential_refresh_does_not_expose_provider_details(connection_setup):
    connection, _ = connection_setup
    with pytest.raises(RuntimeError) as error:
        native.refresh_table_credentials(connection, ("iceberg_v3",), "missing")
    assert error.value.__suppress_context__
    connection.execute.side_effect = native.duckdb.Error("SQL contains private-storage-secret")
    with pytest.raises(RuntimeError) as error:
        native.refresh_table_credentials(connection, ("iceberg_v3",), "sensor_events")
    assert "private-storage-secret" not in str(error.value)


def test_external_token_skips_client_secret_exchange(connection_setup, monkeypatch):
    connection, requests = connection_setup
    monkeypatch.setenv("ICEBERG_ACCESS_TOKEN", "private'token")
    monkeypatch.delenv("ICEBERG_CLIENT_SECRET")
    monkeypatch.delenv("ICEBERG_CLIENT_ID")
    assert native.connect_duckdb(("analytics",), "events") is connection
    assert all(request.method == "GET" for request in requests)


def test_failed_connection_closes_and_does_not_expose_provider_sql(connection_setup):
    connection, _ = connection_setup
    connection.execute.side_effect = native.duckdb.Error("SQL contains private-client-secret")
    with pytest.raises(RuntimeError) as error:
        native.connect_duckdb(("analytics",), "events")
    connection.close.assert_called_once()
    assert "private-client-secret" not in str(error.value)
    assert error.value.__suppress_context__


def test_schema_reference_quotes_names():
    assert native.schema_reference(("analytics", 'ne"sted')) == '"lakehouse"."analytics.ne""sted"'


def test_table_reference_quotes_names():
    assert (
        native.table_reference(("analytics", "nested"), 'odd";table')
        == '"lakehouse"."analytics.nested"."odd"";table"'
    )
