"""OpenTelemetry wiring: tracer provider + OTLP exporter + FastAPI instrumentation.

Traces export via OTLP/gRPC to whatever collector is configured
(`WEBHOOK_OTEL_EXPORTER_OTLP_ENDPOINT`) — an OpenTelemetry Collector in
front of Jaeger/Tempo locally, or a managed backend in production. Keeping
the app pointed at a collector (not a vendor SDK) is what lets us swap
backends later without touching application code.
"""

from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased

from webhook_service.core.config import settings

if TYPE_CHECKING:
    from fastapi import FastAPI


def configure_tracing() -> None:
    if not settings.otel_enabled:
        return

    resource = Resource.create(
        {
            SERVICE_NAME: settings.app_name,
            SERVICE_VERSION: "0.1.0",
            "deployment.environment": settings.environment,
        }
    )

    provider = TracerProvider(
        resource=resource,
        sampler=TraceIdRatioBased(settings.otel_traces_sample_ratio),
    )
    exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)


def instrument_app(app: "FastAPI") -> None:
    if not settings.otel_enabled:
        return
    FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz")


def get_tracer(name: str) -> trace.Tracer:
    return trace.get_tracer(name)
