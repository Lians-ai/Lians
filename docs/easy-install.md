# Install Lians without a terminal

Lians Easy is the normal-user path for adding private, local memory to a
supported AI client. It is deliberately smaller than the full Lians engine:
there is no account, API key, Python setup, database server, or embedding model
download.

## Personal setup

1. Download **LiansMemory** for your operating system from
   [GitHub Releases](https://github.com/Lians-ai/Lians/releases).
2. Open the downloaded app.
3. Leave the AI clients you use selected and choose **Install Lians**.
4. Restart those clients.
5. Ask the client to remember one useful fact, then recall it in another chat.

Existing client configuration files are backed up before every change. Memory
is stored in a local SQLite file under the operating system's per-user Lians
data directory. Selected clients use the same `personal` profile, so a fact
saved in one can be recalled in another.

The core tools are intentionally understandable:

| Tool | Purpose |
|---|---|
| `remember` | Save a useful fact, preference, decision, or finding. |
| `recall` | Return a small relevant set instead of replaying entire chats. |
| `list_memories` | Inspect what is saved. |
| `correct_memory` | Replace stale information and retain version history. |
| `forget_memory` | Permanently erase one memory after explicit confirmation. |

## Supported clients

The first desktop release configures Claude Desktop, Cursor, Windsurf, Gemini
CLI, and Codex. It does not modify ChatGPT because ChatGPT connectors use a
hosted HTTP connection rather than a local stdio process. Use a hosted Lians
connector when that distribution is available.

## IT and enterprise deployment

The same artifact supports non-interactive deployment. Review detected paths:

```bash
LiansMemory doctor --json
```

Preview an exact install without writing anything:

```bash
LiansMemory install --clients claude,cursor,codex --plan --json
```

Install for selected clients:

```bash
LiansMemory install --clients claude,cursor,codex --yes --json
```

Remove the managed client entries while preserving the user's memory database:

```bash
LiansMemory uninstall --clients claude,cursor,codex --yes --json
```

Every write is idempotent and creates a timestamped backup when a configuration
already exists. This makes the same flow suitable for MDM, scripted onboarding,
and help-desk diagnostics.

## Easy runtime or full engine?

| Need | Lians Easy | Full Lians engine |
|---|---:|---:|
| One person, several local AI clients | Yes | Yes |
| No dependencies or model download | Yes | No |
| Keyword-based bounded recall | Yes | Yes |
| Semantic retrieval and temporal reconstruction | No | Yes |
| Shared team server and HTTP SDKs | No | Yes |
| Governance, barriers, receipts, and audit workflows | No | Yes |

Start with Lians Easy. Move to the full engine when the deployment—not the
first-run setup—actually requires those capabilities.
