import httpx
from anthropic import AsyncAnthropic
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

# Resilience against a flaky egress path. The dev container's TLS egress to
# api.anthropic.com corrupts records (~1/3 of large requests fail with
# `SSLV3_ALERT_BAD_RECORD_MAC` → `APIConnectionError`; occasionally a corrupted
# body parses as a spurious 400). Left unretried, that surfaces as the "agent
# stopped responding" symptom. Two knobs handle it:
#   - timeout: `read` is httpx's PER-CHUNK gap timeout, so a legitimately
#     slow-but-streaming answer is NOT killed; only a genuine stall (no bytes for
#     this long) trips it, converting an infinite hang into a fast, retryable
#     error instead of a frozen stream.
#   - max_retries: the anthropic SDK re-issues connection errors / timeouts /
#     5xx / 429 transparently (exponential backoff). At a ~1/3 per-request
#     failure rate, 5 attempts drive the user-visible failure rate to
#     ~0.33**5 ≈ 0.4%, so the corruption self-heals without the user seeing it.
# Applied to chat AND title clients. The real fix is the network path itself
# (see the infra section of the PR); this keeps the app usable meanwhile.
_STREAM_STALL_TIMEOUT_S = 45.0
_CONNECT_TIMEOUT_S = 10.0
_MAX_RETRIES = 5

_TIMEOUT = httpx.Timeout(
    connect=_CONNECT_TIMEOUT_S,
    read=_STREAM_STALL_TIMEOUT_S,
    write=_CONNECT_TIMEOUT_S,
    pool=_CONNECT_TIMEOUT_S,
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
    # Own the underlying SDK client so we control the stall timeout + retries
    # (a bare `api_key=` provider uses the SDK's very long default timeout and
    # would hang the SSE stream on a silent response). `max_retries` makes the
    # SDK re-issue a timed-out/5xx/429 request transparently.
    client = AsyncAnthropic(
        api_key=settings.anthropic_api_key,
        timeout=_TIMEOUT,
        max_retries=_MAX_RETRIES,
    )
    return AnthropicModel(
        model_id,
        provider=AnthropicProvider(anthropic_client=client),
        settings=_CACHE_SETTINGS if cache else None,
    )
