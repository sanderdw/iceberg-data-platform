import datetime
import decimal

import pytest

from conversationalbi.results import ResultStore
from conversationalbi.semantic import worker
from conversationalbi.tickets import RunTickets, ThreadOwners
from tests import flights


def test_plain_values_stay_exact_in_a_browser():
    assert worker.plain(2**60) == str(2**60) and worker.plain(42) == 42
    assert worker.plain(decimal.Decimal("12.50")) == 12.5
    assert worker.plain(decimal.Decimal("1" * 20)) == "1" * 20
    assert worker.plain(float("nan")) is None
    assert worker.plain(datetime.date(2026, 1, 2)) == "2026-01-02"


def test_execute_reports_truncation():
    connection = flights.local()
    result = worker.execute(connection, 'SELECT id FROM "FLIGHT" ORDER BY id', [], 5)
    assert len(result["rows"]) == 5 and result["truncated"] and result["columns"][0]["name"] == "id"


def test_a_locked_session_cannot_read_local_files():
    connection = worker.connect()
    worker.lock(connection)
    # Queries still run after the lock, in UTC.
    assert worker.execute(connection, "SELECT current_setting('TimeZone')", [], 1)["rows"] == [["UTC"]]
    with pytest.raises(Exception, match="disabled"):
        connection.execute("SELECT * FROM read_csv('/etc/passwd')")
    with pytest.raises(Exception, match="configuration"):
        connection.execute("SET disabled_filesystems = ''")


def test_results_belong_to_their_owner():
    store = ResultStore(per_owner=2)
    first = store.put("a", {"rows": [1]})
    assert store.get("a", first)["rows"] == [1] and store.get("b", first) is None
    store.put("a", {"rows": [2]})
    store.put("a", {"rows": [3]})
    assert store.get("a", first) is None  # Oldest result of that owner is dropped.


def test_tickets_and_threads():
    tickets = RunTickets(ttl=60)
    ticket = tickets.issue("session", "sub")
    assert tickets.resolve(ticket) == ("session", "sub") and tickets.resolve("x") is None
    threads = ThreadOwners()
    assert threads.claim("t", "a") and threads.claim("t", "a") and not threads.claim("t", "b")
