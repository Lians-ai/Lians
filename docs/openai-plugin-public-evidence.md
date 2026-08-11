# OpenAI plugin public verification evidence

This page contains sanitized, externally verifiable evidence for the draft
Lians universal plugin. It intentionally excludes the production topology,
deployment procedure, credentials, private reviewer delivery, rollback runbook,
and operator-only go/no-go checklist.

The currently cited build is
`e72fad2c7f98ecf54b6553a90bf8d862046c1abc` at schema
`0030_force_hosted_mcp_rls`. Production workflow
[31349405257](https://github.com/Lians-ai/Lians/actions/runs/31349405257)
is the newest public workflow evidence cited by the submission bundle.

Three separate production rehearsals recorded machine start to first readiness
at `197.528`, `197.947`, and `197.963` seconds. Each workflow recorded a
single immediate post-MCP result with health, liveness, and readiness `ok`.
The cited evidence does not attest an extended observation window or later
degradation state. The configured `420`-second health-check grace was lowered by the
provider to an effective one minute, so 420 seconds was not honored.

During minute `2026-08-10T03:41Z`, a sanitized authenticated OAuth E2E passed
protected-resource metadata, OIDC discovery, dynamic client registration,
browser login, authorization callback, token exchange, repository JWT
verification, the authenticated endpoint checker, MCP
remember/recall/confirmed-forget calls, and session cleanup. The final tool
completion was `2026-08-10T03:41:10.126400Z`; generic provider tool labels are
mapped only by the harness's fixed remember/recall/forget order. The reviewer
account login was observed at `2026-08-10T03:40:58Z`.

The initial availability selected by the operator is the United States and the
United Kingdom. Portal selection and submission remain pending. This evidence
does not claim OpenAI publisher or business verification, domain verification,
tool-scan completion, submission, approval, publication, or directory listing.

No credentials, tokens, client identifiers, memory payloads or references, raw
responses, production secrets, or local paths are included in this evidence.
