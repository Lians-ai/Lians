# WORM / Immutable Storage (SEC 17a-4)

SEC Rule 17a-4(f) requires broker-dealers to preserve records in a **non-rewriteable,
non-erasable (WORM)** format. Lians gives you two complementary layers; this page is
the deployment reference for turning them on and attesting the posture to an examiner.

## Two layers of immutability

| Layer | What it is | Provided by |
|-------|------------|-------------|
| **Logical WORM** | An append-only, SHA-256 hash-chained audit log. Any edit, reorder, or deletion is detectable with `verify_chain`. | **Lians, always.** The app only ever INSERTs into `event_log`; it never UPDATEs or DELETEs it. |
| **Physical WORM** | The bytes cannot be rewritten or erased, even by a DBA or storage admin. | **The operator**, via object-locked storage + a restricted DB role. |

Logical WORM proves tampering *happened*; physical WORM prevents it. 17a-4 wants
both. Query the combined posture at `GET /v1/compliance/worm` and set
`WORM_MODE=true` once the physical controls below are in place.

## 1. Restrict the application DB role

The app role must not be able to mutate the audit tables. As a superuser/owner:

```sql
-- Append-only audit: the app may INSERT and SELECT, never UPDATE/DELETE.
REVOKE UPDATE, DELETE, TRUNCATE ON event_log     FROM lians_app;
REVOKE UPDATE, DELETE, TRUNCATE ON merkle_anchors FROM lians_app;
GRANT  INSERT, SELECT             ON event_log     TO lians_app;
GRANT  INSERT, SELECT             ON merkle_anchors TO lians_app;
```

Run the app as `lians_app` (a **non-superuser, non-BYPASSRLS** role — the same role
RLS isolation requires). Now even a compromised app cannot rewrite history.

## 2. Object-locked backups & exports

Send audit exports (`/v1/admin/audit/export`) and database backups to storage with
**immutability enabled**:

- **AWS S3 Object Lock — Compliance mode** with a retention period ≥ your
  regulatory window (17a-4 is typically 6 years; the first 2 years readily
  accessible). Compliance mode cannot be shortened or bypassed, even by the root
  account.
- **Azure Blob immutable storage** (time-based retention, locked policy).
- **GCS bucket lock** (retention policy, locked).

```bash
aws s3api put-object-lock-configuration --bucket lians-audit \
  --object-lock-configuration 'ObjectLockEnabled=Enabled,Rule={DefaultRetention={Mode=COMPLIANCE,Years=6}}'
```

Schedule the export + PITR backup to that bucket; the Merkle anchors let you prove
a batch of audit rows existed at a point in time without trusting the database.

## 3. Attest the posture

```bash
curl -H "X-API-Key: $KEY" https://lians.internal/v1/compliance/worm
# { "worm_mode": true, "audit_chain_append_only": true,
#   "audit_chain_status": "ok", "physical_worm_attested": true,
#   "standard": "SEC 17a-4(f)",
#   "recommendation": "logical + physical WORM controls attested; retention
#     periods, designated-third-party access, and organizational controls
#     remain operator responsibilities (docs/worm-storage.md)" }
```

Set `WORM_MODE=true` only after §1 and §2 are actually in place — the flag asserts
the physical control to examiners; it does not create it.

## 17a-4(f) mapping

| Requirement | Control |
|-------------|---------|
| Non-rewriteable, non-erasable | Object Lock (Compliance) + revoked UPDATE/DELETE |
| Verify automatically the quality/accuracy of the recording | `verify_chain` (SHA-256 chain) + Merkle anchors |
| Serialize and time-date the records | `event_log` ordered hash chain with timestamps |
| Readily downloadable | `/v1/admin/audit/export` |
| Duplicate copy stored separately | Object-locked bucket in a second region/account |

See also [compliance.md](compliance.md), [security-whitepaper.md](security-whitepaper.md),
and [soc2-hipaa-readiness.md](soc2-hipaa-readiness.md).
