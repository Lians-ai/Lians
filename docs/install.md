# Install Lians

One core memory contract, five native SDKs, and a self-hostable server. Pick your
language — every network client covers recall, point-in-time `recall_at`, snapshot,
backtest, crypto-shred erasure, audit-chain verification, and the relationship
graph. Python local mode implements the core memory workflows in-process rather
than speaking REST.

> **Just want to try it?** The fastest path is Python local mode — no server, no
> Docker, no API key:
> ```bash
> pip install lians-sdk[local]
> ```

## Install matrix

| Language | Install | Import / entry point |
|----------|---------|----------------------|
| **Python** | `pip install lians-sdk` | `from lians import LiansClient` |
| **Python (local, no server)** | `pip install lians-sdk[local]` | `from lians import LocalLiansClient` |
| **TypeScript / Node** | `npm install @lians-ai/lians` | `import { LiansClient } from "@lians-ai/lians"` |
| **Go** | `go get github.com/Lians-ai/Lians/agentmem/sdk/go@v0.5.0` | `lians.NewClient(url, key)` |
| **Java** (JVM 11+) | Maven `ai.lians:lians-sdk:0.5.0` | `new LiansClient(opts)` |
| **C** (C99 + libcurl) | `cmake -B build && cmake --build build` | `lians_client_new(...)` |

All SDK source packages are versioned in lock-step at `0.5.0`. Before installing,
confirm that version has been published to the selected registry.

## Run the server

The SDKs (except Python local mode) talk to a Lians server. Self-host it:

```bash
git clone https://github.com/Lians-ai/Lians
cd Lians/agentmem
docker compose up --build        # Postgres + pgvector + API on :8000
```

Health check: `curl localhost:8000/livez`. See
[production deployment](deploy.md) for KMS, non-superuser RLS, air-gap, and WORM
requirements.

## 30-second hello, by language

### Python (local — no server)
```python
from lians import LocalLiansClient
from datetime import datetime, timezone

mem = LocalLiansClient()
mem.add(agent_id="desk", content="NVDA guidance raised to $40B",
        event_time=datetime(2025, 11, 19, tzinfo=timezone.utc),
        metadata={"ticker": "NVDA", "metric": "guidance"})
print(mem.recall(agent_id="desk", query="NVDA guidance")["memories"])
```

### Python (server)
```python
from lians import LiansClient
mem = LiansClient(base_url="https://mem.yourfirm.internal", api_key="...")
```

### TypeScript / Node
```ts
import { LiansClient } from "@lians-ai/lians";
const client = new LiansClient({ baseUrl: "http://localhost:8000", apiKey: process.env.LIANS_API_KEY! });
await client.addMemory({ agent_id: "desk", content: "NVDA guidance raised to $40B",
                         event_time: "2025-11-19T16:00:00Z", metadata: { ticker: "NVDA" } });
const { memories } = await client.recall({ agent_id: "desk", query: "NVDA guidance" });
```

### Go
```go
import "github.com/Lians-ai/Lians/agentmem/sdk/go"

c := lians.NewClient("http://localhost:8000", os.Getenv("LIANS_API_KEY"))
_, _ = c.AddMemory(ctx, lians.AddMemoryRequest{
    AgentID: "desk", Content: "NVDA guidance raised to $40B",
    EventTime: time.Date(2025, 11, 19, 16, 0, 0, 0, time.UTC),
    Metadata: map[string]any{"ticker": "NVDA"},
})
```

### Java
```xml
<dependency>
  <groupId>ai.lians</groupId>
  <artifactId>lians-sdk</artifactId>
  <version>0.5.0</version>
</dependency>
```
```java
import ai.lians.LiansClient;
import ai.lians.LiansClientOptions;

var client = new LiansClient(LiansClientOptions.builder()
        .baseUrl("http://localhost:8000").apiKey(System.getenv("LIANS_API_KEY")).build());
```

### C
```bash
cd agentmem/sdk/c && cmake -B build && cmake --build build   # needs libcurl
```
```c
#include <stdlib.h>
#include "lians.h"
lians_client_t *c = lians_client_new(
    "http://localhost:8000", getenv("LIANS_API_KEY"), NULL
);
```

## MCP — use Lians as a native tool

Any MCP host (Claude Desktop, Cursor, Windsurf) can use Lians directly. See the
[MCP section of the README](../README.md#mcp---native-tool-in-any-ai-client).

## Framework integrations (Python)

```bash
pip install lians-sdk[langchain]    # LangChain chat history & tools
pip install lians-sdk[langgraph]    # LangGraph node factories
pip install lians-sdk[crewai]       # CrewAI tools
pip install lians-sdk[all]          # everything
```

## Verify a release

```bash
pip install lians-sdk==0.5.0
npm view @lians-ai/lians version
go list -m github.com/Lians-ai/Lians/agentmem/sdk/go@v0.5.0
```

Maintainers: see [RELEASING.md](../RELEASING.md) — one `vX.Y.Z` tag ships all five.
