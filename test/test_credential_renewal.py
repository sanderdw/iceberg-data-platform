"""Notebook PyIceberg tables renew vended storage credentials, and metadata commits retry on 409."""

import logging
import time

import pytest

pytest.importorskip("pyiceberg")
from pyiceberg.catalog.rest import RestCatalog, TableResponse
from pyiceberg.exceptions import CommitFailedException, NoSuchTableError
from pyiceberg.partitioning import UNPARTITIONED_PARTITION_SPEC
from pyiceberg.schema import Schema
from pyiceberg.table.metadata import new_table_metadata
from pyiceberg.table.sorting import UNSORTED_SORT_ORDER

from user_portal.notebook import connection

IDENTIFIER = ("analytics", "events")
LOCATION = "s3://bucket/analytics/events/metadata/00001.json"


def vended(key, expires_in):
    return {"s3.access-key-id": key, "s3.secret-access-key": f"secret-{key}", "s3.session-token": f"token-{key}",
            "s3.session-token-expires-at-ms": str(int((time.time() + expires_in) * 1000))}


class FakeCatalog:
    def __init__(self, *responses, fallback=None):
        self.responses, self.fallback, self.calls = list(responses), fallback, []

    def load_credentials(self, identifier, location):
        self.calls.append(("credentials", identifier, location))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def load_table(self, identifier):
        self.calls.append(("table", identifier))

        class Loaded:
            io = connection.InternalS3FileIO(self.fallback)

        return Loaded


@pytest.fixture
def file_io(monkeypatch):
    monkeypatch.setenv("ICEBERG_S3_ENDPOINT", "http://rustfs:9000")

    def make(properties, catalog):
        io = connection.InternalS3FileIO({**properties, "s3.endpoint": "http://localhost:9000", "comment": "x"})
        io.renew_from(catalog, IDENTIFIER, LOCATION)
        return io

    return make


def test_fileio_renews_credentials_shortly_before_they_expire(file_io):
    catalog = FakeCatalog(vended("new", 3600))
    io = file_io(vended("old", connection.RENEW_BEFORE_SECONDS - 10), catalog)
    filesystems = io.fs_by_scheme
    io.new_input("s3://bucket/analytics/events/data/1.parquet")
    assert catalog.calls == [("credentials", IDENTIFIER, LOCATION)]
    assert io.properties["s3.access-key-id"] == "new" and io.properties["s3.session-token"] == "token-new"
    # The internal endpoint and other properties stay; file systems with the old keys are dropped.
    assert io.properties["s3.endpoint"] == "http://rustfs:9000" and io.properties["comment"] == "x"
    assert io.fs_by_scheme is not filesystems
    io.new_output("s3://bucket/analytics/events/data/2.parquet")
    assert len(catalog.calls) == 1  # Valid for an hour now.


def test_fileio_keeps_credentials_that_are_not_about_to_expire_or_have_no_stated_expiry(file_io):
    catalog = FakeCatalog()
    io = file_io(vended("old", 3600), catalog)
    assert not io.renew_credentials()
    no_expiry = vended("old", 0)
    del no_expiry["s3.session-token-expires-at-ms"]
    assert not file_io(no_expiry, catalog).renew_credentials()
    assert catalog.calls == []


def test_fileio_loads_the_table_when_the_catalog_has_no_credentials_endpoint(file_io):
    catalog = FakeCatalog(NotImplementedError("Server does not support endpoint"), fallback=vended("loaded", 3600))
    io = file_io(vended("old", 10), catalog)
    assert io.renew_credentials()
    assert [call[0] for call in catalog.calls] == ["credentials", "table"]
    assert io.properties["s3.access-key-id"] == "loaded"


def test_failed_renewal_keeps_the_old_credentials_and_tries_again_later(file_io, caplog):
    catalog = FakeCatalog(NoSuchTableError("private-session-token"), vended("new", 3600))
    io = file_io(vended("old", 10), catalog)
    with caplog.at_level(logging.WARNING):
        assert not io.renew_credentials()
    assert io.properties["s3.access-key-id"] == "old"
    assert "private-session-token" not in caplog.text and "analytics.events" in caplog.text
    assert not io.renew_credentials()  # Not again on every file within RENEW_RETRY_SECONDS.
    assert len(catalog.calls) == 1
    io._retry_at = 0
    assert io.renew_credentials() and io.properties["s3.access-key-id"] == "new"


def test_unbound_fileio_never_calls_a_catalog(monkeypatch):
    monkeypatch.setenv("ICEBERG_S3_ENDPOINT", "http://rustfs:9000")
    io = connection.InternalS3FileIO(vended("old", 10))
    assert not io.renew_credentials(force=True)


def test_rest_catalog_tables_bind_their_fileio_for_renewal(monkeypatch):
    monkeypatch.setenv("ICEBERG_S3_ENDPOINT", "http://rustfs:9000")
    catalog = object.__new__(RestCatalog)  # No server: only the response handling runs.
    catalog.properties = {"py-io-impl": "user_portal.notebook.connection.InternalS3FileIO"}
    catalog._auth_manager = None
    metadata = new_table_metadata(Schema(), UNPARTITIONED_PARTITION_SPEC, UNSORTED_SORT_ORDER,
                                  "s3://bucket/analytics/events", {})
    response = TableResponse(**{"metadata-location": LOCATION, "metadata": metadata, "config": vended("old", 10)})
    table = catalog._response_to_table(IDENTIFIER, response)
    assert isinstance(table.io, connection.InternalS3FileIO)
    assert table.io._source == (catalog, IDENTIFIER, LOCATION)


def test_commit_retries_conflicts_with_a_fresh_table_and_growing_jittered_waits(monkeypatch):
    monkeypatch.setattr(connection.random, "uniform", lambda low, high: high)
    loads, seen, waits = iter(["fresh-1", "fresh-2"]), [], []

    def change(table):
        seen.append(table)
        if len(seen) < 3:
            raise CommitFailedException("409: concurrent commit")
        return "committed"

    assert connection.commit_with_retry(lambda: next(loads), change, table="loaded", sleep=waits.append) == "committed"
    assert seen == ["loaded", "fresh-1", "fresh-2"]
    assert waits == [0.2, 0.4]


def test_commit_retry_is_bounded():
    attempts, waits = [], []

    def change(table):
        attempts.append(table)
        raise CommitFailedException("409")

    with pytest.raises(CommitFailedException):
        connection.commit_with_retry(lambda: "table", change, attempts=3, max_delay=0.3, sleep=waits.append)
    assert len(attempts) == 3 and len(waits) == 2 and all(0 <= wait <= 0.3 for wait in waits)


def test_commit_retry_never_repeats_other_errors():
    attempts = []

    def change(table):
        attempts.append(table)
        raise ValueError("400: bad request")

    with pytest.raises(ValueError):
        connection.commit_with_retry(lambda: "table", change, sleep=lambda wait: pytest.fail("no retry"))
    assert attempts == ["table"]
