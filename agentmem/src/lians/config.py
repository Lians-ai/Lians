from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Enables fail-closed validation for secrets and other production controls.
    deployment_environment: str = "development"
    # Server-owned processing location used by namespace residency policy.
    # This value is never accepted from an HTTP header or request payload.
    deployment_region: str = "local"

    # DB
    database_url: str = "postgresql+asyncpg://agentmem:agentmem@localhost:5432/agentmem"
    database_pool_size: int = Field(default=10, ge=1, le=200)
    database_max_overflow: int = Field(default=20, ge=0, le=400)
    database_pool_timeout_seconds: float = Field(default=30.0, ge=0.1, le=300)
    database_statement_timeout_ms: int = 30_000
    database_lock_timeout_ms: int = 5_000
    database_idle_transaction_timeout_ms: int = 60_000
    # Alembic runs under a separate identity and explicitly larger budget.
    migration_statement_timeout_ms: int = 1_500_000
    migration_lock_timeout_ms: int = 5_000
    migration_idle_transaction_timeout_ms: int = 300_000

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    runtime_cache_enabled: bool = False
    # Narrow production exception for colocated Unix-domain sockets. Network
    # TCP connections still require peer-verifying TLS.
    production_allow_local_data_service_sockets: bool = False

    # Embeddings
    # "voyage"               — Voyage AI (best finance quality, requires VOYAGE_API_KEY)
    # "openai"               — OpenAI text-embedding-3-small (dev fallback, requires OPENAI_API_KEY)
    # "sentence-transformers" — fully self-hosted, no external API calls
    # "bge-onnx"             — exact hash-pinned BGE v1.5 ONNX, local CPU only
    # (requires pip install lians-platform[local])
    # "local"                — deterministic hash-projection for unit tests only
    embedding_provider: str = "local"
    voyage_api_key: str = ""
    openai_api_key: str = ""
    embedding_provider_timeout_seconds: float = 20.0
    embedding_dim: int = 1024
    # Model for sentence-transformers provider. Must produce 1024-dim embeddings.
    # For air-gapped deployments: pre-download and set to an absolute local path.
    # arctic-embed-l-v2.0 replaced bge-large-en-v1.5 as the default after
    # scoring +10pts evidence retrieval on LOCOMO (82.4% vs 72.5% hit@10) at
    # the same dimensionality; existing stores keep working by pinning the
    # old model via SENTENCE_TRANSFORMER_MODEL — embeddings from different
    # models never mix in one store.
    sentence_transformer_model: str = "Snowflake/snowflake-arctic-embed-l-v2.0"
    # Artifact staged by ``lians-bge-onnx-export``. The provider accepts only
    # the pinned BAAI revision and verifies the manifest/model/tokenizer hashes
    # before its first inference. It never downloads or reindexes anything.
    bge_onnx_artifact_dir: str = ""
    # Zero delegates thread selection to ONNX Runtime. Eight is the measured
    # low-latency default on the reference 8-core CPU.
    bge_onnx_intra_op_threads: int = Field(default=8, ge=0, le=256)

    # Crypto
    master_encryption_key: str = ""  # base64-encoded 32 bytes (used by kms_provider="env")
    # Dedicated HMAC key for tenant-scoped, non-reversible subject references.
    # Keep this stable across master-key rotations and separate from every DEK/
    # wrapping key. Accepts an exact 32-byte value encoded as base64 or hex.
    subject_reference_key: SecretStr = SecretStr("")
    # Stable, non-secret identifier embedded in every new master-key envelope.
    # Production requires an explicit value. Development falls back to a fixed
    # local-only identifier so older test fixtures remain usable.
    master_key_id: str = ""
    # Rotation is deliberately bounded to one predecessor.  Configure the
    # identifier and the selected provider's previous material together, run
    # the offline rewrap workflow, then remove both only after it reports zero
    # legacy/previous-key values.
    master_key_previous_id: str = ""
    master_encryption_key_previous: str = ""

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
    kms_aws_previous_key_id: str = ""
    kms_aws_previous_region: str = ""
    kms_aws_previous_encrypted_key: str = ""

    # Azure Key Vault settings (used when kms_provider="azure")
    kms_azure_vault_url: str = ""               # e.g. https://myvault.vault.azure.net/
    kms_azure_secret_name: str = "agentmem-master-key"
    kms_azure_previous_vault_url: str = ""
    kms_azure_previous_secret_name: str = ""

    # HashiCorp Vault settings (used when kms_provider="vault")
    kms_vault_addr: str = "http://127.0.0.1:8200"
    kms_vault_token: str = ""
    kms_vault_path: str = "agentmem/master-key"
    kms_vault_mount_point: str = "secret"
    kms_vault_previous_addr: str = ""
    kms_vault_previous_path: str = ""
    kms_vault_previous_mount_point: str = ""

    # API
    admin_secret: str = "dev-admin-secret-change-in-prod"
    # Public and break-glass administration are separate network surfaces.
    # ``all`` exists only for local development/test compatibility and is
    # rejected by production startup validation.
    api_surface: Literal["public", "admin", "all"] = "public"

    # Decision Receipt signing. ``local`` preserves the original raw-key
    # deployment mode. ``vault-transit`` signs through a pinned HashiCorp
    # Vault Transit Ed25519 key so private material never enters this process.
    receipt_signing_provider: str = "local"
    # Local provider only: raw 32-byte Ed25519 private key as base64 or hex.
    # Empty keeps development receipts hash-verifiable but explicitly grades
    # the deployment-signature evidence as missing.
    receipt_signing_private_key: str = ""
    # Development placeholder. Production requires a stable environment-specific
    # identifier published with the independently trusted receipt public key.
    receipt_signing_key_id: str = "lians-receipt-key"
    # Vault Transit provider. The key version and raw public key are mandatory
    # trust pins: startup reads Vault metadata and refuses a mismatch. Tokens
    # are redacted by Pydantic and are never included in signer errors or logs.
    receipt_vault_addr: str = ""
    receipt_vault_token: SecretStr = SecretStr("")
    # Preferred with Vault Agent/Kubernetes auth: an absolute, read-only token
    # file that is reopened for every request so rotation needs no API restart.
    # Mutually exclusive with receipt_vault_token.
    receipt_vault_token_file: str = ""
    # Optional Vault Enterprise namespace sent as X-Vault-Namespace.
    receipt_vault_namespace: str = ""
    receipt_vault_mount_point: str = "transit"
    receipt_vault_key_name: str = ""
    receipt_vault_key_version: int = 0
    receipt_vault_public_key: str = ""
    receipt_vault_timeout_seconds: float = 5.0

    # LLM adjudication (Stage 3 supersession)
    anthropic_api_key: str = ""          # falls back to ANTHROPIC_API_KEY env var
    llm_adjudication_model: str = "claude-haiku-4-5-20251001"
    supersession_llm_stage: bool = False
    llm_provider_timeout_seconds: float = 20.0

    # Recall hot cache (Redis)
    recall_cache_enabled: bool = True
    recall_cache_ttl_seconds: int = Field(default=60, ge=1, le=86_400)
    # Supersession review queue — supersessions below this confidence are flagged for review
    supersession_review_threshold: float = 0.75
    # Candidate discovery is complete-or-error: both the row count and the
    # materialized byte budget must fit before a memory mutation may proceed.
    supersession_candidate_limit: int = Field(default=500, ge=10, le=5_000)
    supersession_candidate_bytes_limit: int = Field(
        default=32 * 1024 * 1024,
        ge=1 * 1024 * 1024,
        le=256 * 1024 * 1024,
    )
    # Exclusive relationship writes emit one immutable audit record and one
    # durable integration event per invalidated edge. They are atomic only
    # while the complete prior-edge set fits this transaction ceiling.
    graph_exclusive_invalidation_limit: int = Field(default=500, ge=1, le=5_000)
    # /v1/graph/extract validates the complete candidate set before the first
    # edge write. The byte budget covers the returned triplets plus a
    # conservative per-edge response/audit reserve.
    graph_extract_candidate_limit: int = Field(default=250, ge=1, le=5_000)
    graph_extract_candidate_bytes_limit: int = Field(
        default=2 * 1024 * 1024,
        ge=64 * 1024,
        le=64 * 1024 * 1024,
    )
    # Decision evidence normalization is atomic. Reject the mutation before
    # claiming coverage when caller metadata would amplify beyond either
    # complete-candidate ceiling; accepted sets are written in bounded pages.
    decision_evidence_candidate_limit: int = Field(default=5_000, ge=100, le=10_000)
    decision_evidence_candidate_bytes_limit: int = Field(
        default=16 * 1024 * 1024,
        ge=1 * 1024 * 1024,
        le=128 * 1024 * 1024,
    )

    # Logging
    log_level: str = "INFO"       # DEBUG | INFO | WARNING | ERROR
    log_json: bool = True         # False = human-readable format for local dev

    # Rate limiting (per API key, sliding window)
    rate_limit_per_minute: int = Field(default=300, ge=1, le=1_000_000)
    # Independent client-network ceiling catches brute force with constantly
    # changing credentials. It is a multiplier of rate_limit_per_minute so
    # shared enterprise NATs retain useful headroom.
    rate_limit_network_multiplier: int = Field(default=20, ge=1, le=1_000)
    rate_limit_admin_per_minute: int = Field(default=60, ge=1, le=1_000_000)
    # Redis outage posture: "local" keeps a bounded per-process safety limiter,
    # "deny" returns 503, and "open" is development-only.
    rate_limit_backend_failure_mode: str = "local"
    # Comma-separated CIDRs for the immediate reverse proxy / service-mesh peers
    # allowed to supply X-Forwarded-For. Empty means forwarded headers are
    # ignored and the socket peer is the client identity.
    trusted_proxy_cidrs: str = ""
    max_request_body_bytes: int = Field(
        default=2_000_000,
        ge=1_024,
        le=16_777_216,
    )
    # Response/export budgets are independent of the inbound body cap. Every
    # content-bearing page or complete signed export must fit before plaintext
    # is decrypted/materialized; hash-only projections use the smaller budget.
    content_export_page_bytes_limit: int = Field(
        default=32 * 1024 * 1024,
        ge=1 * 1024 * 1024,
        le=256 * 1024 * 1024,
    )
    hash_only_export_page_bytes_limit: int = Field(
        default=16 * 1024 * 1024,
        ge=1 * 1024 * 1024,
        le=256 * 1024 * 1024,
    )
    audit_export_page_bytes_limit: int = Field(
        default=16 * 1024 * 1024,
        ge=1 * 1024 * 1024,
        le=256 * 1024 * 1024,
    )
    lineage_response_bytes_limit: int = Field(
        default=16 * 1024 * 1024,
        ge=1 * 1024 * 1024,
        le=256 * 1024 * 1024,
    )

    # Tenant OIDC administrators may issue workload credentials only with a
    # bounded lifetime. The API rejects configuration outside these bounds;
    # production startup also fails closed on invalid values.
    workload_credential_min_ttl_seconds: int = 300
    workload_credential_max_ttl_seconds: int = 2_592_000  # 30 days
    # Persist approximate usage metadata at most once per credential/window.
    # The auth query uses a conditional UPDATE so concurrent requests cannot
    # continually rewrite the same row.
    workload_credential_last_used_write_interval_seconds: int = 900

    # Universal Recorder defaults to hash/reference capture. Full prompts,
    # arguments, results, and artifacts require an explicit deployment opt-in.
    recorder_allow_full_capture: bool = False
    # The legacy-standard OTLP endpoint is protected by the same minimization
    # policy as the Universal Recorder so it cannot become a raw-prompt bypass.
    otlp_capture_mode: str = "hash_only"
    # OTLP batches are byte-bounded by the HTTP middleware and cardinality-bounded
    # here. The trace cap also bounds the number of authoritative decisions that
    # one request can derive inside a single database transaction.
    otlp_max_spans_per_request: int = Field(default=2_000, ge=1, le=10_000)
    otlp_max_genai_traces_per_request: int = Field(default=500, ge=1, le=2_000)

    # Background retention scheduler
    # Interval between automated prune cycles (hours). Set to 0 to disable.
    retention_prune_interval_hours: float = Field(default=24.0, ge=0.0, le=168.0)
    # Eligible tenants are keyset-paged and each leader-elected cycle has a
    # hard ceiling. Large sweeps continue from the singleton database cursor, so
    # neither enumeration memory nor a single lock-holding cycle is unbounded.
    retention_namespace_page_size: int = Field(default=64, ge=1, le=256)
    retention_max_namespaces_per_cycle: int = Field(default=512, ge=1, le=5_000)

    # Bounded Prometheus inventory is refreshed from authoritative database
    # rows, never inferred from process-local queue mutations.
    observability_refresh_seconds: float = Field(default=30.0, ge=5.0, le=300.0)

    # Durable exhaustive impact-assessment worker. Every API replica may poll;
    # PostgreSQL row leases and SKIP LOCKED make claims mutually exclusive.
    # Each page commits its matches and cursor together, so a crash can only
    # replay an idempotent page, never skip part of the frozen snapshot.
    impact_assessment_worker_enabled: bool = True
    impact_assessment_worker_poll_seconds: float = 1.0
    impact_assessment_worker_batch_size: int = 4
    impact_assessment_worker_concurrency: int = 2
    impact_assessment_worker_lease_seconds: int = 120
    impact_assessment_worker_page_size: int = 250
    impact_assessment_worker_max_pages_per_claim: int = 1
    impact_assessment_worker_retry_base_seconds: float = 2.0
    impact_assessment_worker_retry_max_seconds: float = 300.0
    impact_assessment_worker_max_attempts: int = 8

    # Decisions with more than 500 already-recorded Recorder events commit a
    # fixed-snapshot job instead of rejecting the authoritative decision or
    # indexing only a prefix. Pages, evidence links, and cursors commit together.
    recorder_evidence_index_worker_enabled: bool = True
    recorder_evidence_index_worker_poll_seconds: float = Field(
        default=1.0, ge=0.05, le=60.0
    )
    recorder_evidence_index_worker_batch_size: int = Field(default=4, ge=1, le=100)
    recorder_evidence_index_worker_concurrency: int = Field(default=2, ge=1, le=32)
    recorder_evidence_index_worker_lease_seconds: int = Field(
        default=120, ge=30, le=3_600
    )
    recorder_evidence_index_worker_page_size: int = Field(default=100, ge=1, le=100)
    recorder_evidence_index_worker_max_pages_per_claim: int = Field(
        default=2, ge=1, le=20
    )
    recorder_evidence_index_worker_retry_base_seconds: float = Field(
        default=2.0, gt=0, le=3_600
    )
    recorder_evidence_index_worker_retry_max_seconds: float = Field(
        default=300.0, gt=0, le=3_600
    )
    recorder_evidence_index_worker_max_attempts: int = Field(default=8, ge=1, le=100)

    # Data-subject erasure destroys the DEK in the request transaction, then
    # drains the frozen derivative-store snapshot through durable bounded pages.
    subject_erasure_worker_enabled: bool = True
    subject_erasure_worker_poll_seconds: float = Field(
        default=1.0, ge=0.05, le=60.0
    )
    subject_erasure_worker_batch_size: int = Field(default=4, ge=1, le=100)
    subject_erasure_worker_concurrency: int = Field(default=2, ge=1, le=32)
    subject_erasure_worker_lease_seconds: int = Field(
        default=120, ge=30, le=3_600
    )
    subject_erasure_worker_page_size: int = Field(default=250, ge=1, le=500)
    subject_erasure_worker_max_pages_per_claim: int = Field(
        default=2, ge=1, le=20
    )
    subject_erasure_worker_retry_base_seconds: float = Field(
        default=2.0, gt=0, le=3_600
    )
    subject_erasure_worker_retry_max_seconds: float = Field(
        default=300.0, gt=0, le=3_600
    )
    subject_erasure_worker_max_attempts: int = Field(default=8, ge=1, le=100)

    # Tenant enable/disable/revoke freezes one exact SCIM User snapshot and
    # reconciles it through leased, crash-resumable pages. Disabling also closes
    # every linked binding in the configuration transaction before work queues.
    scim_reconciliation_worker_enabled: bool = True
    scim_reconciliation_worker_poll_seconds: float = Field(
        default=1.0, ge=0.05, le=60.0
    )
    scim_reconciliation_worker_batch_size: int = Field(default=4, ge=1, le=100)
    scim_reconciliation_worker_concurrency: int = Field(default=2, ge=1, le=32)
    scim_reconciliation_worker_lease_seconds: int = Field(
        default=120, ge=30, le=3_600
    )
    scim_reconciliation_worker_page_size: int = Field(default=100, ge=1, le=500)
    scim_reconciliation_worker_max_pages_per_claim: int = Field(
        default=2, ge=1, le=20
    )
    scim_reconciliation_worker_retry_base_seconds: float = Field(
        default=2.0, gt=0, le=3_600
    )
    scim_reconciliation_worker_retry_max_seconds: float = Field(
        default=300.0, gt=0, le=3_600
    )
    scim_reconciliation_worker_max_attempts: int = Field(default=8, ge=1, le=100)

    # A SCIM Group list is complete-or-error. Membership is batch-loaded for
    # the whole page and bounded in both rows and exact compact JSON bytes.
    scim_group_list_member_row_limit: int = Field(default=10_000, ge=1, le=100_000)
    scim_group_list_response_bytes: int = Field(
        default=8 * 1024 * 1024,
        ge=64 * 1024,
        le=64 * 1024 * 1024,
    )

    # Stripe usage metering. Billable facts are committed transactionally to a
    # Postgres outbox; every API replica may run the SKIP-LOCKED worker safely.
    # Provider delivery is disabled when api_key is empty, but already-staged
    # facts remain durable so credential rotation cannot lose usage.
    stripe_api_key: str = ""
    # Protected units are the product-native commercial contract. The memory
    # meters remain available for existing deployments and compatibility plans.
    stripe_meter_decision_event: str = "lians_authoritative_decision"
    stripe_meter_protected_action_event: str = "lians_protected_action"
    stripe_meter_write_event: str = "agentmem_memory_write"
    stripe_meter_recall_event: str = "agentmem_memory_recall"
    # Stripe can reject an accepted event asynchronously. Production operators
    # must acknowledge a durable thin-event destination for those reports.
    stripe_meter_async_error_destination_configured: bool = False
    stripe_meter_worker_enabled: bool = True
    stripe_meter_worker_poll_seconds: float = 1.0
    stripe_meter_worker_batch_size: int = 64
    stripe_meter_delivery_concurrency: int = 8
    stripe_meter_lease_seconds: int = 60
    stripe_meter_provider_timeout_seconds: float = 10.0
    stripe_meter_retry_base_seconds: float = 2.0
    stripe_meter_retry_max_seconds: float = 900.0
    stripe_meter_max_attempts: int = 12
    # Stripe promises identifier de-duplication for at least 24 hours. Stop
    # automatic replay before that boundary so a long-outage recovery cannot
    # double-bill an ambiguously accepted event.
    stripe_meter_idempotency_window_seconds: int = 82_800
    # Stripe accepts event timestamps from the past 35 calendar days. A 34-day
    # application bound leaves a timezone/calendar and operator-response buffer.
    stripe_meter_max_event_age_seconds: int = 2_937_600

    # CORS — comma-separated list of allowed origins for browser clients.
    # Use "*" for open-access demo instances.  In production, list explicit origins,
    # e.g. "https://app.example.com,https://admin.example.com".
    cors_origins: str = "*"

    # Air-gapped mode disables every application-managed, payload-bearing egress
    # path and rejects conflicting configuration at startup. Identity discovery,
    # KMS, DNS, database, and Redis may still require operator-approved network
    # paths; enforce those independently with a deny-by-default egress policy.
    airgap_mode: bool = False

    # Outbound application telemetry. AIRGAP_MODE rejects a configured
    # exporter and telemetry.py also stays no-op as defense in depth.
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "lians"

    # ── SIEM audit streaming ──────────────────────────────────────────────────
    # When set, every audit-chain event is forwarded (fire-and-forget) to this
    # HTTP collector — e.g. a Splunk HEC URL or a Datadog/Elastic intake. The
    # token, if set, is sent as `Authorization: <siem_token>`. Empty = disabled.
    siem_url: str = ""
    siem_token: str = ""

    # Development-only compatibility delivery for /v1/webhooks. Domain events
    # are always offered to the durable integration outbox first. Production
    # rejects this in-process retry path because it cannot survive restarts.
    legacy_webhooks_enabled: bool = False
    # A hard namespace-wide ceiling keeps every compatibility list and fan-out
    # bounded, including databases created before the durable outbox existed.
    legacy_webhook_max_endpoints_per_namespace: int = Field(
        default=50,
        ge=1,
        le=500,
    )

    # Durable enterprise integration outbox. The worker leases rows from
    # Postgres with SKIP LOCKED, so every API replica may run one safely.
    integration_worker_enabled: bool = True
    integration_worker_poll_seconds: float = Field(default=1.0, ge=0.05, le=60.0)
    integration_worker_batch_size: int = Field(default=64, ge=1, le=1_000)
    integration_delivery_concurrency: int = Field(default=8, ge=1, le=100)
    integration_lease_seconds: int = Field(default=180, ge=130, le=3_600)
    integration_retry_base_seconds: float = Field(default=2.0, ge=0.1, le=3_600.0)
    integration_retry_max_seconds: float = Field(default=900.0, ge=0.1, le=3_600.0)
    integration_max_payload_bytes: int = Field(
        default=262_144,
        ge=1_024,
        le=10_000_000,
    )
    integration_max_response_digest_bytes: int = Field(
        default=65_536,
        ge=0,
        le=1_000_000,
    )
    # Also caps active registrations per namespace. Enqueue rechecks with
    # LIMIT + 1 so legacy or concurrently over-cap configurations fail closed.
    integration_max_destinations_per_event: int = Field(default=100, ge=1, le=1_000)
    # Private RFC1918/ULA destinations are an explicit deployment opt-in;
    # loopback, link-local, metadata, multicast, and reserved ranges stay blocked.
    integration_allow_private_network: bool = False
    integration_allow_insecure_http: bool = False
    # Audit payloads are hash/reference-only by default. Enabling this copies
    # the already-audited payload into the encrypted outbox event.
    integration_include_audit_payload: bool = False

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
    session_cache_ttl_seconds: int = Field(default=300, ge=1, le=86_400)
    session_cache_max_entries: int = Field(default=512, ge=1, le=100_000)

    # Experimental secondary Merkle anchors. EventLog remains serialized per
    # event; production rejects process-local windows until membership and
    # external anchor publication are transactionally durable.
    merkle_batch_enabled: bool = False  # opt-in — won't break existing chain
    merkle_batch_size: int = Field(default=64, ge=2, le=4096)

    # Change 9: Postgres RLS barrier enforcement.
    # When True, the DB session variable ``agentmem.barrier_group`` is set
    # before each query so the RLS policy enforces the barrier at the DB layer.
    # Enabled by default after migration 0011_rls_barriers applies the policy.
    # Set False only on non-Postgres backends (SQLite tests) or before running
    # the migration on an existing cluster.
    rls_barriers_enabled: bool = True

    # ── Observability ──────────────────────────────────────────────────────────

    # Expose GET /metrics in Prometheus text format.
    # Requires prometheus-client>=0.19 (pip install lians-platform[metrics]).
    # Disable to suppress the endpoint entirely (returns 404).
    # Disabled by default because metric labels include tenant namespaces.
    # Enable only behind an authenticated/private monitoring network.
    metrics_enabled: bool = False
    metrics_bearer_token: str = ""

    # ── Domain adapter ─────────────────────────────────────────────────────────

    # Active domain adapter.  Controls entity normalization and which metadata
    # keys participate in the keyed supersession fast path.
    #
    # "finance"     — financial entities: ticker/ISIN/CUSIP normalization,
    #                 structured keys: ticker, metric, entity, isin, cusip,
    #                 instrument, field, period, quarter.  Default for
    #                 financial deployments.
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
