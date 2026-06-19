from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # DB
    database_url: str = "postgresql+asyncpg://agentmem:agentmem@localhost:5432/agentmem"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Embeddings
    # "voyage"               — Voyage AI (best finance quality, requires VOYAGE_API_KEY)
    # "openai"               — OpenAI text-embedding-3-small (dev fallback, requires OPENAI_API_KEY)
    # "sentence-transformers" — fully self-hosted, no external API calls (requires pip install agentmem[local])
    # "local"                — deterministic hash-projection for unit tests only
    embedding_provider: str = "local"
    voyage_api_key: str = ""
    openai_api_key: str = ""
    embedding_dim: int = 1024
    # Model for sentence-transformers provider. Must produce 1024-dim embeddings.
    # For air-gapped deployments: pre-download and set to an absolute local path.
    sentence_transformer_model: str = "BAAI/bge-large-en-v1.5"

    # Crypto
    master_encryption_key: str = ""  # base64-encoded 32 bytes

    # API
    api_secret_seed: str = "dev-seed-change-in-prod"
    admin_secret: str = "dev-admin-secret-change-in-prod"

    # LLM adjudication (Stage 3 supersession)
    anthropic_api_key: str = ""          # falls back to ANTHROPIC_API_KEY env var
    llm_adjudication_model: str = "claude-haiku-4-5-20251001"
    supersession_llm_stage: bool = False

    # Recall hot cache (Redis)
    recall_cache_enabled: bool = True
    recall_cache_ttl_seconds: int = 60
    # Supersession review queue — supersessions below this confidence are flagged for review
    supersession_review_threshold: float = 0.75

    # Logging
    log_level: str = "INFO"       # DEBUG | INFO | WARNING | ERROR
    log_json: bool = True         # False = human-readable format for local dev

    # Rate limiting (per API key, sliding window)
    rate_limit_per_minute: int = 300

    # Air-gapped mode — guarantees no customer data leaves the deployment boundary.
    # When True, startup validation enforces:
    #   1. EMBEDDING_PROVIDER must be "sentence-transformers" or "local"
    #   2. SUPERSESSION_LLM_STAGE must be False
    # Set to True for any regulated deployment where data must not leave the network.
    airgap_mode: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
