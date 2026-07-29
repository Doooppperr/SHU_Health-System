from __future__ import annotations

from contextlib import contextmanager

from opentelemetry import trace


def init_observability(app):
    if not app.config.get("OTEL_ENABLED"):
        app.extensions["otel_enabled"] = False
        return

    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.flask import FlaskInstrumentor
    from opentelemetry.instrumentation.requests import RequestsInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create(
        {
            "service.name": app.config.get("OTEL_SERVICE_NAME", "healthdoc-backend"),
            "deployment.environment": app.config.get("ENV", "unknown"),
        }
    )
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(
        endpoint=app.config.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    FlaskInstrumentor().instrument_app(
        app,
        excluded_urls="/api/health",
        tracer_provider=provider,
    )
    RequestsInstrumentor().instrument(tracer_provider=provider)
    app.extensions["otel_enabled"] = True
    app.extensions["otel_provider"] = provider


@contextmanager
def span(name: str, **attributes):
    tracer = trace.get_tracer("healthdoc.agent")
    safe = {
        key: value
        for key, value in attributes.items()
        if value is not None and isinstance(value, (str, bool, int, float))
    }
    with tracer.start_as_current_span(name, attributes=safe) as current:
        yield current
