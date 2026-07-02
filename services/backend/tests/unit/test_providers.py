"""Provider seam — selection logic + Anthropic prompt-cache settings.

Before the Pydantic AI v2 upgrade there were NO tests over `chat/providers/`:
the provider classes (`AnthropicModel` / `AnthropicProvider` / `OpenAIChatModel`
/ `AzureProvider`) and the prompt-cache settings were exercised only indirectly
(every other test mocks the model with `FunctionModel`/`TestModel`). The v2
upgrade touches exactly this construction surface, so these guard it:

  - `build_chat_model()` picks by preference order (OpenAI > Anthropic >
    Azure), falls back to the next when a key is unset, and raises when none is
    configured (the orchestration contract in providers/__init__.py).
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
from flat_chat.chat.providers.openai import build_openai_model
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


def test_anthropic_client_carries_stall_timeout_and_retries():
    """The custom AsyncAnthropic client keeps its read-stall timeout + retry
    budget — resilience against a flaky/corrupting egress that would otherwise
    freeze the SSE stream. Guards against a silent revert to the SDK defaults
    (very long timeout). The budget (read × (1+retries)) is deliberately tight so
    a persistent stall fails FAST → RUN_ERROR banner, not a multi-minute freeze;
    the SSE inactivity watchdog is the hard backstop above it. See anthropic.py.
    """
    from flat_chat.chat.providers.anthropic import _MAX_RETRIES, _TIMEOUT

    assert _MAX_RETRIES == 3
    assert _TIMEOUT.read == 12.0
    # The SDK self-heal budget must stay UNDER the SSE inactivity watchdog (60s)
    # so retries complete/exhaust before the watchdog fires. See anthropic.py +
    # chat/service.py:_SSE_INACTIVITY_TIMEOUT_S.
    assert _TIMEOUT.read * (1 + _MAX_RETRIES) < 60.0
    model = build_anthropic_model(
        _settings(anthropic_api_key="sk-test"), "claude-sonnet-4-6", cache=True
    )
    client = model._provider.client  # the AsyncAnthropic we constructed
    assert client.max_retries == _MAX_RETRIES
    assert client.timeout.read == _TIMEOUT.read


# --- OpenAI builder (standard, non-Azure) ------------------------------------


def test_openai_model_uses_model_id():
    model = build_openai_model(_settings(openai_api_key="sk-test"), "gpt-4o")
    assert isinstance(model, OpenAIChatModel)
    assert model.model_name == "gpt-4o"


def test_openai_builder_requires_model_id():
    with pytest.raises(RuntimeError, match="model id is empty"):
        build_openai_model(_settings(openai_api_key="sk-test"), "")


def test_openai_honours_base_url_override():
    model = build_openai_model(
        _settings(openai_api_key="sk-test", openai_base_url="https://proxy.example/v1"),
        "gpt-4o",
    )
    # base_url flows onto the underlying AsyncOpenAI client.
    assert "proxy.example" in str(model._provider.client.base_url)


def test_openai_empty_base_url_falls_back_to_openai_endpoint():
    # Regression: docker-compose injects OPENAI_BASE_URL as an empty string
    # (`${OPENAI_BASE_URL:-}`). If we passed None/"" through, the SDK would use
    # the empty env value verbatim → UnsupportedProtocol before any network call.
    # An empty setting MUST resolve to the canonical OpenAI endpoint.
    model = build_openai_model(
        _settings(openai_api_key="sk-test", openai_base_url=""), "gpt-4o"
    )
    assert "api.openai.com" in str(model._provider.client.base_url)


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


def test_prefers_openai_when_all_keys_set(patch_settings):
    # OpenAI is top of the preference order (OpenAI > Anthropic > Azure).
    patch_settings(
        openai_api_key="sk-test",
        openai_model="gpt-4o",
        anthropic_api_key="sk-test",
        anthropic_model="claude-sonnet-4-6",
        azure_openai_api_key="k",
        azure_openai_endpoint="https://x.openai.azure.com",
        azure_openai_deployment="gpt-deploy",
    )
    assert isinstance(build_chat_model(), OpenAIChatModel)


def test_prefers_anthropic_when_both_keys_set(patch_settings):
    # Anthropic wins over Azure when OpenAI is unset (OpenAI > Anthropic > Azure).
    patch_settings(
        openai_api_key="",
        anthropic_api_key="sk-test",
        anthropic_model="claude-sonnet-4-6",
        azure_openai_api_key="k",
        azure_openai_endpoint="https://x.openai.azure.com",
        azure_openai_deployment="gpt-deploy",
    )
    assert isinstance(build_chat_model(), AnthropicModel)


def test_falls_back_to_azure_when_higher_providers_unset(patch_settings):
    patch_settings(
        openai_api_key="",
        anthropic_api_key="",
        azure_openai_api_key="k",
        azure_openai_endpoint="https://x.openai.azure.com",
        azure_openai_deployment="gpt-deploy",
        azure_openai_api_version="2024-12-01-preview",
    )
    assert isinstance(build_chat_model(), OpenAIChatModel)


def test_raises_when_no_provider_configured(patch_settings):
    patch_settings(openai_api_key="", anthropic_api_key="", azure_openai_api_key="")
    with pytest.raises(RuntimeError, match="No LLM provider configured"):
        build_chat_model()


# --- build_title_model(): shares selection, different id, no cache -----------


def test_title_model_uses_openai_title_id(patch_settings):
    patch_settings(openai_api_key="sk-test", openai_title_model="gpt-4o-mini")
    model = build_title_model()
    assert isinstance(model, OpenAIChatModel)
    assert model.model_name == "gpt-4o-mini"


def test_title_model_uses_anthropic_title_id_without_cache(patch_settings):
    patch_settings(
        openai_api_key="",
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
        openai_api_key="",
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
