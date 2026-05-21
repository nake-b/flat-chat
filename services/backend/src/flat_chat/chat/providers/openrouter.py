import asyncio
import logging

from openai import AsyncOpenAI
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterProvider
from pydantic_ai.settings import ModelSettings

from flat_chat.core.config import Settings

logger = logging.getLogger(__name__)

_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# OpenAI SDK default is 2 retries on actual HTTP 5xx/429. We bump to 5 — useful
# for genuine wire-level failures.
_HTTP_MAX_RETRIES = 5

# App-level retry budget for ModelHTTPError raised by Pydantic AI's
# `_validate_completion` — covers OpenRouter's "200 OK with {error: {...}} in
# body" quirk that the OpenAI SDK cannot see. Stacks on top of HTTP retries.
_BODY_ERROR_MAX_ATTEMPTS = 5
_BODY_ERROR_BACKOFF_BASE_SEC = 0.5


class _RetryingOpenRouterModel(OpenRouterModel):
    """OpenRouterModel that retries body-embedded ModelHTTPError on 5xx/429.

    When OpenRouter returns `200 OK` with `{"error": {"code": 5xx, ...}}` in the
    body, the OpenAI SDK sees success and skips retry. Pydantic AI's
    `_validate_completion` then raises `ModelHTTPError` — which would propagate
    to the caller untreated. We catch it here, in the provider layer, so the
    application code can stay clean.
    """

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        for attempt in range(_BODY_ERROR_MAX_ATTEMPTS):
            try:
                return await super().request(
                    messages, model_settings, model_request_parameters
                )
            except ModelHTTPError as exc:
                transient = exc.status_code >= 500 or exc.status_code == 429
                last = attempt == _BODY_ERROR_MAX_ATTEMPTS - 1
                if not transient or last:
                    raise
                delay = _BODY_ERROR_BACKOFF_BASE_SEC * (2**attempt)
                logger.warning(
                    "OpenRouter transient error (status=%s, attempt=%d/%d) — "
                    "retrying in %.1fs. body=%r",
                    exc.status_code,
                    attempt + 1,
                    _BODY_ERROR_MAX_ATTEMPTS,
                    delay,
                    exc.body,
                )
                await asyncio.sleep(delay)
        raise RuntimeError("unreachable: retry loop exited without return or raise")


def build_openrouter_model(settings: Settings) -> Model:
    """Build an OpenRouter-backed chat model with two stacked retry budgets:
    HTTP-level (OpenAI SDK) and body-embedded errors (our subclass).
    """
    client = AsyncOpenAI(
        api_key=settings.openrouter_api_key,
        base_url=_OPENROUTER_BASE_URL,
        max_retries=_HTTP_MAX_RETRIES,
    )
    return _RetryingOpenRouterModel(
        settings.openrouter_model,
        provider=OpenRouterProvider(openai_client=client),
    )
