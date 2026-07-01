"""Chat-model dispatch — the single LLM-provider seam.

`build_chat_model()` returns a Pydantic AI `Model` assembled from whichever
provider key is set in `Settings`. Two providers are wired: Anthropic-direct
(preferred when its key is set, for native prompt caching) and Azure OpenAI.
When both keys are set, Anthropic wins.

# Layering

Four layers, each with exactly one job:

  Env         — `.env` and `docker-compose.yml`. Raw strings. Compose
                forwards `.env` to the container; every var uses a `:-`
                default so missing values become empty, never warnings.
                Defaults belong in Settings, not in Compose interpolation.

  Settings    — `core/config.py`. Type-validated env values. No business
                logic and no "required when X" rules. Optional fields
                default to `""` (or a sensible literal); the provider that
                actually consumes the value decides whether empty is OK.

  Builder     — `chat/providers/<name>.py`. Each builder
                  (a) validates its own inputs and raises with a clear
                      message if its key is set but the rest is incomplete,
                  (b) constructs and returns a `Model`,
                  (c) owns provider-specific settings (e.g. Anthropic cache
                      breakpoints live in `anthropic.py`, not on the Agent).

  Orchestrator — this file. Decides *whether* to build each provider by
                 checking key presence only. Never reads provider-specific
                 fields.

  Agent       — `chat/agent.py`. Provider-agnostic. Calls `build_chat_model()`
                (cached) and passes the result to `agent.run(model=...)`.
                Knows nothing about which provider runs.

# Adding a provider

  1. Add `<name>_api_key` + the rest of the provider's config to
     `core/config.py` (all default to `""` unless there is a sensible
     literal default).
  2. Sync `.env.example`, `docker-compose.yml` (with `:-` defaults), and
     the `services/backend/README.md` config table.
  3. Create `chat/providers/<name>.py` with
     `build_<name>_model(settings) -> Model` that raises on incomplete
     config and returns a constructed Model.
  4. Wire it into `build_chat_model()` below — pick the provider by key
     presence; document the preference order if more than one can be set.
"""

import logging
from collections.abc import Callable
from functools import lru_cache

from pydantic_ai.models import Model

from flat_chat.chat.providers.anthropic import build_anthropic_model
from flat_chat.chat.providers.azure import build_azure_model
from flat_chat.core.config import settings

__all__ = ["build_chat_model", "build_title_model"]

logger = logging.getLogger(__name__)


def _select(
    *,
    anthropic: Callable[[], Model],
    azure: Callable[[], Model],
) -> Model:
    """Pick a provider by key presence and build via the supplied thunk.

    Anthropic wins when both keys are set — its native prompt caching is the
    reason that provider exists at all. To force Azure during local dev, unset
    ANTHROPIC_API_KEY in your .env. The thunks defer the actual (model-id /
    deployment-specific) construction so this helper stays provider-agnostic.
    """
    if settings.anthropic_api_key:
        return anthropic()
    if settings.azure_openai_api_key:
        return azure()
    raise RuntimeError(
        "No LLM provider configured. Set ANTHROPIC_API_KEY or "
        "AZURE_OPENAI_API_KEY (with AZURE_OPENAI_ENDPOINT, "
        "AZURE_OPENAI_DEPLOYMENT, AZURE_OPENAI_API_VERSION) in .env."
    )


@lru_cache(maxsize=1)
def build_chat_model() -> Model:
    def anthropic() -> Model:
        logger.info(
            "LLM provider: anthropic-direct (model=%s)", settings.anthropic_model
        )
        return build_anthropic_model(settings, settings.anthropic_model, cache=True)

    def azure() -> Model:
        logger.info(
            "LLM provider: azure-openai (deployment=%s)",
            settings.azure_openai_deployment,
        )
        return build_azure_model(settings, settings.azure_openai_deployment)

    return _select(anthropic=anthropic, azure=azure)


@lru_cache(maxsize=1)
def build_title_model() -> Model:
    """Cheap/fast model for one-shot conversation titling.

    Shares provider selection with `build_chat_model` (Anthropic wins when both
    keys are set), but uses a different model id and no prompt-caching
    breakpoints — titling is a single ~50-token call per conversation and the
    cache would never pay back. Azure falls back to the chat deployment when no
    dedicated title deployment is configured.
    """
    return _select(
        anthropic=lambda: build_anthropic_model(
            settings, settings.anthropic_title_model, cache=False
        ),
        azure=lambda: build_azure_model(
            settings,
            settings.azure_openai_title_deployment or settings.azure_openai_deployment,
        ),
    )
