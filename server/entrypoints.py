"""Application entrypoints shared by source and published container images."""

import logging
import os
import tomllib
from pathlib import Path

# Health checks and proxied notebook traffic would drown the traces.
# The sign-in callback carries its one-time code in the URL.
UNTRACED = "health$,/auth/callback,//[^/]+/workspaces/"
tracing = False


def logs(service, traces=True):
    """Send the platform's own records, including the Bridge audit trail, to stderr at INFO.

    Uvicorn configures only its own loggers, so without this every INFO record is dropped.
    Libraries stay at WARNING: httpx alone would log every Polaris call.
    """
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    for name in ("server", "user_portal", "iceberg"):
        logging.getLogger(name).setLevel(logging.INFO)
    otel(service, traces)


def otel(service, traces=True):
    """Also export the same records, and traces, over OTLP/HTTP when OTEL_EXPORTER_OTLP_ENDPOINT is set.

    The standard OTEL_* variables configure the rest: OTEL_EXPORTER_OTLP_HEADERS for a backend's API key,
    OTEL_SERVICE_NAME and OTEL_RESOURCE_ATTRIBUTES for the resource. Without an endpoint nothing is imported.
    """
    global tracing
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or os.environ.get("OTEL_SDK_DISABLED") == "true":
        return
    from opentelemetry import trace
    from opentelemetry._logs import set_logger_provider
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.logging.handler import LoggingHandler
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({"service.name": os.environ.get("OTEL_SERVICE_NAME") or service,
                                "service.version": version()})
    provider = LoggerProvider(resource=resource)
    provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    set_logger_provider(provider)
    # Beside the stderr handler, so the container logs stay as they are.
    logging.getLogger().addHandler(LoggingHandler(logger_provider=provider))
    if traces:
        tracer = TracerProvider(resource=resource)
        tracer.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(tracer)
        # The proxy's calls to notebooks too; the instrumentation reads this variable only.
        os.environ.setdefault("OTEL_PYTHON_HTTPX_EXCLUDED_URLS", UNTRACED)
        HTTPXClientInstrumentor().instrument()
        tracing = True


def traced(app):
    if tracing:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        excluded = os.environ.get("OTEL_PYTHON_FASTAPI_EXCLUDED_URLS", UNTRACED)
        FastAPIInstrumentor.instrument_app(app, excluded_urls=excluded, exclude_spans=["receive", "send"])
        app.add_middleware(QuietNotebooks)
    return app


class QuietNotebooks:
    """No spans for the HTTP calls of proxied notebook traffic either.

    Each proxied request checks the user's access in Polaris again; untraced, those calls would be
    root spans, several per notebook request.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket") and scope["path"].startswith("/workspaces/"):
            from opentelemetry.instrumentation.utils import suppress_http_instrumentation

            with suppress_http_instrumentation():
                return await self.app(scope, receive, send)
        return await self.app(scope, receive, send)


def version():
    try:
        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        return tomllib.loads(pyproject.read_text())["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return "unknown"


def identity(portal):
    from .oidc import OIDC

    env = dict(os.environ)
    env.setdefault("OIDC_CLIENT_ID", "iceberg-admin" if portal == "admin" else "iceberg-users")
    env.setdefault("OIDC_CLIENT_SECRET", env.get(f"OIDC_{'PORTAL' if portal == 'admin' else 'USERS'}_SECRET", ""))
    env.setdefault("OIDC_ORIGIN", env.get(f"{'PORTAL' if portal == 'admin' else 'USER'}_ORIGIN", ""))
    if not env["OIDC_CLIENT_SECRET"]:
        raise RuntimeError("Keycloak client secret is required")
    return OIDC(env)


def admin():
    from .app import create_app

    logs("iceberg-portal")
    oidc = identity("admin")
    from .identity import UserManagement

    return traced(create_app(oidc=oidc, secure_cookie=oidc.secure,
                             session_cookie="iceberg_admin", user_management=UserManagement()))


def users():
    from user_portal.app import create_app

    logs("iceberg-users")
    oidc = identity("users")
    return traced(create_app(oidc=oidc, session_cookie="iceberg_user"))


def bridge():
    from .bridge import create_bridge_app

    logs("iceberg-bridge")
    return traced(create_bridge_app())


def monitor():
    from .monitoring import create_monitor_app

    # Logs only: the monitor polls every service, and its own requests would be most of the traces.
    logs("iceberg-monitor", traces=False)
    return create_monitor_app()
