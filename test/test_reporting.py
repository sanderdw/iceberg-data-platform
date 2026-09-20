"""Reporting proof: parameterization, snapshot/cache boundaries and render isolation."""

import time
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from server.models import ServiceError
from user_portal.reporting.query import ReportQuery, compile_queries, exact_value
from user_portal.reporting.service import ReportingProof
from user_portal.reporting.worker import board_definition, render_board

QUERY = {"measure": "energy", "timestamp": "measured_at", "category": "street"}
DATA = {"records": 2, "total": "123.4567890123456789", "rows": [["2026-09-01T00:00:00+00:00", "Street", 1.5]]}


def test_visual_query_quotes_identifiers_binds_values_and_pins_snapshot():
    attack = "' OR true; COPY secrets TO 'https://invalid'; --"
    query = ReportQuery(**QUERY, category_value=attack)
    source = {"namespace": ["a.b", 'c"d'], "table": 't"x', "snapshotId": "9007199254740993"}
    summary, trend, params = compile_queries(source, query)
    assert attack not in summary + trend and params == [attack]
    assert '"lakehouse"."a.b.c""d"."t""x" AT (VERSION => 9007199254740993)' in summary
    assert "LIMIT 1001" in trend and "?" in summary and "?" in trend
    with pytest.raises(ValueError, match="snapshot"):
        compile_queries({**source, "snapshotId": "1); DROP TABLE t"}, query)


def test_empty_preflight_does_not_read_a_concurrently_created_snapshot():
    source = {"namespace": ["analytics"], "table": "events", "snapshotId": None}
    summary, trend, _ = compile_queries(source, ReportQuery(**QUERY))
    assert "WHERE false" in summary and "WHERE false" in trend


@pytest.mark.parametrize(
    "extra", [{"sql": "SELECT 1"}, {"grain": "day'); DROP TABLE t; --"}, {"category_value": 42}]
)
def test_visual_contract_rejects_sql_and_untyped_parameters(extra):
    with pytest.raises(ValidationError):
        ReportQuery.model_validate({**QUERY, **extra})


def test_exact_api_values_are_separate_from_chart_numbers():
    assert exact_value(9007199254740993) == "9007199254740993"
    assert exact_value(Decimal("123.4567890123456789")) == "123.4567890123456789"
    assert exact_value(datetime(2026, 9, 1, tzinfo=UTC)) == "2026-09-01T00:00:00+00:00"
    for value in (float("nan"), float("inf"), "a" * 513):
        with pytest.raises(ValueError):
            exact_value(value)
    data = deepcopy(DATA)
    board = board_definition(data)
    assert data == DATA
    assert isinstance(board["queries"]["summary"]["values"][0][1], float)
    assert all(q["type"] == "values" for q in board["queries"].values())
    empty = board_definition({**DATA, "records": 0, "total": None, "rows": []})
    assert "trend" not in empty["queries"]


def test_renderer_never_receives_inherited_credentials(monkeypatch):
    monkeypatch.setenv("POLARIS_CLIENT_SECRET", "platform-secret")
    monkeypatch.setenv("ICEBERG_TOKEN", "viewer-token")

    def render(args, **kwargs):
        assert "platform-secret" not in str(kwargs) and "viewer-token" not in str(kwargs)
        assert kwargs["timeout"] == 20
        assert "--no-cache" in args
        Path(args[args.index("--output") + 1]).write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("user_portal.reporting.worker.subprocess.run", render)
    assert "<svg" in render_board(DATA)[0]


@pytest.fixture
def proof():
    session = SimpleNamespace(
        id="s1", user_id="u1", team="team", environment="development", expires=time.monotonic() + 3600
    )
    details = {"currentSnapshotId": "1", "uuid": "uuid", "schemaId": 0}
    directory = Mock()
    directory.details.side_effect = lambda *args: deepcopy(details)
    directory.preview_request.side_effect = lambda *args: {
        "snapshotId": details["currentSnapshotId"],
        "token": "viewer-secret",
    }
    runner = Mock(return_value={"data": deepcopy(DATA), "svg": "<svg/>"})
    service = ReportingProof(directory, runner)

    def run(query=None, **kwargs):
        return service.run(session, "db", ["analytics"], "events", query or QUERY, **kwargs)

    return session, details, directory, runner, service, run


def test_cache_is_scoped_to_identity_context_parameters_snapshot_and_schema(proof):
    session, details, directory, runner, _, run = proof
    assert not run()["cached"]
    cached = run()
    assert cached["cached"] and runner.call_count == 1
    assert cached["timing"]["querySeconds"] == cached["timing"]["renderSeconds"] == 0
    assert directory.details.call_count == 4  # Each hit rechecks access before and after.
    for field, value in (("user_id", "u2"), ("id", "s2"), ("team", "team2"), ("environment", "production")):
        setattr(session, field, value)
        assert not run()["cached"]
    for field, value in (("currentSnapshotId", "2"), ("schemaId", 1), ("uuid", "recreated")):
        details[field] = value
        assert not run()["cached"]
    assert not run({**QUERY, "category_value": "Other"})["cached"]
    assert not run(refresh=True)["cached"]
    assert run()["cached"]


def test_cached_result_cannot_bypass_revocation_or_session_expiry(proof):
    session, _, directory, runner, _, run = proof
    run()
    directory.details.side_effect = ServiceError(403, "Access revoked")
    with pytest.raises(ServiceError, match="revoked"):
        run()
    assert runner.call_count == 1
    session.expires = 0
    with pytest.raises(ServiceError, match="Sign in"):
        run()


@pytest.mark.parametrize("change", ["context", "schema", "revoke", "expire"])
def test_inflight_changes_discard_result_and_do_not_cache(proof, change):
    session, details, directory, runner, service, run = proof

    def execute(request):
        if change == "context":
            session.environment = "production"
        elif change == "schema":
            details["schemaId"] = 2
        elif change == "revoke":
            directory.details.side_effect = ServiceError(403, "Revoked")
        else:
            session.expires = 0
        return {"data": deepcopy(DATA)}

    runner.side_effect = execute
    with pytest.raises(ServiceError):
        run()
    assert not service.cache


def test_cache_is_bounded_and_expires(proof):
    _, _, _, runner, service, run = proof
    for index in range(12):
        run({**QUERY, "category_value": str(index)})
    assert len(service.cache) == 8
    for key, (_, result) in list(service.cache.items()):
        service.cache[key] = (0, result)
    assert not run({**QUERY, "category_value": "11"})["cached"]
    assert runner.call_count == 13 and len(service.cache) == 1
