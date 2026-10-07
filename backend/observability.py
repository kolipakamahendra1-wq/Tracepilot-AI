"""Prometheus metrics, OpenTelemetry tracing and optional Langfuse LLM tracing.

All of it degrades to a no-op when not configured.
"""
import os
from contextlib import contextmanager

from prometheus_client import Counter, Histogram

INVESTIGATIONS = Counter("tracepilot_investigations_total", "Investigations run", ["status"])
INVESTIGATION_SECONDS = Histogram("tracepilot_investigation_seconds", "Investigation duration")
TOOL_CALLS = Counter("tracepilot_tool_calls_total", "Diagnostic tool calls", ["tool", "status"])
LLM_TOKENS = Counter("tracepilot_llm_tokens_total", "LLM tokens", ["kind"])
LLM_COST = Counter("tracepilot_llm_cost_usd_total", "Estimated LLM spend in USD")
RATE_LIMITED = Counter("tracepilot_rate_limited_total", "Requests rejected by the rate limiter")


def setup_tracing(app) -> None:
    """Instrument FastAPI with OpenTelemetry; export OTLP only if an endpoint is configured."""
    from opentelemetry import trace
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider

    provider = TracerProvider(resource=Resource.create({"service.name": os.getenv("OTEL_SERVICE_NAME", "tracepilot-api")}))
    if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)


@contextmanager
def llm_span(name: str, model: str, prompt: str):
    """Langfuse generation span if LANGFUSE_* keys are set, else a no-op. Yields a callable to record output."""
    record = lambda output, usage=None: None  # noqa: E731
    if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
        yield record
        return
    try:
        from langfuse import get_client

        cm = get_client().start_as_current_observation(name=name, as_type="generation", model=model, input=prompt)
        gen = cm.__enter__()
    except Exception:  # noqa: BLE001 - tracing must never break an investigation
        yield record
        return

    def record(output, usage=None):  # noqa: F811
        try:
            gen.update(output=output, usage_details=usage)
        except Exception:  # noqa: BLE001
            pass

    try:
        yield record
    finally:
        try:
            cm.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
