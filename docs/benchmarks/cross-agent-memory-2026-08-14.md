# Cross-agent memory evidence

Date: August 14, 2026

Status: live two-client handoff and workload-scoped token evidence

## Outcome

A live hosted-MCP test completed this sequence with synthetic data:

1. Cursor Agent CLI stored one project-scoped memory.
2. A separate Cursor chat recalled the exact codeword.
3. A fresh Claude Code session, authenticated to the same hosted Lians account,
   recalled the same exact codeword.
4. The memory was permanently forgotten after explicit confirmation.
5. A fresh Cursor recall returned `ABSENT`.
6. A fresh Claude check also returned `ABSENT`; Claude attempted an additional
   shell fallback, which the permission policy denied.

This proves one live Cursor-to-Claude continuity path. It does not prove a
five-client handoff, typical latency, or installed-MCP token economics.

## Cursor native-rule comparison

Cursor CLI 2026.08.11-e8db854 ran a balanced
candidate/native/native/candidate sequence on its free-plan `auto` model.

- The native arm loaded a synthetic 201-line, 21,166-character
  always-applied `.cursor/rules` file.
- The bounded arm received one 568-character relevant evidence pack.
- Both arms answered a derived-date question whose gold answer was not copied
  into either user prompt.
- Provider input accounting included fresh, cache-read, and cache-write tokens.

| Metric | Bounded context | Cursor rule |
|---|---:|---:|
| Runs | 2 | 2 |
| Exact answers | 2/2 | 2/2 |
| Pooled provider input tokens | 30,048 | 39,914 |
| Mean latency ratio | 0.9333x | 1.0000x |

Observed effect: **24.7181% fewer input tokens** and a **1.3283x**
same-input-budget multiplier, with all four answers exact. Cursor did not report
cost. The machine-readable aggregate is
[`cross-agent-memory-2026-08-14.json`](cross-agent-memory-2026-08-14.json).

## Platform matrix

| Client | Live result | Boundary or blocker |
|---|---|---|
| Cursor | Authenticated; store and fresh-chat recall exact | Token A/B used bounded prompt delivery, not an installed MCP call |
| Claude Code | Authenticated; fresh-session recall of Cursor record exact | Installed MCP handoff tested; token economics measured separately |
| Codex | Hosted MCP configuration installed | Running plugin still used a separate local database; restart and hosted OAuth remained |
| GitHub Copilot CLI | Hosted MCP configuration recognized | Organization policy disabled third-party MCP and denied the model call |
| Gemini CLI 0.55.1 | Google OAuth completed for two eligible browser sessions | Google returned `UNSUPPORTED_CLIENT` and directed this CLI client/tier to Antigravity |

## Prior Claude context evidence

Two separate balanced Claude Code tests also retained exact answer quality:

- bounded evidence versus full frozen history: 97.1564% fewer reported input
  tokens on one LOCOMO workload;
- bounded evidence versus a synthetic large native auto-memory index: 86.0779%
  fewer reported input tokens.

Those are high-history stress tests. They are not typical-memory averages or
provider-quota increases.

## Claim boundary

Allowed:

- A memory stored through Cursor was recalled exactly from a fresh Claude Code
  session through the same hosted Lians account.
- Confirmed forgetting made the synthetic record absent from a fresh Cursor
  recall.
- In the declared Cursor large-rule workload, bounded context reduced reported
  input tokens by 24.72% while preserving four of four exact answers.

Not allowed:

- Lians has completed live continuity tests across every packaged client.
- Lians increases provider plan quotas or saves 24.72% on every prompt.
- The running Codex local plugin and hosted Lians account are already one store.
- Copilot and Gemini passed their installed-MCP tests.
