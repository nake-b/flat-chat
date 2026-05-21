"""Chat-model dispatch — the single provider seam in the codebase.

`build_chat_model(settings)` walks the configured providers (by key presence)
and returns one Model. When multiple providers are configured, returns a
`FallbackModel` that fails over on `ModelAPIError` (covers 429/5xx HTTP
errors from providers).

Design intent — dev/prod isolation by config only:
  - Dev .env has free-tier keys (OPENROUTER, later GROQ/GEMINI/...) →
    multi-provider chain.
  - Prod .env has one paid key (e.g. OPENAI_API_KEY) → single model,
    no chain.
Same code path either way; "fallback vanishes" in prod simply because only
one provider key is set.

To add a provider:
  1. Add `<provider>_api_key` (and any `<provider>_model`) to Settings.
  2. Add a builder in `chat/providers/<provider>.py`.
  3. Add the `if settings.<provider>_api_key:` branch below.
"""

from functools import lru_cache

from pydantic_ai.models import Model
from pydantic_ai.models.fallback import FallbackModel

from flat_chat.chat.providers.openrouter import build_openrouter_model
from flat_chat.core.config import settings

__all__ = ["build_chat_model"]


@lru_cache(maxsize=1)
def build_chat_model() -> Model:
    candidates: list[Model] = []

    if settings.openrouter_api_key:
        candidates.append(build_openrouter_model(settings))

    # Add more providers here as keys are added to Settings. Order = preference.

    if not candidates:
        raise RuntimeError(
            "No LLM provider configured. Set at least one provider key in "
            ".env (e.g. OPENROUTER_API_KEY — get one at "
            "https://openrouter.ai/keys; free models still require an account)."
        )
    if len(candidates) == 1:
        return candidates[0]
    return FallbackModel(*candidates)
