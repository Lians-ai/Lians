<p align="center">
  <a href="https://github.com/Lians-ai/Lians">
    <img src="https://raw.githubusercontent.com/Lians-ai/Lians/HEAD/docs/assets/logo-blue.png" width="340" alt="Lians">
  </a>
</p>

# @lians-ai/lians

**Provider-neutral memory for TypeScript and Node agents.** Store useful facts
and recall relevant current context across models and sessions.

## Install

```bash
npm install @lians-ai/lians
```

The TypeScript client connects to a hosted or self-hosted Lians service. For
zero-setup local SQLite memory, use the Python `LocalLiansClient` or the Lians
MCP server.

## Quickstart

```ts
import { LiansClient } from "@lians-ai/lians";

const memory = new LiansClient({
  baseUrl: "https://memory.example.com",
  apiKey: process.env.LIANS_API_KEY!,
});

await memory.addMemory({
  agent_id: "my-agent",
  content: "The project uses TypeScript and Vitest.",
  event_time: new Date().toISOString(),
  metadata: { project: "demo", topic: "tooling" },
});

const { memories } = await memory.recall({
  agent_id: "my-agent",
  query: "Which language and test runner should I use?",
});

console.log(memories.map((item) => item.content));
```

## What the client supports

- Add and recall memories
- Point-in-time recall and snapshots
- Supersession and memory history
- Typed requests, responses, and `LiansError` failures
- LangChain and webhook helpers

Full documentation: [github.com/Lians-ai/Lians](https://github.com/Lians-ai/Lians)
