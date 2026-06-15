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

    # Azure OpenAI — classic Azure OpenAI Service. `deployment` is the name
    # you typed when creating the deployment in Foundry (not the underlying
    # model name, though they're often the same). `api_version` must be a
    # preview version for o-series reasoning models.
    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_deployment: str = ""
    azure_openai_api_version: str = "2024-12-01-preview"

    jina_api_key: str = ""
    jina_base_url: str = "https://api.jina.ai/v1"

    phoenix_enabled: bool = False
    phoenix_endpoint: str = "http://localhost:6006/v1/traces"

    # Application log level for the `flat_chat` namespace. Third-party
    # loggers stay at WARNING regardless — see core/observability.py.
    # Standard names: DEBUG / INFO / WARNING / ERROR / CRITICAL.
    log_level: str = "INFO"


settings = Settings()
