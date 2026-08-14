# Lians Easy desktop preview

Lians Easy is a technical preview for adding private, local memory to a
supported AI client. It is deliberately smaller than the full Lians engine.
There is no account, API key, database server, or embedding-model download.

There are currently no signed Windows, notarized macOS, or Linux
`LiansMemory` assets in [GitHub Releases](https://github.com/Lians-ai/Lians/releases).
Do not send normal users to that page expecting a desktop download. For a live,
managed setup, use [Lians Personal for $10/month](https://www.lians.ai/upgrade?plan=starter).
For free local memory today, use the [published MCP setup](install.md#mcp--use-lians-as-a-native-tool).

## Personal setup

Developers evaluating the preview can run it from a source checkout:

```bash
python -m pip install -e packages/lians-easy
python -m lians_easy
```

Choose the clients to configure, select **Install Lians**, restart those
clients, then ask one to remember a useful fact and recall it in another chat.

Existing client configuration files are backed up before every change. Memory
is stored in a local SQLite file under the operating system's per-user Lians
data directory. Selected clients use the same `personal` profile, so a fact
saved in one can be recalled in another.

## Release trust gate

Do not promote the desktop installer broadly until its Windows builds are
code-signed and its macOS builds are signed with an Apple Developer ID and
notarized. Until those credentials and release steps are in place, treat the
standalone artifacts as prerelease builds for technical evaluation; Windows
SmartScreen and macOS Gatekeeper may otherwise show operating-system trust
warnings.

The core tools are intentionally understandable:

| Tool | Purpose |
|---|---|
| `remember` | Save a useful fact, preference, decision, or finding. |
| `recall` | Return a small relevant set instead of replaying entire chats. |
| `list_memories` | Inspect what is saved. |
| `correct_memory` | Replace stale information and retain version history. |
| `forget_memory` | Permanently erase one memory after explicit confirmation. |

## Supported clients

The first desktop release configures Claude Desktop, Cursor, Windsurf,
Antigravity CLI, Gemini CLI, and Codex. Choose Antigravity for a consumer Google
account. The Gemini CLI target is for supported Standard or Enterprise
subscriptions, API keys, and Vertex AI configurations after Google's June 18,
2026 consumer-login retirement. It does not modify ChatGPT because ChatGPT
connectors use a hosted HTTP connection rather than a local stdio process. Use
a hosted Lians connector when that distribution is available.

## IT and enterprise deployment

The same artifact supports non-interactive deployment. Review detected paths:

```bash
LiansMemory doctor --json
```

Preview an exact install without writing anything:

```bash
LiansMemory install --clients claude,cursor,antigravity,codex --plan --json
```

Install for selected clients:

```bash
LiansMemory install --clients claude,cursor,antigravity,codex --yes --json
```

Remove the managed client entries while preserving the user's memory database:

```bash
LiansMemory uninstall --clients claude,cursor,antigravity,codex --yes --json
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
