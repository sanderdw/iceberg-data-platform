"""Native catalog connection routing, identifier quoting and credential hygiene."""

from typing import ClassVar
from unittest.mock import Mock

import httpx
import pytest

pytest.importorskip("duckdb")
from user_portal.notebook import duckdb_connection as native


def notebook_environment(monkeypatch):
    for key, value in {
        "ICEBERG_CATALOG_URI": "http://polaris:8181/api/catalog",
        "ICEBERG_TOKEN_URI": "http://polaris:8181/api/catalog/v1/oauth/tokens",
        "ICEBERG_DATABASE": "team's warehouse",
        "ICEBERG_CLIENT_ID": "reader",
        "ICEBERG_CLIENT_SECRET": "private-client-secret",
        "ICEBERG_S3_ENDPOINT": "http://rustfs:9000",
    }.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def connection_setup(monkeypatch):
    notebook_environment(monkeypatch)
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
    # DuckDB holds only the loopback proxy's secret; the proxy adds the owner's current token.
    proxy = native.catalog_proxy._proxies["http://polaris:8181/api/catalog"]
    assert f"TOKEN '{proxy.secret}'" in sql and "private''token" not in sql
    assert f"ENDPOINT '{proxy.endpoint}'" in sql and proxy.endpoint.startswith("http://127.0.0.1:")
    assert proxy.endpoint.endswith("/api/catalog")
    assert "ATTACH 'team''s warehouse'" in sql
    assert "READ_ONLY" in sql and "ACCESS_DELEGATION_MODE 'none'" in sql
    assert "PERSISTENT" not in sql
    assert "SET TimeZone = 'UTC'" in sql  # Dates derived from timestamps match the models' UTC convention.


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
    assert 'CREATE OR REPLACE SECRET "table_storage"' in sql
    assert "ENDPOINT 'rustfs:9000'" in sql and "SCOPE 's3://bucket/table/'" in sql


def test_multi_table_queries_keep_distinct_scoped_storage_secrets(connection_setup):
    connection, _ = connection_setup
    native.refresh_table_credentials(connection, ("ai_flights",), "flights", secret_name="flights_flights")
    native.refresh_table_credentials(connection, ("ai_flights",), "routes", secret_name="flights_routes")
    sql = [call.args[0] for call in connection.execute.call_args_list]
    assert 'SECRET "flights_flights"' in sql[0]
    assert 'SECRET "flights_routes"' in sql[1]
    assert all("SCOPE 's3://bucket/table/'" in statement for statement in sql)


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


def test_variant_columns_do_not_warn_in_the_marimo_datasets_panel(caplog):
    pytest.importorskip("marimo")
    from marimo._data import get_datasets

    connection = native.duckdb.connect()
    connection.execute("CREATE TABLE events AS SELECT {'kind': 'door'}::VARIANT AS payload")
    (database,) = get_datasets.get_databases_from_duckdb(connection)
    assert database.schemas[0].tables[0].columns[0].type == "unknown"
    assert "Unknown DuckDB type" not in caplog.text


def test_schema_reference_quotes_names():
    assert native.schema_reference(("analytics", 'ne"sted')) == '"lakehouse"."analytics.ne""sted"'


def test_table_reference_quotes_names():
    assert (
        native.table_reference(("analytics", "nested"), 'odd";table')
        == '"lakehouse"."analytics.nested"."odd"";table"'
    )


@pytest.mark.parametrize("exists,owner,dropped", [
    (0, None, False),
    (1, ("04_example",), True),
    (1, ("another_example",), None),
    (1, None, None),
])
def test_example_replaces_only_the_table_it_created(exists, owner, dropped):
    connection = Mock()
    connection.execute.return_value.fetchall.side_effect = [[(exists,)], [owner] if owner else []]
    if dropped is None:
        with pytest.raises(RuntimeError, match="did not create it"):
            native.drop_example_table(connection, ("iceberg_v3", "nested"), "sensor_events", "04_example")
    else:
        native.drop_example_table(connection, ("iceberg_v3", "nested"), "sensor_events", "04_example")
    calls = connection.execute.call_args_list
    assert calls[0].args[1] == ["iceberg_v3.nested", "sensor_events"]
    assert any(call.args[0].startswith("DROP TABLE") for call in calls) == bool(dropped)
    if exists:
        assert calls[1].args == (
            'SELECT value FROM iceberg_table_properties("lakehouse"."iceberg_v3.nested"."sensor_events") WHERE key = ?',
            [native.EXAMPLE_PROPERTY],
        )


