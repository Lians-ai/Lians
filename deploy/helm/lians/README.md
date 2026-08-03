# Lians production Helm chart

This chart distributes the Lians API and, when explicitly enabled, its durable
OpenTelemetry gateway, monitoring objects, migration hook, and suspended backup
reference. It is intentionally fail-closed: workload images must use immutable
`sha256` digests, application credentials must already exist as Kubernetes
Secrets, and the API will not start against a database that differs from the
single Alembic head packaged in the image.

The chart does **not** install PostgreSQL or Redis. It does not claim to make
either dependency highly available. Use externally operated production services
with tested replication, failover, encrypted transport, point-in-time recovery,
and capacity controls.

## Prerequisites

- Kubernetes 1.27 or newer and Helm 3.
- A dedicated namespace. The chart intentionally installs a namespace-wide
  default-deny policy and must not share a namespace with unrelated workloads.
- A CNI that enforces `networking.k8s.io/v1` NetworkPolicy. Core Kubernetes
  policies select IP ranges, not DNS names; resolve and maintain the configured
  managed-service CIDRs or add equivalent CNI-native FQDN policies.
- External PostgreSQL with `pgvector`, TLS verification, backups, a dedicated
  DDL-capable migrator, and a non-owner runtime login inheriting the fixed
  `lians_runtime` capability role.
- External Redis with peer-verifying TLS (`rediss://`), authentication, persistence
  appropriate to rate-limit/cache usage, and a tested failure policy.
- AWS KMS, Azure Key Vault, or Vault. Production rejects environment-only KMS.
- An OCI image produced by the release pipeline and pinned by digest.
- Optional: Prometheus Operator CRDs for `ServiceMonitor`/`PrometheusRule`, an
  ingress controller, and Metrics Server for the default HPA. Enabling the
  collector additionally requires a named `ReadWriteOnce` StorageClass whose
  CSI/provider encryption, key custody, retention, deletion, and incident
  controls are approved for raw telemetry.

Create the namespace and all Secrets before rendering an install. The chart does
not create plaintext Secrets or cloud credentials.

## Secret contract

`existingSecrets.application.name` references a Secret containing:

- `SUBJECT_REFERENCE_KEY`, a dedicated raw 32-byte HMAC key encoded as exactly
  64 hexadecimal characters or canonical base64; it must differ from every
  encryption, receipt-signing, API, metrics, and integration key;
- `METRICS_BEARER_TOKEN` of at least 32 characters when metrics are enabled;
- `STRIPE_API_KEY` when `config.metering.enabled=true`; the chart projects it
  only into API pods and never into migration, collector, or backup workloads;
- any selected external embedding/LLM keys such as `VOYAGE_API_KEY`,
  `OPENAI_API_KEY`, or `ANTHROPIC_API_KEY`.

`existingSecrets.database.runtime` and `.migration` must name different Secret
objects. Each configured key contains one SQLAlchemy async `DATABASE_URL`. The
runtime URL is projected only into the API and its read-only schema preflight;
the migration URL is projected only into the opt-in Alembic hook. The chart
refuses one shared Secret. `existingSecrets.redis` independently references the
`REDIS_URL`. For this supported external-service chart, both database Secrets must
contain a network URL with exactly one `sslmode=verify-full`, for example
`postgresql+asyncpg://USER:PASSWORD@db.example:5432/lians?sslmode=verify-full`.
The Redis Secret must use `rediss://` with a certificate hostname; Lians forces
certificate and hostname verification even when the URL omits those query flags.

When `config.metering.enabled=true`, authoritative decisions and successful
single-use Gate permit consumptions are the product-native protected units.
Memory writes and recalls remain separate compatibility units. Every usage fact
is committed to PostgreSQL with its billable source and each replica runs the
leased delivery worker. The chart requires four distinct event names, requires
the worker, injects `STRIPE_API_KEY` from the bounded application Secret, and
renders the retry/lease/idempotency controls into the ConfigMap.
It also requires `asyncErrorDestinationConfigured=true`, which is an operator
attestation that Stripe's asynchronous meter-error thin events reach a durable,
monitored event destination.
Maintain Stripe HTTPS destinations in `networkPolicy.externalHttpsCidrs` (or an
independently reviewed CNI FQDN policy) and operate the
[durable metering runbook](../../../docs/durable-metering.md). Air-gap mode
rejects metering outright.
Runtime database sessions also enforce bounded statement, lock-wait, and idle
transaction timeouts. These are separate from the reviewed migration-job budget;
do not raise API timeouts to make an unsafe blocking migration fit.

