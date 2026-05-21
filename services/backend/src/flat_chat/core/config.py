from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Ignore POSTGRES_USER/PASSWORD/DB and other compose-only vars that share
    # the .env file with backend-relevant settings.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(...)

    # Azure OpenAI — classic Azure OpenAI Service. `deployment` is the name
    # you typed when creating the deployment in Foundry (not the underlying
    # model name, though they're often the same). `api_version` must be a
    # preview version for o-series reasoning models.
    azure_openai_api_key: str = Field(...)
    azure_openai_endpoint: str = Field(...)
    azure_openai_deployment: str = Field(...)
    azure_openai_api_version: str = "2024-12-01-preview"

    jina_api_key: str = ""
    jina_base_url: str = "https://api.jina.ai/v1"

    phoenix_enabled: bool = False
    phoenix_endpoint: str = "http://localhost:6006/v1/traces"


settings = Settings()
