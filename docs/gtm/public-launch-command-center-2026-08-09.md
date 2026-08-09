# Lians public launch command center

Date: 2026-08-09

## Launch position

Lians is the open-source evidence and memory layer for reconstructing what an AI system could know when it acted. The launch proof is the reproducible lookahead-bias demo because it turns an abstract temporal-memory failure into a falsifiable result.

Public claims must remain inside these boundaries:

- The Lians v0.5.0 GitHub release and `lians-sdk` 0.5.0 on PyPI are public.
- The TypeScript package remains on npm 0.4.0 until npm authorizes the GitHub publisher.
- The LangChain documentation pull request was merged.
- Lians is present in the official MCP Registry and has a public Glama listing.
- A protected Codex Ultra repeat measured 2.04x same-budget usage with exact answers on that frozen task.
- The broader 120-turn Codex matrix measured 2.22x pooled economics, but failed the every-prompt protected gate. Never present that result as a universal quota increase.
- The lookahead demo uses seeded synthetic data. Its reported return and Sharpe values illustrate contamination, not investment performance.

## Show HN submission

Title:

`Show HN: Your agent's memory can leak the future into backtests`

URL:

`https://www.lians.ai/blog/backtest-lookahead`

Text:

> We built a reproducible demo of a quiet failure in AI-agent evaluation: semantic memory retrieval can return facts that did not exist at the simulated decision time.
>
> The harness runs the same simple strategy on the same seeded synthetic market twice. Present-time retrieval produces the attractive result. Point-in-time retrieval exposes it as fiction. The run logs each contaminated retrieval with the decision timestamp and the fact's knowledge timestamp.
>
> This is not only a trading problem. Any historical replay can be contaminated, including support agents on old tickets, coding agents on old issues, and regulated decision systems on prior cases.
>
> The implementation is Apache 2.0, runs locally, and supports `recall_at` for point-in-time reconstruction. We would especially value criticism of the method and receipt format.

First comment:

> A useful distinction is event time versus knowledge time. Filtering on when an event happened does not catch a correction that arrived later. The demo models both axes so a late revision cannot appear in an earlier reconstruction. The full project and SDK are linked from the article.

## Product Hunt launch page

Name: `Lians`

Tagline: `Reconstruct what your AI knew when it acted`

Short description:

`Open-source, bitemporal memory and decision evidence for AI agents. Recall facts as they were knowable at a prior time, preserve provenance, and export verifiable decision receipts.`

Maker comment:

> AI systems often make consequential decisions using facts, permissions, policies, and memory that later change. Ordinary logs show outputs. Lians reconstructs the state that was actually available when the decision happened.
>
> We built Lians around two primitives: bitemporal memory and verifiable evidence. You can record when a fact was true and when the system learned it, query with `recall_at`, and preserve the sources behind the result.
>
> The project is Apache 2.0 and available for Python, TypeScript, MCP, and local deployment. Our favorite demo shows how an agent memory layer can leak future information into a historical backtest, then fixes the contamination with point-in-time retrieval.
>
> We are looking for direct product criticism, integration feedback, and teams with one consequential workflow that must remain reconstructable.

Topics: `Artificial Intelligence`, `Developer Tools`, `Open Source`, `Data Infrastructure`, `Compliance`

Media order:

1. Existing Lians Open Graph card.
2. Lookahead-bias result chart.
3. Decision receipt screenshot.
4. Compatibility test screenshot.
5. Installation and `recall_at` code sample.

## LinkedIn launch post

> Most AI audit trails answer: what did the system output?
>
> The harder question is: what could it actually know when it acted?
>
> Facts get corrected. Policies change. Permissions move. Agent memory keeps accumulating. If you replay an old decision against today's state, you can produce a confident explanation for a decision the system never made.
>
> We built Lians to reconstruct the historical state behind consequential AI decisions.
>
> The strongest example is a reproducible lookahead-bias demo. The same agent backtest, on the same seeded synthetic data, looks excellent when its memory can retrieve future facts. Pin retrieval to decision time and the apparent edge disappears. Every contaminated retrieval has a timestamped receipt.
>
> Lians is Apache 2.0, supports point-in-time recall, ships through Python and MCP, and now has merged LangChain documentation plus an official MCP Registry listing.
>
> Try the demo: https://www.lians.ai/blog/backtest-lookahead
>
> Explore the project: https://github.com/Lians-ai/Lians

## arXiv readiness and publication gate

The regulated-memory preprint is still a draft in pull request 26. It must not be described as an indexed arXiv paper.

Before submission:

- Merge the paper source and freeze its comparison artifact.
- Re-run every table against the named public versions.
- Add a limitations section that distinguishes executable tests from documentation checks.
- Confirm all author names, affiliations, ORCID identifiers, and the corresponding-author email.
- Select the most defensible arXiv category and confirm whether endorsement is required.
- Build the source through arXiv's TeX environment and inspect the generated PDF.
- Submit, retain the submission identifier, and wait for moderation before announcing an arXiv URL.

Announcement after indexing:

> We published the methods behind Lians' regulated-memory evaluation. The paper focuses on five properties that ordinary relevance benchmarks miss: point-in-time recall, stale-revision suppression, erasure evidence, audit reconstruction, and lookahead guards. Code, adapters, and evidence artifacts are public so every result can be challenged.

## Launch sequence

1. Deploy the measurement, sitemap, schemas, contact form, and newsletter capture.
2. Confirm all launch URLs return 200 and the funnel report receives aggregate events.
3. Submit the expanded sitemap and request indexing for the homepage, Product, Pricing, Docs, and comparison pages.
4. Publish Show HN first and respond to substantive comments.
5. Publish the LinkedIn post once the HN discussion has a stable URL.
6. Schedule Product Hunt for the next eligible launch day with all media attached.
7. Send roundup wave one with the live launch and benchmark links.
8. Publish the first Decision Evidence Brief to the owned list.
