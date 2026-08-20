<p align="center">
  <a href="https://github.com/Lians-ai/Lians">
    <img src="docs/assets/lians-lotus.svg" width="190" alt="Lians lotus">
  </a>
</p>

<p align="center"><strong>Your AI says it is done. Lians checks the receipts.</strong></p>

<p align="center">
  <a href="docs/quickstart.md"><strong>Quickstart</strong></a> ·
  <a href="docs/why-lians.md">Why Lians</a> ·
  <a href="docs/benchmarks/continuitybench-v0.1.md">ContinuityBench</a> ·
  <a href="docs/install.md">Install</a> ·
  <a href="docs/">Docs</a> ·
  <a href="https://github.com/Lians-ai/Lians/issues">Issues</a>
</p>

<p align="center">
  <a href="https://pypi.org/project/lians-sdk"><img src="https://img.shields.io/pypi/v/lians-sdk?label=PyPI" alt="PyPI version"></a>
  <a href="https://www.npmjs.com/package/@lians-ai/lians"><img src="https://img.shields.io/npm/v/%40lians-ai%2Flians?label=npm" alt="npm version"></a>
  <a href="https://registry.modelcontextprotocol.io/?q=io.github.ebeirne%2Flians"><img src="https://img.shields.io/badge/MCP-Official%20Registry-blueviolet" alt="MCP Official Registry"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="Apache 2.0 license"></a>
</p>

## Lians Check

**The evidence-backed proof-of-done check for AI coding agents.**

Claude Code, Codex, Cursor, and other coding agents can confidently report that
work is finished without current proof. Lians runs the checks itself, binds the
result to the current repository state, and gives the person reviewing the work
one clear answer.

```text
NO PROOF
NEEDS WORK
READY TO REVIEW
```

- **Measure.** Lians runs the project's configured tests, build, type checks,
  and lint commands without asking an AI to grade itself.
- **Bind.** Every receipt matches the current Git state. A later change requires
  fresh evidence.
- **Fail closed.** Changed check policies, missing proof, failed commands, and
  checks that modify the workspace cannot produce a ready result.
- **Require review.** `READY TO REVIEW` is a handoff to a person, never a
  claim that the work is correct, approved, or safe to deploy.
- **Stay local.** The first product path needs no Lians account, AI password, or
  provider API key.

Lians works with your existing AI account and editor. It does not replace your
model, Git, CI, repository instructions, or human review.

## Check the work in two commands

The first source preview is intentionally small:

```bash
python -m pip install -e ./packages/lians-easy
lians init
lians check
```

`lians init` discovers a short set of high-signal project commands and asks the
user to authorize them. `lians check` executes those exact commands without a
shell, records output hashes, rejects changed policies, and creates a signed
local receipt for the current code. Review and commit `.lians/check.json` when
the same policy should be shared with a team.

For automation or an already reviewed setup, use `lians init --yes`. If Lians
cannot discover the right command, provide one explicitly:

```bash
lians init --command "tests=python -m pytest -q" --yes
```

For automation, `lians check --json` exits `0` when ready, `1` when work is
needed, and `2` when no authorized proof policy is available.

## One clear result after every check

```text
NO PROOF
Lians Check is not set up for this project.
Next: Run lians init.

NEEDS WORK
FAIL  Tests  4.21s
Next: Fix Tests and run lians check again.

READY TO REVIEW
PASS  Tests  3.84s
PASS  Build  8.11s
Next: Review the current changes.
```

The trust model remains deliberately strict. The local runner creates
`measured_local` evidence. Agent summaries stay `agent_attested` and cannot open
the review gate. Trusted CI evidence requires an exact GitHub attestation and
commit match. Read [why Lians exists](docs/why-lians.md), the full
[Lians Guard product contract](docs/lians-guard.md), and the current [market
pressure test](docs/market-pressure-test-2026-08.md).

## Optional cross-agent recovery

Choose the AI tool you already use:

| Tool | Fastest setup |
|---|---|
| Codex app, CLI, or IDE | [One command](integrations/codex) |
| Claude Code | [Two plugin commands](integrations/lians-plugin) |
| Cursor | [One-click MCP install](integrations/cursor) |
| Other MCP clients | [Minimal MCP setup](docs/install.md#existing-ai-client-use-mcp) |

For example, after [installing `uv`](https://docs.astral.sh/uv/getting-started/installation/), connect Codex with:

```bash
codex mcp add lians --env LIANS_MCP_ENABLED_TOOLS=remember,recall,list_memories,correct_memory,forget_memory -- uvx --from "lians-sdk[mcp]" lians-mcp
```

Restart Codex, then save one safe project fact and recover it in a fresh chat.
Local memory is stored in `~/.lians/mcp.db` by default. This is the available
free recovery path; the full Guard workflow is currently a developer preview.

[Follow the complete quickstart](docs/quickstart.md) for setup, recovery,
correction, deletion, and the Guard preview boundary.

## What a fresh coding agent receives

Lians can generate a bounded project handoff instead of replaying a transcript:

```text
Reported complete; verify:
- migrated the orders API to /v2/orders

Still open:
- verify the migration against current Git state
- update documentation

Decisions:
- keep pytest

Changed:
- /v1/orders is stale; use /v2/orders

Next:
- update documentation before touching unrelated UI
```

The handoff is derived from current Lians state, not a manually maintained
summary. Agent-reported work remains visible without being mislabeled as
verified completion.

## Why this is not another generic memory layer

Native memories are convenient when work stays inside one product. General
memory is no longer a scarce category. Lians uses local memory for recovery,
then focuses on the expensive gap: current task state and evidence-backed
readiness.

The [current competitive landscape](docs/competitive-landscape.md) pressure
tests this position against native Claude Code, Codex, Cursor, GitHub Copilot,
Entire, Factory, and AI review workflows.

| Approach | Best fit | Boundary |
|---|---|---|
| Native tool memory | One AI tool, minimal setup | Usually stays inside that vendor |
| `AGENTS.md` or `CLAUDE.md` | Stable repository instructions | Must be maintained manually |
| Transcript replay | Reconstructing one conversation | Large, noisy, and may revive stale decisions |
| Free Lians recovery | Resume current project context across supported tools | Requires a local connection to each tool |
| Lians Guard | Detect stale state and gate readiness with typed evidence | Team workflow is still in developer preview |

Lians is not claiming that every project needs a separate memory layer. See the
[honest comparison and decision guide](docs/why-lians.md).

## Project status

Lians is under active development. Available recovery features and preview Guard
features are separated here so the repository does not imply a production
guarantee that does not exist yet.

| Capability | Status |
|---|---|
| `lians init` project check discovery and authorization | Developer preview |
| `lians check` measured local runner and signed receipt | Developer preview |
| Fail-closed policy changes and workspace-mutation detection | Developer preview |
| Local memory through MCP and Python | Available |
| Codex, Claude Code, and Cursor local recovery setup | Available |
| Inspect, correct, and confirmed permanent deletion | Available |
| Bounded context and signed selection receipts | Available |
| Automatic Claude-to-Codex project handoff | Beta |
| Typed evidence and evidence-backed task gate | Developer preview |
| Local Git workspace fingerprint on checkpoints | Developer preview |
| Automatic stale evidence invalidation | In development |
| Attested GitHub Actions evidence intake | Developer preview |
| Local Guard reporting | Developer preview |
| Shared team queue | Planned |
| Cross-platform clean-install CI | Required by the new Guard workflow; first hosted run pending |
| Guided desktop installer and local control center | Release candidate |

The macOS and Windows desktop builds remain release candidates pending platform
signing and notarization. See the [desktop preview boundary](docs/easy-install.md).

## Current evidence

The included Claude-to-Codex continuity fixture recovered **10/10 expected
facts**, exposed **0 stale facts as current**, and produced a **231-token
handoff**. These are bounded beta results, not a promise that every live coding
session extracts perfectly. [Run the experiment](experiments/cross-agent-continuity/README.md).

The developing [ContinuityBench v0.1](docs/benchmarks/continuitybench-v0.1.md)
publishes the proposed cross-agent, freshness, correction, erasure, provenance,
and boundedness test contract. Its current Lians fixture is evidence for that
fixture only; it is not presented as a completed competitor leaderboard.

A separate live test saved a synthetic project fact through Cursor, recalled it
in a new Cursor chat and a fresh Claude Code session, and confirmed it was gone
after deletion. [Read the test method](docs/benchmarks/cross-agent-memory-2026-08-14.md).

The Guard correctness benchmark exercises missing evidence, unknown criteria,
failed constraints, blockers, stale updates, and drift signals. It is a local,
deterministic test of the configured policy, not proof of semantic correctness
or a production outcome. Run `packages/lians-easy/benchmarks/task_contract_correctness.py`
to inspect the cases.

## Build with Lians

Use the local Python SDK inside an application:

```bash
pip install "lians-sdk[local]"
```

```python
from datetime import datetime, timezone
from lians import LocalLiansClient

memory = LocalLiansClient(db_path=".lians/memory.db")
memory.add(
    agent_id="my-agent",
    content="The project uses Python 3.12 and pytest.",
    event_time=datetime.now(timezone.utc),
)

result = memory.recall(
    agent_id="my-agent",
    query="Which Python version and test runner should I use?",
)
```

See the [install guide](docs/install.md) for TypeScript, Go, Java, C, framework
integrations, and self-hosting.

Running a class, club, hackathon, or campus developer group? Use the
[student and community kit](docs/student-community-kit.md). Contributors and
package integrators can start with
[Supported paths and repository status](docs/supported-paths.md).

<details>
<summary><strong>Advanced capabilities</strong></summary>

Lians also includes tools for project-scoped agent handoffs, signed selection
and review receipts, local research and browser briefs, temporal reconstruction,
lineage, information barriers, confirmed erasure, and bounded formal checks.
These capabilities are useful for advanced or governed deployments but are not
required for the starter memory workflow.

- [Memory engine](docs/memory-engine.md)
- [Cross-agent continuity experiment](experiments/cross-agent-continuity/README.md)
- [Agent-work verification](docs/formal-verification.md)
- [Security model](docs/security-whitepaper.md)
- [Community and managed product boundary](docs/community-cloud-boundary.md)
- [Supported paths and repository status](docs/supported-paths.md)

</details>

## Development

```bash
git clone https://github.com/Lians-ai/Lians.git
cd Lians
python -m pip install -e ".[dev]"
python scripts/test_all.py
```

Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request. Feature
ideas, integration requests, and reproducible bugs are welcome in
[GitHub Issues](https://github.com/Lians-ai/Lians/issues).

If Lians helps your workflow, [star the repository](https://github.com/Lians-ai/Lians/stargazers)
so other AI-tool users can find it.

## License

Apache 2.0. See [LICENSE](LICENSE).

<!-- mcp-name: io.github.ebeirne/lians -->