def test_marimo_discovery_does_not_describe_uncredentialed_catalog_tables(connection_setup, monkeypatch):
    pytest.importorskip("marimo")
    from marimo._data import get_datasets

    connection, _ = connection_setup
    native.connect_duckdb(["ai_flights"], "flights")
    queried = []

    def execute(conn, query):
        queried.append(query)
        return [("id", "VARCHAR", "YES", None, None, None)]

    monkeypatch.setattr(get_datasets, "execute_duckdb_query", execute)
    assert get_datasets.get_table_columns(connection, '"lakehouse"."synthetic"."assets"') == []
    assert not queried
    assert get_datasets.get_table_columns(connection, '"lakehouse"."ai_flights"."flights"')
    assert get_datasets.get_table_columns(connection, '"temp"."main"."FLIGHT"')
    native.refresh_table_credentials(connection, ["ai_flights"], "routes", secret_name="routes")
    assert get_datasets.get_table_columns(connection, '"lakehouse"."ai_flights"."routes"')
    native.refresh_table_credentials(connection, ["ai_flights"], "airports")
    # Replacing the default secret removes the discovery permission it backed.
    assert get_datasets.get_table_columns(connection, '"lakehouse"."ai_flights"."flights"') == []
    assert get_datasets.get_table_columns(connection, '"lakehouse"."ai_flights"."airports"')
    assert all('"synthetic"' not in query for query in queried)


def test_marimo_discovery_for_unmanaged_connections_is_unchanged(monkeypatch):
    pytest.importorskip("marimo")
    from marimo._data import get_datasets

    execute = Mock(return_value=[("id", "INTEGER", "YES", None, None, None)])
    monkeypatch.setattr(get_datasets, "execute_duckdb_query", execute)
    with native.duckdb.connect() as connection:
        assert get_datasets.get_table_columns(connection, '"lakehouse"."other"."events"')
        execute.assert_called_once()


class FakeTimer:
    """Records the planned renewal instead of starting a thread."""

    planned: ClassVar[list] = []

    def __init__(self, interval, function):
        self.interval, self.function, self.cancelled = interval, function, False
        FakeTimer.planned.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True


@pytest.fixture
def renewal_setup(monkeypatch):
    """A catalog that vends credentials expiring in an hour, with a clock the test moves."""
    notebook_environment(monkeypatch)
    connection, requests = Mock(), []
    monkeypatch.setattr(native.duckdb, "connect", lambda **kwargs: connection)
    FakeTimer.planned = []
    clock = {"now": 1_000_000.0}
    catalog = {"endpoints": [native.CREDENTIALS_ENDPOINT], "key": "first-key", "fail": None, "storage": True}
    monkeypatch.setattr(native, "_Timer", FakeTimer)
    monkeypatch.setattr(native, "_clock", lambda: clock["now"])

    def handler(request):
        requests.append(request)
        expires = str(int((clock["now"] + 3600) * 1000))
        if request.url.path.endswith("/oauth/tokens"):
            return httpx.Response(200, json={"access_token": "private'token"})
        if request.url.path.endswith("/config"):
            return httpx.Response(200, json={"overrides": {"prefix": "db"}, "endpoints": catalog["endpoints"]})
        if catalog["fail"]:
            return httpx.Response(catalog["fail"], json={"error": {"message": "private-session-token"}})
        storage = {"s3.access-key-id": catalog["key"], "s3.secret-access-key": "private-storage-secret",
                   "s3.session-token": "private-session-token", "s3.session-token-expires-at-ms": expires}
        if request.url.path.endswith("/credentials"):
            return httpx.Response(200, json={"storage-credentials": [
                {"prefix": "s3://other/", "config": {**storage, "s3.access-key-id": "wrong-prefix"}},
                {"prefix": "s3://bucket/table", "config": storage}] if catalog["storage"] else []})
        return httpx.Response(200, json={"metadata": {"location": "s3://bucket/table"},
                                         "config": {"s3.endpoint": "http://localhost:9000", **storage}})

    transport, real_client = httpx.MockTransport(handler), httpx.Client
    monkeypatch.setattr(native.httpx, "Client", lambda **kwargs: real_client(transport=transport))
    return connection, requests, catalog, clock


