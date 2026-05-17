import logging

from flat_chat.core.config import settings

logger = logging.getLogger(__name__)


def setup_observability() -> None:
    if not settings.phoenix_enabled:
        return

    try:
        from openinference.instrumentation.pydantic_ai import (
            PydanticAIInstrumentor,
        )
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            SimpleSpanProcessor,
        )

        provider = TracerProvider()
        exporter = OTLPSpanExporter(endpoint=settings.phoenix_endpoint)
        provider.add_span_processor(SimpleSpanProcessor(exporter))

        PydanticAIInstrumentor().instrument(tracer_provider=provider)
        logger.info("Phoenix observability enabled → %s", settings.phoenix_endpoint)
    except ImportError:
        logger.warning(
            "Phoenix deps not installed — run: "
            "uv sync --group dev"
        )
