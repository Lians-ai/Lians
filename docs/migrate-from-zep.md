# Migrate from Zep to Lians

In September 2024, Zep shut down its Community Edition — the free, self-hosted open-source server. If you were running Zep CE, your options were to pay for Zep Cloud or find a replacement.

Lians is a fully open-source, self-hostable alternative. It runs on the same Postgres + pgvector stack and adds capabilities Zep CE never had: bitemporal memory, tamper-evident audit chains, GDPR crypto-shred, and information barriers.

## Why Lians instead of Zep Cloud

| | Lians | Zep Cloud |
|---|---|---|
| Self-hosted | ✓ free, open-source | ✗ cloud-only |
| Local dev mode | ✓ SQLite, zero setup | ✗ |
| Bitemporal model | ✓ | Partial |
| SEC 17a-4 audit chain | ✓ | ✗ |
| GDPR crypto-shred + audit survival | ✓ | ✗ |
| Information barriers (DB-layer RLS) | ✓ | ✗ |
| Backtest contamination detection | ✓ | ✗ |
| Finance/healthcare/legal adapters | ✓ | ✗ |
| Free tier | ✓ 10K memories, 1K recalls | Limited |
| Apache 2.0 license | ✓ | ✓ |

## Conceptual mapping

| Zep concept | Lians equivalent |
|---|---|
| `session_id` | `agent_id` |
| `ZepClient` | `LiansClient` / `LocalLiansClient` |
| Memory search | `mem.recall(agent_id, query)` |
| Add message | `mem.add(agent_id, content, event_time)` |
| Message history | `mem.add_from_messages(agent_id, messages)` |
| Session summary | Not needed — supersession handles this automatically |
| Knowledge graph | Domain adapters (finance, healthcare, legal) |

## Installation

```bash
# Lians SDK
pip install lians-sdk[local]     # local SQLite mode — instant start
pip install lians-sdk            # connects to a Lians server

# TypeScript
npm install @lians-ai/lians
```

## Code comparison

**Zep (Python):**
```python
from zep_cloud.client import AsyncZep

client = AsyncZep(api_key="your-api-key")
await client.memory.add(session_id="session-123", messages=[...])
result = await client.memory.search(session_id="session-123", text="query")
```

**Lians (Python):**
```python
from lians import AsyncLiansClient
from datetime import datetime, timezone

async with AsyncLiansClient(base_url="https://api.lians.dev", api_key="lians_...") as mem:
    await mem.add(
        agent_id="session-123",
        content="...",
        event_time=datetime.now(timezone.utc),
    )
    results = await mem.recall(agent_id="session-123", query="query")
```

**Zep (TypeScript):**
```typescript
import { ZepClient } from "@getzep/zep-cloud";
const client = new ZepClient({ apiKey: "your-api-key" });
await client.memory.add("session-123", { messages: [...] });
```

**Lians (TypeScript):**
```typescript
import { LiansClient } from "@lians-ai/lians";
const mem = new LiansClient({ baseUrl: "https://api.lians.dev", apiKey: "lians_..." });
await mem.add({ agentId: "session-123", content: "...", eventTime: new Date() });
```

## Self-hosted server setup

If you were running Zep CE via Docker, the switch is straightforward:

```bash
git clone https://github.com/Lians-ai/Lians.git && cd Lians/agentmem

# Copy the demo env (local embeddings, no API keys required)
cp .env.demo .env

# Start Postgres + Redis + Lians server
docker compose up --build -d

# Provision a demo API key and open the demo dashboard
python scripts/seed_demo.py
open demo/index.html
```

For production deployment (Fly.io, Kubernetes, bare Docker): [docs/deploy.md](deploy.md)

## MCP server

Lians is listed on the [official MCP Registry](https://registry.modelcontextprotocol.io/v0/servers/io.github.ebeirne%2Flians/versions/latest). Add it to Claude Desktop, Cursor, or Windsurf:

```json
{
  "mcpServers": {
    "lians": {
      "command": "uvx",
      "args": ["--from", "lians-sdk[mcp]", "lians-mcp"],
      "env": {
        "LIANS_URL": "https://your-lians-server.internal",
        "LIANS_API_KEY": "lians_...",
        "LIANS_AGENT_ID": "my-agent"
      }
    }
  }
}
```

## What you gain over Zep CE

Beyond what Zep CE offered, Lians adds:

- **Bitemporal facts** — stale facts are suppressed at the database layer, not the application layer
- **Point-in-time recall** — `mem.recall_at(agent_id, query, as_of=datetime(...))` answers "what did we know on date X?"
- **Backtest check** — `mem.backtest_check(agent_id, simulation_as_of=...)` detects lookahead bias
- **Audit chain** — every write is recorded in a SHA-256 Merkle chain; exportable for regulators
- **GDPR erasure** — `mem.erase(subject_id, request_ref)` crypto-shreds all content while the audit trail survives
- **Information barriers** — `barrier_group` column with PostgreSQL RLS prevents one agent from seeing another's data, enforced at the database layer
