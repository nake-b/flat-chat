from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    database_url: str = "postgresql://flat_chat:flat_chat@localhost:5432/flat_chat"


settings = Settings()
