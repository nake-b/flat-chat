from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    database_url: str = "postgresql://flat_chat:flat_chat@localhost:5432/flat_chat"

    llm_model: str = "openrouter/openrouter/free"
    llm_api_key: str = ""
    llm_temperature: float = 0.7
    llm_max_tokens: int = 1024
    llm_num_retries: int = 5
    llm_retry_after: int = 5


settings = Settings()
