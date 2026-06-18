from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # DB
    database_url: str = "postgresql+asyncpg://agentmem:agentmem@localhost:5432/agentmem"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Embeddings
    embedding_provider: str = "local"  # "voyage" | "openai" | "local"
    voyage_api_key: str = ""
    openai_api_key: str = ""
    embedding_dim: int = 1024

    # Crypto
    master_encryption_key: str = ""  # base64-encoded 32 bytes

    # API
    api_secret_seed: str = "dev-seed-change-in-prod"
    admin_secret: str = "dev-admin-secret-change-in-prod"

    # LLM adjudication (Stage 3 supersession)
    anthropic_api_key: str = ""          # falls back to ANTHROPIC_API_KEY env var
    llm_adjudication_model: str = "claude-haiku-4-5-20251001"
    supersession_llm_stage: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
