from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Ignore POSTGRES_USER/PASSWORD/DB and other compose-only vars that share
    # the .env file with backend-relevant settings.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(...)

    # LLM provider keys are intentionally NOT defined here. The starter ships
    # with no agent — bring your own framework and add whatever config it
    # needs (see HACKATHON.md). Add fields here the same way `database_url`
    # is declared, then surface them in `.env.example` + `docker-compose.yml`.

    # Optional: semantic-search embeddings (Jina v3). Without a key, search
    # still works — `sort_by="relevance"` degrades gracefully to recency.
    jina_api_key: str = ""
    jina_base_url: str = "https://api.jina.ai/v1"

    phoenix_enabled: bool = False
    phoenix_endpoint: str = "http://localhost:6006/v1/traces"

    # Application log level for the `flat_chat` namespace. Third-party
    # loggers stay at WARNING regardless — see core/observability.py.
    # Standard names: DEBUG / INFO / WARNING / ERROR / CRITICAL.
    log_level: str = "INFO"


settings = Settings()
