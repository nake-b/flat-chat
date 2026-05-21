import logging

from flat_chat.core.config import settings

logger = logging.getLogger(__name__)


def setup_observability() -> None:
    if not settings.phoenix_enabled:
        return

    try:
        from openinference.instrumentation.pydantic_ai import (
            OpenInferenceSpanProcessor,
        )
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from pydantic_ai import Agent

        provider = TracerProvider()
        provider.add_span_processor(OpenInferenceSpanProcessor())
        provider.add_span_processor(
            SimpleSpanProcessor(OTLPSpanExporter(endpoint=settings.phoenix_endpoint))
        )
        trace.set_tracer_provider(provider)

        # Tell Pydantic AI to emit OTel spans for every agent run, model
        # request, and tool call. Without this, OpenInferenceSpanProcessor
        # has nothing to enrich.
        Agent.instrument_all()

        logger.info("Phoenix observability enabled → %s", settings.phoenix_endpoint)
    except ImportError:
        logger.warning("Phoenix deps not installed — run: uv sync --group dev")
