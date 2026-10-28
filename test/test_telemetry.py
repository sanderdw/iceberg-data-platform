"""OpenTelemetry export: off without an endpoint, and the same records plus traces over OTLP/HTTP with one."""

import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import ClassVar

from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

ROOT = Path(__file__).resolve().parent.parent

# A notebook route and an ordinary route that both call out; sync handlers run in the threadpool.
PROXY = ("import httpx; from fastapi import FastAPI; from fastapi.testclient import TestClient; "
         "from server.entrypoints import logs, traced; logs('iceberg-users'); app = FastAPI(); "
         "app.get('/workspaces/{id}/x')(lambda id: httpx.get('{endpoint}/authorize-notebook').status_code); "
         "app.get('/api/teams')(lambda: httpx.get('{endpoint}/authorize-api').status_code); "
         "client = TestClient(traced(app)); client.get('/workspaces/w1/x'); client.get('/api/teams')")
EMIT = ("import httpx; from server.entrypoints import logs; logs('iceberg-bridge'); "
        "from server.bridge import audit; "
        "audit('listing.read', {'iceberg.share.id': 'share-1', 'iceberg.extension': 'openmetadata'}, "
        "'listing read: %s extension=%s', 'share-1', 'openmetadata'); "
        "httpx.get('{endpoint}/ping'); httpx.get('{endpoint}/workspaces/w1/')")


def run(code, **env):
    clean = {k: v for k, v in os.environ.items() if not k.startswith("OTEL_")}
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env={**clean, **env},
                          capture_output=True, text=True, check=True, timeout=60)


class Collector(BaseHTTPRequestHandler):
    received: ClassVar[list] = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.received.append((self.path, self.headers.get("Authorization"), body))
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


def test_without_an_endpoint_nothing_is_imported_or_sent():
    result = run("import logging, sys; from server.entrypoints import logs; logs('iceberg-bridge'); "
                 "print(sorted({type(h).__name__ for h in logging.getLogger().handlers})); "
                 "print(any(m.startswith('opentelemetry') for m in sys.modules))")
    assert result.stdout.split() == ["['StreamHandler']", "False"]


def test_otlp_export_carries_the_audit_fields_and_traces():
    server = HTTPServer(("127.0.0.1", 0), Collector)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    try:
        result = run(EMIT.replace("{endpoint}", endpoint), OTEL_EXPORTER_OTLP_ENDPOINT=endpoint,
                     OTEL_EXPORTER_OTLP_HEADERS="Authorization=test-key")
    finally:
        server.shutdown()
    # stderr keeps the readable line.
    assert "INFO iceberg.bridge.audit: listing read: share-1 extension=openmetadata" in result.stderr
    paths = {path for path, _, _ in Collector.received}
    assert {"/v1/logs", "/v1/traces"} <= paths
    assert {auth for _, auth, _ in Collector.received} == {"test-key"}

    logs = ExportLogsServiceRequest()
    for path, _, body in Collector.received:
        if path == "/v1/logs":
            logs.MergeFromString(body)
    resource = {a.key: a.value.string_value for a in logs.resource_logs[0].resource.attributes}
    assert resource["service.name"] == "iceberg-bridge"
    assert resource["service.version"]
    records = [r for rl in logs.resource_logs for sl in rl.scope_logs for r in sl.log_records]
    record = next(r for r in records if r.event_name == "iceberg.bridge.listing.read")
    attributes = {a.key: a.value.string_value for a in record.attributes}
    assert attributes["iceberg.share.id"] == "share-1"
    assert attributes["iceberg.extension"] == "openmetadata"
    assert record.body.string_value == "listing read: share-1 extension=openmetadata"

    traces = ExportTraceServiceRequest()
    for path, _, body in Collector.received:
        if path == "/v1/traces":
            traces.MergeFromString(body)
    spans = [s for rs in traces.resource_spans for ss in rs.scope_spans for s in ss.spans]
    urls = [a.value.string_value for s in spans for a in s.attributes if a.key in ("http.url", "url.full")]
    assert any(url.endswith("/ping") for url in urls)
    # Proxied notebook traffic stays out.
    assert not any("/workspaces/" in url for url in urls)


def collect(code):
    Collector.received.clear()
    server = HTTPServer(("127.0.0.1", 0), Collector)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    try:
        run(code.replace("{endpoint}", endpoint), OTEL_EXPORTER_OTLP_ENDPOINT=endpoint)
    finally:
        server.shutdown()
    traces = ExportTraceServiceRequest()
    for path, _, body in Collector.received:
        if path == "/v1/traces":
            traces.MergeFromString(body)
    return [s for rs in traces.resource_spans for ss in rs.scope_spans for s in ss.spans]


def test_proxied_notebook_requests_make_no_spans_at_all():
    spans = collect(PROXY)
    urls = [a.value.string_value for s in spans for a in s.attributes if a.key in ("http.url", "url.full")]
    assert any(url.endswith("/authorize-api") for url in urls)
    assert not any(url.endswith("/authorize-notebook") for url in urls)
    assert not any("/workspaces/" in url for url in urls)


def test_audit_names_the_user_by_id_and_sign_in_name():
    from types import SimpleNamespace

    from server.bridge import actor

    user = SimpleNamespace(principal={"name": "portal-1", "properties": {"portal.name": "demo-admin"}})
    assert actor(user) == {"user.id": "portal-1", "user.name": "demo-admin"}
    assert actor(SimpleNamespace(principal=None)) == {}
