"""Chat-model dispatch — the single provider seam in the codebase.

`build_chat_model()` returns the configured Model. Currently Azure OpenAI
only — single provider, no fallback chain. To add a provider later, follow
the pattern in `providers/azure.py`, add the keys to Settings, and switch
on them here.
"""

from functools import lru_cache

from pydantic_ai.models import Model

from flat_chat.chat.providers.azure import build_azure_model
from flat_chat.core.config import settings

__all__ = ["build_chat_model"]


@lru_cache(maxsize=1)
def build_chat_model() -> Model:
    if not settings.azure_openai_api_key:
        raise RuntimeError(
            "No LLM provider configured. Set AZURE_OPENAI_API_KEY (and "
            "AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT, "
            "AZURE_OPENAI_API_VERSION) in .env."
        )
    return build_azure_model(settings)
