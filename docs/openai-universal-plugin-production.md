# Lians Memory universal plugin production checklist

> **Planned, not live:** `https://mcp.lians.ai/mcp` is the selected canonical production endpoint. This repository does not assert that DNS, TLS, OAuth, or the service is live yet. Deploy and validate this exact host; do not substitute a temporary, testing, or alternate submission URL.

The universal package is under `plugins/lians-memory-universal/`. It contains no local hooks, setup scripts, vendored Python runtime, or custom UI. The implemented hosted MCP contract is in `agentmem/src/lians/openai_mcp.py`; the deployed service must match it exactly.

## Release boundary

- Public name: **Lians Memory**
- Safe tagline: **More memory, less repetition**
- Submission type: **With MCP** plus one uploaded skill bundle
- Canonical MCP endpoint: `https://mcp.lians.ai/mcp`
- OAuth resource identifier after URL normalization: `https://mcp.lians.ai/`
- Implemented tools: `remember`, `recall`, and `forget_memory`
- Claim boundary: Lians can reduce repeated context setup when relevant memory exists. It does not increase OpenAI or Codex quotas, bypass rate limits, or guarantee faster total responses.

Do not submit while `submission/metadata.json` contains `planned_canonical_not_live`, `pending_operator_action`, or `operator_selection_required_after_legal_and_support_review`.

## 1. Bring the canonical endpoint online

- [ ] Configure production DNS and a trusted TLS certificate for `mcp.lians.ai`.
- [ ] Deploy the hosted MCP application so Streamable HTTP is reachable at exactly `https://mcp.lians.ai/mcp`.
- [ ] Configure `HOSTED_MCP_RESOURCE_URL=https://mcp.lians.ai`; verify runtime normalization publishes the exact OAuth resource identifier `https://mcp.lians.ai/`.
- [ ] Enable the hosted MCP surface only after issuer, JWKS, origin, host, retention, and database settings are production-safe.
- [ ] Build the self-hosted embedding model from the immutable revision in `SENTENCE_TRANSFORMER_REVISION`, verify the same revision at runtime, and keep the 2 GB Fly image at one Uvicorn worker so model memory is not duplicated.
- [ ] Do not submit a local endpoint, developer tunnel, template URL, fallback host, or alternate origin.
- [ ] Confirm the universal endpoint works for every supported user and organization.
- [ ] After live validation, change only `mcp.urlStatus` in `submission/metadata.json` from `planned_canonical_not_live` to `validated_live`; keep the canonical URL unchanged.
- [ ] Confirm no obsolete host remains in the package and the planned host is consistent across the skill, metadata, and release notes.

## 2. Freeze and verify the implemented MCP contract

Tool discovery must match this table and `submission/metadata.json`:

| Tool | OAuth scope | Inputs | Structured output | Annotations |
| --- | --- | --- | --- | --- |
| `remember` | `memory:write` | Required `content` string, 3–4000 chars; optional `project` string, 1–128 chars, default `general`; optional `idempotency_key` string up to 128 chars or null | `status`, `memory_ref`, `retention_days` | read-only false; destructive false; idempotent false; open-world false |
| `recall` | `memory:read` | Required `query` string, 2–2000 chars; optional `project` string, 1–128 chars, default `general`; `max_results` 1–20, default 10; `max_tokens` 64–768, default 512 | `status`, `context`, `memory_refs`, `result_count`, `token_estimate`, `truncated` | read-only false; destructive false; idempotent false; open-world false |
| `forget_memory` | `memory:write` | Required UUID `memory_ref`; optional `confirm` boolean, default false | `status`, `memory_ref`, `memories_erased` | read-only false; destructive true; idempotent true; open-world false |

