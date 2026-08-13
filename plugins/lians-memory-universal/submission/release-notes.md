# Lians Memory 0.1.0

Initial universal-directory submission draft for ChatGPT and Codex.

- Adds one focused Lians Memory skill for explicit durable storage, bounded recall, and confirmed deletion.
- Defines the intended hosted MCP dependency as Streamable HTTP.
- Exposes three implemented tools: `remember`, audit-writing `recall`, and destructive `forget_memory`.
- Requires explicit confirmation before `forget_memory` immediately crypto-shreds one selected memory from active service storage; encrypted provider backups may retain a recoverable copy for up to 5 days.
- Specifies OAuth 2.1 as the required authentication design for user-specific memory.
- Includes five positive and three negative reviewer cases.
- Selects the **United States** and **United Kingdom** as the initial launch countries.
- Includes no custom UI, local hooks, local setup scripts, or bundled runtime.
- Uses the claim boundary "More memory, less repetition"; it does not claim to increase OpenAI quotas or bypass rate limits.

## Deployment status

This is a submission draft. The plugin has not been submitted, approved, published, or listed. The **United States** and **United Kingdom** are the operator-selected launch scope, but both countries still must be selected in the OpenAI portal.

The canonical `https://mcp.lians.ai/mcp` endpoint is live at production build `e72fad2c7f98ecf54b6553a90bf8d862046c1abc` with schema `0030_force_hosted_mcp_rls`. The guarded production workflow and a fresh no-token endpoint check verified HTTPS, protected-resource metadata, and the unauthenticated OAuth challenge. Three consecutive production rehearsals on distinct Machine IDs reached first `1/1 passing` readiness in `197.528`, `197.947`, and `197.963` seconds, all below the 360-second hosted startup timeout. Each cited workflow then recorded one immediate post-MCP result with health, liveness, and readiness `ok`; it does not attest an extended observation window or later degradation state. Fly warned that the configured 420-second (`7m0s`) health-check grace was lowered to an effective one minute, so 420 seconds was not honored. During minute `2026-08-10T03:41Z`, a separate sanitized production OAuth E2E passed OIDC discovery, DCR, browser login and callback, token exchange, repository JWT verification, the authenticated endpoint checker, MCP remember/recall/confirmed-forget calls, and session cleanup. Auth0 displayed the reviewer login at `2026-08-10T03:40:58Z`. No DCR cleanup endpoint was advertised, so the exact temporary client was manually deleted and the registered-client inventory was verified as zero.

The `Lians, Ai` business identity is verified in the OpenAI portal, the submitter's Apps Management owner role is validated, and the public synthetic two-record reviewer fixture is live and verified. Secure portal credential entry, developer-mode rehearsal, the skill/domain/tool scans, the demo, portal selection of the operator-approved United States and United Kingdom scope, the full five-positive/three-negative evaluation, submission, review, and publication all remain pending. Do not substitute a temporary, testing, or alternate endpoint, and do not submit until every production-checklist gate passes.