`config.impactAssessmentWorker` configures autonomous exhaustive blast-radius
processing on every API replica. The production chart requires it enabled,
requires its lease to exceed the database statement timeout by at least 15
seconds, and renders bounded claim, concurrency, page, retry, and poison-job
attempt limits. PostgreSQL `SKIP LOCKED` claims are global, but each page is
processed under the job's exact tenant/barrier RLS context. Keep the worker
alerts and [operations runbook](../../../docs/production-operations.md) active;
the caller `/advance` endpoint is a compatible control path, not a scheduler.
The same fail-closed contract applies to `config.recorderEvidenceIndexWorker`,
`config.subjectErasureWorker`, and `config.scimReconciliationWorker`: every
replica runs bounded, leased, retry-limited pages, and production rendering
requires each loop enabled with a lease longer than its database statement
budget. `/readyz` closes if any required process loop terminates. Prometheus
rules page on unhealthy/stale workers and failed or over-age durable jobs without
exporting tenant, subject, IdP, or payload identity.
Do not add `ssl_cert_reqs=none` or disable hostname checking. The chart hard-codes
the local-socket exception off. Because Helm cannot inspect existing Secret bytes,
the API and runtime init preflight validate the runtime URL, while the migration
preflight independently validates the migrator URL before opening the engine.
`SUBJECT_REFERENCE_KEY` is projected only into the API container, never a
ConfigMap, init container, migration Job, or receipt-signing Secret. Rotate it
only through the subject-reference migration/compatibility procedure: changing
it changes deterministic subject references and can break lookup continuity.

`config.database` emits `DATABASE_POOL_SIZE`, `DATABASE_MAX_OVERFLOW`, and
`DATABASE_POOL_TIMEOUT_SECONDS`. Set `connectionBudget` to the database connection
allocation reserved for API replicas *after* reserving capacity for migration,
backup/restore, monitoring, break-glass, and provider operations. Rendering fails
unless `(poolSize + maxOverflow) * autoscaling.maxReplicas <= connectionBudget`
(or uses `replicaCount` when HPA is disabled). This is a ceiling, not a sizing
recommendation; validate steady-state and failover behavior below it.

Before the first migration, a database/security administrator provisions the
capability and login roles. Supply passwords through the database provider or
secret manager, never the SQL change record:

```sql
CREATE ROLE lians_runtime NOLOGIN NOSUPERUSER NOBYPASSRLS;
CREATE ROLE lians_app LOGIN INHERIT NOSUPERUSER NOBYPASSRLS;
GRANT lians_runtime TO lians_app;
CREATE ROLE lians_migrator LOGIN NOSUPERUSER NOBYPASSRLS;
```

Use site-specific login names if required, but the NOLOGIN capability role is
exactly `lians_runtime`: maintained migrations grant supported runtime functions
and relation privileges to that role. Neither that capability nor the runtime
login may own an application
database, schema, relation, function, or type. The migration login/schema owner is
NOSUPERUSER/NOBYPASSRLS, does not inherit `lians_runtime`, is separately held, and is never
available to an API pod. Helm preflights these invariants and migration fails if
the capability role was not pre-provisioned. Audit `rolsuper`, `rolbypassrls`,
membership, ownership, default privileges, and append-only revocations after
every migration and restore.

`existingSecrets.receipts` is a separate signing Secret. For `provider=local`,
set only `localPrivateKeyKey` to a hex- or canonical-base64-encoded raw 32-byte
Ed25519 private-key entry. For
`provider=vault-transit`, leave that empty and configure exactly one of
`vaultTokenKey` (direct environment compatibility) or `vaultTokenFileKey`
(preferred rotating, read-only Secret projection). The Vault mode injects no
local private key. It requires a credential-free HTTPS origin, safe mount/key
segments, exact positive key version, raw public-key pin, bounded timeout, and
reviewed Vault CIDRs. See the
[receipt-signing trust contract](../../../docs/receipt-signing.md).

