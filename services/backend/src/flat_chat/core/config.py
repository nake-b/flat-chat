from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    database_url: str = Field(...)

    # Provider model fields default to empty — required-ness is enforced in
    # `chat/providers/__init__.py` only when the matching API key is set.
    openrouter_api_key: str = ""
    openrouter_model: str = ""

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"

    jina_api_key: str = ""
    jina_base_url: str = "https://api.jina.ai/v1"

    phoenix_enabled: bool = False
    phoenix_endpoint: str = "http://localhost:6006/v1/traces"


settings = Settings()
