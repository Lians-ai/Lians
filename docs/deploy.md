# Production deployment

The supported production distribution is the fail-closed Helm chart in
`deploy/helm/lians`, published on release as an attested and keyless-signed OCI
artifact. Docker Compose is for local development and staging. The
raw manifests under `k8s/` are readable integration references, not a substitute
for supplying site-specific identities, immutable image digests, network ranges,
TLS, and external Secrets.

## Required platform

- Kubernetes 1.27 or newer with Helm 3 and an enforcing NetworkPolicy CNI.
- PostgreSQL 16 with `pgvector`, TLS verification, HA/PITR, and separate
  migration, application, backup, and restore roles.
- Authenticated Redis on peer-verifying `rediss://` transport. Redis is a cache and
  rate-limit dependency, never the recovery source of truth.
- AWS KMS, Azure Key Vault, or Vault reached through workload identity and
  least-privilege policy. Production rejects an environment-only master key.
- A dedicated namespace, externally managed TLS certificate, and an ingress/WAF
  boundary with request and DDoS controls.
- Digest-pinned API, collector, and backup images whose signatures, provenance,
  SBOMs, and vulnerability decisions have been independently verified.

See the chart's [complete contract](../deploy/helm/lians/README.md), the
[threat model](threat-model.md), and [production operations](production-operations.md)
before an install.

## 1. Verify the release

The production workflow builds each supported platform once, scans every exact
staged payload digest, composes only those payloads, emits SBOM/provenance
evidence, and signs/attests the resulting immutable index. Resolve the tag to
that digest and verify it before copying it into deployment configuration:

```bash
gh attestation verify oci://ghcr.io/OWNER/REPOSITORY@sha256:DIGEST \
  --repo OWNER/REPOSITORY
cosign verify \
  --certificate-oidc-issuer=https://token.actions.githubusercontent.com \
  --certificate-identity-regexp='^https://github.com/OWNER/REPOSITORY/.github/workflows/supply-chain.yml@refs/(heads/main|tags/v.+)$' \
  ghcr.io/OWNER/REPOSITORY@sha256:DIGEST
```

Admission must reject tags, unknown issuers, failed signatures/attestations, and
images outside the approved repository. Verification commands and policy examples
live in [supply-chain-security.md](supply-chain-security.md).

## 2. Create external identities and Secrets

Do not place credentials in a values file. Pre-create:

- an application Secret containing a dedicated 32-byte hex/base64
  `SUBJECT_REFERENCE_KEY`, `METRICS_BEARER_TOKEN` when enabled, and only the
  selected model-provider keys; the subject-reference HMAC key must differ from
  encryption/signing/API keys and the supported public pods receive no global
  admin credential;
- a separate receipt-signing Secret containing either the raw local Ed25519 key
  or exactly one Vault Transit token source; prefer the rotating, read-only
  token-file projection;
- different runtime and migration database Secret objects, each containing its own
  network `DATABASE_URL` with exactly one `sslmode=verify-full`, plus an independent
  Redis `REDIS_URL` Secret using `rediss://` with a certificate hostname;
- the selected KMS Secret contract described by the chart;
- optional, separately scoped Recorder, ServiceMonitor, and backup credentials.

Use an external-secret controller or equivalent managed-secret workflow. The
chart reads named keys rather than using `envFrom`, and
`existingSecrets.rolloutRevision` must change whenever external secret content
changes.

Pre-provision `lians_runtime` as a non-owner `NOLOGIN NOSUPERUSER NOBYPASSRLS`, grant its
membership to a non-owner runtime login, and hold the distinct migrator login
outside the API workload. The migrator must also be NOSUPERUSER/NOBYPASSRLS and
must not inherit `lians_runtime`. The chart verifies these properties before
startup or migration. Maintained migrations grant runtime capabilities to the
fixed group.