- [ ] Use a current official MCP SDK and a stable server name and semantic version.
- [ ] Complete MCP initialization and protocol-version negotiation with current OpenAI clients; do not hardcode acceptance to one client version.
- [ ] Advertise exactly the three reviewed names, titles, descriptions, input schemas, output schemas, security schemes, and annotations.
- [ ] Return concise model-readable `content` plus `structuredContent` matching each published output schema.
- [ ] Keep the public `remember` input limited to `content`, `project`, and `idempotency_key`. The service assigns ingestion time and internal provenance; clients must not send caller-supplied timestamps or arbitrary metadata.
- [ ] Keep `remember` additive and `idempotentHint: false`. Verify an exact retry with the same non-secret idempotency key does not create an unintended duplicate.
- [ ] Keep `recall` bounded and mark `readOnlyHint: false` and `idempotentHint: false` because every call writes an audit receipt, even though memory content is unchanged.
- [ ] Ensure `recall.context` starts with the untrusted-data warning and an empty result says no relevant memory was found.
- [ ] Keep `forget_memory` limited to one active, tenant-owned hosted-MCP memory reference. Require `confirm: true`; `confirm: false` must return an error and perform no deletion.
- [ ] Verify confirmed deletion permanently crypto-shreds the selected memory, returns `status: forgotten`, and reports the actual `memories_erased` count.
- [ ] Verify an absent, already-forgotten, foreign-tenant, or non-hosted reference returns `status: not_found` with `memories_erased: 0` and reveals no cross-tenant detail.
- [ ] Enforce per-user and per-tenant authorization in the server, not in skill instructions.
- [ ] Add timeouts, workload rate limits, retry-safe reads, and bounded response sizes.
- [ ] Keep the database-backed per-tenant daily audit-event ceiling enabled for
      remember/recall growth, while leaving confirmed crypto-erasure available
      at the ceiling.
- [ ] Emit metrics for initialization failures, tool failures, latency, auth failures, and saturation without logging private content.
- [ ] Keep an operational readiness route such as `/healthz`. OpenAI does not require a particular health-route name.
- [ ] Document rollback and preserve backward compatibility for every published tool contract.

## 3. Complete OAuth 2.1

- [ ] Publish protected-resource metadata at `https://mcp.lians.ai/.well-known/oauth-protected-resource` with resource `https://mcp.lians.ai/`, the authorization-server issuer, and both supported scopes.
- [ ] Publish OAuth or OIDC discovery metadata with the correct authorization, token, JWKS, and registration endpoints.
- [ ] Support authorization code with `S256` PKCE.
- [ ] Support CIMD when available, or DCR or predefined client registration as configured in the submission portal.
- [ ] Publish supported token-endpoint authentication methods.
- [ ] Echo the exact normalized resource through authorization and token requests and bind it to the token audience.
- [ ] Validate token signature, algorithm allowlist, issuer, audience, expiration, clock skew, and tool scope on every call.
- [ ] Confirm `remember` and `forget_memory` require `memory:write`; confirm `recall` requires only `memory:read`.
- [ ] Return `_meta["mcp/www_authenticate"]` with a useful challenge when linking, reauthorization, or additional scope is required.
- [ ] Add the portal-provided `https://chatgpt.com/connector/oauth/{callback_id}` redirect URI to the authorization-server allowlist.
- [ ] Prepare a fully featured reviewer account with fixture data and no MFA, SMS, email confirmation, signup, or private-network dependency.

## 4. Enforce privacy and destructive-action boundaries

