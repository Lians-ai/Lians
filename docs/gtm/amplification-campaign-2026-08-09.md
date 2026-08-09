# Lians proof amplification campaign

Date: 2026-08-09

## Campaign rule

Every announcement points to one verifiable artifact, one useful takeaway, and one action. Avoid combining all wins into a generic company update.

## LangChain documentation merge

Primary post:

> Lians is now documented in LangChain's memory integrations. The important part is not another connector. It is the ability to ask what an agent could know at a prior moment, with provenance for the retrieved facts. If your evaluation replays old decisions, point-in-time memory prevents today's corrections from leaking into yesterday's answer.
>
> Docs merge: https://github.com/langchain-ai/docs/pull/4949
> Quickstart: https://www.lians.ai/docs

Tutorial: `Point-in-time agent memory with LangChain and Lians`

Outline:

1. Record an original policy fact.
2. Record a later correction.
3. Run present-time recall.
4. Run `recall_at` before the correction.
5. Export the decision evidence and compare the source versions.

Partner ask: invite LangChain to reshare the tutorial after it is published, with the merged documentation link as the factual basis.

## MCP Registry and Glama

Primary post:

> Lians can now be discovered through the official MCP Registry and Glama. The default tool surface stays deliberately small: remember, recall, and point-in-time recall. That gives coding agents durable memory without exposing a sprawling tool schema on every prompt.
>
> MCP setup: https://www.lians.ai/docs

Tutorial: `Give an MCP host governed memory in three tools`

Partner ask: request a listing refresh that uses `Lians-ai/Lians`, v0.5.0, and the current description. Do not claim any directory endorsement beyond the verified listing state.

## v0.5.0 release

Primary post:

> Lians v0.5.0 is live on GitHub and PyPI. This release adds the decision-evidence work behind reproducible AI decisions and the newest memory controls. Python users can install `lians-sdk==0.5.0`. The npm package is still 0.4.0 while publisher authorization is repaired.
>
> Release: https://github.com/Lians-ai/Lians/releases/tag/v0.5.0

Release tutorial: `From an agent action to a decision receipt`

The tutorial should end with a receipt that shows the query time, fact versions, source references, and verifier result.

## Codex memory usage-extension work

Primary post:

> We tested whether durable retrieval can extend practical model usage on the same billing plan. On one frozen Codex Ultra memory task, a pre-model Lians recall reduced estimated per-task credits from 3.249 to 1.5965 while preserving the exact answer, equal to 2.04x same-budget usage for that task.
>
> The broader 120-turn matrix passed pooled economics at 2.22x but failed the every-prompt protected gate. That means the honest result is workload-specific: memory can extend usage when retrieval preserves answer quality, not that Lians universally increases account quota.
>
> Evidence and reproduction steps: https://github.com/Lians-ai/Lians/blob/master/docs/benchmarks/provider-usage-extension-2026-08-08.md

Tutorial: `Measure memory economics without inventing a quota claim`

Outline:

1. Freeze the prompts and expected answers.
2. Compare full-context and bounded-retrieval conditions.
3. Gate economics on quality first.
4. Report provider-returned cost separately from estimated cost.
5. Publish both the pooled result and failed cells.

## Seven-day coordinated cadence

| Day | Main artifact | Secondary action |
| --- | --- | --- |
| 1 | Lookahead Show HN launch | Answer comments and record recurring objections |
| 2 | LinkedIn launch | Ask LangChain for a reshare of the integration tutorial |
| 3 | v0.5.0 decision-receipt tutorial | Refresh MCP Registry and Glama descriptions |
| 4 | Codex usage-extension methodology post | Share the exact claim boundary in developer communities |
| 5 | Product Hunt launch | Send roundup wave one |
| 6 | Decision Evidence Brief issue 1 | Invite replies with benchmark requests |
| 7 | Launch evidence recap | Publish traffic, completion, and conversion counts without visitor identifiers |
