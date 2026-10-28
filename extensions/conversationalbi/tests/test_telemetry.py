"""OpenTelemetry export: off without an endpoint, logs and traces over OTLP/HTTP with one."""

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
EMIT = ("import logging, httpx; from conversationalbi.__main__ import telemetry; "
        "logging.basicConfig(level=logging.INFO); telemetry('iceberg-conversationalbi'); "
        "logging.getLogger('conversationalbi').info('ready'); httpx.get('{endpoint}/ping')")


def run(code, **env):
    clean = {k: v for k, v in os.environ.items() if not k.startswith("OTEL_")}
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env={**clean, **env},
                          capture_output=True, text=True, check=True, timeout=60)


class Collector(BaseHTTPRequestHandler):
    received: ClassVar[list] = []

    def do_POST(self):
        self.received.append((self.path, self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


def test_without_an_endpoint_nothing_is_imported():
    result = run("import sys; from conversationalbi.__main__ import telemetry; telemetry('iceberg-conversationalbi'); "
                 "print(any(m.startswith('opentelemetry') for m in sys.modules))")
    assert result.stdout.strip() == "False"


def test_logs_and_traces_reach_the_endpoint():
    server = HTTPServer(("127.0.0.1", 0), Collector)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    try:
        run(EMIT.replace("{endpoint}", endpoint), OTEL_EXPORTER_OTLP_ENDPOINT=endpoint)
    finally:
        server.shutdown()
    assert {"/v1/logs", "/v1/traces"} <= {path for path, _ in Collector.received}
    logs = ExportLogsServiceRequest()
    for path, body in Collector.received:
        if path == "/v1/logs":
            logs.MergeFromString(body)
    resource = {a.key: a.value.string_value for a in logs.resource_logs[0].resource.attributes}
    assert resource["service.name"] == "iceberg-conversationalbi"
    records = [r.body.string_value for rl in logs.resource_logs for sl in rl.scope_logs for r in sl.log_records]
    assert "ready" in records


def test_agent_runs_are_traced_with_model_and_tool_calls():
    Collector.received.clear()
    server = HTTPServer(("127.0.0.1", 0), Collector)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    code = ("from conversationalbi.__main__ import telemetry; telemetry('iceberg-conversationalbi'); "
            "from pydantic_ai import Agent; from pydantic_ai.models.test import TestModel; "
            "agent = Agent(TestModel()); agent.tool_plain(lambda: 'ok', name='list_models'); "
            "agent.run_sync('Which carrier is late most often?')")
    try:
        run(code, OTEL_EXPORTER_OTLP_ENDPOINT=endpoint)
    finally:
        server.shutdown()
    traces = ExportTraceServiceRequest()
    for path, body in Collector.received:
        if path == "/v1/traces":
            traces.MergeFromString(body)
    spans = [s for rs in traces.resource_spans for ss in rs.scope_spans for s in ss.spans]
    names = {s.name for s in spans}
    assert any(name.startswith("invoke_agent") or name == "agent run" for name in names), names
    assert any(name.startswith("chat ") for name in names), names
    assert any(name.startswith("execute_tool list_models") or name == "running tool" for name in names), names
    attributes = {a.key for s in spans for a in s.attributes}
    assert "gen_ai.usage.input_tokens" in attributes
