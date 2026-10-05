import asyncio
import datetime
import decimal
import sys
from types import SimpleNamespace

import pytest

from conversationalbi.auth import BrowserLogin
from conversationalbi.config import Settings
from conversationalbi.errors import CbiError
from conversationalbi.results import ResultStore
from conversationalbi.semantic import executor, worker
from conversationalbi.semantic.compiler import ModelRef
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


def test_results_are_bounded_by_bytes_not_only_by_count():
    rows = {"rows": ["x" * 90]}  # About 100 bytes of JSON.
    store = ResultStore(max_bytes=500, per_owner_bytes=250)
    a = [store.put("a", rows) for _ in range(3)]
    # One owner holds at most two such results, so the third pushes out the first.
    assert store.get("a", a[0]) is None and all(store.get("a", id) for id in a[1:])
    b = [store.put("b", rows) for _ in range(2)]
    c = store.put("c", rows)
    # The whole store holds at most four: everyone's oldest makes room.
    assert store.get("a", a[1]) is None and store.get("c", c) and all(store.get("b", id) for id in b)


def test_model_keys_keep_dotted_namespaces_apart():
    dotted = ModelRef(database="db-" + "0" * 32, namespace=["a.b"], name="m")
    nested = ModelRef(database="db-" + "0" * 32, namespace=["a", "b"], name="m")
    assert dotted.key != nested.key


@pytest.mark.parametrize("target", ["https://evil.example", "//evil.example", "/\\evil.example", "/\\\\evil",
                                    "evil", "/ok\\..\\x"])
def test_sign_in_returns_only_to_this_origin(target):
    login = BrowserLogin(SimpleNamespace(origin="https://cbi", client_id="c", issuer="https://kc/realms/r"), None)
    login.start(target)
    assert next(iter(login.pending.values()))["return_to"] == "/"
    login.start("/#/ask?model=x")
    assert list(login.pending.values())[-1]["return_to"] == "/#/ask?model=x"


def test_tickets_and_threads():
    tickets = RunTickets(ttl=60)
    ticket = tickets.issue("session", "sub")
    assert tickets.resolve(ticket) == ("session", "sub") and tickets.resolve("x") is None
    threads = ThreadOwners()
    assert threads.claim("t", "a") and threads.claim("t", "a") and not threads.claim("t", "b")


def test_a_result_too_large_stops_the_worker(tmp_path, monkeypatch):
    # A worker that keeps writing: the gateway must stop reading, and the worker, at the limit.
    (tmp_path / "chatty.py").write_text("import sys\nwhile True:\n    sys.stdout.write('x' * 65536)\n")
    monkeypatch.setattr(executor, "MAX_OUTPUT", 200_000)
    monkeypatch.setattr(sys, "path", [str(tmp_path), *sys.path])
    monkeypatch.setattr(executor, "ROOT", tmp_path)
    run = executor.Executor(timeout=10, module="chatty")
    with pytest.raises(CbiError) as raised:
        asyncio.run(run.run({}))
    assert raised.value.code == "result_too_large"


def test_max_rows_never_exceeds_what_a_query_may_ask():
    env = {"OIDC_ISSUER": "http://localhost:8080/realms/iceberg", "BRIDGE_CLIENT_ID": "ext-conversationalbi",
           "BRIDGE_CLIENT_SECRET": "s" * 48}
    assert Settings.from_env({**env, "BI_MAX_ROWS": "50000"}).max_rows == 5000
    assert Settings.from_env({**env, "BI_MAX_ROWS": "250"}).max_rows == 250
