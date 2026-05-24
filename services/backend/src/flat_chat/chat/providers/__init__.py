"""Chat-model dispatch — the single LLM-provider seam.

`build_chat_model()` returns a Pydantic AI `Model` assembled from whichever
provider keys are set in `Settings`. When multiple keys are configured, the
return value is a `FallbackModel` chain; first listed = preferred, the rest
fail over on `ModelAPIError` (covers 429/5xx).

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
                 fields. Wraps multiple builds in `FallbackModel`.

  Agent       — `chat/agent.py`. Provider-agnostic. Calls `build_chat_model()`
                (cached) and passes the result to `agent.run(model=...)`.
                Knows nothing about which provider runs.

# Dev/prod by configuration alone

  Dev `.env`:  multiple keys → `FallbackModel` chain (free-tier failover).
  Prod `.env`: one paid key → single `Model`, chain vanishes naturally.

Same code path either way.

# Adding a provider

  1. Add `<name>_api_key` + `<name>_model` to `core/config.py` (both default
     to `""` unless there is a sensible literal default like
     `anthropic_model = "claude-sonnet-4-6"`).
  2. Sync `.env.example`, `docker-compose.yml` (with `:-` defaults), and
     the `services/backend/README.md` config table.
  3. Create `chat/providers/<name>.py` with
     `build_<name>_model(settings) -> Model` that raises on incomplete
     config and returns a constructed Model.
  4. Append a branch below in the order you want for fallback preference:
       if settings.<name>_api_key:
           candidates.append(build_<name>_model(settings))
"""

from functools import lru_cache

from pydantic_ai.models import Model
from pydantic_ai.models.fallback import FallbackModel

from flat_chat.chat.providers.anthropic import build_anthropic_model
from flat_chat.chat.providers.openrouter import build_openrouter_model
from flat_chat.core.config import settings

__all__ = ["build_chat_model"]


@lru_cache(maxsize=1)
def build_chat_model() -> Model:
    candidates: list[Model] = []

    # Preferred first. When both keys are set, Anthropic-direct wins so
    # native prompt caching applies; OpenRouter becomes the fallback.
    if settings.anthropic_api_key:
        candidates.append(build_anthropic_model(settings))

    if settings.openrouter_api_key:
        candidates.append(build_openrouter_model(settings))

    # Add more providers here. Order = preference.

    if not candidates:
        raise RuntimeError(
            "No LLM provider configured. Set at least one provider key in "
            ".env — either ANTHROPIC_API_KEY (paid, native prompt caching) or "
            "OPENROUTER_API_KEY (free models available; get a key at "
            "https://openrouter.ai/keys)."
        )
    if len(candidates) == 1:
        return candidates[0]
    return FallbackModel(*candidates)
