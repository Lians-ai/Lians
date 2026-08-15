from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Enables fail-closed validation for secrets and other production controls.
    deployment_environment: str = "development"

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
    # arctic-embed-l-v2.0 replaced bge-large-en-v1.5 as the default after
    # scoring +10pts evidence retrieval on LOCOMO (82.4% vs 72.5% hit@10) at
    # the same dimensionality; existing stores keep working by pinning the
    # old model via SENTENCE_TRANSFORMER_MODEL — embeddings from different
    # models never mix in one store.
    sentence_transformer_model: str = "Snowflake/snowflake-arctic-embed-l-v2.0"
    # Immutable Hugging Face commit for production builds. Blank remains useful
    # for an absolute, operator-managed local model directory.
    sentence_transformer_revision: str = ""
    # Exact, hash-pinned local BGE v1.5 ONNX artifact configuration.
    bge_onnx_artifact_dir: str = ""
    # Zero delegates thread selection to ONNX Runtime.
    bge_onnx_intra_op_threads: int = Field(default=8, ge=0, le=256)

    # Crypto
    master_encryption_key: str = ""  # base64-encoded 32 bytes (used by kms_provider="env")
    # Optional raw Ed25519 private key (base64-encoded 32-byte seed) used to
    # sign Evidence Pack v2 manifests. Blank keeps packs explicitly unsigned.
    evidence_signing_private_key: str = ""
    evidence_signing_key_id: str = "lians-local"

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
    # Server-side key for non-reversible API-key fingerprints.
    api_secret_seed: str = "dev-seed-change-in-prod"
    admin_secret: str = "dev-admin-secret-change-in-prod"
    # Narrow credential for the public website's API-key provisioning broker.
    # It cannot access audit export, billing configuration, retention, or other
    # administrative routes. Leave empty when the provisioning API is unused.
    provisioning_secret: str = ""
    # None follows the environment: enabled in development, disabled in production.
    expose_api_docs: bool | None = None
    expose_health_details: bool | None = None

    # Public OpenAI plugin MCP resource server. Disabled until a stable HTTPS
    # origin and an OAuth 2.1 authorization server are configured.
    hosted_mcp_enabled: bool = False
    hosted_mcp_resource_url: str = "https://mcp.lians.ai"
    hosted_mcp_issuer_url: str = ""
    hosted_mcp_jwks_url: str = ""
    hosted_mcp_service_documentation_url: str = "https://www.lians.ai/privacy"
    hosted_mcp_jwt_algorithms: str = "RS256"
    hosted_mcp_jwt_leeway_seconds: int = Field(default=30, ge=0, le=300)
    hosted_mcp_max_token_lifetime_seconds: int = Field(default=3600, ge=60, le=86400)
    # Verified JWT claim that identifies the account/workspace boundary. The
    # issuer must emit it for every hosted MCP access token.
    hosted_mcp_tenant_claim: str = "tenant_id"
    hosted_mcp_allowed_hosts: str = ""
    hosted_mcp_allowed_origins: str = "https://chatgpt.com"
    hosted_mcp_retention_days: int = Field(default=365, ge=1, le=3650)
    # Audit rows contain hashes and operation metadata, not memory plaintext.
    hosted_mcp_audit_retention_days: int = Field(default=365, ge=1, le=3650)
    # Cold local embedding models can take substantially longer to initialize
    # than an individual MCP tool is allowed to run. Keep these deadlines
    # independent so hosted startup remains fail-closed without weakening the
    # per-call latency bound.
    hosted_mcp_startup_timeout_seconds: int = Field(default=360, ge=1, le=900)
    hosted_mcp_tool_timeout_seconds: int = Field(default=30, ge=1, le=120)
    hosted_mcp_max_concurrent_inference: int = Field(default=1, ge=1, le=8)
    hosted_mcp_inference_queue_timeout_seconds: float = Field(
        default=0.1, ge=0.01, le=5.0
    )
    # Weighted, per-tenant work units per fixed minute (remember=5, recall=2,
    # forget=1), enforced after OAuth in addition to the pre-auth IP budget.
    hosted_mcp_rate_limit_per_minute: int = Field(default=60, ge=5, le=10_000)
    hosted_mcp_max_memories_per_tenant: int = Field(default=10_000, ge=1, le=1_000_000)
    hosted_mcp_max_stored_bytes_per_tenant: int = Field(
        default=40_000_000, ge=4_096, le=10_000_000_000
    )
    hosted_mcp_max_write_bytes_per_day: int = Field(
        default=1_000_000, ge=4_096, le=1_000_000_000
    )
    # Durable bound on append-only audit growth. Each hosted tool operation
    # that can write an audit row reserves from this UTC-day tenant budget.
    hosted_mcp_max_audit_events_per_day: int = Field(
        default=5_000, ge=100, le=1_000_000
    )
    # Portal-issued domain proof. Empty keeps the challenge endpoint at 404.
    openai_apps_challenge_token: str = ""

    # Consumer Lians Bridge sign-in for opaque, zero-knowledge cloud sync.
    # This is a public native-client flow: the Bridge holds no client secret,
    # while this API validates only short-lived JWT access tokens.
    cloud_sync_oauth_enabled: bool = False
    cloud_sync_oauth_resource_url: str = "https://api.lians.ai"
    cloud_sync_oauth_issuer_url: str = ""
    cloud_sync_oauth_jwks_url: str = ""
    cloud_sync_oauth_jwt_algorithms: str = "RS256"
    cloud_sync_oauth_jwt_leeway_seconds: int = Field(default=30, ge=0, le=300)
    cloud_sync_oauth_max_token_lifetime_seconds: int = Field(
        default=3600, ge=60, le=86400
    )
    cloud_sync_oauth_startup_timeout_seconds: int = Field(default=15, ge=1, le=120)
    # Optional Auth0 Organization claim. Leave empty for a personal account;
    # issuer + subject remains the stable identity boundary.
    cloud_sync_oauth_organization_claim: str = ""

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
    max_request_body_bytes: int = 2_000_000

    # Background retention scheduler
    # Interval between automated prune cycles (hours). Set to 0 to disable.
    retention_prune_interval_hours: float = 24.0
    # Outcome-learning maintenance is opt-in. It never deletes memories:
    # repeated ignored/duplicate signals demote importance and flag
    # consolidation candidates for review.
    learning_maintenance_interval_hours: float = 0.0
    learning_maintenance_min_signals: int = 3
    # Crash-safe side effects (webhooks, SIEM). "embedded" is convenient for
    # one-process installs; production can run `lians-worker` separately.
    durable_job_worker_mode: str = "embedded"  # embedded | external | disabled
    durable_job_poll_seconds: float = 1.0

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

    # ── SIEM audit streaming ──────────────────────────────────────────────────
    # When set, every audit-chain event is forwarded (fire-and-forget) to this
    # HTTP collector — e.g. a Splunk HEC URL or a Datadog/Elastic intake. The
    # token, if set, is sent as `Authorization: <siem_token>`. Empty = disabled.
    siem_url: str = ""
    siem_token: str = ""

    # Opt-in LLM relationship extraction for /v1/graph/extract (else rule-based).
    graph_extract_llm: bool = False

    # ── Auto-metadata extraction (auto-supersession parity) ───────────────────
    # When True, a memory ingested WITHOUT any structured keys has them derived
    # from its content at write time (via the active domain adapter) so the
    # deterministic keyed-supersession fast path can fire — the mem0/Zep-style
    # "just send text and we work out what it supersedes" convenience.
    #
    # Kept in the regulated-determinism posture: the extractor is rule-based by
    # default (auditable, reproducible, no network), caller-supplied keys are
    # never overridden, and every auto-derived key is provenance-tagged under
    # metadata._auto_meta.  Off by default — existing deployments keep
    # caller-only keying and identical behavior.
    auto_metadata_enabled: bool = False
    # Optional LLM fallback used only when the deterministic extractor finds
    # nothing.  Requires anthropic_api_key.  Never blocks the write (fail-open).
    auto_metadata_llm: bool = False
    auto_metadata_model: str = "claude-haiku-4-5-20251001"

    # ── Interjection extraction (sub-turn durable facts) ──────────────────────
    # Conversational turns bury durable facts as mid-clause asides ("remind me
    # I eat fish now" dropped mid-task); stored whole, the turn's embedding
    # dilutes the fact and revisions can never supersede it.  When enabled,
    # add_memory extracts such clauses (rule-based, see interjection.py) and
    # stores each as a derived memory (metadata._derived/._parent) beside the
    # raw turn.  Default ON since the 2026-07-11 A/B: LOCOMO evidence retrieval
    # 83.5/69.4 with extraction vs 82.4/68.5 published baseline (neutral-to-
    # positive on every conversation), agent_sim interjection probes 100%.
    # Set false to keep raw-turn-only ingestion.
    interjection_extraction_enabled: bool = True

    # Deterministic memory compiler. The raw event remains authoritative; the
    # compiler adds a versioned type/entity/temporal projection under
    # metadata._lians_compiled for retrieval and audit. No network or LLM call.
    memory_compiler_enabled: bool = True

    # Explicit serving-mode latency budgets. These are observability budgets,
    # not cancellation deadlines: a response that exceeds its mode budget is
    # returned with deadline_exceeded=true and recorded for SLO enforcement.
    recall_fast_budget_ms: float = 100.0
    recall_deep_budget_ms: float = 800.0
    recall_reconstruct_budget_ms: float = 2000.0

    # ── Memory admission control ──────────────────────────────────────────────
    # off     — no admission evaluation
    # monitor — evaluate + tag + audit, always admit (default; observe first)
    # enforce — reject injection/blocked-source writes; hold PII/PHI/MNPI for review
    admission_mode: str = "monitor"
    # Comma-separated source labels that are never admitted (e.g. "scraped,unverified").
    admission_blocked_sources: str = ""

    # ── WORM / immutable storage posture ──────────────────────────────────────
    # Set true when the deployment backs the audit log with write-once-read-many
    # storage (e.g. S3 Object Lock in Compliance mode + app DB role with no
    # UPDATE/DELETE on event_log). Surfaced via /v1/compliance/worm for examiners.
    # See docs/worm-storage.md — this asserts intent; physical WORM is a deploy control.
    worm_mode: bool = False

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
    # Disabled by default because metric labels include tenant namespaces.
    # Enable only behind an authenticated/private monitoring network.
    metrics_enabled: bool = False

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
