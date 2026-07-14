from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="DM_", extra="ignore")

    database_url: str = "postgresql+asyncpg://dm:dm@localhost:5433/dm_agent"
    anthropic_api_key: str | None = None  # falls back to ANTHROPIC_API_KEY / ant profile

    narrator_model: str = "claude-opus-4-8"
    utility_model: str = "claude-haiku-4-5"
    narrator_max_tokens: int = 8192

    # Knowledge layer (M2). Changing embedding_model may change the vector
    # dimension (EMBED_DIM in db.models) and require a migration.
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    default_ruleset: str = "dnd5e"


@lru_cache
def get_settings() -> Settings:
    return Settings()
