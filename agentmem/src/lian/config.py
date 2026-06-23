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
    master_encryption_key: str = ""  # base64-encoded 32 bytes (used by kms_provider="env")

    # KMS provider — controls how the master_encryption_key is fetched at startup
    # "env"   — read MASTER_ENCRYPTION_KEY env var (default; dev-friendly)
    # "aws"   — AWS KMS envelope decryption (requires boto3)
    # "azure" — Azure Key Vault Secrets (requires azure-keyvault-secrets + azure-identity)
    # "vault" — HashiCorp Vault KV v2 (requires hvac)
    kms_provider: str = "env"

    # AWS KMS settings (used when kms_provider="aws")
    kms_aws_key_id: str = ""          # CMK ARN or alias (optional; KMS infers from CiphertextBlob)
    kms_aws_region: str = "us-east-1"
    kms_aws_encrypted_key: str = ""   # base64 CiphertextBlob from GenerateDataKey

    # Azure Key Vault settings (used when kms_provider="azure")
    kms_azure_vault_url: str = ""               # e.g. https://myvault.vault.azure.net/
    kms_azure_secret_name: str = "agentmem-master-key"

    # HashiCorp Vault settings (used when kms_provider="vault")
    kms_vault_addr: str = "http://127.0.0.1:8200"
    kms_vault_token: str = ""
    kms_vault_path: str = "agentmem/master-key"
    kms_vault_mount_point: str = "secret"

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

    # Background retention scheduler
    # Interval between automated prune cycles (hours). Set to 0 to disable.
    retention_prune_interval_hours: float = 24.0

    # Stripe usage metering — optional; metering is silently disabled when api_key is empty.
    # Requires pip install agentmem[billing] (stripe>=7.0.0).
    # Set stripe_customer_id per namespace via PUT /v1/admin/billing/{namespace}.
    stripe_api_key: str = ""
    stripe_meter_write_event: str = "agentmem_memory_write"
    stripe_meter_recall_event: str = "agentmem_memory_recall"

    # CORS — comma-separated list of allowed origins for browser clients.
    # Use "*" for open-access demo instances.  In production, list explicit origins,
    # e.g. "https://app.example.com,https://admin.example.com".
    cors_origins: str = "*"

    # Air-gapped mode — guarantees no customer data leaves the deployment boundary.
    # When True, startup validation enforces:
    #   1. EMBEDDING_PROVIDER must be "sentence-transformers" or "local"
    #   2. SUPERSESSION_LLM_STAGE must be False
    # Set to True for any regulated deployment where data must not leave the network.
    airgap_mode: bool = False

    # ── Performance roadmap (Changes 3 / 7 / 8) ───────────────────────────────

    # Change 3: async LLM adjudication worker.  When True, Stage-3 LLM verdicts
    # are computed off the write path and applied retroactively.  Requires
    # supersession_llm_stage=True; no-op otherwise.
    llm_adjudication_async: bool = True

    # Change 7: in-process session cache TTL and size limit.
    session_cache_ttl_seconds: int = 300
    session_cache_max_entries: int = 512

    # Change 8: Merkle-batch audit chain.  When True, audit events are batched
    # into Merkle windows before the serial chain anchor is written, reducing
    # write serialization to one DB row per window.  Set to False to use the
    # classic per-event serial chain (suitable for very low write rates).
    merkle_batch_enabled: bool = False  # opt-in — won't break existing chain
    merkle_batch_size: int = 64         # events per Merkle window

    # Change 9: Postgres RLS barrier enforcement.
    # When True, the DB session variable ``agentmem.barrier_group`` is set
    # before each query so the RLS policy enforces the barrier at the DB layer.
    # Enabled by default after migration 0011_rls_barriers applies the policy.
    # Set False only on non-Postgres backends (SQLite tests) or before running
    # the migration on an existing cluster.
    rls_barriers_enabled: bool = True

    # ── Observability ──────────────────────────────────────────────────────────

    # Expose GET /metrics in Prometheus text format.
    # Requires prometheus-client>=0.19 (pip install agentmem[metrics]).
    # Disable to suppress the endpoint entirely (returns 404).
    metrics_enabled: bool = True

    # ── Domain adapter ─────────────────────────────────────────────────────────

    # Active domain adapter.  Controls entity normalization and which metadata
    # keys participate in the keyed supersession fast path.
    #
    # "finance"     — financial entities: ticker/ISIN/CUSIP normalization,
    #                 structured keys: ticker, metric, entity, isin, cusip,
    #                 instrument, field.  Default for financial deployments.
    # "healthcare"  — clinical entities: ICD-10 normalization, NPI validation,
    #                 medication name canonicalization.
    #                 structured keys: patient_id, condition, medication,
    #                 encounter_id, provider_id, procedure_code.
    #                 Requires HIPAA BAA before processing real PHI.
    # "legal"       — legal entities: matter ID / docket normalization,
    #                 jurisdiction abbreviation, claim type canonicalization.
    #                 structured keys: matter_id, jurisdiction, claim_type,
    #                 party_id, privilege_date, document_type.
    # "passthrough" — no normalization, no structured keys; pure semantic
    #                 supersession only.  Starting point for custom verticals.
    #
    # Custom adapters can be registered via adapters.register_adapter() before
    # startup and referenced by name here.
    domain_adapter: str = "finance"


@lru_cache
def get_settings() -> Settings:
    return Settings()
