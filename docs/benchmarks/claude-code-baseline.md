# Claude Code bounded-context baseline

This is the first narrow product experiment for the claim **Use less context.
Get more AI.** It compares two isolated Claude Code print-mode calls on the
same synthetic market-research task:

1. **Full replay** sends all 24 saved project facts again.
2. **Lians bounded** asks the local Lians store for the three most relevant
   facts under a fixed context budget.

Both variants receive the same question and must return the same exact
three-field JSON object. The report keeps Claude's uncached input, cache
creation input, cache-read input, and output token counts separate. It also
records deterministic answer correctness, prompt hashes, the Lians selection
receipt, run order, and the synthetic fixture version.

## Safe first run

Install the local package from the repository and inspect the plan. This step
does not contact Claude:

```bash
python -m pip install -e packages/lians-easy
lians experiment claude
lians experiment claude --json
```

Before a live run, `claude auth status` must show an authenticated subscription
session and no `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, Bedrock, Vertex, or
Foundry override may be active in the process environment. The experiment fails
closed if it detects an API-key or cloud-provider route.

```bash
lians experiment claude --run --output claude-context-report.json
```

Use `--repetitions 2` to alternate variant order and reduce one-call ordering
noise. This makes four Claude calls, so the default remains one repetition.
Existing Lians memory is never read or changed; the fixture uses a disposable
encrypted store.

## Claim boundary

This is a synthetic baseline, not a promise of universal savings. A successful
result means only that the bounded variant preserved the exact expected answer
while using fewer provider-reported input tokens in these isolated calls.

Claude Code's current documentation says that print-mode and Agent SDK usage on
subscription plans uses a separate Agent SDK allowance. Therefore this test
does **not** establish that Lians extends the ordinary interactive Claude Pro
allowance. It also does not enlarge Claude's context window. Any public claim
must preserve that distinction and link the raw report.

- [Claude Code print mode and usage metadata](https://code.claude.com/docs/en/headless)
- [Claude Code CLI reference](https://code.claude.com/docs/en/cli-usage)
- [Using Claude Code with Pro or Max](https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-or-max-plan)
