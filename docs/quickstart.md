# Lians Check quickstart

The smallest Lians product answers one question: does the current AI-generated
work have fresh evidence behind it?

## Check the current work

Install the developer preview from a clone, then initialize the repository once:

```bash
python -m pip install -e ./packages/lians-easy
lians init
```

Lians shows the discovered project commands and asks for an exact confirmation
before authorizing them. Review and commit `.lians/check.json` if the same policy
should be shared with a team. After Claude Code, Codex, Cursor, or another agent
says the work is finished, run:

```bash
lians check
```

The result is `NO PROOF`, `NEEDS WORK`, or `READY TO REVIEW`. A ready result
means the authorized commands passed for the current Git state. Human review is
still required, and Lians does not claim semantic correctness or deployment
safety.

`lians check --json` exits `0` when ready, `1` when work is needed, and `2` when
no authorized proof policy is available.

For an already reviewed, noninteractive setup:

```bash
lians init --yes
```

To supply a command that discovery did not find:

```bash
lians init --command "tests=python -m pytest -q" --yes
```

## Optional cross-agent recovery

Set up the available free recovery layer, give an AI coding tool one safe project
fact, then confirm that the current fact survives a fresh chat. The local setup
requires no Lians account or provider API key.

This quickstart proves local recovery and correction. The full Lians Guard
workflow, including automatic lifecycle checkpoints, stale workspace detection,
typed completion evidence, and trusted CI intake, remains a developer preview.

## 1. Choose a client

### Codex

Install [`uv`](https://docs.astral.sh/uv/getting-started/installation/), then run:

```bash
codex mcp add lians --env LIANS_MCP_ENABLED_TOOLS=remember,recall,list_memories,correct_memory,forget_memory -- uvx --from "lians-sdk[mcp]" lians-mcp
```

Restart Codex. Run `codex mcp list` or type `/mcp` in the terminal UI to confirm
that `lians` is connected.

### Claude Code

Run these commands inside Claude Code:

```text
/plugin marketplace add Lians-ai/Lians
/plugin install lians@lians-plugins
```

Restart Claude Code after installation.

### Cursor

Use the [one-click Cursor installer](../integrations/cursor), review the local
MCP configuration, and approve it in Cursor.

For another MCP client, follow the [generic MCP setup](install.md#existing-ai-client-use-mcp).

## 2. Save one safe project fact

In a project chat, say:

```text
Remember that this project uses Python 3.12 and pytest.
```

Approve the `remember` tool if your client asks. Do not use passwords, API keys,
personal data, or other secrets as test values.

## 3. Recall it in a fresh chat or second tool

Open a new chat in the same project and ask:

```text
What Python version and test runner does this project use?
```

Approve the `recall` tool if prompted. The answer should mention Python 3.12
and pytest without requiring the previous transcript.

For the clearest continuity proof, save the fact in one connected tool and ask
the question in another, for example, remember it in Claude Code and recall it
from a fresh Codex task. Both tools must resolve to the same project and local
Lians store.

## 4. Correct the memory

Say:

```text
Correct that memory: this project now uses Python 3.13 and pytest.
```

Open another fresh chat and ask the same question. Current-state recall should
use Python 3.13 rather than presenting Python 3.12 as current.

## 5. Inspect or delete saved memory

Ask your AI tool to list the project's Lians memories. To remove the test fact,
ask it to permanently forget that memory and confirm the deletion when prompted.

Deletion is intentionally explicit. Confirm that the correct memory reference
is selected before approving it.

## Where local data is stored

The basic MCP setup stores local memory in `~/.lians/mcp.db`. Set
`LIANS_LOCAL_DB` in the MCP server environment to choose another location.

Lians does not ask for your Claude, Cursor, or Codex password or provider API
key. The first use may download a local semantic model.

## Preview the Guard tools from source

Contributors can install the preview package from a clone:

```bash
python -m pip install -e ./packages/lians-easy
lians mcp
```

The preview MCP server includes `start_task`, `checkpoint_task`, `task_status`,
`continue_work`, `configure_verification`, `verify_work`, and
`verification_status`. Positive completion evidence must declare one of
`measured_local`, `measured_ci`, or `human_confirmed`. Agent summaries and
touched files remain recovery context and do not satisfy completion criteria.

Read [the Lians Guard contract](lians-guard.md) before evaluating the preview.

## Troubleshooting

- Confirm that `uvx` runs in a terminal.
- Restart the AI client after changing its MCP configuration.
- Confirm that the `lians` or `lians-memory` server is connected in the client.
- Allow extra time during the first run while the local model initializes.
- If a write times out during initialization, retry it after the server is ready;
  the timed-out write is not queued.

For client-specific details, see the [Codex](../integrations/codex),
[Claude Code](../integrations/lians-plugin), and
[Cursor](../integrations/cursor) guides.
