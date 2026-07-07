from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="DM_", extra="ignore")

    database_url: str = "postgresql+asyncpg://dm:dm@localhost:5433/dm_agent"
    anthropic_api_key: str | None = None  # falls back to ANTHROPIC_API_KEY / ant profile

    narrator_model: str = "claude-opus-4-8"
    utility_model: str = "claude-haiku-4-5"
    narrator_max_tokens: int = 8192


@lru_cache
def get_settings() -> Settings:
    return Settings()
