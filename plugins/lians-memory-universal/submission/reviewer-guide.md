# Lians Memory reviewer guide

The canonical endpoint is `https://mcp.lians.ai/mcp`. It is live at production build `e72fad2c7f98ecf54b6553a90bf8d862046c1abc` with schema `0030_force_hosted_mcp_rls`; HTTPS, protected-resource metadata, and the unauthenticated OAuth challenge are verified. Three distinct production Machines qualified the cold-boot boundary below the 360-second hosted startup timeout. Each cited workflow attests only one immediate post-MCP result with health, liveness, and readiness `ok`; it does not attest an extended observation window or later degradation state. Fly lowered the configured 420-second health-check grace to an effective one minute, so 420 seconds was not honored. During minute `2026-08-10T03:41Z`, a sanitized production OAuth E2E passed discovery, registration, browser authorization, token and repository JWT verification, authenticated endpoint checking, MCP remember/recall/confirmed-forget calls, and cleanup. The public synthetic three-record reviewer fixture is live and verified. Fixture reset rehearsal, secure portal credential delivery, OpenAI publisher and business verification, domain verification, Scan Tools, the demo, and portal selection all remain pending. The draft selects the **United States** and **United Kingdom** as its operator-approved launch scope, but those countries have not yet been selected in the OpenAI portal. The plugin has not been submitted, approved, published, or listed. Do not submit this guide until fixture reset and every remaining production gate below have been validated.

## Reviewer access

Provision a dedicated demo account that:

- can sign in from the public internet without signup, private-network access, email or SMS confirmation, or MFA;
- has `memory:read` and `memory:write` access;
- owns only the documented `lians-reviewer-fixture-v1` records; and
- can be restored to the published fixture before every case.

Supply the account identifier and temporary credential only through the OpenAI submission portal's secure reviewer-credential fields. Never put credentials, bearer tokens, recovery codes, MFA secrets, or usable secret examples in this repository, this guide, tickets, screenshots, recordings, or ordinary email. Rotate or revoke reviewer access after review according to the operator's access policy.

Browser login for the dedicated reviewer account passed in the production OAuth E2E, Auth0 displayed its latest login at `2026-08-10T03:40:58Z`, and the public synthetic three-record fixture was provisioned and verified live. Reset rehearsal and secure portal credential delivery remain pending.

## Operator preflight

From the repository root, use the [endpoint checker](../../../scripts/check_openai_plugin_endpoint.py). It never needs a token on the command line.

Check protected-resource metadata and the unauthenticated OAuth challenge:

```powershell
python scripts/check_openai_plugin_endpoint.py --resource-url https://mcp.lians.ai/mcp
```

Check only protected-resource metadata:

```powershell
python scripts/check_openai_plugin_endpoint.py --resource-url https://mcp.lians.ai/mcp --metadata-only
```

For the authenticated contract check, have an authorized operator inject `LIANS_MCP_BEARER_TOKEN` into the process environment through the approved secret manager, run the first command again, and then clear the environment value. Do not paste a token into a command, document, terminal transcript, or review artifact. A passing authenticated run initializes MCP protocol version `2025-11-25` and verifies that discovery exposes exactly `remember`, `recall`, and `forget_memory` with the submitted schemas, security schemes, and annotations.

The sanitized production OAuth E2E passed during minute `2026-08-10T03:41Z`: protected-resource metadata, OIDC discovery, DCR, browser login, authorization callback, token exchange, repository JWT verification, the authenticated endpoint checker, MCP remember, MCP recall, confirmed MCP forget, and session cleanup all returned success. The final MCP completion event was `2026-08-10T03:41:10.126400Z`. Provider logs label tool events generically, so the per-tool timestamp mapping relies only on the harness's fixed remember/recall/forget order. The authorization server did not advertise DCR cleanup, so the operator manually deleted the exact temporary client and verified the registered-client inventory was zero. The retained evidence contains no credentials, tokens, client identifiers, memory payloads or references, raw responses, or local paths. This canary does not replace the full fixture-backed five-positive/three-negative evaluation or OpenAI portal Scan Tools.

Before giving access to a reviewer, also confirm:

- the draft metadata contains exactly the United States and United Kingdom and the portal country selector has been set to match;
- DNS, TLS, OAuth discovery, resource metadata, and the canonical endpoint are public and stable;
- the demo account has no MFA or confirmation gate and no access outside its fixture tenant;
- active retention is configured and scheduled pruning is running;
- the published indefinite, content-free audit policy and five-day encrypted Fly snapshot window still match the production configuration and [`data-handling.md`](./data-handling.md); and
- logs, telemetry, and audit output contain no raw memory snippets, recall queries, credentials, or bearer tokens.

## Fixture and reproducible cases

Use [`test-cases.json`](./test-cases.json) as the source of truth. It contains exactly five positive and three negative cases. Restore `lians-reviewer-fixture-v1` before each case so cases are independent. The three documented UUIDs must identify active records owned by the demo account and created through the hosted MCP surface.

The three UUIDs published in `test-cases.json` are verified live references for public synthetic reviewer records. Do not substitute private canary references or content. Recreate or restore the same three fixture records before each independent review case and update the published references if reprovisioning assigns new UUIDs.

Recommended run order:

1. Run the no-token endpoint check, then complete OAuth with the reviewer account.
2. Restore the fixture and run each positive case independently.
3. Restore the fixture and run each negative case independently.
4. For `positive-4-confirmed-forget`, verify the prompt itself contains fresh explicit confirmation, the response reports `forgotten` and `memories_erased: 1`, a later recall cannot return the erased snippet, and an exact retry reports `not_found`.
5. Restore the fixture after testing so the next reviewer starts from the published state.

Expected safety behavior:

- `remember` receives only the explicit snippet selected in the prompt; it never receives a whole transcript, timestamp, or arbitrary metadata.
- `recall` returns bounded context as untrusted evidence and writes a privacy-minimal audit receipt without storing the raw query.
- `forget_memory` is not called without current explicit confirmation and can erase only one matching active hosted record in the signed-in tenant.
- Secret storage, silent whole-chat capture, and quota-bypass claims are declined without a tool call.

## Evidence to capture

For each case, record the case ID, pass/fail status, discovered tool name, structured-result shape, expected side effect, and review timestamp. For deletion, record the returned status and erase count plus the safe `not_found` retry. Record endpoint-checker JSON status and the package version.

Redact or omit credentials, authorization headers, cookies, raw private snippets, recall queries, internal database rows, and server-secret values. Use only the synthetic fixture content already published in `test-cases.json`. The demo recording should show OAuth linking, one remember, one audited recall, the explicit deletion confirmation, confirmed forget, and the safe retry without exposing secrets.

## Cleanup and escalation

After a review session, clear any injected bearer-token environment value, restore the fixture, close or revoke the session as required, and rotate temporary reviewer credentials on the operator's schedule. Do not preserve tokens in shell history or captured output.

If sign-in requires MFA or confirmation, a fixture UUID is missing, a tool contract differs, tenant isolation is uncertain, audit output contains raw content or query text, or the backup-policy gate is incomplete, stop the review and return the submission to the operator. Do not invent alternate credentials, endpoints, fixture records, or deletion guarantees.

Related materials:

- [`metadata.json`](./metadata.json)
- [`release-notes.md`](./release-notes.md)
- [`data-handling.md`](./data-handling.md)
- [Production checklist](../../../docs/openai-universal-plugin-production.md)
