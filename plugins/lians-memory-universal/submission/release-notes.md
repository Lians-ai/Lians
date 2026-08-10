# Lians Memory 0.1.0

Initial universal-directory submission draft for ChatGPT and Codex.

- Adds one focused Lians Memory skill for explicit durable storage, bounded recall, and confirmed deletion.
- Defines the intended hosted MCP dependency as Streamable HTTP.
- Exposes three implemented tools: `remember`, audit-writing `recall`, and destructive `forget_memory`.
- Requires explicit confirmation before `forget_memory` permanently crypto-shreds one selected memory.
- Specifies OAuth 2.1 as the required authentication design for user-specific memory.
- Includes five positive and three negative reviewer cases.
- Selects the **United States** and **United Kingdom** as the initial launch countries.
- Includes no custom UI, local hooks, local setup scripts, or bundled runtime.
- Uses the claim boundary "More memory, less repetition"; it does not claim to increase OpenAI quotas or bypass rate limits.

## Deployment status

This is a submission draft. The plugin has not been submitted, approved, published, or listed. The **United States** and **United Kingdom** are the operator-selected launch scope, but both countries still must be selected in the OpenAI portal.

The canonical `https://mcp.lians.ai/mcp` endpoint is live at production build `e72fad2c7f98ecf54b6553a90bf8d862046c1abc` with schema `0030_force_hosted_mcp_rls`. The guarded production workflow and a fresh no-token endpoint check verified HTTPS, protected-resource metadata, and the unauthenticated OAuth challenge. Three consecutive production rehearsals on distinct Machine IDs reached first `1/1 passing` readiness in `197.528`, `197.947`, and `197.963` seconds, all below the 360-second hosted startup timeout. Each cited workflow then recorded one immediate post-MCP result with health, liveness, and readiness `ok`; it does not attest an extended observation window or later degradation state. Fly warned that the configured 420-second (`7m0s`) health-check grace was lowered to an effective one minute, so 420 seconds was not honored. The no-token check intentionally skipped authenticated MCP initialization and tool discovery.

Authenticated MCP validation, the reviewer fixture, OpenAI publisher and business verification, OpenAI domain verification, Scan Tools, the demo, portal selection of the operator-approved United States and United Kingdom scope, submission, review, and publication all remain pending. Do not substitute a temporary, testing, or alternate endpoint, and do not submit until every production-checklist gate passes.
