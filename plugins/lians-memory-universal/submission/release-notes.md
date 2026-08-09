# Lians Memory 0.1.0

Initial universal-directory submission draft for ChatGPT and Codex.

- Adds one focused Lians Memory skill for explicit durable storage, bounded recall, and confirmed deletion.
- Defines the intended hosted MCP dependency as Streamable HTTP.
- Exposes three implemented tools: `remember`, audit-writing `recall`, and destructive `forget_memory`.
- Requires explicit confirmation before `forget_memory` permanently crypto-shreds one selected memory.
- Specifies OAuth 2.1 as the required authentication design for user-specific memory.
- Includes five positive and three negative reviewer cases.
- Includes no custom UI, local hooks, local setup scripts, or bundled runtime.
- Uses the claim boundary "More memory, less repetition"; it does not claim to increase OpenAI quotas or bypass rate limits.

## Deployment status

This is a submission draft. `https://mcp.lians.ai/mcp` is the selected canonical production endpoint, but this repository does not assert that it is live yet. Deploy and validate that exact host; do not substitute a temporary, testing, or alternate submission URL. Submit only after the hosted service, OAuth flow, domain verification, privacy controls, destructive-action checks, and reviewer fixture pass the production checklist.
