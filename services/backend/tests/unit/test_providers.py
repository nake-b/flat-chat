"""Provider seam — selection logic + Anthropic prompt-cache settings.

Before the Pydantic AI v2 upgrade there were NO tests over `chat/providers/`:
the provider classes (`AnthropicModel` / `AnthropicProvider` / `OpenAIChatModel`
/ `AzureProvider`) and the prompt-cache settings were exercised only indirectly
(every other test mocks the model with `FunctionModel`/`TestModel`). The v2
upgrade touches exactly this construction surface, so these guard it:

  - `build_chat_model()` prefers Anthropic, falls back to Azure, raises when
    neither is configured (the orchestration contract in providers/__init__.py).
  - the Anthropic model carries the three `anthropic_cache_*` breakpoints — the
    whole reason the Anthropic-direct provider exists (~5600 cached prefix
    tokens/turn). A silent drop here would degrade cost/latency invisibly.

These construct models but never call them, so no network / API key is needed.
"""

import pytest
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel

from flat_chat.chat.providers import build_chat_model, build_title_model
from flat_chat.chat.providers.anthropic import _CACHE_SETTINGS, build_anthropic_model
from flat_chat.chat.providers.azure import build_azure_model
from flat_chat.core.config import Settings

_DB = "postgresql://unset:unset@unset/unset"


def _settings(**overrides) -> Settings:
    return Settings(database_url=_DB, **overrides)


# --- Anthropic builder + prompt caching --------------------------------------


def test_anthropic_model_has_all_cache_breakpoints():
    model = build_anthropic_model(
        _settings(anthropic_api_key="sk-test"), "claude-sonnet-4-6", cache=True
    )
    assert isinstance(model, AnthropicModel)
    assert model.model_name == "claude-sonnet-4-6"
    # The cache config travels on the model (Agent stays provider-agnostic).
    assert model.settings["anthropic_cache_instructions"] is True
    assert model.settings["anthropic_cache_tool_definitions"] is True
    assert model.settings["anthropic_cache_messages"] is True


def test_anthropic_title_variant_has_no_cache_breakpoints():
    # `cache=False` (the titling variant) omits the breakpoints — a single
    # ~50-token call would never pay back the cache.
    model = build_anthropic_model(
        _settings(anthropic_api_key="sk-test"), "claude-haiku-4-5", cache=False
    )
    assert isinstance(model, AnthropicModel)
    assert model.model_name == "claude-haiku-4-5"
    assert model.settings is None


def test_cache_settings_constant_enables_all_three():
    # Guards the constant directly — the builder copies it onto the model.
    assert _CACHE_SETTINGS["anthropic_cache_instructions"] is True
    assert _CACHE_SETTINGS["anthropic_cache_tool_definitions"] is True
    assert _CACHE_SETTINGS["anthropic_cache_messages"] is True


def test_anthropic_builder_requires_model_id():
    with pytest.raises(RuntimeError, match="model id is empty"):
        build_anthropic_model(_settings(anthropic_api_key="sk-test"), "")


# --- Azure builder -----------------------------------------------------------


def test_azure_model_uses_deployment_as_model_id():
    model = build_azure_model(
        _settings(
            azure_openai_api_key="k",
            azure_openai_endpoint="https://x.openai.azure.com",
            azure_openai_deployment="gpt-deploy",
            azure_openai_api_version="2024-12-01-preview",
        ),
        "gpt-deploy",
    )
    assert isinstance(model, OpenAIChatModel)
    assert model.model_name == "gpt-deploy"


def test_azure_builder_reports_missing_config():
    with pytest.raises(RuntimeError, match="AZURE_OPENAI_ENDPOINT"):
        build_azure_model(_settings(azure_openai_api_key="k"), "")


# --- Orchestration: build_chat_model() selection -----------------------------


@pytest.fixture
def patch_settings(monkeypatch):
    """Patch the provider-module settings singleton and clear the lru_cache.

    `build_chat_model()` reads the `settings` imported into
    `flat_chat.chat.providers`, and is `@lru_cache`d — both must be reset.
    """
    import flat_chat.chat.providers as providers

    def _apply(**attrs):
        for name, value in attrs.items():
            monkeypatch.setattr(providers.settings, name, value)
        build_chat_model.cache_clear()
        build_title_model.cache_clear()

    yield _apply
    build_chat_model.cache_clear()
    build_title_model.cache_clear()


def test_prefers_anthropic_when_both_keys_set(patch_settings):
    patch_settings(
        anthropic_api_key="sk-test",
        anthropic_model="claude-sonnet-4-6",
        azure_openai_api_key="k",
        azure_openai_endpoint="https://x.openai.azure.com",
        azure_openai_deployment="gpt-deploy",
    )
    assert isinstance(build_chat_model(), AnthropicModel)


def test_falls_back_to_azure_when_anthropic_unset(patch_settings):
    patch_settings(
        anthropic_api_key="",
        azure_openai_api_key="k",
        azure_openai_endpoint="https://x.openai.azure.com",
        azure_openai_deployment="gpt-deploy",
        azure_openai_api_version="2024-12-01-preview",
    )
    assert isinstance(build_chat_model(), OpenAIChatModel)


def test_raises_when_no_provider_configured(patch_settings):
    patch_settings(anthropic_api_key="", azure_openai_api_key="")
    with pytest.raises(RuntimeError, match="No LLM provider configured"):
        build_chat_model()


# --- build_title_model(): shares selection, different id, no cache -----------


def test_title_model_uses_anthropic_title_id_without_cache(patch_settings):
    patch_settings(
        anthropic_api_key="sk-test",
        anthropic_title_model="claude-haiku-4-5",
    )
    model = build_title_model()
    assert isinstance(model, AnthropicModel)
    assert model.model_name == "claude-haiku-4-5"
    assert model.settings is None  # titling never attaches cache breakpoints


def test_title_model_azure_falls_back_to_chat_deployment(patch_settings):
    # No dedicated title deployment configured → reuse the chat deployment.
    patch_settings(
        anthropic_api_key="",
        azure_openai_api_key="k",
        azure_openai_endpoint="https://x.openai.azure.com",
        azure_openai_deployment="gpt-deploy",
        azure_openai_api_version="2024-12-01-preview",
        azure_openai_title_deployment="",
    )
    model = build_title_model()
    assert isinstance(model, OpenAIChatModel)
    assert model.model_name == "gpt-deploy"