`config.kms.envelopeKeyId` is the stable non-secret identifier embedded in every
new envelope. It is distinct from an AWS ARN, Azure secret version, or Vault
path. `config.receipts.signingKeyId` is the separately published receipt issuer
key ID; the chart rejects the generic development identifier.

For `config.kms.provider=aws`, `existingSecrets.kms.name` must contain
`KMS_AWS_ENCRYPTED_KEY`; region and CMK identifier are non-secret values. During
the bounded dual-key window it must also contain
`KMS_AWS_PREVIOUS_ENCRYPTED_KEY`, while the previous envelope ID and optional
region/CMK ID remain values. Grant decrypt through pod workload identity, not
static AWS keys. For `vault`, the Secret contains `KMS_VAULT_TOKEN`; current
and previous paths/addresses/mounts are values. For `azure`, annotate the
application ServiceAccount for workload identity; current and previous vault
URLs/secret names are non-secret and no KMS Secret is mounted. Configure a
previous provider slot only together with `previousEnvelopeKeyId`, and follow
the [rotation runbook](../../../docs/master-key-rotation.md).

The optional collector Secret contains `LIANS_INGEST_API_KEY`. This is an
ordinary scoped Lians workload credential, not a cloud credential. A
ServiceMonitor credential Secret must exist in the ServiceMonitor namespace and
contain the configured bearer-token key.

The chart does not deploy the Gate enforcement mediator. Set
`networkPolicy.gateMediator.enabled=true` only when a separately reviewed
mediator pod with the configured exact label selectors is installed in the same
namespace. Helm then admits that pod to the API, admits only evaluator-labelled
callers to the mediator, and grants mediator egress only to DNS, the configured
Gate TLS port, and explicit provider CIDRs. An empty `providerCidrs` deliberately
prevents provider dispatch. See the
[standalone mediator deployment](../../gate-mediator/README.md) and never route
it through the public API Ingress.

Set `networkPolicy.gateMediator.metricsIngressEnabled=true` only with the
mediator's distinct bearer-protected `/metrics` endpoint. The configured
namespace and pod selectors then admit exactly the Prometheus scraper; the
standalone deployment provides a separately reviewed ServiceMonitor example.

Increment `existingSecrets.rolloutRevision` whenever an external Secret changes.
Helm hashes ConfigMap values automatically, but cannot read or hash a Secret it
does not own. Secret keys are projected individually rather than through an
unbounded `envFrom`, so a credential Secret cannot override deployment region,
capture posture, KMS provider, or another trusted non-secret setting.

## Controlled install and upgrade

Copy `production-values.example.yaml` to a protected deployment repository and
replace every `CHANGE_ME`. The example deliberately fails schema validation
until all site-specific references, CIDRs, and digests are supplied.

Synthetic files under `tests/` are non-secret render fixtures for AWS, Azure,
Vault, and air-gap postures. They prove chart structure in CI and must never be
used as deployment configuration or treated as evidence that a cloud identity,
Secret, endpoint, or retention policy exists.

Review the image provenance, SBOM, vulnerability decision, and every Alembic
revision between the currently deployed and target images. Then run the
explicit migration hook for the controlled operation:

```console
helm upgrade --install lians ./deploy/helm/lians \
  --namespace lians \
  --values /secure/config/lians-production.yaml \
  --set migrations.enabled=true \
  --atomic \
  --timeout 30m
```

For a tagged release, verify the published chart attestation and Cosign identity
as described in the [supply-chain runbook](../../../docs/supply-chain-security.md),
then replace the local chart path above with its immutable OCI reference:

```console
helm upgrade --install lians \
  oci://ghcr.io/lians-ai/charts/lians@sha256:VERIFIED_CHART_DIGEST \
  --namespace lians \
  --values /secure/config/lians-production.yaml \
  --set migrations.enabled=true \
  --atomic \
  --timeout 30m
```

