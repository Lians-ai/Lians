# Lians Bridge desktop preview

The intended generally available package and nontechnical first-run contract
are defined in [the consumer installer contract](consumer-installer.md). The
current preview exercises the same Bridge and client configuration engine but
does not yet satisfy its signing and release gates.

Lians Bridge is a technical preview for carrying private, local memory across
supported AI clients. It is deliberately smaller than the full Lians engine.
There is no account, API key, database server, or embedding-model download.

There are currently no Authenticode-signed Windows, notarized macOS, or Linux
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
clients, then ask Cursor to remember a useful project rule. Start a new Codex
or Claude task in the same repository and inspect the receipt attached to the
recalled context.

Existing client configuration files are backed up before every change. Memory
is stored in a local SQLite file under the operating system's per-user Lians
data directory. Selected clients use the same `personal` profile, so a fact
saved in one can be recalled in another. Memory values are encrypted at rest;
Windows protects the root key with DPAPI. Corrections and scope changes create
inspectable versions, while confirmed forgetting crypto-erases the full
lineage so old wording cannot return from a different client.

Setup is transactional per AI client. If one integration cannot be verified,
its exact original files and permissions are restored while successful clients
remain connected. The GUI retries only the failed client IDs instead of
repeating work that already passed. **Save help report** writes a redacted JSON
diagnostic without copying memory content, AI-app settings, exception text,
credentials, or user paths.

## Cross-tool experience

The preview implements the first product loop directly:

1. `remember` records an explicit preference, decision, fact, or handoff.
2. Antigravity, Claude, Codex, and Gemini CLI prompt hooks request a
   project-aware context pack before the agent starts the task.
3. Cursor uses the same MCP tools plus a generated, always-applied project rule.
4. A receipt reports the memories used, project, estimated tokens, selection
   reasons, and exclusions.
5. Pause, correction, scope, and confirmed forget changes apply to the shared
   store immediately.

The React control center can be served by the local Bridge during development:

```bash
python -m lians_easy bridge --app-dir /path/to/memory-checkup/local-dist
```

The hosted control center demonstrates the same information architecture, but
uses sample data when it is not connected to a loopback Bridge. It never asks a
user to paste raw AI-provider credentials.

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

The first desktop release configures Google Antigravity, Claude Desktop,
Cursor, Windsurf, Gemini CLI, and Codex. It does not modify ChatGPT because
ChatGPT connectors use a hosted HTTP connection rather than a local stdio
process. Use a hosted Lians connector when that distribution is available.

## IT and enterprise deployment

The same artifact supports non-interactive deployment. Review detected paths:

```bash
LiansMemory doctor --json
```

Preview an exact install without writing anything:

```bash
LiansMemory install --clients antigravity,claude,cursor,gemini,codex --plan --json
```

Install for selected clients:

```bash
LiansMemory install --clients antigravity,claude,cursor,gemini,codex --yes --json
```

Remove the managed client entries while preserving the user's memory database:

```bash
LiansMemory uninstall --clients antigravity,claude,cursor,gemini,codex --yes --json
```

Every write is idempotent and creates a timestamped backup when a configuration
already exists. This makes the same flow suitable for MDM, scripted onboarding,
and help-desk diagnostics.

## Easy runtime or full engine?

| Need | Lians Bridge | Full Lians engine |
|---|---:|---:|
| One person, several local AI clients | Yes | Yes |
| No account, API key, or model download | Yes | No |
| Keyword-based bounded recall | Yes | Yes |
| Encrypted local values and signed context receipts | Yes | Yes |
| Semantic retrieval and temporal reconstruction | No | Yes |
| Shared team server and HTTP SDKs | No | Yes |
| Governance, barriers, receipts, and audit workflows | No | Yes |

Start with Lians Bridge. Move to the full engine when the deployment—not the
first-run setup—actually requires those capabilities.