This check is also enforced inside the server. In production, startup and
`/readyz` reject a changed session role, `SUPERUSER`, `BYPASSRLS`, database/role
creation or replication privileges, missing effective `lians_runtime`
inheritance, ownership of application objects, or membership in an
application-owner role. Raw Kubernetes, Render, and other supported deployment
wrappers therefore fail closed if the runtime URL is wired to the wrong identity
or database privileges drift after startup.

The raw `k8s/` reference uses the same separation. `agentmem-database-runtime`,
`agentmem-redis`, `agentmem-application`, `agentmem-receipt-signing`,
`agentmem-kms`, and `agentmem-recorder-ingest` are independently projected by
key; no pod imports a complete Secret. The intentionally non-Kustomized
`migration-secret.yaml` is applied for the one-shot migration window and removed
before API rollout. Never merge those objects or add the migration Secret to the
Deployment, even when an external-secret controller supplies the values.
The public application Secret also omits `ADMIN_SECRET`; the non-Kustomized
`admin-secret.yaml` belongs only on a separately isolated administrative
deployment.

The raw reference applies namespace-wide default-deny and uses TEST-NET
destination CIDRs as deliberately non-runnable egress placeholders. Before the
migration Job, apply an environment overlay that replaces each placeholder with
the exact database, Redis, observability, and authenticated egress-gateway/private
endpoint CIDR. DNS rules select only the reviewed `kube-system` DNS workload.
Never make an example runnable by removing `to` selectors or adding
`0.0.0.0/0`/`::/0`.

The chart hard-codes `API_SURFACE=public`, does not register break-glass routers,
and does not inject `ADMIN_SECRET`. Run the `API_SURFACE=admin` process only as a
separate private deployment behind strong operator identity, restrictive ingress
and egress, and alerting on every use. Tenant administration should move
immediately to OIDC. Provision SCIM and short-lived workload credentials only
after identity mappings have been reviewed.

The chart hard-codes the local data-service socket exception off. Both API startup
and its init preflight validate the runtime database transport; the migration init
preflight independently validates the migrator URL. Redis clients force certificate
and hostname verification. Configure `config.database.poolSize`, `maxOverflow`, and
`poolTimeoutSeconds`; rendering rejects a worst-case HPA pool ceiling above
`config.database.connectionBudget`. That budget is the API allocation remaining
after non-API and emergency connection reserves, not PostgreSQL's raw
`max_connections` value.
For the preferred remote-signing posture, follow the pinned-key and rotation
contract in [receipt-signing.md](receipt-signing.md).

## 3. Configure a production values file

Copy `deploy/helm/lians/production-values.example.yaml` into a protected
deployment repository. Replace every `CHANGE_ME` with:

- verified image repositories and `sha256` digests;
- exact existing Secret names and workload-identity annotations;
- the authoritative processing region and explicit HTTPS CORS origins;
- current master-key and receipt-signing key identifiers;
- PostgreSQL, Redis, IdP/KMS/integration, ingress, monitoring, and backup network
  ranges;
- a trusted-proxy CIDR chain matching the actual ingress path;
- dedicated observability and inbound Recorder collector settings.

If the Recorder collector is enabled, name an independently verified encrypted
StorageClass and record the approved raw-OTLP queue custody policy. The file queue
persists producer bytes before Lians applies hash/metadata minimization.

The chart intentionally has no runnable production defaults. `helm lint --strict`
and `helm template` must fail until every required control is supplied.

For air-gap operation, use the self-hosted embedding image, leave the application
OTLP exporter empty, disable integration delivery, and independently enforce
deny-by-default egress. The application rejects configured external models, SIEM,
Stripe, and OTLP exporters and disables legacy webhooks, metering, and outbox
delivery, but network policy remains the authoritative boundary.

## 4. Migrate in a controlled window

The application never mutates schema at startup. Review every migration between
the deployed and target image, take and verify a recoverable backup, then run the
chart's isolated migration hook:

```bash
helm upgrade --install lians ./deploy/helm/lians \
  --namespace lians \
  --create-namespace \
  --values /secure/config/lians-production.yaml \
  --set migrations.enabled=true \
  --atomic \
  --timeout 30m
```

