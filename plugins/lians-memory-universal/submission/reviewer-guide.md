# Lians Memory reviewer guide

The canonical endpoint is `https://mcp.lians.ai/mcp`. It is planned and is not live yet. The draft selects the **United States** and **United Kingdom** as its initial launch countries; that availability choice does not mean the plugin is submitted, approved, published, or live. Do not submit this guide until the endpoint, OAuth login, fixture reset, and every production gate below have been validated.

## Reviewer access

Provision a dedicated demo account that:

- can sign in from the public internet without signup, private-network access, email or SMS confirmation, or MFA;
- has `memory:read` and `memory:write` access;
- owns only the documented `lians-reviewer-fixture-v1` records; and
- can be restored to the published fixture before every case.

Supply the account identifier and temporary credential only through the OpenAI submission portal's secure reviewer-credential fields. Never put credentials, bearer tokens, recovery codes, MFA secrets, or usable secret examples in this repository, this guide, tickets, screenshots, recordings, or ordinary email. Rotate or revoke reviewer access after review according to the operator's access policy.

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

Before giving access to a reviewer, also confirm:

- the draft metadata and portal country selector contain exactly the United States and United Kingdom;
- DNS, TLS, OAuth discovery, resource metadata, and the canonical endpoint are public and stable;
- the demo account has no MFA or confirmation gate and no access outside its fixture tenant;
- active retention is configured and scheduled pruning is running;
- the published indefinite, content-free audit policy and five-day encrypted Fly snapshot window still match the production configuration and [`data-handling.md`](./data-handling.md); and
- logs, telemetry, and audit output contain no raw memory snippets, recall queries, credentials, or bearer tokens.

## Fixture and reproducible cases

Use [`test-cases.json`](./test-cases.json) as the source of truth. It contains exactly five positive and three negative cases. Restore `lians-reviewer-fixture-v1` before each case so cases are independent. The three documented UUIDs must identify active records owned by the demo account and created through the hosted MCP surface.

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
