import logging

from opentelemetry import trace

from flat_chat.core.config import settings

logger = logging.getLogger(__name__)


def setup_observability() -> None:
    """Wire OTel → Phoenix and enable Pydantic AI span emission.

    Called from the FastAPI lifespan at startup. Importing this module
    has no side effects on its own.

    We build the pipeline explicitly rather than calling `phoenix.otel.register()`
    because register()'s default exporter gets silently dropped the first time
    `add_span_processor` is called — and we need to add `OpenInferenceSpanProcessor`
    for LLM-attribute enrichment. Explicit is cheaper than the workaround.
    """
    if not settings.phoenix_enabled:
        return
    try:
        from openinference.instrumentation.pydantic_ai import (
            OpenInferenceSpanProcessor,
        )
        from phoenix.otel import BatchSpanProcessor, HTTPSpanExporter, TracerProvider
        from pydantic_ai import Agent

        provider = TracerProvider()
        # Enrichment: tags Pydantic AI's native spans with OpenInference
        # llm.*/tool.* attributes so Phoenix renders them as chat UI.
        provider.add_span_processor(OpenInferenceSpanProcessor())
        # Transport: batch + flush spans over OTLP/HTTP to the Phoenix collector.
        provider.add_span_processor(
            BatchSpanProcessor(HTTPSpanExporter(endpoint=settings.phoenix_endpoint))
        )
        trace.set_tracer_provider(provider)
        Agent.instrument_all()
        logger.info("Phoenix observability enabled → %s", settings.phoenix_endpoint)
    except ImportError:
        logger.warning("Phoenix deps not installed — run: uv sync")


def shutdown_observability() -> None:
    """Flush buffered spans before process exit. Safe to call when disabled."""
    if not settings.phoenix_enabled:
        return
    provider = trace.get_tracer_provider()
    # ProxyTracerProvider (the no-op default before setup runs) has no shutdown.
    if hasattr(provider, "shutdown"):
        provider.shutdown()
