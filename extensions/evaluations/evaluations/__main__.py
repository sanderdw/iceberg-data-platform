import logging
import os

import uvicorn

from .config import VERSION

# Health checks would drown the traces, and the sign-in callback's URL carries the one-use code.
UNTRACED = "health$,/auth/callback"


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
    # Every score as a gen_ai.evaluation.result event (runner.py); not in the container log.
    logging.getLogger("evaluations.scores").addHandler(LoggingHandler(logger_provider=provider))
    HTTPXClientInstrumentor().instrument()
    # Every FastAPI app created from now on.
    excluded = os.environ.get("OTEL_PYTHON_FASTAPI_EXCLUDED_URLS", UNTRACED)
    FastAPIInstrumentor().instrument(excluded_urls=excluded, exclude_spans=["receive", "send"])
    # The LLM judge's calls.
    from pydantic_ai import Agent

    Agent.instrument_all()


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # httpx logs every request at INFO and PyIceberg every FileIO it loads; keep the log about runs.
    for name in ("httpx", "pyiceberg"):
        logging.getLogger(name).setLevel(logging.WARNING)
    telemetry("iceberg-evaluations")
    uvicorn.run("evaluations.app:create_app", factory=True, host=os.environ.get("HOST", "0.0.0.0"),
                port=int(os.environ.get("PORT", "3012")), proxy_headers=True, access_log=False)


if __name__ == "__main__":
    main()
