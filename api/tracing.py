"""
OpenTelemetry tracing setup.
Provides distributed tracing for agent pipeline execution.
Falls back to no-op if OTLP endpoint is unavailable (demo safe).
"""
import structlog
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.resources import Resource

_tracer: trace.Tracer | None = None


def setup_tracing(service_name: str = "invoice-agent-system") -> trace.Tracer:
    """Initialize OpenTelemetry tracer. Falls back to console exporter if OTLP unavailable."""
    global _tracer
    if _tracer:
        return _tracer

    from config import settings

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    if settings.enable_tracing:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            otlp_exporter = OTLPSpanExporter(endpoint=settings.otlp_endpoint, insecure=True)
            provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
        except Exception:
            # OTLP unavailable — use console exporter (visible in terminal)
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer(service_name)
    return _tracer


def get_tracer() -> trace.Tracer:
    return _tracer or setup_tracing()
