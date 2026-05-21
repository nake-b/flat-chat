from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Ignore POSTGRES_USER/PASSWORD/DB and other compose-only vars that share
    # the .env file with backend-relevant settings.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(...)

    openrouter_api_key: str = ""
    openrouter_model: str = Field(...)

    jina_api_key: str = ""
    jina_base_url: str = "https://api.jina.ai/v1"

    phoenix_enabled: bool = False
    phoenix_endpoint: str = "http://localhost:6006/v1/traces"


settings = Settings()