Tagged releases may replace the local chart path with the independently verified
digest reference
`oci://ghcr.io/lians-ai/charts/lians@sha256:VERIFIED_CHART_DIGEST`. Never promote
the semantic-version tag without pinning the digest returned by the signed chart
workflow.

The hook requires the pre-created safe `lians_runtime` capability role, uses only
the migration Secret, requires exactly one packaged Alembic head, upgrades
PostgreSQL, and checks that the database revision equals that head. API pods use
only the runtime Secret and additionally reject superuser, `BYPASSRLS`, missing
capability membership, or application database/schema/relation/function/type ownership.
The application repeats and strengthens that assertion at startup and on every
production readiness probe, including the ability to assume an owner role.
It also derives a live catalog inventory: every public table carrying
`namespace` must have enabled RLS plus an applicable namespace policy, every
`barrier_group` table must have a restrictive barrier policy, and all ordinary
tenant tables must force RLS. `api_keys` and `identity_bindings` are the only
FORCE exceptions: direct runtime reads are RLS-constrained, while exact,
PUBLIC-revoked SECURITY DEFINER functions owned by those tables return only the
active authentication record before a namespace exists. Readiness verifies the
function owner, fixed search path, `row_security=off`, grants, and non-owner,
non-BYPASSRLS runtime identity. A new tenant table or auth-function drift
therefore prevents production startup instead of waiting for an incident.
Do not enable the hook continuously in a GitOps reconciler.

For the 0.5 OTLP boundary, do not let the ordinary `upgrade head` hook run while
an old trace writer is live. Apply through `0054_otel_barrier`, quiesce and drain
`POST /v1/traces` and `PUT /api/v1/models/*`, then run the online terminal
`0054a_otel_barrier_contract`, then continue through the exact release head
`0063_admin_identity_indexes`. The later revisions seed the durable
retention cursor, install the exact auth lookups, build the pending-admission index
concurrently, and contract auth-table RLS after the documented authentication
fence. The terminal revision adds the immutable DecisionRecord v3 authorization
snapshot while preserving v1/v2 rows without inventing historical authorization,
then installs the forced-RLS fixed-snapshot Recorder indexing queue. The final
online-only companions build repairable concurrent indexes for Recorder audit
binding, Recorder decision/run pages, live-memory supersession, exclusive live
graph edges, barrier-aware ledger/decision/artifact keyset pages, and every
subject-table traversal used by durable erasure. The final revisions add the
resumable subject-erasure queue and indexes for bounded, converging memory-lineage
graphs and their immutable audit bindings, workload-credential pages, and
metering-event inventory pages. The SCIM revisions add the forced-RLS, leased,
fixed-User-snapshot binding reconciliation queue and its bounded traversal
indexes. Their online-only companion definition-checks and repairs interrupted
concurrent builds on both existing large tables. The release head uses the same
online repair contract for exact admin API-key and identity-provider/binding
inventory pages. Production requires the
Recorder evidence indexing, subject-erasure, and SCIM reconciliation workers;
its readiness fails closed if the process loop terminates.
They are not substitutes for the required OTLP writer fence. The
collector's persistent queue must absorb this bounded ingest pause. Reopen
ValidMind PUTs only after exact link-pair reconciliation. See the
[rolling-upgrade runbook](rolling-upgrade-0.5.md) for the required fence and
rollback posture.

### Fly and Render migration isolation

The checked-in Fly workflow deploys only an immutable digest produced by the
approved supply-chain workflow. Before deployment it runs the same image as a
one-shot migrator with `FLY_MIGRATION_DATABASE_URL`, executes the migration-role
preflight, upgrades the single Alembic head, and proves the exact release schema
contract. The Fly application receives only `DATABASE_URL`. Treat those two
secrets as different principals and rotate them independently.

