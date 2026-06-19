"""
OpenTelemetry instrumentation for AgentMem.

Install the optional otel extras:
    pip install 'agentmem[otel]'

Then set in .env:
    OTEL_SERVICE_NAME=agentmem
    OTEL_EXPORTER_OTLP_ENDPOINT=http://your-collector:4317

If the otel packages are not installed or OTEL_EXPORTER_OTLP_ENDPOINT is
empty, this module silently provides no-op stubs — no code changes needed
in callers.

Usage in service functions:
    from .telemetry import tracer

    with tracer.start_as_current_span("memory.add") as span:
        span.set_attribute("namespace", namespace)
        span.set_attribute("agent_id", req.agent_id)
        ...
"""
from __future__ import annotations

import os


class _NoOpSpan:
    """Drop-in replacement when OTel is not installed."""
    def set_attribute(self, *a, **k): pass
    def record_exception(self, *a, **k): pass
    def set_status(self, *a, **k): pass
    def __enter__(self): return self
    def __exit__(self, *a): pass


class _NoOpTracer:
    def start_as_current_span(self, name: str, **kwargs) -> _NoOpSpan:
        return _NoOpSpan()


def _build_tracer():
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        return _NoOpTracer()

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        service_name = os.environ.get("OTEL_SERVICE_NAME", "agentmem")
        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=endpoint)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        return trace.get_tracer("agentmem")

    except ImportError:
        return _NoOpTracer()


tracer = _build_tracer()


def instrument_fastapi(app) -> None:
    """Auto-instrument FastAPI request spans if OTel is available."""
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(app)
    except ImportError:
        pass


def instrument_sqlalchemy(engine) -> None:
    """Auto-instrument SQLAlchemy query spans if OTel is available."""
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        return
    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        SQLAlchemyInstrumentor().instrument(engine=engine)
    except ImportError:
        pass
