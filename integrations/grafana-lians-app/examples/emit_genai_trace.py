"""Emit one GenAI decision trace through Grafana Alloy.

Install:
  pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
Run:
  OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 python emit_genai_trace.py
"""
from __future__ import annotations

import os
import time

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318").rstrip("/")
provider = TracerProvider(resource=Resource.create({"service.name": "lians-demo-agent"}))
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")))
trace.set_tracer_provider(provider)
tracer = trace.get_tracer("lians.grafana.demo", "0.1.0")

with tracer.start_as_current_span("chat planning-recommendation") as span:
    span.set_attribute("gen_ai.operation.name", "chat")
    span.set_attribute("gen_ai.provider.name", "openai")
    span.set_attribute("gen_ai.request.model", "gpt-5")
    span.set_attribute("gen_ai.response.model", "gpt-5-2026-06-01")
    span.set_attribute("gen_ai.agent.id", "planning-assistant")
    span.set_attribute("gen_ai.usage.input_tokens", 128)
    span.set_attribute("gen_ai.usage.output_tokens", 52)
    span.set_attribute("lians.decision.id", "6b0b9c76-7112-4d73-84d7-561566ceb99f")
    span.set_attribute("lians.decision.type", "study_plan_recommendation")
    span.set_attribute("lians.decision.outcome", "recommended")
    span.set_attribute("lians.workflow.id", "weekly-study-plan")
    span.set_attribute("lians.policy.version", "study-policy-3")
    span.set_attribute("lians.capture.status", "complete")
    span.set_attribute("lians.knowledge.as_of", "2026-07-27T12:00:00Z")
    time.sleep(0.05)

provider.shutdown()
print("Sent one GenAI decision trace through Alloy.")