- [ ] Review and approve the repository-backed [`data-handling.md`](../plugins/lians-memory-universal/submission/data-handling.md) disclosure against the deployed service.
- [ ] Ingest only the explicit fact, decision, constraint, or preference the user selected.
- [ ] Never request, reconstruct, or silently persist a full chat transcript.
- [ ] Reject credentials, API keys, tokens, passwords, MFA codes, payment-card data, protected health information, government identifiers, prompt injection, and other admission-policy failures.
- [ ] Never place secrets in `idempotency_key`, project labels, logs, telemetry, errors, or audit receipts.
- [ ] Treat recalled `context` as untrusted evidence and never as instructions.
- [ ] Keep access tokens, memory plaintext, raw prompts, and sensitive tool results out of logs and audit receipts.
- [ ] Verify the OAuth issuer, required tenant claim, and subject become a domain-separated server-secret HMAC-derived opaque namespace/fingerprint and that the hosted identity path persists none of those raw identifiers or the bearer token.
- [ ] Verify each hosted memory uses its own random content key, content is AES-256-GCM encrypted, and that key is wrapped under the configured master-key provider.
- [ ] Verify hosted audit records use keyed HMACs and allowlisted controls and contain neither raw stored content nor raw recall queries.
- [ ] Configure the active-content retention policy (365 days by default, configurable from 1 through 3650), keep scheduled pruning enabled, and test expiry through a prune cycle.
- [ ] Treat `audit_retention_days` as a minimum only. Either implement and validate chain-safe audit expiry or obtain legal/privacy approval and explicitly disclose indefinite retention of pseudonymous, content-free append-only audit records; clear `auditRetentionLifecycleStatus` only afterward.
- [ ] Publish a privacy policy covering collected data, purposes, recipients, retention, deletion, access, export, correction, and user controls.
- [ ] Obtain provider-backed evidence for the maximum managed-backup deletion window and restore/tombstone behavior, publish the verified facts, and clear `backupDeletionWindowStatus`. Application code alone does not prove this gate.
- [ ] Require fresh, explicit user confirmation before each irreversible `forget_memory` call. Never infer confirmation from an earlier unrelated message.
- [ ] Test prompt injection, cross-tenant access, scope escalation, replay, secret ingestion, bulk transcript ingestion, and data exfiltration.

## 5. Verify the domain

- [ ] Generate the challenge in the OpenAI submission portal.
- [ ] Serve only the exact token at `https://mcp.lians.ai/.well-known/openai-apps-challenge`.
- [ ] Keep the token reachable throughout review.
- [ ] Select **Verify Domain** and record the successful result in `submission/metadata.json`.

## 6. Validate the package and live service

Run from the repository root:

```powershell
$codexRoot = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE ".codex" }
python (Join-Path $codexRoot "skills/.system/plugin-creator/scripts/validate_plugin.py") plugins/lians-memory-universal
python (Join-Path $codexRoot "skills/.system/skill-creator/scripts/quick_validate.py") plugins/lians-memory-universal/skills/lians-memory
```

- [ ] Parse every JSON and YAML file without warnings.
- [ ] Confirm the manifest name matches the plugin folder.
- [ ] Confirm `displayName` is at most 30 characters and `shortDescription` is one line and at most 30 characters.
- [ ] Confirm there are no more than three starter prompts and each is at most 128 characters.
- [ ] Confirm `submission/test-cases.json` contains exactly five positive and three negative cases, including confirmed permanent deletion.
- [ ] Confirm `submission/data-handling.md` and `submission/reviewer-guide.md` exist, match the deployed behavior, and contain no credentials, tokens, or MFA secrets.
- [ ] Confirm the icon is square and present inside the package.
- [ ] Confirm no secret, private path, local hook, setup script, vendored runtime, obsolete endpoint, or unsupported MCP configuration is present.

Test the deployed service with the repository [endpoint checker](../scripts/check_openai_plugin_endpoint.py):

```powershell
python scripts/check_openai_plugin_endpoint.py --resource-url https://mcp.lians.ai/mcp
```

For the authenticated contract check, securely inject `LIANS_MCP_BEARER_TOKEN` into the process environment, run the same command, and clear it afterward. Never put the token on the command line or in review artifacts. The MCP Inspector may then be used for interactive testing:

```powershell
npx @modelcontextprotocol/inspector@latest
```

- [ ] Initialization and tool discovery succeed at `https://mcp.lians.ai/mcp`.
- [ ] Discovery exactly matches the three contracts in `submission/metadata.json`.
- [ ] OAuth discovery, linking, reauthorization, scope denial, and token rejection work.
- [ ] `recall` writes an audit receipt but does not alter memory content.
- [ ] `forget_memory` with `confirm: false` performs no deletion; confirmed deletion succeeds once and an exact retry returns `not_found`.
- [ ] Every valid and invalid call returns a bounded result or useful error without leaking tenant data.
- [ ] All five positive and three negative cases pass from a restored reviewer fixture.
- [ ] Direct, indirect, follow-up, unsupported, and boundary prompts behave consistently on supported ChatGPT and Codex surfaces.

