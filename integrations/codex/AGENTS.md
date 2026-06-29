# Lians Memory — Codex Agent Instructions

This repository (or session) uses **Lians**, a financial-grade memory layer, to
give the agent persistent, compliance-safe memory across runs. When working here,
treat Lians as the source of truth for facts that change over time — guidance
revisions, dosage changes, matter status, prior decisions.

Use it in regulated contexts: **financial institutions, healthcare, and legal
firms**. Lians' bitemporal model means stale facts are excluded automatically, so
the context you reason over is never contaminated by outdated revisions.

## When to use memory

- **Before answering** a question that depends on prior facts, *recall* first.
- **After establishing** a new fact or making a decision, *remember* it with the
  business event-time (when it became true), not the current time.
- **For audit questions** ("what did we know on/before <date>"), use point-in-time
  recall — never present-state recall.

## Setup

```bash
pip install lians-sdk            # hosted/self-hosted client
# or
pip install lians-sdk[local]     # zero-setup local SQLite, no server/API key
```

Environment (hosted/self-hosted mode):

```
LIANS_URL=https://api.lians.dev          # or your self-hosted server
LIANS_API_KEY=lians_...                  # free key at api.lians.dev
LIANS_AGENT_ID=codex-session             # memory namespace for this agent
```

## Core operations

```python
from lians import LiansClient            # or LocalLiansClient (no env vars)
from datetime import datetime, timezone
import os

mem = LiansClient(base_url=os.environ["LIANS_URL"], api_key=os.environ["LIANS_API_KEY"])
agent = os.environ.get("LIANS_AGENT_ID", "codex-session")

# Remember — event_time is the BUSINESS time the fact became true
mem.add(agent_id=agent,
        content="NVDA FY2026 revenue guidance raised to $40B",
        event_time=datetime(2025, 11, 19, tzinfo=timezone.utc),
        metadata={"ticker": "NVDA", "metric": "revenue_guidance"})

# Recall — current (non-stale) facts only
for m in mem.recall(agent_id=agent, query="NVDA revenue guidance")["memories"]:
    print(m["event_time"], m["content"])

# Point-in-time — what did we know on a past date?
mem.recall_at(agent_id=agent, query="NVDA revenue guidance",
              as_of=datetime(2025, 9, 1, tzinfo=timezone.utc))
```

## Drop-in agent loop (recommended)

The harness wraps recall-before / remember-after in one object so you don't have
to hand-wire it into the turn loop:

```python
from lians import LiansClient, LiansMemoryHarness

harness = LiansMemoryHarness(
    LiansClient(base_url=os.environ["LIANS_URL"], api_key=os.environ["LIANS_API_KEY"]),
    agent_id=agent,
    domain="finance",          # or "healthcare" / "legal"
)

def call_model(context: str, query: str) -> str:
    ...  # your model call; inject `context` into the prompt

answer = harness.run_turn(user_query, generate=call_model)   # recall → model → remember
```

## Compliance surfaces (use, don't fake)

| Need | Call |
|------|------|
| Reconstruct full state at date T | `mem.snapshot(agent_id, as_of=T)` |
| Verify audit chain integrity | `mem.verify_chain()` |
| Detect lookahead bias in a backtest | `mem.backtest_check(agent_id, simulation_as_of=T)` |
| GDPR/HIPAA crypto-shred a subject | `mem.erase(subject_id, request_ref)` |

## Rules

- Never invent an `event_time` you weren't given — store the precision you have.
- Never paraphrase audit/snapshot output — report it literally.
- If a recalled fact's `content` is `null`, it was crypto-shredded; say so.
- `erase()` is irreversible and requires a request reference — confirm first.

## MCP alternative

If you prefer native tools over the SDK, run Lians as an MCP server and Codex gets
eight memory tools automatically (`remember`, `recall`, `recall_at`, `reconstruct`,
`list_conflicts`, `memory_lineage`, `fact_history`, `backtest_check`). See
`config.example.toml` in this folder.
