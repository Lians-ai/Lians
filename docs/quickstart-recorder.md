# Universal Recorder and Gate: local quickstart

This walkthrough takes about 10–15 minutes on a machine with Docker, Python
3.11+, and a warm container cache. It records only synthetic data, stores
captured content as hashes by default, evaluates a runtime policy, and closes an
investigation through owned work plus immutable attestations.

## 1. Start Lians (about 7 minutes)

From the repository root in PowerShell:

```powershell
Set-Location .\agentmem
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
docker compose up --build -d --wait --wait-timeout 180
Invoke-RestMethod http://localhost:8000/readyz
```

The checked-in development environment uses a deliberately local admin secret.
Never use it outside an isolated developer machine. Production startup requires
separate strong secrets and receipt-signing key material.

## 2. Provision a synthetic namespace key (about 1 minute)

The plaintext key is returned once. Keep it in an environment variable; do not
print it, paste it into source, or commit it.

```powershell
$adminSecret = ((Get-Content .env | Select-String '^ADMIN_SECRET=').Line -split '=', 2)[1]
$adminHeaders = @{ "X-Admin-Secret" = $adminSecret }
$keyBody = @{
  namespace = "recorder-quickstart"
  label = "synthetic-quickstart-owner"
  role = "owner"
  scopes = @("read", "write", "admin")
} | ConvertTo-Json
$created = Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/v1/admin/api-keys `
  -Headers $adminHeaders `
  -ContentType "application/json" `
  -Body $keyBody
$mediatorBody = @{
  namespace = "recorder-quickstart"
  label = "synthetic-quickstart-mediator"
  role = "analyst"
  scopes = @("read", "write")
} | ConvertTo-Json
$mediator = Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/v1/admin/api-keys `
  -Headers $adminHeaders `
  -ContentType "application/json" `
  -Body $mediatorBody
$env:LIANS_URL = "http://localhost:8000"
$env:LIANS_API_KEY = $created.key
$env:LIANS_MEDIATOR_API_KEY = $mediator.key
Remove-Variable created
Remove-Variable mediator
Remove-Variable adminSecret
Remove-Variable adminHeaders
Remove-Variable keyBody
Remove-Variable mediatorBody
```

An OIDC/workload token can be used instead: leave `LIANS_API_KEY` unset and set
`LIANS_ACCESS_TOKEN`. The Python and TypeScript clients send exactly one normal
credential and `whoami()` shows the resolved tenant, identity type, scopes, and
barrier without exposing the credential.

## 3. Run the Python workflow (about 2 minutes)

```powershell
py -m pip install -e .\sdk\python
py .\sdk\python\examples\recorder_control_quickstart.py
```

The example does the following:

1. Reads authenticated capabilities and the admin deployment-readiness report.
2. Builds native Lians, OTLP GenAI, MCP JSON-RPC, and A2A events with a shared
   run/trace boundary and stable retry identities.
3. Batch-ingests them and checks first-receipt readiness.
4. Creates and activates a versioned Gate policy with a separately identified
   mediator and a 30-second permit ceiling.
5. Evaluates a high-risk synthetic action bound to a canonical downstream-request
   digest, then independently re-hashes and consumes the allow permit using
   `LIANS_MEDIATOR_API_KEY`. If that credential is omitted, a deliberately
   unredeemable `LIANS_MEDIATOR_PRINCIPAL_REF` placeholder is used instead.
6. Opens a synthetic investigation, assigns a remediation task, attests the
   task closure, then attests the case closure.

Expected output contains counts, readiness, a Gate disposition, a permit ID (when
allowed), and generated resource IDs. It never prints permit tokens, request
payloads, or credentials. Permit consumption occurs only through the separately
authenticated mediator credential, never the evaluator credential.

Recorder event output deliberately separates the caller-claimed `agent_id`
from the server-derived `ingested_by_principal_ref`. Check
`actor_attribution`, `ingested_by_auth_method`, and `event_hash_version` when
provenance matters. Version 1 is explicitly unverified legacy history; new
events are v2 and authoritative reads verify their exact core-audit binding.
See [Recorder integrity](recorder-integrity.md).

## 4. Optional TypeScript workflow (about 3 minutes)

In a second PowerShell session, set the same `LIANS_URL` and `LIANS_API_KEY`,
then run:

```powershell
Set-Location .\agentmem\sdk\typescript
npm install
npx --yes tsx .\examples\recorder-control-quickstart.ts
```

## Instrument your own boundary

All builders default to `hash_only`. Pass correlation and idempotency values
from the framework boundary instead of generating new ones per retry:

```python
from lians import lians_event

event = lians_event(
    "decision.completed",
    {
        "model_id": "review-model-v3",
        "policy_version": "release-2026-08",
        "input": {"synthetic_id": "SYNTHETIC-42"},
        "output": {"disposition": "review"},
        "evidence": ["synthetic-source:v1"],
    },
    run_id="framework-run-id",
    idempotency_key="framework-run-id:decision.completed",
    agent_id="review-agent",
    capture_mode="hash_only",
    sensitive_fields=["authorization", "api_key", "token"],
)
```

The SDK never logs request bodies or credentials. Hash-only controls what the
Recorder persists; it does not make plaintext safe to send over an untrusted
network, so use TLS and do not place credentials in event payloads. Secret-like
fields are redacted by the service even if full capture is explicitly enabled.
The optional [native Recorder hooks](recorder-native-hooks.md) go further: they
hash observable framework inputs and outputs locally before queueing or HTTP
transport, with explicit bounded-buffer and capture-gap behavior.

Gate evaluation normally omits `principal_scopes` and
`principal_barrier_group`; Lians derives them from authenticated API-key or OIDC
identity. A full signed Decision Receipt may be passed as `receipt.document` for
cryptographic verification. The Gate persists its hash reference, not the
document. Policies that require approvals accept only immutable IDs produced by
`create_gate_approval()` / `createGateApproval()`; free-form approval claims are
rejected. An evaluation also names an exact canonical mediator principal, a bounded
TTL, and SHA-256 of the canonical provider/tool request. In production the provider
accepts only that separate mediator; the mediator recomputes the actual request hash
and calls `consume_gate_execution_permit()` exactly once before dispatch. See
[Gate execution permits](gate-execution-permits.md). Decision Receipt exports remain hash-only by default; request
`include_source_content=True` (Python) or `includeSourceContent: true`
(TypeScript) only for an explicitly authorized export.

When finished, return to the `agentmem` directory and run
`docker compose stop`. Use `docker compose down` only when you intend to remove
the containers; the named Postgres and Redis volumes remain unless explicitly
deleted.