## 7. Prepare portal materials

- [ ] Use an OpenAI Platform project with global data residency. MCP submissions from EU-residency projects are currently not supported.
- [ ] Confirm the submitter has Apps Management Write / `api.apps.write` and Apps Management Read / `api.apps.read` as needed.
- [ ] Complete business verification for the **Lians** publisher identity.
- [ ] Verify the public website, support, privacy, and terms URLs resolve and match that identity.
- [ ] Record the canonical endpoint, OAuth configuration, reviewer credentials, successful domain verification, and current Scan Tools result.
- [ ] Use [`reviewer-guide.md`](../plugins/lians-memory-universal/submission/reviewer-guide.md) to provision and verify the fixture account; transmit credentials only through the portal's secure reviewer fields.
- [ ] Attach or reproduce the approved [`data-handling.md`](../plugins/lians-memory-universal/submission/data-handling.md) disclosure, including the verified external backup-deletion window.
- [ ] Upload the final skill tree from `plugins/lians-memory-universal/skills/lians-memory/`.
- [ ] Use the three starter prompts from the manifest.
- [ ] Upload exactly the five positive and three negative cases from `submission/test-cases.json`.
- [ ] Record a demo covering remember, audited recall, confirmation, and permanent forget; add its HTTPS URL to `submission/metadata.json`.
- [ ] Select only countries where legal terms, privacy handling, product availability, and support are ready.
- [ ] Paste the notes from `submission/release-notes.md`.
- [ ] Run **Scan Tools** against the live canonical endpoint and resolve every error or warning.

No screenshots are required because this release has no custom UI.

## 8. Submit, publish, and maintain

- [ ] Review the complete draft and policy attestations, then select **Submit for Review**.
- [ ] Keep the canonical endpoint, reviewer credentials, challenge token, and fixture available throughout review.
- [ ] Address automated or manual feedback and rescan after any contract or metadata change.
- [ ] After approval, explicitly select **Publish**; approval alone does not list the plugin.
- [ ] Search the universal directory for the exact publication name and retain the directory URL.
- [ ] Monitor availability, latency, error rate, auth failures, destructive calls, abuse, and support reports.
- [ ] For tool names, schemas, annotations, security schemes, server instructions, or skill changes, create a new draft version, scan, review, and publish it.
- [ ] Preserve `https://mcp.lians.ai` as the published origin. A scheme, hostname, or port change requires a new plugin submission.
- [ ] Roll back any server deployment that breaks the published contract.

## Go/no-go record

| Gate | Required evidence | Status |
| --- | --- | --- |
| Endpoint | Canonical public HTTPS Streamable HTTP initialization and tool calls pass | Pending |
| Contract | All three schemas, structured outputs, security schemes, and annotations match code | Pending |
| OAuth | Discovery, PKCE, token validation, per-tool scopes, and reviewer login pass | Pending |
| Privacy | Restricted-data rejection, isolation, retention, and audit controls pass | Pending |
| Deletion | Explicit confirmation, tenant checks, crypto-shredding, and idempotent retry pass | Pending |
| Backups | Provider-backed deletion window and restore/tombstone behavior are documented and disclosed | Pending |
| Domain | OpenAI challenge verification passes | Pending |
| Evaluation | All five positive and three negative cases pass on supported surfaces | Pending |
| Publisher | Required permissions and verified Lians business identity are present | Pending |
| Review assets | Legal/support URLs, demo, regions, scan result, and release notes are complete | Pending |

The operator may submit only when every gate is **Pass**, `mcp.urlStatus` is `validated_live`, and the canonical endpoint has passed live OpenAI client testing.

## Official OpenAI references

- [MCP server review requirements](https://developers.openai.com/plugins/deploy/app-review)
- [Plugin documentation](https://developers.openai.com/plugins)