The hook first verifies `lians_runtime` exists as a non-owner NOLOGIN,
NOSUPERUSER/NOBYPASSRLS role, verifies the migrator is also non-superuser,
cannot bypass RLS, and does not inherit the runtime capability, verifies the image
contains exactly one schema head, and reads the external database revision using
only the migrator Secret.
It then runs `alembic upgrade head` and verifies the resulting revision exactly
matches the packaged head. Application pods use only the runtime Secret and
refuse startup unless their login inherits `lians_runtime`, is non-superuser,
cannot bypass RLS, owns no application database/schema/relation/function/type, and sees the exact packaged
head. With migrations disabled, a stale or empty database leaves pods blocked.

Do not enable migrations automatically in a continuous reconciler without a
separate migration review and rollback/restore decision. Database rollback is a
recovery procedure, not an implicit Helm rollback.

## Identity, provisioning, and governance

OIDC providers, subject bindings, SCIM tenants, credentials, group entitlements,
and namespace governance policies are encrypted/audited tenant records. They are
managed through Lians administrative APIs after installation rather than placed
in Helm values. This prevents reusable SCIM tokens or IdP lifecycle state from
being copied into release metadata.

- OIDC administration on an isolated private admin process: `/v1/admin/identity/*`
- SCIM administration on that private process: `/v1/admin/enterprise/scim/*`
- SCIM service-provider endpoints: `/scim/v2/{tenant_id}/*`
- Namespace residency/capture/quota policy: `/v1/admin/governance/*`
- Deployment posture: `/v1/platform/capabilities` and admin readiness endpoints

The supported chart is the public/data-plane surface and hard-codes
`API_SURFACE=public`; no break-glass router is registered and no `ADMIN_SECRET`
is projected into its pods. Break-glass routes must run as a separate
`API_SURFACE=admin` process behind a private
ingress, independent strong operator authentication, restrictive NetworkPolicy,
and dedicated audit/alerting. Do not route `/v1/admin/*` through this public
Service or rely on the static secret as the network boundary.

`config.deploymentRegion` is server-owned input to residency enforcement and
must match independent deployment inventory. Recorder full capture remains off
by default; changing it requires both the deployment opt-in and an active tenant
policy that permits the capture mode. Air-gap mode requires the self-hosted
embedding model, an empty `config.telemetry.exporterEndpoint`, and
`config.integration.workerEnabled=false`; the chart rejects mixed postures.
The independently reviewed NetworkPolicy remains the authoritative boundary for
approved DNS, IdP, KMS, PostgreSQL, and Redis paths.

## Network boundary

The chart creates namespace-wide default-deny ingress and egress plus explicit
allow policies:

- ingress-controller namespace to API port 8000;
- monitoring namespace to the bearer-protected metrics endpoint;
- collector pods to authenticated OTLP/HTTP ingestion;
- DNS only to selected DNS pods;
- PostgreSQL, Redis, HTTPS/KMS/IdP/integration, Vault, and external OTLP only to
  configured destination CIDRs and ports;
- telemetry producers only from labeled namespaces to collector ports 4317/4318;
- collector egress only to DNS and Lians API pods;
- migration and backup jobs only to their explicitly required destinations.

IP allowlists for managed services require operational maintenance. If the CNI
supports DNS-aware policies, install an additional reviewed policy rather than
broadening the chart to unrestricted port 443.

### Trusted proxy client identity

`config.trustedProxyCidrs` must contain the exact source CIDRs of the ingress
controller or service-mesh peers that open connections directly to the API
pods. These are proxy addresses, not public client networks. The chart renders
the list as `TRUSTED_PROXY_CIDRS`; wildcard and world-open ranges are rejected.

The release image starts Uvicorn with `--no-proxy-headers`, leaving the original
socket peer intact. The application consults `X-Forwarded-For` only when that
peer is trusted, accepts one bounded comma-separated chain, and walks trusted
hops from right to left. An untrusted peer, malformed value, more than 32 hops,
or a value over 2,048 characters falls back to the socket peer. `Forwarded` and
`X-Real-IP` are deliberately ignored so there is one client-identity authority.

