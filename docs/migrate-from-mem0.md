# Migrate from mem0 to Lians

mem0 is a popular starting point for agent memory. If you're hitting issues with **stale facts contaminating your LLM context**, you've found the core architectural limit: mem0's memory algorithm is ADD-only - it does not supersede outdated facts. When a fact changes, both the old and new versions compete for the top-k slots in your context window.

Lians fixes this with a bitemporal model. Superseded facts are excluded at the database layer before they ever reach your LLM.

## The core difference

**mem0 (April 2026 algorithm):** Single-pass ADD-only extraction. When "NVDA guidance is $35B" is superseded by "NVDA guidance revised to $40B", both facts exist in the store with equal retrieval weight.

**Lians:** A three-stage supersession pipeline detects the relationship and marks the old fact `valid_to = NOW()`. Present recall filters `WHERE valid_to IS NULL` - stale facts are invisible at the DB layer.

### Benchmark result

| System | Stale facts in top-5 (5-revision chain) | Point-in-time recall (4 queries) |
|---|---|---|
| Lians | **0 / 4** | **4 / 4** |
| mem0 | 4 / 4 | 0 / 4 |

→ Full numbers: [docs/benchmark.md](benchmark.md)

## Drop-in replacement

The Lians client API mirrors mem0's interface closely. Most migrations are a find-and-replace.

### Installation

```bash
# Remove mem0
pip uninstall mem0ai

# Install Lians
pip install lians-sdk[local]     # local SQLite, zero setup - identical to mem0's library mode
# or
pip install lians-sdk            # connect to a Lians server or cloud
```

### Code comparison

**mem0:**
```python
from mem0 import Memory

m = Memory()
m.add("NVDA FY2026 revenue guidance raised to $40B", user_id="analyst-1")
results = m.search("NVDA revenue guidance", user_id="analyst-1")
```

**Lians:**
```python
from lians import LocalLiansClient
from datetime import datetime, timezone

mem = LocalLiansClient()
mem.add(
    agent_id="analyst-1",
    content="NVDA FY2026 revenue guidance raised to $40B",
    event_time=datetime(2025, 11, 19, 16, tzinfo=timezone.utc),  # when it happened
)
results = mem.recall(agent_id="analyst-1", query="NVDA revenue guidance")
```

The main additions are:
- `agent_id` instead of `user_id` (same concept, different name)
- `event_time` - the business timestamp of the fact (when it happened, not when you wrote it)
- No LLM key required - Lians runs locally with no external API calls

### Switching to the hosted server

```python
# Local mode (SQLite, zero setup)
from lians import LocalLiansClient
mem = LocalLiansClient()

# Hosted mode (your server or the current Lians API endpoint)
from lians import LiansClient
mem = LiansClient(base_url="https://agentmem-lotus.fly.dev", api_key="lians_...")

# Both share the same API surface - one line change
```

## Features you gain

| Feature | mem0 | Lians |
|---|---|---|
| Stale fact suppression | ✗ | ✓ bitemporal supersession |
| Point-in-time recall | ✗ | ✓ `recall_at(as_of=datetime(...))` |
| SEC 17a-4 audit chain | ✗ | ✓ SHA-256 hash chain |
| GDPR crypto-shred | ✗ | ✓ per-subject AES-256-GCM |
| Information barriers | ✗ | ✓ PostgreSQL RLS |
| Backtest contamination detection | ✗ | ✓ `backtest_check()` |
| No LLM key required | ✗ (GPT-5-mini default) | ✓ local embeddings available |
| Self-hosted server | ✓ | ✓ |

## LangChain migration

**mem0:**
```python
from mem0.integrations.langchain import Mem0ChatMessageHistory
history = Mem0ChatMessageHistory(session_id="user-123")
```

**Lians:**
```python
from lians.langchain_integration import LiansChatHistory
history = LiansChatHistory(client=mem, agent_id="user-123")
```

## Environment variables

mem0 requires an OpenAI API key by default. Lians does not - it runs with local embeddings out of the box.

```bash
# mem0 required
OPENAI_API_KEY=sk-...

# Lians - no key needed for local mode
# For production quality embeddings (optional):
EMBEDDING_PROVIDER=voyage
VOYAGE_API_KEY=pa-...
```

## Self-hosted server

If you were running mem0's self-hosted server:

```bash
# Lians self-hosted
git clone https://github.com/Lians-ai/Lians.git && cd Lians/agentmem
cp .env.demo .env
docker compose up --build -d
python scripts/seed_demo.py   # provisions a demo API key
```

Full deploy guide: [docs/deploy.md](deploy.md)
