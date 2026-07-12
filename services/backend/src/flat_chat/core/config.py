from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Ignore POSTGRES_USER/PASSWORD/DB and other compose-only vars that share
    # the .env file with backend-relevant settings.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(...)

    # Provider fields default to empty — required-ness is enforced inside
    # `chat/providers/__init__.py` only when the matching API key is set.
    # Anthropic-direct: preferred when its key is set (native prompt caching).
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
    # Cheap/fast model used ONCE per conversation to summarise the first turn
    # into a sidebar title. Separate from `anthropic_model` so chat keeps its
    # cache-friendly Sonnet while titling uses Haiku without paying for the
    # cache breakpoints. See `chat/providers/__init__.py:build_title_model`.
    anthropic_title_model: str = "claude-haiku-4-5-20251001"

    # Azure OpenAI — classic Azure OpenAI Service. `deployment` is the name
    # you typed when creating the deployment in Foundry (not the underlying
    # model name, though they're often the same). `api_version` must be a
    # preview version for o-series reasoning models.
    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_deployment: str = ""
    azure_openai_api_version: str = "2024-12-01-preview"
    # Optional separate deployment for title generation. Empty = reuse the
    # chat deployment.
    azure_openai_title_deployment: str = ""

    # OpenAI — standard (non-Azure) OpenAI API. Preferred provider when its key
    # is set (order: OpenAI > Anthropic > Azure). Runs on SDK defaults for now
    # (no custom stall-timeout/retry client — see `providers/openai.py`).
    openai_api_key: str = ""
    openai_model: str = "gpt-5.4"
    # Cheap/fast model for one-shot sidebar titling (see build_title_model).
    openai_title_model: str = "gpt-5.4-nano"
    # Optional base-URL override for OpenAI-compatible endpoints/proxies.
    openai_base_url: str = ""

    jina_api_key: str = ""
    jina_base_url: str = "https://api.jina.ai/v1"

    # Per-user LLM token budget — the §3 gate in llm-rate-limit.md. Rolling-24h
    # cap on `total_tokens` (input + output) summed across a user's runs, keyed
    # on `get_user_id()`. Checked BEFORE each run in
    # `ChatService.dispatch_agent_request`: a caller over budget is rejected with
    # 429 having spent zero tokens. `0` DISABLES the per-user gate (the per-run
    # backstop in chat/service.py always applies regardless). Default is generous
    # so normal chat + demos never hit it; lower it in a deployment to cap cost.
    llm_daily_token_budget: int = 2_000_000

    # Routing engines (internal compose services; no public port). OSRM serves
    # car drive-time matrices; MOTIS serves public-transit travel times. Both
    # reached backend→service by hostname. See agent-compound-docs/decisions/
    # travel-time-routing.md.
    osrm_url: str = "http://osrm:5000"
    motis_url: str = "http://motis:8080"

    # Auth (fastapi-users). `jwt_secret` signs the session cookie's JWT and is
    # REQUIRED — there is deliberately no insecure default that could ship. Tests
    # set a sentinel in conftest; dev/prod set it in `.env`. Rotating it logs
    # everyone out (existing cookies fail to verify). See AUTH.md.
    jwt_secret: str = Field(...)
    jwt_lifetime_seconds: int = 60 * 60 * 24 * 7  # 7 days — generous for the MVP

    # Login cookie `Secure` attribute. FALSE for the local HTTP MVP; set TRUE in
    # any HTTPS deployment (the browser↔nginx leg is what matters — nginx may
    # still talk plain HTTP to the backend). See AUTH.md.
    cookie_secure: bool = False

    # Seeded accounts (created by `scripts/seed_users.py`). There is NO
    # public registration — users exist only via this script. The dev account is
    # an admin (superuser); the optional professor account is a regular user and
    # is only created when BOTH prof vars are set. Override in any deployment.
    dev_user_email: str = "dev@flatchat.dev"
    dev_user_password: str = "dev"
    prof_user_email: str = ""
    prof_user_password: str = ""

    phoenix_enabled: bool = False
    phoenix_endpoint: str = "http://localhost:6006/v1/traces"

    # Application log level for the `flat_chat` namespace. Third-party
    # loggers stay at WARNING regardless — see core/observability.py.
    # Standard names: DEBUG / INFO / WARNING / ERROR / CRITICAL.
    log_level: str = "INFO"


settings = Settings()