Determine these CIDRs from observed ingress-to-pod connections and the
controller's maintained address plan. Include every trusted proxy hop that can
appear at the right of the chain, and test two independent clients through the
real ingress. If a deployment overrides the image command, it must retain
`--no-proxy-headers`; enabling Uvicorn's own parsing bypasses this trust model.

## Durable collector

`otelCollector.enabled=true` deploys a two-or-more replica StatefulSet. Every
replica has a private `ReadWriteOnce` PVC-backed `file_storage` queue, infinite
retry elapsed time, a PDB, topology spreading, anti-affinity, health probes, and
restricted security contexts. The queue contains raw producer-supplied OTLP
bytes before the Lians endpoint applies `metadata_only`/`hash_only` minimization;
application redaction does not protect this disk. The chart therefore refuses an
enabled collector unless the operator names a StorageClass, attests encryption
at rest, acknowledges raw-payload custody, and records an approved custody-policy
reference. These are operator assertions: independently verify CSI/provider key
policy, snapshots/replicas, node access, backup behavior, support access, and
deletion evidence.

Queue state is not replicated between collector replicas. PVCs are retained on
scale-down and StatefulSet deletion because they may be temporarily authoritative
for unaccepted spans. Restrict PVC read/attach/snapshot/delete privileges, alert
on queue age/bytes and unexpected volume attachment, and define maximum custody
time plus reviewed secure deletion after successful drain or incident hold.
Preserve a replica's PVC during an outage; do not retain it indefinitely merely
because the chart uses infinite retry. Size storage for the maximum accepted
evidence rate and recovery window.
The gateway attaches one scoped Lians ingest credential to exported batches, so
its producer namespace selector is a trust boundary. Do not share one collector
release across mutually untrusted tenants; deploy a separately credentialed
release or gateway boundary for each attribution domain. The application
`config.telemetry.exporterEndpoint` is deliberately a separate observability
collector. Pointing Lians' own instrumentation at this inbound gateway would
feed receiver spans back into `/v1/traces` and create recursive ingestion.

## Monitoring

The optional ServiceMonitors preserve the job labels expected by the included
Prometheus rules (`agentmem` and `lians-otel-collector`). API metrics require a
bearer token even inside the monitoring NetworkPolicy boundary. The included
rules cover request error-budget burn, recall latency, collector scrape health,
queue utilization, and potential enqueue loss. Route alerts to owned runbooks
before enabling paging.

## Backup reference

The optional backup CronJob is suspended by default. It references an existing
encrypted PVC, database credential Secret, non-secret provider identity ConfigMap, and a
digest-pinned backup image. It does not accept cloud access keys in values. Bind
the dedicated ServiceAccount to provider workload identity and restrict its
permissions to the immutable destination.

The backup Secret supplies `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`,
`LIANS_DATABASE_ID`, `WORM_DESTINATION`, files `pgpass` and `ca.crt`, and no cloud
credential. An enabled backup requires at least one explicit, non-world-open
`wormDestinationCidrs` entry; both JSON Schema and templates reject an empty,
placeholder, `0.0.0.0/0`, or `::/0` list, so the NetworkPolicy never emits an
empty `to` selector. Include the reviewed workload-identity exchange and
immutable object-store endpoint ranges. Unsuspend only after a manual bundle has
passed checksum verification, provider retention attestation, and an isolated
restore drill.

## Security and availability notes

- Containers run as UID/GID 10001, drop all capabilities, prohibit privilege
  escalation, use a read-only root filesystem, and mount bounded emptyDirs only
  for temporary/cache paths.
- Workloads do not mount Kubernetes API tokens. Provider workload-identity
  admission may inject its own short-lived projected token where configured.
- HPA, PDB, anti-affinity, topology spread, zero-unavailable rolling updates,
  startup/readiness/liveness probes, and graceful termination protect the API.
  They do not replace multi-zone external PostgreSQL/Redis or capacity testing.
- TLS is terminated by a configured Ingress and an existing TLS Secret. Enforce
  re-encryption/mTLS to the pod with controller- or mesh-specific settings where
  the threat model requires it.
