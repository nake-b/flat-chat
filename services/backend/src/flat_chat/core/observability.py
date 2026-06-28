"""Observability wiring — logs and traces.

Two setup steps, both called from the FastAPI lifespan in `main.py`:

  setup_logging()       — Python stdlib logging via dictConfig. Always on.
  setup_observability() — OpenTelemetry → Phoenix for LLM traces. Toggle via
                          PHOENIX_ENABLED. Pulls heavyweight optional deps.

Logging runs first so any output from observability setup (and the rest of
lifespan / request handling) lands in our configured handler instead of
disappearing into the default `lastResort` path.

Importing this module has no side effects.
"""

import logging
from contextvars import ContextVar
from logging.config import dictConfig

from opentelemetry import trace

from flat_chat.core.config import settings

logger = logging.getLogger(__name__)


# Per-request context. Set at the top of `ChatService.dispatch_agent_request`
# from the AG-UI envelope (session is the long-lived conversation id; run is
# the per-turn id). Every downstream log line (chat, search, …) inherits both
# via the asyncio context. Each request runs in its own task with its own
# context — no leakage between requests, no manual reset needed.
#
# `core/database.py` also reads these to inject a `/* session=… run=… */`
# comment on every SQL statement, so a stalled `pg_stat_activity` row tells
# you which conversation/turn fired it.
session_id_var: ContextVar[str] = ContextVar("session_id", default="")
run_id_var: ContextVar[str] = ContextVar("run_id", default="")


class _RequestContextFilter(logging.Filter):
    """Injects `session_prefix` into every LogRecord.

    Renders as ` [session=<sid> run=<rid>]` with whichever parts are set,
    and empty when neither is — so startup and background logs don't
    carry a noisy `[session=]` marker.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        parts: list[str] = []
        if sid := session_id_var.get():
            parts.append(f"session={sid}")
        if rid := run_id_var.get():
            parts.append(f"run={rid}")
        record.session_prefix = f" [{' '.join(parts)}]" if parts else ""
        return True


def setup_logging() -> None:
    """Configure stdlib logging for the `flat_chat` namespace.

    Format intentionally differs from uvicorn's so devs can tell which side
    is talking in `docker compose logs backend`. Third-party loggers (httpx,
    sqlalchemy, …) stay at WARNING via root so the stream stays focused on
    our app. Set LOG_LEVEL=DEBUG in `.env` to see verbose flat_chat output.

    The `session_prefix` token in the format is filled in by
    `_RequestContextFilter` from the `session_id_var` / `run_id_var`
    ContextVars (see above).
    """
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {
                "request_context": {"()": _RequestContextFilter},
            },
            "formatters": {
                "default": {
                    "format": (
                        "%(asctime)s %(levelname)-7s "
                        "%(name)s%(session_prefix)s: %(message)s"
                    ),
                    "datefmt": "%Y-%m-%d %H:%M:%S",
                },
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                    "formatter": "default",
                    "filters": ["request_context"],
                },
            },
            "loggers": {
                # Our app namespace — gets the configured level + handler.
                # propagate=False so messages don't double-print via root.
                "flat_chat": {
                    "level": settings.log_level.upper(),
                    "handlers": ["console"],
                    "propagate": False,
                },
            },
            # Third-party + anything else: WARNING via the same handler.
            "root": {
                "level": "WARNING",
                "handlers": ["console"],
            },
        }
    )


def setup_observability() -> None:
    """Wire OTel → Phoenix and enable Pydantic AI span emission.

    We use the bare `opentelemetry.sdk.trace.TracerProvider` rather than
    `phoenix.otel.TracerProvider` because the latter eagerly installs a
    default `SimpleSpanProcessor` (warning about it being replaced) and
    prints tracing details on stdout — both noise we don't want. We still
    use phoenix.otel's `BatchSpanProcessor` / `HTTPSpanExporter` as
    convenience re-exports, and set the OpenInference project-name resource
    attribute ourselves so spans are grouped under the right project in
    Phoenix.
    """
    if not settings.phoenix_enabled:
        return
    try:
        from openinference.instrumentation.pydantic_ai import (
            OpenInferenceSpanProcessor,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from phoenix.otel import PROJECT_NAME, BatchSpanProcessor, HTTPSpanExporter
        from pydantic_ai import Agent
        from pydantic_ai.models.instrumented import InstrumentationSettings

        provider = TracerProvider(
            resource=Resource.create({PROJECT_NAME: "default"}),
        )
        # Enrichment: tags Pydantic AI's native spans with OpenInference
        # llm.*/tool.* attributes so Phoenix renders them as chat UI.
        provider.add_span_processor(OpenInferenceSpanProcessor())
        # Transport: batch + flush spans over OTLP/HTTP to the Phoenix collector.
        provider.add_span_processor(
            BatchSpanProcessor(HTTPSpanExporter(endpoint=settings.phoenix_endpoint))
        )
        trace.set_tracer_provider(provider)
        # Pydantic AI v2 defaults the instrumentation data format to version 5
        # (aggregated token usage on the run span). The current
        # `openinference-instrumentation-pydantic-ai` (0.1.x) was built for the
        # version-4 format and reads per-request usage attributes, so we pin
        # version=4 (tracer_provider=None → the global provider set above) to
        # keep Phoenix rendering token usage. Drop this pin once an openinference
        # release supports version 5. See agent-compound-docs/decisions/
        # pydantic-v2-migration.md.
        Agent.instrument_all(
            InstrumentationSettings(
                version=4, use_aggregated_usage_attribute_names=False
            )
        )
        logger.info("Phoenix observability enabled → %s", settings.phoenix_endpoint)
    except ImportError:
        logger.warning("Phoenix deps not installed — run: uv sync")


def shutdown_observability() -> None:
    """Flush buffered spans before process exit. Safe to call when disabled."""
    if not settings.phoenix_enabled:
        return
    try:
        from opentelemetry.sdk.trace import TracerProvider
    except ImportError:
        return
    provider = trace.get_tracer_provider()
    # ProxyTracerProvider (the no-op default before setup runs) is the API base
    # type, not the SDK one — only the SDK provider exposes `shutdown()`.
    if isinstance(provider, TracerProvider):
        provider.shutdown()
