"""OpenAI provider — standard (non-Azure) OpenAI API.

Deliberately runs on the OpenAI SDK defaults (timeout, retries). It is NOT yet
hardened with the custom stall-timeout / retry client the Anthropic builder
carries (`providers/anthropic.py`) — we're first checking whether the OpenAI
egress path works out of the box through the dev container's flaky network. If
it shows the same TLS-record corruption, revisit and inject a custom
`AsyncOpenAI(timeout=…, max_retries=…)` via `OpenAIProvider(openai_client=…)`,
mirroring the Anthropic pattern.

Unlike Anthropic (explicit cache breakpoints) OpenAI does prompt caching
server-side automatically, so there are no provider-specific model settings to
attach here.
"""

import logging

from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from flat_chat.core.config import Settings

logger = logging.getLogger(__name__)


def build_openai_model(settings: Settings, model_id: str) -> Model:
    """Build a standard OpenAI chat model.

    Owns its own validation: the orchestrator only checks for key presence, so
    an empty `model_id` raises here with a clear message. `openai_base_url`
    (optional) targets OpenAI-compatible endpoints/proxies.
    """
    if not model_id:
        raise RuntimeError(
            "OPENAI_API_KEY is set but the requested model id is empty "
            "(check OPENAI_MODEL / OPENAI_TITLE_MODEL in .env)."
        )
    # Pass an EXPLICIT base URL, never None/empty. If base_url is None the SDK
    # falls back to the OPENAI_BASE_URL env var — and docker-compose injects that
    # as an empty string (`${OPENAI_BASE_URL:-}`), which the SDK would then use
    # verbatim → `UnsupportedProtocol: URL missing http(s)://` before any network
    # call. So empty config → the canonical OpenAI endpoint.
    base_url = settings.openai_base_url.strip() or "https://api.openai.com/v1"
    return OpenAIChatModel(
        model_id,
        provider=OpenAIProvider(
            api_key=settings.openai_api_key,
            base_url=base_url,
        ),
    )
