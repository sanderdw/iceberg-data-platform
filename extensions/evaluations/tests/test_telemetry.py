"""OpenTelemetry export: off without an endpoint, logs and traces over OTLP/HTTP with one."""

import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import ClassVar

from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest

ROOT = Path(__file__).resolve().parent.parent
EMIT = ("import logging, httpx; from evaluations.__main__ import telemetry; "
        "logging.basicConfig(level=logging.INFO); telemetry('iceberg-evaluations'); "
        "logging.getLogger('evaluations').info('ready'); httpx.get('{endpoint}/ping')")


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
    result = run("import sys; from evaluations.__main__ import telemetry; telemetry('iceberg-evaluations'); "
                 "print(any(m.startswith('opentelemetry') for m in sys.modules))")
    assert result.stdout.strip() == "False"


def test_the_sign_in_callback_is_not_traced():
    Collector.received.clear()
    server = HTTPServer(("127.0.0.1", 0), Collector)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    code = ("from evaluations.__main__ import telemetry; telemetry('iceberg-evaluations'); "
            "import fastapi; from fastapi.testclient import TestClient; app = fastapi.FastAPI(); "
            "[app.get(path)(lambda: {}) for path in ('/auth/callback', '/other')]; client = TestClient(app); "
            "client.get('/auth/callback?code=one-use-code&state=s'); client.get('/other')")
    try:
        run(code, OTEL_EXPORTER_OTLP_ENDPOINT=endpoint)
    finally:
        server.shutdown()
    traces = b"".join(body for path, body in Collector.received if path == "/v1/traces")
    assert b"/other" in traces and b"one-use-code" not in traces


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
    assert resource["service.name"] == "iceberg-evaluations"
    records = [r.body.string_value for rl in logs.resource_logs for sl in rl.scope_logs for r in sl.log_records]
    assert "ready" in records


def test_scores_are_gen_ai_evaluation_events_on_the_case_span():
    Collector.received.clear()
    server = HTTPServer(("127.0.0.1", 0), Collector)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    code = ("from evaluations.__main__ import telemetry; telemetry('iceberg-evaluations'); "
            "from evaluations.runner import TRACER, emit_scores; "
            "span = TRACER.start_span('evaluate case'); "
            "emit_scores({'name': 'delay', 'scores': {'grounded': {'value': False, 'reason': 'Not 42.'}}}, "
            "span.get_span_context()); span.end()")
    try:
        result = run(code, OTEL_EXPORTER_OTLP_ENDPOINT=endpoint)
    finally:
        server.shutdown()
    assert "grounded" not in result.stderr
    logs = ExportLogsServiceRequest()
    for path, body in Collector.received:
        if path == "/v1/logs":
            logs.MergeFromString(body)
    [record] = [r for rl in logs.resource_logs for sl in rl.scope_logs for r in sl.log_records]
    assert record.event_name == "gen_ai.evaluation.result"
    attributes = {a.key: a.value for a in record.attributes}
    assert attributes["gen_ai.evaluation.name"].string_value == "grounded"
    assert attributes["gen_ai.evaluation.score.value"].double_value == 0.0
    assert attributes["gen_ai.evaluation.score.label"].string_value == "fail"
    assert len(record.trace_id) == 16 and any(record.trace_id)
