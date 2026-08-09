# Decision Evidence Brief, issue 1

Subject: `The hidden future in agent memory`

Preview: `A reproducible lookahead-bias demo, Lians v0.5.0, and an honest test of same-budget model usage.`

## What changed

Lians v0.5.0 is public on GitHub and PyPI. The release advances the evidence layer for teams that need to reconstruct an AI decision after facts, policies, permissions, or memory have changed.

The fastest way to understand the problem is our lookahead-bias demo. It runs the same simple agent strategy twice on seeded synthetic data. Present-time memory can retrieve facts from the future of the simulated decision. Point-in-time memory cannot. The attractive backtest result disappears when the information boundary is enforced.

Read and reproduce it: https://www.lians.ai/blog/backtest-lookahead

## Integration note

LangChain merged Lians into its memory documentation, and Lians is discoverable through the official MCP Registry. The default MCP profile exposes only three tools for ordinary use: remember, recall, and point-in-time recall.

Start here: https://www.lians.ai/docs

## Benchmark note

We also tested a narrower economic question: can bounded durable recall complete more comparable memory tasks under the same model budget?

On one frozen Codex Ultra task, both conditions returned the exact answer. The Lians pre-model hook reduced estimated per-task credits from 3.249 to 1.5965, equal to 2.04x same-budget usage for that task.

The broader 120-turn matrix reached 2.22x pooled economics but failed the every-prompt quality and economics gate. We are publishing the failure because averages should not become universal quota claims.

Methods and artifacts: https://github.com/Lians-ai/Lians/blob/master/docs/benchmarks/provider-usage-extension-2026-08-08.md

## One question for readers

Which historical AI decision would be hardest for your team to reproduce today?

Reply with the workflow, the facts that change, and the evidence you would need. We will choose one anonymized pattern for the next brief.

You are receiving the monthly Decision Evidence Brief because you explicitly subscribed on lians.ai. You can unsubscribe by emailing privacy@lians.ai until the self-service preference endpoint is available.
