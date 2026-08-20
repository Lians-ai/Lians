# Activation measurement boundary

## Goal

Count a real product outcome without turning a local-memory product into a
surveillance product.

Lians considers a device locally activated when:

1. at least one supported AI client is connected; and
2. at least one context event reuses saved memory.

`lians status --json` reports this local state under `activation`. An empty
context request does not count. Fresh-session completion remains self-reported
until a supported client provides a trustworthy, content-free session boundary.

## What the current branch does

- counts successful context-reuse events locally;
- distinguishes `not_connected`, `connected`, and `reuse_observed`;
- reports that measurement is local-only;
- records no memory content for growth measurement; and
- makes no external analytics request.

This is enough to validate activation during onboarding calls. It is not enough
to claim a global unique-user count.

## Requirements before anonymous aggregation ships

Aggregation must remain off until all of these exist:

- an explicit, unbundled opt-in after the user sees local activation;
- a public payload schema and plain-language explanation;
- a random installation identifier generated locally after consent;
- a one-command view, reset, and deletion path;
- a published raw-event retention period no longer than 90 days;
- server-side rate limiting and duplicate suppression; and
- tests proving that memory text, prompts, paths, project names, credentials,
  source hashes, IP-derived location, and receipt contents cannot enter a
  payload.

## Allowed aggregate events

| Event | Meaning |
|---|---|
| `installation_completed` | Supported package or installer completed |
| `client_connected` | At least one supported client is configured |
| `memory_reused` | A context event selected at least one saved memory |
| `fresh_session_confirmed` | User explicitly confirms the two-session test |
| `day_7_active` | A content-free event occurred seven days after activation |
| `day_30_active` | A content-free event occurred thirty days after activation |

Allowed dimensions are schema version, Lians release, supported client family,
operating-system family, coarse calendar date, campaign code entered by the
user, and the consented random installation ID. All other fields are rejected.

## Public claim rule

Until aggregation is implemented and independently checked, publish package
downloads, stars, and self-reported activations as separate numbers. Never call
their sum “users.”
