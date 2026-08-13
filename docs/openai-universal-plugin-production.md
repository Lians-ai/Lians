# Lians Memory universal plugin production checklist

> **Production OAuth/MCP E2E, publisher verification, and reviewer fixture provisioning passed; remaining OpenAI portal gates pending:** production workflow [31349405257](https://github.com/Lians-ai/Lians/actions/runs/31349405257) most recently deployed build `e72fad2c7f98ecf54b6553a90bf8d862046c1abc` at schema `0030_force_hosted_mcp_rls`. Three distinct production Machines passed the cold-boot qualification, and each cited workflow recorded a single immediate post-MCP result with health, liveness, and readiness `ok`. The cited workflows do not attest an extended post-MCP observation window or later degradation state. During the minute `2026-08-10T03:41Z`, a sanitized production OAuth E2E passed protected-resource metadata, OIDC discovery, DCR, browser login, authorization callback, token exchange, repository JWT verification, the authenticated endpoint checker, MCP remember/recall/confirmed-forget calls, and session cleanup. Auth0 recorded the reviewer login at `2026-08-10T03:40:58Z`; the `Lians, Ai` business identity is verified in the OpenAI portal; the submitter's Apps Management owner role is validated; and the published two-record reviewer fixture is live and verified. Secure portal credential entry, developer-mode rehearsal, the skill/domain/tool scans, the demo, portal selection of the operator-approved United States and United Kingdom scope, submission, review, and publication remain pending.

The universal package is under `plugins/lians-memory-universal/`. It contains no local hooks, setup scripts, vendored Python runtime, or custom UI. The implemented hosted MCP contract is in `agentmem/src/lians/openai_mcp.py`; the deployed service must match it exactly.

## Release boundary

- Public name: **Lians Memory**
- Safe tagline: **More memory, less repetition**
- Submission type: **With MCP** plus one uploaded skill bundle
- Canonical MCP endpoint: `https://mcp.lians.ai/mcp`
- OAuth resource identifier after URL normalization: `https://mcp.lians.ai/`
- Implemented tools: `remember`, `recall`, and `forget_memory`
- Initial launch countries: **United States** and **United Kingdom**
- Verified production build: `e72fad2c7f98ecf54b6553a90bf8d862046c1abc`
- Verified production schema: `0030_force_hosted_mcp_rls`
- Verified public boundary: HTTPS, protected-resource metadata, and unauthenticated OAuth challenge
- Verified authenticated boundary: OIDC discovery, DCR, browser login and callback, token exchange, repository JWT verification, authenticated endpoint contract checking, MCP remember/recall/confirmed-forget calls, and session cleanup passed during minute `2026-08-10T03:41Z`
- Reviewer evidence: Auth0 displayed the latest login at `2026-08-10T03:40:58Z`, and the public synthetic two-record fixture is live and verified; developer-mode rehearsal and secure portal credential delivery remain pending
- Publisher evidence: the `Lians, Ai` business identity is verified in the OpenAI portal, and the submitter's Apps Management owner role is validated
- Temporary DCR cleanup: no cleanup endpoint was advertised, so the exact temporary client was manually deleted and the remaining registered-client inventory was verified as zero
- Cold-boot qualification: three successful production rehearsals on distinct Machine IDs; machine-start to first `1/1 passing` readiness was `197.528`, `197.947`, and `197.963` seconds, so the observed maximum `197.963` seconds was below the 360-second hosted startup timeout
- Post-MCP health: each workflow recorded one single immediate result with health, liveness, and readiness `ok`; it does not attest an extended observation window or later degradation state
- Fly grace disclosure: the deploy logs warned that the configured 420-second (`7m0s`) HTTP-check grace period was lowered to an effective one minute; the configured 420 seconds was **not honored** and is not part of the qualification claim
- Pending portal boundary: secure reviewer credential entry, developer-mode rehearsal, the skill/domain/tool scans, launch-country selection, demo, submission, review, and publication
- Claim boundary: Lians can reduce repeated context setup when relevant memory exists. It does not increase OpenAI or Codex quotas, bypass rate limits, or guarantee faster total responses.

`mcp.urlStatus: validated_live` records that the canonical public endpoint is live. `mcp.liveVerification.authenticatedMcpStatus: validated_production_oauth_e2e` separately records the sanitized operator-run production OAuth/MCP check; it is not an OpenAI portal Scan Tools result or approval. Do not submit while `submission/metadata.json` contains a pending submission gate. The availability value `operator_selected_pending_submission` records the approved launch-country scope; it does not mean the countries are selected in the portal or that the plugin is submitted, approved, published, or listed.

## 1. Bring the canonical endpoint online

- [x] Configure production DNS and a trusted TLS certificate for `mcp.lians.ai`.
- [x] Deploy the hosted MCP application so Streamable HTTP is reachable at exactly `https://mcp.lians.ai/mcp`.
- [x] Configure `HOSTED_MCP_RESOURCE_URL=https://mcp.lians.ai`; the protected-resource document publishes the exact normalized OAuth resource identifier `https://mcp.lians.ai/`.
- [ ] Reverify the now-enabled surface's issuer, JWKS, origin, host, retention, and database settings before submission.
- [ ] Build the self-hosted embedding model from the immutable revision in `SENTENCE_TRANSFORMER_REVISION`, verify the same revision at runtime, and keep the 2 GB Fly image at one Uvicorn worker so model memory is not duplicated.
- [x] Do not submit a local endpoint, developer tunnel, template URL, fallback host, or alternate origin.
- [ ] Confirm the universal endpoint works for every supported user and organization.
- [x] Record `mcp.urlStatus` as `validated_live` without changing the canonical URL; record the later authenticated OAuth/MCP E2E separately from portal-only gates.
- [x] Confirm no obsolete host remains in the package and the canonical host is consistent across the skill, metadata, and release notes.

## 2. Freeze and verify the implemented MCP contract

Tool discovery must match this table and `submission/metadata.json`:

| Tool | OAuth scope | Inputs | Structured output | Annotations |
| --- | --- | --- | --- | --- |
| `remember` | `memory:write` | Required `content` string, 3–4000 chars; optional `project` string, 1–128 chars, default `general`; optional `idempotency_key` string up to 128 chars or null | `status`, `memory_ref`, `retention_days` | read-only false; destructive false; idempotent false; open-world false |
| `recall` | `memory:read` | Required `query` string, 2–2000 chars; optional `project` string, 1–128 chars, default `general`; `max_results` 1–20, default 10; `max_tokens` 64–768, default 512 | `status`, `context`, `memory_refs`, `result_count`, `token_estimate`, `truncated` | read-only false; destructive false; idempotent false; open-world false |
| `forget_memory` | `memory:write` | Required UUID `memory_ref`; optional `confirm` boolean, default false | `status`, `memory_ref`, `memories_erased` | read-only false; destructive true; idempotent true; open-world false |

- [ ] Use a current official MCP SDK and a stable server name and semantic version.
- [x] Complete authenticated MCP initialization and protocol-version negotiation with the production endpoint checker; do not hardcode acceptance to one client version.
- [x] Verify production discovery advertises exactly the three reviewed names, titles, descriptions, input schemas, output schemas, security schemes, and annotations.
- [ ] Return concise model-readable `content` plus `structuredContent` matching each published output schema.
- [ ] Keep the public `remember` input limited to `content`, `project`, and `idempotency_key`. The service assigns ingestion time and internal provenance; clients must not send caller-supplied timestamps or arbitrary metadata.
- [ ] Keep `remember` additive and `idempotentHint: false`. Verify an exact retry with the same non-secret idempotency key does not create an unintended duplicate.
- [ ] Keep `recall` bounded and mark `readOnlyHint: false` and `idempotentHint: false` because every call writes an audit receipt, even though memory content is unchanged.
- [ ] Ensure `recall.context` starts with the untrusted-data warning and an empty result says no relevant memory was found.
- [ ] Keep `forget_memory` limited to one active, tenant-owned hosted-MCP memory reference. Require `confirm: true`; `confirm: false` must return an error and perform no deletion.
- [ ] Verify confirmed removal immediately crypto-shreds the selected memory from active service storage, returns `status: forgotten`, reports the actual `memories_erased` count, and discloses that encrypted provider backups may retain a recoverable copy for up to 5 days.
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

- [x] Publish protected-resource metadata at `https://mcp.lians.ai/.well-known/oauth-protected-resource` with resource `https://mcp.lians.ai/`, the authorization-server issuer, and both supported scopes.
- [x] Return an unauthenticated `WWW-Authenticate` challenge bound to that exact protected-resource metadata URL.
- [x] Publish OIDC discovery metadata with the authorization, token, JWKS, and registration endpoints; the production E2E resolved it successfully.
- [ ] Confirm authorization code with `S256` PKCE independently of the completed browser login, callback, and token-exchange evidence.
- [x] Support DCR for the tested production flow. The authorization server did not advertise DCR cleanup, so the exact temporary client was manually deleted and the registered-client inventory was verified as zero.
- [ ] Publish supported token-endpoint authentication methods.
- [ ] Echo the exact normalized resource through authorization and token requests and bind it to the token audience.
- [x] Pass repository JWT verification for the production token without recording the token or its private claims.
- [ ] Confirm `remember` and `forget_memory` require `memory:write`; confirm `recall` requires only `memory:read`.
- [ ] Return `_meta["mcp/www_authenticate"]` with a useful challenge when linking, reauthorization, or additional scope is required.
- [ ] Add the portal-provided `https://chatgpt.com/connector/oauth/{callback_id}` redirect URI to the authorization-server allowlist.
- [x] Prepare the dedicated reviewer account and provision the two published fixed records. Browser login succeeded, Auth0 displayed the reviewer account's latest login at `2026-08-10T03:40:58Z`, and the `architecture-current` and `region-current` references were verified live.
- [ ] Rehearse all five positive and three negative cases in OpenAI developer mode. Case 4 must create a disposable record without an idempotency key, forget its returned UUID, and retry the exact forget call for `not_found`/`0`; it must not delete or require resetting a fixed fixture record.
- [ ] Enter the temporary reviewer credential only in the portal's secure field.

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
- [x] Treat `audit_retention_days` as a minimum only. The operator approved indefinite retention of pseudonymous, content-free append-only audit records on 2026-08-09, and the public privacy policy discloses it.
- [x] Publish a privacy policy covering collected data, purposes, recipients, retention, deletion, access, export, correction, and user controls.
- [x] Record provider-backed backup evidence: the encrypted Fly PostgreSQL volumes and current snapshots report five-day retention; Fly documents snapshot restoration; the public policy discloses that deleted content can remain recoverable until snapshot expiry. A restored pre-deletion snapshot is not claimed to contain a later tombstone.
- [ ] Require fresh, explicit user confirmation before each destructive `forget_memory` call, including disclosure of immediate active-service crypto-shredding and the encrypted provider backup window of up to 5 days. Never infer confirmation from an earlier unrelated message.
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

- [x] Parse every JSON and YAML file without warnings.
- [x] Confirm the manifest name matches the plugin folder.
- [x] Confirm `displayName` is at most 30 characters and `shortDescription` is one line and at most 30 characters.
- [x] Confirm there are no more than three starter prompts and each is at most 128 characters.
- [x] Confirm `submission/test-cases.json` contains exactly five positive and three negative cases, including self-cleaning confirmed active-storage removal with an exact `not_found`/`0` retry.
- [ ] Confirm `submission/data-handling.md` and `submission/reviewer-guide.md` exist, match the deployed behavior, and contain no credentials, tokens, or MFA secrets.
- [x] Confirm the icon is square and present inside the package.
- [x] Confirm no secret, private path, local hook, setup script, vendored runtime, obsolete endpoint, or unsupported MCP configuration is present.

Test the deployed service with the repository [endpoint checker](../scripts/check_openai_plugin_endpoint.py):

```powershell
python scripts/check_openai_plugin_endpoint.py --resource-url https://mcp.lians.ai/mcp
```

The latest workflow no-token run, [31349405257](https://github.com/Lians-ai/Lians/actions/runs/31349405257), passed on 2026-08-10 for build `e72fad2c7f98ecf54b6553a90bf8d862046c1abc`: `https`, `protected_resource_metadata`, and `unauthenticated_challenge` were `ok`; `authenticated_mcp` was intentionally skipped in that workflow. A later sanitized operator-run production OAuth E2E passed during minute `2026-08-10T03:41Z`, including OIDC discovery, DCR, browser authorization, token exchange, repository JWT verification, the authenticated endpoint checker, and MCP remember/recall/confirmed-forget calls. The sanitized event trail's final MCP completion timestamp is `2026-08-10T03:41:10.126400Z`; because the provider labels each tool event generically, the remember/recall/forget timestamp mapping is derived from the harness's fixed call order. No credential, token, client identifier, memory payload or reference, raw response, or local path is retained in this evidence.

For the authenticated contract check, securely inject `LIANS_MCP_BEARER_TOKEN` into the process environment, run the same command, and clear it afterward. Never put the token on the command line or in review artifacts. The MCP Inspector may then be used for interactive testing:

```powershell
npx @modelcontextprotocol/inspector@latest
```

- [x] The public no-token endpoint check passes at `https://mcp.lians.ai/mcp` without exposing a credential.
- [x] Authenticated initialization and tool discovery succeed at `https://mcp.lians.ai/mcp`.
- [x] Production discovery exactly matches the three contracts in `submission/metadata.json`.
- [x] OIDC discovery, DCR, browser login, authorization callback, token exchange, and repository JWT verification pass.
- [ ] Reauthorization, scope-denial, and invalid-token cases pass on every supported OpenAI client surface.
- [x] One synthetic production canary completes MCP remember, recall, and confirmed forget, followed by session cleanup.
- [ ] `recall` writes an audit receipt but does not alter memory content.
- [ ] `forget_memory` with `confirm: false` performs no removal; confirmed active-storage removal succeeds once and an exact retry returns `not_found` with `memories_erased: 0`.
- [ ] Every valid and invalid call returns a bounded result or useful error without leaking tenant data.
- [ ] All five positive and three negative cases pass in OpenAI developer mode using only the two fixed fixture UUIDs; case 4 creates and removes its own disposable record.
- [ ] Direct, indirect, follow-up, unsupported, and boundary prompts behave consistently on supported ChatGPT and Codex surfaces.

## 7. Prepare portal materials

- [ ] Use an OpenAI Platform project with global data residency. MCP submissions from EU-residency projects are currently not supported.
- [x] Validate the submitter's Apps Management owner role for Apps Management Write / `api.apps.write` and Apps Management Read / `api.apps.read`.
- [x] Confirm that the **Lians, Ai** business identity is verified in the OpenAI portal.
- [x] Verify the public website, support, privacy, and terms URLs resolve and match that identity.
- [ ] Record the canonical endpoint, OAuth configuration, reviewer credentials, successful domain verification, and current Scan Tools result.
- [ ] Use [`reviewer-guide.md`](../plugins/lians-memory-universal/submission/reviewer-guide.md) to provision and verify the fixture account; transmit credentials only through the portal's secure reviewer fields.
- [ ] Attach or reproduce the approved [`data-handling.md`](../plugins/lians-memory-universal/submission/data-handling.md) disclosure, including the verified external backup-deletion window.
- [ ] Upload the final skill tree from `plugins/lians-memory-universal/skills/lians-memory/`.
- [ ] Use the three starter prompts from the manifest.
- [ ] Upload exactly the five positive and three negative cases from `submission/test-cases.json`.
- [ ] Record a demo covering remember, audited recall, confirmation, immediate active-storage crypto-shredding, the up-to-5-day encrypted provider backup disclosure, and the safe retry; add its HTTPS URL to `submission/metadata.json`.
- [x] Approve **United States** and **United Kingdom** as the initial launch-country scope.
- [ ] In the portal, select exactly **United States** and **United Kingdom**; add countries only after separate legal, privacy, product-availability, and support review.
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
| Public endpoint | Canonical HTTPS route, protected-resource metadata, and unauthenticated challenge pass | Pass (2026-08-10, build `e72fad2`) |
| Cold boot | Three distinct production Machines reach first `1/1 passing` readiness below the 360-second startup timeout; each workflow also records one immediate post-MCP health result | Pass (`197.528`, `197.947`, `197.963` seconds; max `197.963`) |
| Authenticated MCP | Authenticated initialization, discovery, and tool calls pass | Pass (operator OAuth E2E, minute `2026-08-10T03:41Z`) |
| Contract | All three schemas, structured outputs, security schemes, and annotations match code | Pass (authenticated endpoint checker) |
| OAuth | Discovery, PKCE, token validation, per-tool scopes, and reviewer login pass | Partial (production discovery, DCR, browser login/callback, token exchange, repository token verification, and reviewer login passed; PKCE-specific and negative scope/token cases remain) |
| Privacy | Restricted-data rejection, isolation, retention, and audit controls pass | Pending |
| Deletion | Explicit confirmation, tenant checks, crypto-shredding, and idempotent retry pass | Partial (confirmed-forget canary passed; tenant and retry cases remain) |
| Backups | Provider-backed deletion window and restore/tombstone behavior are documented and disclosed | Pass (2026-08-09) |
| Domain | OpenAI challenge verification passes | Pending |
| Evaluation | All five positive and three negative cases pass on supported surfaces | Pending (developer-mode rehearsal not yet run) |
| Publisher | Required permissions and verified `Lians, Ai` business identity are present | Pass (OpenAI portal verification and Apps Management owner role observed) |
| Reviewer fixture | Public no-MFA account, two fixed fixture records, and secure portal credentials pass | Partial (browser login and both fixed records passed; secure portal delivery remains) |
| Scan Tools | OpenAI Scan Tools completes with no unresolved error or warning | Pending |
| Portal availability | Exactly United States and United Kingdom are selected in the portal | Pending |
| Review assets | Legal/support URLs, demo, regions, scan result, and release notes are complete | Pending |

The operator may submit only when every gate is **Pass**, `mcp.urlStatus` is `validated_live`, and the canonical endpoint has passed live OpenAI client testing.

## Official OpenAI references

- [MCP server review requirements](https://developers.openai.com/plugins/deploy/app-review)
- [Plugin documentation](https://developers.openai.com/plugins)