def secrets_created(mock):
    return [call.args[0] for call in mock.execute.call_args_list if "SECRET \"table_storage\"" in call.args[0]]


def test_storage_secret_renews_itself_through_a_cursor_before_it_expires(renewal_setup):
    connection, requests, catalog, clock = renewal_setup
    native.connect_duckdb(("analytics",), "events")
    (timer,) = FakeTimer.planned
    assert timer.interval == 3600 - native.RENEW_BEFORE_SECONDS
    catalog["key"] = "second-key"
    clock["now"] += timer.interval
    timer.function()
    # Only the lighter credentials endpoint, with the longest matching prefix, through a separate cursor.
    assert requests[-1].url.path.endswith("/tables/events/credentials")
    (renewed,) = secrets_created(connection.cursor.return_value)
    assert "KEY_ID 'second-key'" in renewed and "SCOPE 's3://bucket/table/'" in renewed
    assert "ENDPOINT 'rustfs:9000'" in renewed
    connection.cursor.return_value.close.assert_called_once()
    assert FakeTimer.planned[-1].interval == 3600 - native.RENEW_BEFORE_SECONDS  # The next renewal.


@pytest.mark.parametrize("endpoints,storage", [([], True), ([native.CREDENTIALS_ENDPOINT], False)])
def test_renewal_loads_the_table_without_a_usable_credentials_endpoint(renewal_setup, endpoints, storage):
    connection, requests, catalog, clock = renewal_setup
    catalog["endpoints"], catalog["storage"] = endpoints, storage
    native.refresh_table_credentials(connection, ("analytics",), "events")
    catalog["key"] = "second-key"
    clock["now"] += 3600
    FakeTimer.planned[-1].function()
    assert requests[-1].url.path.endswith("/tables/events")
    assert "KEY_ID 'second-key'" in secrets_created(connection.cursor.return_value)[0]


def test_failed_renewal_is_retried_until_expiry_without_exposing_credentials(renewal_setup, caplog):
    connection, _, catalog, clock = renewal_setup
    native.connect_duckdb(("analytics",), "events")
    catalog["fail"] = 503
    clock["now"] += 3600 - native.RENEW_BEFORE_SECONDS
    FakeTimer.planned[-1].function()
    assert secrets_created(connection.cursor.return_value) == []
    assert FakeTimer.planned[-1].interval == native.RENEW_RETRY_SECONDS
    assert "private" not in caplog.text and "analytics.events" in caplog.text
    clock["now"] += native.RENEW_BEFORE_SECONDS  # Expired: renewing stops until the user reconnects.
    FakeTimer.planned[-1].function()
    assert native._renewals[connection].secrets == {}


def test_credentials_vended_close_to_expiry_are_not_renewed_in_a_loop(renewal_setup):
    connection, _, _, clock = renewal_setup
    native.connect_duckdb(("analytics",), "events")
    clock["now"] += 3500  # Polaris could hand out cached credentials that expire this soon.
    native.refresh_table_credentials(connection, ("analytics",), "events")
    assert FakeTimer.planned[-1].interval == 3600 - native.RENEW_BEFORE_SECONDS
    renewal = native._renewals[connection]
    renewal._track("table_storage", ("analytics",), "events", "s3://bucket/table/", clock["now"] + 60)
    assert FakeTimer.planned[-1].interval == native.RENEW_RETRY_SECONDS


def test_renewal_stops_with_a_closed_connection(renewal_setup):
    connection, requests, _, clock = renewal_setup
    native.connect_duckdb(("analytics",), "events")
    sent = len(requests)
    connection.cursor.side_effect = native.duckdb.ConnectionException("Connection already closed")
    clock["now"] += 3600
    FakeTimer.planned[-1].function()
    assert len(requests) == sent


def test_secret_replaced_for_another_table_is_not_overwritten_by_a_pending_renewal(renewal_setup):
    connection, _, _, clock = renewal_setup
    native.connect_duckdb(("analytics",), "events")
    renewal = native._renewals[connection]
    stale = renewal.secrets["table_storage"]
    native.refresh_table_credentials(connection, ("analytics",), "other")
    clock["now"] += 3600
    renewal._renew_one(connection.cursor(), None, None, None, "table_storage", stale)
    assert secrets_created(connection.cursor.return_value) == []
    assert renewal.secrets["table_storage"][1] == "other"
