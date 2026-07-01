from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
from pydantic_ai.providers.anthropic import AnthropicProvider

from flat_chat.core.config import Settings

# Prompt caching breakpoints — applied at the model layer so the cache
# config travels with the model object and the Agent stays provider-agnostic.
#   - instructions:     the static INSTRUCTIONS block (dynamic
#                       @agent.instructions are auto-excluded by Pydantic AI)
#   - tool definitions: the toolset JSON schemas
#   - messages:         the growing conversation tail
_CACHE_SETTINGS = AnthropicModelSettings(
    anthropic_cache_instructions=True,
    anthropic_cache_tool_definitions=True,
    anthropic_cache_messages=True,
)


def build_anthropic_model(
    settings: Settings, model_id: str, *, cache: bool = True
) -> Model:
    """Build an Anthropic-direct chat model.

    `cache=True` (the chat default) attaches the prompt-caching breakpoints;
    `cache=False` (titling) omits them — a single ~50-token call per
    conversation would never pay back the cache. Owns its own validation: the
    orchestrator only checks for key presence, so an empty `model_id` raises
    here with a clear message.
    """
    if not model_id:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is set but the requested model id is empty "
            "(check ANTHROPIC_MODEL / ANTHROPIC_TITLE_MODEL in .env)."
        )
    return AnthropicModel(
        model_id,
        provider=AnthropicProvider(api_key=settings.anthropic_api_key),
        settings=_CACHE_SETTINGS if cache else None,
    )
