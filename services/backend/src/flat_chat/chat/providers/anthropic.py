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


def build_anthropic_model(settings: Settings) -> Model:
    """Build an Anthropic-direct chat model with prompt caching enabled.

    Owns its own validation — the orchestrator only checks for key presence.
    """
    if not settings.anthropic_model:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is set but ANTHROPIC_MODEL is empty. "
            "Set a model id (e.g. 'claude-sonnet-4-6')."
        )
    return AnthropicModel(
        settings.anthropic_model,
        provider=AnthropicProvider(api_key=settings.anthropic_api_key),
        settings=_CACHE_SETTINGS,
    )
