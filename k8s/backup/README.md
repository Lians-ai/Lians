# Kubernetes logical-backup job

This overlay packages the independently verifiable logical-copy control. It is
intentionally suspended and is not part of the base application kustomization.

Before enabling it:

1. Build `ops/backup/Dockerfile.worm`, push it to an immutable registry digest, and
   set that digest in this overlay.
2. Select an encrypted storage class and size the PVC above the largest expected
   database plus working headroom.
3. Create `lians-backup-credentials` from `backup-secret.example.yaml` through an
   external secret manager. Never apply the example values.
4. Grant the backup identity only the PostgreSQL read and metadata permissions
   needed by `pg_dump`; keep it separate from the application and migration roles.
5. Create `lians-backup-worm-identity` from
   `backup-worm-identity.example.yaml`, retaining only the selected provider's
   ownership fields. Bind `lians-backup-worm` to that provider's workload
   identity; do not mount static cloud credentials.
6. Restrict HTTPS egress to the selected identity, control-plane, and object-store
   endpoints using a private endpoint, egress gateway, or FQDN-aware policy.
7. Unsuspend the CronJob, observe one successful verified bundle and a four-file
   provider-attestation result that passes standalone provider verification, then
   complete an isolated restore drill.

The CronJob exits successfully only after the local bundle and sealed handoff are
verified; every backup object is immutable; and the uploader has stored the exact
canonical core attestation bytes create-only under its deterministic digest-derived
name in the same locked/versioned prefix. It requires the core attestation,
`<core>.sha256`, `<core>.anchor.json`, and `<core>.anchor.json.sha256`, then invokes
`verify_worm_attestation.py` and rechecks all four files. The anchor record binds the
core digest to the exact provider object ID, checksums, retention, and hold state.
Both upload and standalone verification use the provider SDK default workload
identity and expose no credential options.

The immutable provider object is the authenticity and integrity anchor within the
configured provider trust boundary; it requires no application signing secret and
does not replace provider audit logs, independently governed IAM, or retention
administration. Preserve all four files in the recovery evidence boundary and alert
on any pending handoff.
