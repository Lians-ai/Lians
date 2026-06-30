# Lians Threat Model

A STRIDE threat model for the Lians memory layer. Scope: the self-hosted API
server, its Postgres/Redis dependencies, and the SDK clients. Out of scope: the
customer's own network, IdP, and the LLMs that consume recalled context.

## Assets

- **Memory content** (PII / PHI / MNPI) and its embeddings
- **Audit chain** integrity (regulatory evidence)
- **Subject keys (DEKs)** and the master key
- **API keys** and admin secret
- **Tenant / barrier isolation** boundaries

## Trust boundaries

Client ↔ API (network) · API ↔ Postgres/Redis (network) · API ↔ KMS · API ↔ SIEM
/ webhook receivers. The API process is the primary trust boundary enforcing
authn/z; the database is a second boundary enforcing RLS.

## STRIDE

| Threat | Vector | Mitigation |
|--------|--------|------------|
| **Spoofing** | Forged API key | Keys stored as SHA-256 hashes; revocable; TLS; admin endpoints gated by a separate admin secret. SSO at the gateway. |
| **Tampering (data)** | Alter a stored memory/audit row in the DB | Audit chain: each row's hash binds the previous row; `verify_chain` detects any edit/reorder/deletion. Content has a SHA-256 hash; erased content is unrecoverable but its hash persists. |
| **Tampering (transit)** | MITM | TLS required in production. |
| **Repudiation** | "We never knew X" / "we didn't erase Y" | Append-only event log + hash chain; point-in-time `snapshot`/`recall_at` reconstructs exact past state; erasure certificate proves deletion. |
| **Information disclosure (cross-tenant)** | One namespace reads another's rows | RLS namespace policy (transaction-scoped GUC), enforced in the DB; app-layer filters as defense-in-depth. |
| **Information disclosure (cross-barrier)** | One desk/care-team/matter reads another's | RESTRICTIVE RLS barrier policy (0013) keyed on `agentmem.barrier_group`; cross-barrier reads denied at the DB layer (CI-proven with a non-superuser role). |
| **Information disclosure (at rest)** | DB/disk/backup theft | Per-subject AES-256-GCM; DEKs wrapped by a KMS master key; backups inherit encryption. |
| **Information disclosure (egress)** | Data leaves the perimeter via an LLM/embedding API | Air-gap mode hard-fails at startup on any externalizing config; local embedding provider. |
| **Denial of service** | Request flood | Per-API-key Redis rate limiting (fails open); timeouts; `/livez` vs `/readyz` so dependency blips don't cause restart storms. |
| **Elevation of privilege** | Read key performs writes / admin | Per-request scope checks; RBAC roles; admin secret for audit/admin ops. |
| **Replay / duplication** | Network retry double-writes | Idempotency keys (exactly-once writes); SDK auto-sends a stable key on retry. |

## Key residual risks & assumptions

1. **Run as a non-superuser DB role.** Superusers and `BYPASSRLS` roles bypass all
   RLS — the single most important deployment control. Enforced/asserted in CI.
2. **KMS protects the master key.** Compromise of the master key compromises all
   DEKs; use a real KMS (AWS/Azure/Vault) and rotate.
3. **Optional LLM stages** (supersession Stage-3, graph extraction with `use_llm`)
   send text to a model provider — disabled in air-gap mode; rule-based defaults
   keep the core deterministic and in-perimeter.
4. **SIEM / webhook receivers** are trusted endpoints; deliveries are HMAC-signed
   (webhooks) and best-effort (never block the write path).

## Verification

The barrier-isolation control is exercised in CI by a dedicated test that creates
a `NOSUPERUSER`/`NOBYPASSRLS` role and confirms cross-barrier denial
(`test_pgvector.py::test_barrier_group_isolation`). Audit-chain tamper detection,
crypto-shred, and erasure-certificate behavior are covered by the test suite. See
[testing.md](testing.md).
