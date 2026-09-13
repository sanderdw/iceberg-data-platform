import asyncio
import time
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from server import app as portal_app
from server import monitoring
from server.models import ServiceError
from server.monitoring import Monitor, bucket_usage, container_usage


def test_infrastructure_requires_portal_session(portal, monkeypatch):
    monkeypatch.delenv("MONITOR_URL", raising=False)
    response = portal.client.get("/api/infrastructure")
    assert response.status_code == 503
    assert "not configured" in response.json()["error"]
    portal.client.cookies.clear()
    assert portal.client.get("/api/infrastructure").status_code == 401


@pytest.mark.parametrize("upstream_status", [200, 401, 500])
def test_portal_proxies_only_authenticated_snapshots(portal, monkeypatch, upstream_status):
    monkeypatch.setenv("MONITOR_URL", "http://collector")
    client_type = httpx.AsyncClient

    def upstream(request):
        assert request.url.path == "/snapshot"
        assert request.headers["x-monitor-token"]
        return httpx.Response(
            upstream_status,
            json={"services": {"status": "collecting"}}
            if upstream_status == 200
            else {"error": "upstream-private-secret"},
        )

    monkeypatch.setattr(
        portal_app.httpx, "AsyncClient", lambda **kwargs: client_type(transport=httpx.MockTransport(upstream))
    )
    response = portal.client.get("/api/infrastructure")
    assert response.status_code == (200 if upstream_status == 200 else 503)
    assert "upstream-private-secret" not in response.text


def test_collector_endpoint_requires_secret(monkeypatch):
    secret = "monitor-secret-at-least-16"
    monkeypatch.setenv("PORTAL_PASSWORD", secret)
    collector = Mock()
    collector.snapshot.return_value = {"services": {"status": "collecting"}}
    monkeypatch.setattr(monitoring, "Monitor", lambda env: collector)
    client = TestClient(monitoring.create_monitor_app())
    assert client.get("/snapshot").status_code == 401
    assert client.get("/snapshot", headers={"X-Monitor-Token": "wrong"}).status_code == 401
    response = client.get("/snapshot", headers={"X-Monitor-Token": secret})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert secret not in response.text
    assert client.get("/docs").status_code == 404


def test_container_cpu_memory_and_missing_samples():
    sample = {
        "cpu_stats": {"cpu_usage": {"total_usage": 300}, "system_cpu_usage": 2000, "online_cpus": 4},
        "precpu_stats": {"cpu_usage": {"total_usage": 200}, "system_cpu_usage": 1000},
        "memory_stats": {"usage": 1024, "limit": 4096, "stats": {"inactive_file": 256}},
    }
    assert container_usage(sample) == {"cpuPercent": 40.0, "memoryBytes": 768, "memoryLimitBytes": 4096}
    sample["cpu_stats"]["cpu_usage"]["total_usage"] = 10
    assert container_usage(sample)["cpuPercent"] is None  # counter reset
    assert container_usage({}) == {"cpuPercent": None, "memoryBytes": None, "memoryLimitBytes": None}


def test_bucket_scan_paginates_and_rejects_incomplete_totals():
    storage = Mock()
    storage.call.side_effect = [
        {"Contents": [{"Size": 10}], "IsTruncated": True, "NextContinuationToken": "next"},
        {"Contents": [{"Size": 20}, {"Size": 30}]},
    ]
    assert bucket_usage(storage, "bucket", time.monotonic() + 10) == {"bytes": 60, "objects": 3}
    assert storage.call.call_args.kwargs["ContinuationToken"] == "next"
    storage.call.side_effect = None
    storage.call.return_value = {"Contents": [{"Size": 10}], "IsTruncated": True}
    with pytest.raises(ValueError):
        bucket_usage(storage, "bucket", time.monotonic() + 10)
    with pytest.raises(TimeoutError):
        bucket_usage(storage, "bucket", time.monotonic() - 1)
    storage.call.return_value = {"IsTruncated": True, "NextContinuationToken": "next"}
    with pytest.raises(TimeoutError):
        bucket_usage(storage, "bucket", time.monotonic() + 10, max_pages=1)


def test_failed_bucket_does_not_make_partial_total_look_complete():
    monitor = Monitor.__new__(Monitor)
    monitor.provider = Mock()
    monitor.provider.list_databases.return_value = [
        {"name": "first", "bucket": "one", "team": "team"},
        {"name": "second", "bucket": "two", "team": "team"},
    ]
    monitor.provider.storage.call.side_effect = [{"Contents": [{"Size": 42}]}, ServiceError(503, "secret")]
    result = monitor.storage()
    assert result["bytes"] is None and result["objects"] is None
    assert result["complete"] is False
    assert result["buckets"][0]["bytes"] == 42
    assert result["buckets"][1]["status"] == "unavailable"
    assert "secret" not in str(result)


def test_failed_source_preserves_last_successful_timestamp(monkeypatch):
    monitor = Monitor.__new__(Monitor)
    monitor.samples = {"disk": {"status": "online", "updatedAt": "old", "data": {"freeBytes": 42}}}

    async def stop(interval):
        raise asyncio.CancelledError

    monkeypatch.setattr(monitoring.asyncio, "sleep", stop)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(monitor.poll("disk", Mock(side_effect=RuntimeError("secret")), 15))
    assert monitor.samples["disk"]["updatedAt"] == "old"
    assert monitor.samples["disk"]["data"]["freeBytes"] == 42
    assert monitor.samples["disk"]["status"] == "unavailable"
    assert "secret" not in str(monitor.snapshot())


def test_probe_history_is_bounded_and_http_errors_are_failures():
    monitor = Monitor.__new__(Monitor)
    monitor.env, monitor.history = {}, {}
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as client:
        monitor.http = client
        for _ in range(65):
            results = monitor.services()
    for service in results:
        assert service["status"] == "offline"
        assert service["samples"] == 60
        assert len(service["history"]) == 60
        assert service["availabilityPercent"] == 0
        assert service["latencyMs"] is None
