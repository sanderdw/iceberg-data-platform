"""One process, two listeners: the gateway (published) and the agent's AG-UI endpoint (internal only).

They share sessions, run tickets and query results in memory, so the gateway and the agent always
agree on who is asking.
"""

import asyncio
import logging
import os
import signal
from contextlib import nullcontext

import uvicorn

from .config import VERSION

# Health checks would drown the traces.
UNTRACED = "health$"


def telemetry(service):
    """Also export logs and traces over OTLP/HTTP when OTEL_EXPORTER_OTLP_ENDPOINT is set, as the platform does.

    The standard OTEL_* variables configure the rest, such as OTEL_EXPORTER_OTLP_HEADERS for a backend's API key.
    Without an endpoint nothing is imported.
    """
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or os.environ.get("OTEL_SDK_DISABLED") == "true":
        return
    from opentelemetry import trace
    from opentelemetry._logs import set_logger_provider
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.logging.handler import LoggingHandler
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({"service.name": os.environ.get("OTEL_SERVICE_NAME") or service,
                                "service.version": VERSION})
    provider = LoggerProvider(resource=resource)
    provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    set_logger_provider(provider)
    logging.getLogger().addHandler(LoggingHandler(logger_provider=provider))
    tracer = TracerProvider(resource=resource)
    tracer.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tracer)
    HTTPXClientInstrumentor().instrument()
    # Every FastAPI app created from now on.
    excluded = os.environ.get("OTEL_PYTHON_FASTAPI_EXCLUDED_URLS", UNTRACED)
    FastAPIInstrumentor().instrument(excluded_urls=excluded, exclude_spans=["receive", "send"])
    # The agent's model calls (tokens, timing) and tool calls, with their content: prompts, SQL and results.
    from pydantic_ai import Agent

    Agent.instrument_all()


class Server(uvicorn.Server):
    def capture_signals(self):
        return nullcontext()  # main() stops both servers together.


async def serve():
    from .app import create_app  # After telemetry(), so its FastAPI apps are instrumented.

    gateway = create_app()
    host = os.environ.get("HOST", "0.0.0.0")
    servers = [
        Server(uvicorn.Config(gateway, host=host, port=int(os.environ.get("PORT", "3007")), proxy_headers=True,
                              access_log=False)),
        Server(uvicorn.Config(gateway.state.agent_app, host=host, port=int(os.environ.get("AGENT_PORT", "3017")),
                              access_log=False, timeout_keep_alive=75)),
    ]
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: [setattr(s, "should_exit", True) for s in servers])
    await asyncio.gather(*(s.serve() for s in servers))


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    telemetry("iceberg-conversationalbi")
    asyncio.run(serve())


if __name__ == "__main__":
    main()
