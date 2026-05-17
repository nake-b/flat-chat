from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    database_url: str = "postgresql://flat_chat:flat_chat@localhost:5432/flat_chat"

    llm_model: str = "google/gemma-4-31b-it:free"
    llm_api_key: str = ""
    llm_base_url: str = "https://openrouter.ai/api/v1"

    jina_api_key: str = ""
    jina_base_url: str = "https://api.jina.ai/v1"

    phoenix_enabled: bool = False
    phoenix_endpoint: str = "http://localhost:6006/v1/traces"


settings = Settings()
