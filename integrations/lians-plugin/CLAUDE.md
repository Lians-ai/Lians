# Lians Memory — Claude Code Skill

Lians gives your Claude Code session persistent, financial-grade memory. Facts are stored with a bitemporal model — stale facts are suppressed automatically, so you always get the current state without contamination from outdated context.

## Setup

### Option 1: Cloud (zero infrastructure)

```bash
pip install lians-sdk
```

Set in your environment:
```
LIANS_URL=https://api.lians.dev
LIANS_API_KEY=lians_...        # get a free key at api.lians.dev
LIANS_AGENT_ID=claude-session  # any identifier for this agent/session
```

### Option 2: Local (SQLite, no server, no API key)

```bash
pip install lians-sdk[local]
```

No environment variables needed — works instantly.

### Option 3: Self-hosted

```bash
git clone https://github.com/Lians-ai/Lians.git && cd Lians/agentmem
cp .env.demo .env && docker compose up --build -d
python scripts/seed_demo.py   # prints your API key
```

## MCP (Claude Desktop / Cursor / Windsurf)

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "lians": {
      "command": "uvx",
      "args": ["--from", "lians-sdk[mcp]", "lians-mcp"],
      "env": {
        "LIANS_URL": "https://api.lians.dev",
        "LIANS_API_KEY": "lians_...",
        "LIANS_AGENT_ID": "claude-session"
      }
    }
  }
}
```

This gives Claude eight native memory tools: `remember`, `recall`, `recall_at`, `reconstruct`, `list_conflicts`, `memory_lineage`, `fact_history`, `backtest_check`.

## Python quick reference

```python
# Local mode — SQLite, zero setup
from lians import LocalLiansClient
from datetime import datetime, timezone

mem = LocalLiansClient()

# Store a fact with its business timestamp
mem.add(
    agent_id="my-agent",
    content="NVDA FY2026 revenue guidance raised to $40B",
    event_time=datetime(2025, 11, 19, 16, tzinfo=timezone.utc),
    metadata={"ticker": "NVDA", "metric": "revenue_guidance"},
)

# Recall current (non-stale) facts
results = mem.recall(agent_id="my-agent", query="NVDA revenue guidance")
for r in results:
    print(r.content)

# Point-in-time: what did we know on March 1, 2025?
past = mem.recall_at(
    agent_id="my-agent",
    query="NVDA revenue guidance",
    as_of=datetime(2025, 3, 1, tzinfo=timezone.utc),
)

# Store from conversation messages
mem.add_from_messages(
    agent_id="my-agent",
    messages=[
        {"role": "user", "content": "NVDA just raised guidance to $40B"},
        {"role": "assistant", "content": "Noted — updating the revenue guidance."},
    ],
)

# GDPR erasure
mem.erase(subject_id="user-123", request_ref="GDPR-REQ-2025-001")

# Backtest contamination check
from datetime import timedelta
issues = mem.backtest_check(
    agent_id="my-agent",
    simulation_as_of=datetime(2025, 1, 1, tzinfo=timezone.utc),
)
if issues:
    print("Lookahead bias detected:", issues)
```

## TypeScript quick reference

```typescript
import { LiansClient } from "@lians-ai/lians";

const mem = new LiansClient({
  baseUrl: process.env.LIANS_URL!,
  apiKey: process.env.LIANS_API_KEY!,
});

await mem.addMemory({
  agent_id: "my-agent",
  content: "NVDA FY2026 revenue guidance raised to $40B",
  event_time: "2025-11-19T16:00:00Z",
  metadata: { ticker: "NVDA", metric: "revenue_guidance" },
});

const results = await mem.recall({ agent_id: "my-agent", query: "NVDA revenue" });
```

## Key concepts

- **`agent_id`** — the memory namespace. Use one per agent, user, or session.
- **`event_time`** — *when the fact happened* (business time), not when you wrote it. Critical for point-in-time queries and backtest checks.
- **Supersession** — when you write a fact that contradicts an existing one, Lians automatically marks the old fact as superseded. It disappears from `recall()` but remains visible in `recall_at()` for past dates.
- **Free tier** — 10,000 memories + 1,000 recalls/month. No credit card.

## Docs & support

- Full docs: https://github.com/Lians-ai/Lians/tree/master/docs
- Deploy guide: https://github.com/Lians-ai/Lians/blob/master/docs/deploy.md
- MCP Registry: https://registry.modelcontextprotocol.io/v0/servers/io.github.ebeirne%2Flians/versions/latest