The checked-in Render Blueprint describes the existing source-built service but
keeps automatic deploys off. A source build cannot prove that Render executes the
same immutable subject that the release workflow scanned and signed; it is not a
supported production promotion channel. Render does not permit changing an
existing service's runtime in place, so recreate or rename the service with
`runtime: image`, set `image.url` to the independently verified release digest,
and preserve the reviewed environment and routing configuration before sending
traffic. This external recreation is an operator change, not something the
repository can safely perform.

Render pre-deploy commands inherit a web service's environment, so neither the
paused Blueprint nor its replacement places a migrator credential there. An
external protected workflow or operator-controlled one-off job must run the
immutable target image with the distinct migrator URL, in this order:

```bash
python -m lians.migration_preflight
alembic -c alembic.ini upgrade head
python -m lians.migration_contract
```

Only then may the image-runtime Render service deploy. Its `preDeployCommand`
performs a read-only exact schema check with the non-owner runtime URL and aborts
if migration has not finished. The Blueprint deliberately leaves both
`DATABASE_URL` and `REDIS_URL` as operator-supplied secrets: the database URL
must identify the non-owner runtime role with `sslmode=verify-full`, and Redis
must be authenticated `rediss://` with peer and hostname verification. Do not
substitute a generated database-owner URL or a plaintext private-network Redis
URL.

## 5. Bootstrap tenant administration

Never insert API-key hashes directly into PostgreSQL. Use the separately deployed
private admin surface once to create the initial bounded tenant credential; the
plaintext key is returned once and only its digest is stored. The URL below must
resolve only inside the operator network, never through the public Lians Service:

```bash
curl --fail-with-body \
  -H "X-Admin-Secret: $ADMIN_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"namespace":"prod","role":"owner","scopes":["admin"],"label":"bootstrap"}' \
  https://lians-admin.internal.example/v1/admin/api-keys
```

Store the result immediately, configure tenant OIDC and governance policy, issue
bounded workload credentials, then remove routine dependence on the break-glass
admin secret. Recorder capture defaults to hash/reference minimization; full
capture requires both deployment and active namespace-policy approval.

## 6. Prove readiness before traffic

Record evidence for all of the following:

1. `/livez` is process-only and `/readyz` succeeds from the real ingress path.
2. The private admin platform-readiness response reports the expected migration, external
   KMS, receipt key, RLS, capture, governance, identity, and rotation posture.
3. OIDC login, SCIM deprovisioning, workload credential expiry/revocation, tenant
   and barrier isolation, Recorder deduplication, Gate deny/review/allow, receipt
   verification, and audit-chain verification succeed with synthetic data.
4. Independent clients receive distinct rate-limit network buckets through the
   configured trusted proxy chain; spoofed forwarding headers are ignored.
5. Prometheus alerts route to owned runbooks and the OTLP collector's persistent
   queue survives an API outage without enqueue loss.
6. A logical backup reaches provider-native immutable storage, its core attestation
   is independently anchored, and an isolated restore reaches application-ready
   state within the declared RPO/RTO.
7. Network tests prove cross-tenant access and all unapproved ingress/egress fail.

Use [backup-restore.md](backup-restore.md),
[worm-provider-handoff.md](worm-provider-handoff.md),
[master-key-rotation.md](master-key-rotation.md), and
[slo-alerting.md](slo-alerting.md) for the evidence-producing procedures.

## Upgrade, rollback, and recovery

Roll forward is the normal schema strategy. A Helm rollback cannot undo a
database contract and must never invoke a destructive Alembic downgrade
automatically.

- If an image is bad and no incompatible schema change ran, deploy the prior
  verified digest.
- If an additive backward-compatible migration ran, leave it and roll back only
  the application when the old version is known compatible.
- If data shape or integrity may be incompatible, stop writers and forward-repair
  or recover into an isolated PostgreSQL cluster.
- Never restore over the primary, delete the old cluster, or remove an old master
  key until investigation and backup-retention requirements are closed.

After every deployment or rollback, re-verify image provenance, migration head,
configuration/Secret revision, identity, RLS, receipt signing, Gate behavior,
audit append, Recorder ingestion, integration queues, backup freshness, and SLOs.
