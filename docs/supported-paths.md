# Supported paths and repository status

Use this page when you have cloned Lians and need to know which installation,
package, or directory is the supported starting point. It is deliberately
shorter than the architecture and deployment documentation.

Last reviewed: August 14, 2026. The current public release line is `0.5.0`; the
machine-readable registry record is
[`published-release-status.json`](published-release-status.json).

## I just want Lians to work

| What you need | Start here | Status |
|---|---|---|
| Free local recovery in a supported MCP client | `uvx --from "lians-sdk[mcp]" lians-mcp` in the [MCP install guide](install.md#existing-ai-client-use-mcp) | **Recommended** |
| A managed private workspace without running a service | [Lians Personal](https://www.lians.ai/upgrade?plan=starter) | **Current managed** |
| Local memory inside a Python app or notebook | `pip install "lians-sdk[local]"` and [`LocalLiansClient`](../agentmem/sdk/python) | **Recommended** |
| A shared self-hosted HTTP service | [`agentmem/src/lians/`](../agentmem/src/lians) through the [self-hosting guide](install.md#self-host-lians) | **Current, operator-managed** |
| A language client for an existing HTTP service | [`agentmem/sdk/`](../agentmem/sdk) | **Current release line** |
| Evaluate typed evidence, workspace freshness, and the Guard review gate | [`packages/lians-easy/`](../packages/lians-easy) | **Developer preview; source-only, desktop unsigned** |

The current Python distribution is named **`lians-sdk`**, while its import
namespace is **`lians`**. For new work, install `lians-sdk`; do not select a
folder merely because it is named `sdk/python`.

## What each top-level path means

| Path | Status | Use it for |
|---|---|---|
| `agentmem/src/lians/` | **Current** | The memory engine and FastAPI service. |
| `agentmem/sdk/python/` | **Current and published** | `lians-sdk` 0.5.x, `LocalLiansClient`, the HTTP client, and `lians-mcp`. |
| `agentmem/sdk/typescript/` | **Current and published** | The `@lians-ai/lians` package. |
| `agentmem/sdk/go/`, `java/`, `c/` | **Current and versioned** | HTTP clients released with the repository. |
| `integrations/` | **Current, integration-specific** | Tested client and framework setup. Begin with the integration's README. |
| `plugins/lians-memory/` | **Current Codex plugin source** | The repository marketplace plugin and its local runtime. |
| `plugins/lians-memory-universal/` | **Submission/evaluation bundle** | Hosted OpenAI review material; it is not the general local installation path. |
| `packages/lians-easy/` | **Developer preview** | Source and future `lians-bridge` distribution: recovery, task contracts, typed evidence, Git workspace fingerprints, MCP, hooks, and receipts. Automatic stale evidence invalidation and trusted CI intake are not complete. The wheel is not on PyPI, and it is not a signed desktop download. |
| `sdk/python/` | **Legacy** | The older `lians` 0.2.0 thin-client tree. Do not use it for new installs, examples, or integrations. |
| `demo/`, `benchmarks/`, `paper/` | **Evidence and examples** | Reproducible demonstrations, evaluation fixtures, and research - not product installation entry points. |

## Release and trust boundaries

- The verified published versions are the ones in
  [`published-release-status.json`](published-release-status.json), not every
  version string that remains in a historical or compatibility directory.
- The normal free local path is the published `lians-sdk[mcp]` package. It needs
  no Lians account or API key and stores memory in `~/.lians/mcp.db`.
- Lians Bridge remains source-only for normal users. A product-aligned
  `lians-bridge` wheel now has a tested, opt-in trusted-publishing path, but it
  must not be described as installable until its first PyPI release is verified.
  The current GitHub release
  does not contain signed Windows, notarized macOS, or Linux desktop installer
  assets. See the [preview trust gate](easy-install.md#release-trust-gate).
- The full service and remote SDKs are for a hosted or self-hosted HTTP
  deployment. They are not required for one person using local MCP memory.
- A plugin or integration README may provide a shorter client-specific setup,
  but it should resolve to one of the current paths above.

## Maintainer rule

New user-facing documentation must link to a **Recommended** or **Current** path.
Preview paths must say what is missing. Legacy paths must not appear in new
installation examples. When release status changes, update this page and
`published-release-status.json` together.
