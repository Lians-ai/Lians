# Lians Engine on Kubernetes

This bundle packages the shared Lians memory and evidence engine for an
operator-managed Kubernetes environment. It expects externally managed
PostgreSQL 16 with pgvector, Redis, TLS ingress, and secret storage. It does not
install databases or place real credentials in Git.

## Release status

The manifests target `ghcr.io/lians-ai/lians-engine:0.5.0`, but that image is a
release candidate until the GHCR publication workflow runs from an immutable
stable tag and the digest is verified. Do not deploy the tag to production
before the package is public. Production must pin the reported digest rather
than a mutable tag.

The published operator image includes Stripe metering, Prometheus metrics,
OpenTelemetry, and the supported AWS KMS, Azure Key Vault, and HashiCorp Vault
clients. It intentionally excludes the large local embedding model. Use Voyage
or OpenAI embeddings, or build the Dockerfile with `EXTRAS=enterprise,local`
and an immutable local-model revision for an air-gapped deployment.

## 1. Inspect and configure

Copy `k8s/` into a deployment repository. Change these values before applying:

- the browser origins in `configmap.yaml`;
- the ingress hostname and TLS secret in `ingress.yaml`;
- the image tag in `kustomization.yaml` and `migrate-job.yaml` to the same
  verified `ghcr.io/lians-ai/lians-engine@sha256:...` digest;
- the embedding and KMS provider settings for the target environment.

Render the non-secret bundle for review:

```bash
kubectl kustomize k8s/ > /tmp/lians-engine-rendered.yaml
```

The rendered output must not contain a Kubernetes `Secret` or any `CHANGE_ME`
credential. `secret.example.yaml` and `secret.env.example` are references only
and are deliberately excluded from `kustomization.yaml`.

## 2. Create secrets outside Git

An External Secrets Operator, Sealed Secret, or platform-native secret manager
is preferred. For an evaluation cluster only, copy `secret.env.example` to a
private location outside the repository, restrict it to the current user, and
replace every required placeholder:

```bash
install -m 600 k8s/secret.env.example /secure/path/lians-engine.env
kubectl apply -f k8s/namespace.yaml
kubectl -n agentmem create secret generic agentmem-secret \
  --from-env-file=/secure/path/lians-engine.env \
  --dry-run=client -o yaml | kubectl apply -f -
```

Never paste `ADMIN_SECRET`, encryption keys, database credentials, or provider
tokens into source-controlled YAML. Keep `PROVISIONING_SECRET` empty unless a
separate website broker is deployed, and never reuse the admin secret.

## 3. Migrate, then deploy

Run the schema migration as a separate, inspectable operation before changing
the serving Deployment:

```bash
kubectl -n agentmem delete job agentmem-migrate --ignore-not-found
kubectl apply -f k8s/migrate-job.yaml
kubectl -n agentmem wait \
  --for=condition=complete job/agentmem-migrate --timeout=10m
kubectl -n agentmem logs job/agentmem-migrate
kubectl apply -k k8s/
kubectl -n agentmem rollout status deployment/agentmem --timeout=10m
```

The application init container refuses to serve until the database matches the
image's migration head. Readiness checks database and Redis through `/readyz`;
liveness uses `/livez` so a dependency outage does not restart healthy
processes.

## 4. Provision the first tenant key

Use the admin API over a private operations network. The plaintext tenant key
is returned once; store it in the target secret manager and do not put it in a
ticket, shell script, or application log.

```bash
curl --fail-with-body --request POST \
  --header "X-Admin-Secret: $LIANS_ADMIN_SECRET" \
  --header "Content-Type: application/json" \
  --data '{"namespace":"acme-prod","label":"owner","scopes":["read","write","admin"]}' \
  https://memory.example.com/v1/admin/api-keys
```

Verify the public process endpoint, then exercise memory with the new tenant
key:

```bash
curl --fail https://memory.example.com/livez
curl --fail --header "X-API-Key: $LIANS_TENANT_KEY" \
  "https://memory.example.com/v1/memories?agent_id=smoke&limit=1"
```

## 5. Upgrade and rollback

For every upgrade, record the current image digest, take a verified encrypted
database snapshot, run the migration Job with the new digest, and wait for the
rolling Deployment. Roll application code back by restoring the prior digest;
do not automatically downgrade the database. Retain the release provenance,
SBOM, migration output, image digest, health evidence, and rollback digest with
the change record.
