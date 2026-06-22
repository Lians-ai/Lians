# Lian — Production Deploy Checklist

## Prerequisites

| Tool | Minimum version |
|------|----------------|
| Docker / Docker Compose | 24+ |
| PostgreSQL + pgvector | 16 + pgvector 0.7 |
| Python | 3.11+ |
| Node.js (SDK / demo) | 18+ |

---

## 1. Secrets & environment

Copy `.env.example` to `.env` and fill every value:

```env
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/Lian
ENCRYPTION_MASTER_KEY=<32-byte hex, generate with: python -c "import secrets; print(secrets.token_hex(32))">
ADMIN_SECRET=<long random string — never expose in client traffic>
ANTHROPIC_API_KEY=<required when SUPERSESSION_LLM_STAGE=true>
VOYAGE_API_KEY=<required when EMBEDDING_PROVIDER=voyage>
```

**Never commit `.env` to source control.**

---

## 2. Database bootstrap

```bash
# Run migrations — idempotent, safe to repeat
alembic upgrade head

# Verify schema version
alembic current
# Expected: 0011_rls_barriers (head)
```

### Required extensions

```sql
CREATE EXTENSION IF NOT EXISTS vector;      -- pgvector
CREATE EXTENSION IF NOT EXISTS "uuid-ossp"; -- optional, UUIDs handled in Python
```

### pgvector index (important for recall latency)

The migration creates the HNSW index. If you restored from a dump without it:

```sql
CREATE INDEX CONCURRENTLY ix_memories_embedding_hnsw
ON memories USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

---

## 3. First API key

```bash
python -c "
import hashlib, secrets
key = secrets.token_hex(32)
print('Key:', key)
print('Hash:', hashlib.sha256(key.encode()).hexdigest())
"
```

Insert the hash into the database:

```sql
INSERT INTO api_keys (key_hash, namespace, scopes, description)
VALUES ('<hash>', 'prod', ARRAY['read','write'], 'Initial admin key');
```

---

## 4. Deployment targets

### Docker Compose (single node / staging)

```bash
# Default build includes sentence-transformers + pre-baked BAAI/bge-large-en-v1.5.
# For Voyage/OpenAI providers (no local model needed), use a lean build:
#   docker build --build-arg EXTRAS= --build-arg PREDOWNLOAD_MODEL= -t Lian .
docker compose up -d
docker compose logs -f Lian
```

Health check: `curl http://localhost:8000/health`

### Fly.io

```bash
fly deploy --config fly.toml
fly secrets set ENCRYPTION_MASTER_KEY=<value> ADMIN_SECRET=<value> ...
```

### Kubernetes

```bash
kubectl apply -f k8s/
kubectl rollout status deployment/Lian
```

See `k8s/` for `Deployment`, `Service`, `HorizontalPodAutoscaler`, and
`PodDisruptionBudget` manifests.

---

## 5. Grafana dashboard

Import `grafana/Lian-dashboard.json` into your Grafana instance:

1. **Grafana → Dashboards → Import → Upload JSON file**
2. Select `grafana/Lian-dashboard.json`
3. Choose your Prometheus datasource when prompted

The dashboard requires `prometheus_client` to be installed on the server:

```bash
pip install prometheus-client>=0.19
```

Prometheus scrape config:

```yaml
scrape_configs:
  - job_name: Lian
    static_configs:
      - targets: ["Lian:8000"]
    metrics_path: /metrics
    scrape_interval: 15s
```

---

## 6. Security hardening

### Authentication
- [ ] All `api_keys` rows use scoped permissions — no wildcard `*` scopes in production
- [ ] `ADMIN_SECRET` is ≥ 32 chars, rotated every 90 days
- [ ] TLS termination at the load balancer; plain HTTP never exposed externally

### Encryption
- [ ] `ENCRYPTION_MASTER_KEY` stored in a secrets manager (AWS Secrets Manager, GCP Secret Manager, Vault), not in `.env`
- [ ] Master key rotation procedure documented and tested
- [ ] DEK (data encryption key) cache TTL matches your compliance requirement (default: 300 s)

### Network
- [ ] Database not reachable from public internet
- [ ] `GET /metrics` firewalled to internal monitoring network only
- [ ] `GET /v1/admin/*` firewalled — requires `X-Admin-Secret`, but defense-in-depth

### Audit chain
- [ ] `/v1/admin/audit/verify` run weekly and on every major release to confirm chain integrity
- [ ] Audit log archived to WORM storage (S3 Object Lock, Azure Immutable Blob) for SEC 17a-4 compliance

---

## 7. Operational runbook

### Health check

```bash
curl https://mem.yourfirm.internal/health
# Expected: {"status":"ok","db":"ok"}
```

### Conflict queue

Conflicts that exceed the SLA (> 24 h open) should page on-call:

```promql
# Alert rule
agentmem_conflict_queue_depth > 0
```

Resolve via API:

```bash
# List open conflicts
curl -H "X-API-Key: $KEY" https://mem.yourfirm.internal/v1/conflicts?status=open

# Accept memory A (trust the first source)
curl -X POST -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"resolution":"accept_a","note":"Bloomberg authoritative for AAPL EPS"}' \
  https://mem.yourfirm.internal/v1/conflicts/<conflict_id>/resolve
```

### Erasure (GDPR Art. 17 / CCPA)

```bash
curl -X POST -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"subject_id":"user-123","request_ref":"GDPR-2026-001"}' \
  https://mem.yourfirm.internal/v1/erase
```

### Compliance export (SEC / FINRA / CFTC exam)

```bash
curl -H "X-Admin-Secret: $ADMIN_SECRET" \
  "https://mem.yourfirm.internal/v1/admin/audit/export?namespace=prod&limit=10000"
```

### Chain verification

```bash
curl -H "X-Admin-Secret: $ADMIN_SECRET" \
  "https://mem.yourfirm.internal/v1/admin/audit/verify?namespace=prod"
# Expected: {"status":"ok","rows_checked":N}
```

---

## 8. Scaling guidance

| Metric | Recommended action |
|--------|--------------------|
| Write p99 > 500 ms | Add read replicas; check pgvector HNSW index present |
| Recall p99 > 100 ms | Warm Redis cache; increase session cache TTL |
| Conflict queue depth > 20 | Alert compliance team; do not let conflicts age > 24 h |
| DB CPU > 70% | Scale up instance or add connection pooling (PgBouncer) |

---

## 9. Rollback

```bash
# Roll back one migration
alembic downgrade -1

# Roll back to specific revision
alembic downgrade 0007_billing

# Deploy previous container image
docker compose down && docker compose up -d --pull always
```
