# Roundup & Comparison-Set Outreach (GTM plan §2.2)

*Compiled July 3, 2026. Goal: Lians named in 3+ third-party roundups within 90
days. Discovery in this category = roundups + "X vs Y" searches + awesome
lists + AI-assistant answers; get into all four.*

## Tier 1 — active commercial/dev roundups (authors update these regularly)

| Target | URL | Angle for the pitch |
|---|---|---|
| Atlan | atlan.com/know/best-ai-agent-memory-frameworks-2026/ | Enterprise-governance audience — compliance axis is native content for them |
| TECHSY | techsy.io/en/blog/best-ai-agent-memory-tools | "8 tools" listicle; pitch as the 9th with a distinct category (regulated) |
| Vectorize | vectorize.io/articles/best-ai-agent-memory-systems | Infra-technical audience; lead with the eval harness + bitemporal model |
| Developers Digest | developersdigest.tech/blog/best-ai-agent-memory-providers-2026 | Provider comparison (Mem0/Zep/Letta/Cloudflare); pitch the self-hosted-compliance lane they don't cover |
| dev.to @agdex_ai | dev.to/agdex_ai/ai-agent-memory-in-2026-mem0-vs-zep-vs-letta-vs-cognee-a-practical-guide-cfa | "Practical guide" format — offer the reproducible eval + lookahead demo as material |
| dev.to @thedailyagent | dev.to/thedailyagent/top-6-ai-agent-memory-frameworks-for-devs-2026-1fef | Top-6 list; a 7th entry with benchmark receipts is an easy update for them |
| MCP.Directory | mcp.directory/blog/mem0-vs-letta-vs-zep-2026 | We're on the official MCP registry — natural hook for an MCP-first directory |
| Particula | particula.tech/blog/agent-memory-frameworks-tested-mem0-zep-letta-cognee-2026 | They actually *test* — offer adapter + keys-free harness; best fit for the eval |
| Rohit Raj | rohitraj.tech/en/notes/open-source-ai-agent-memory-mem0-vs-zep-letta-2026 | Open-source + benchmarked framing — exactly our lane |
| Powerdrill | powerdrill.ai/blog/best-ai-agent-memory-solutions | "10 best, tested" — same offer as Particula |
| XLR8 | tryxlr8.ai/blogs/best-open-source-ai-memory-frameworks-2026 | Open-source framing |

**Vendor-authored roundups** (Cognee's guides, EverMind's "Mem0 alternatives")
— don't pitch inclusion; they exist to rank their author first. Monitor them
for claims to answer on our comparison pages instead.

## Tier 2 — awesome lists & directories (PRs we can send today)

| Target | URL | Submission shape |
|---|---|---|
| TeleAI-UAGI/Awesome-Agent-Memory | github.com/TeleAI-UAGI/Awesome-Agent-Memory | Systems + benchmarks sections — submit Lians *and* the regulated eval + lookahead demo as benchmark entries |
| IAAR-Shanghai/Awesome-AI-Memory | github.com/IAAR-Shanghai/Awesome-AI-Memory | Engineering-frameworks section |
| tfatykhov/awesome-agent-memory | github.com/tfatykhov/awesome-agent-memory | Systems section |
| GitHub topics | github.com/topics/long-term-memory, /topics/memory-systems | Add topics to the repo: `agent-memory`, `long-term-memory`, `memory-systems`, `bitemporal`, `compliance` |
| AlternativeTo | alternativeto.net | Create listing; mark as alternative to mem0, Zep, Letta ("self-hosted", "privacy-focused" tags) |
| MCP Registry | done ✓ | already listed |

Awesome-list PR text (one line, their format):
> **Lians** — open-source (Apache-2.0) bitemporal memory for regulated agents:
> point-in-time recall, tamper-evident audit chain, GDPR crypto-shred, RLS
> information barriers. Ships a reproducible compliance eval of 6 memory systems.

## Tier 3 — research surfaces (slower, highest durability)

- **"Memory in the Age of AI Agents" survey** (arxiv.org/pdf/2512.13564) and its
  paper list (github.com/Shichun-Liu/Agent-Memory-Paper-List) — when the
  compliance-bench report is written up, submit as a system + benchmark entry;
  surveys get cited by every subsequent roundup and by AI assistants.
- **ICLR 2026 MemAgents workshop** (linked from TeleAI-UAGI list) — the
  compliance benchmark is a plausible workshop paper/poster; deadline check
  needed.

## The pitch (email / DM to roundup authors)

Subject: `data for your agent-memory roundup — compliance benchmark across the systems you cover`

> Hi {name} — your {piece title} is one of the pieces people actually find
> when they search this category, so I wanted to send you material rather
> than a request.
>
> We publish Lians (Apache-2.0, self-hosted agent memory for regulated
> industries) and two artifacts your readers may care about:
>
> 1. A **regulated-memory eval** that scores Mem0, Zep/Graphiti, Letta,
>    Hindsight, Supermemory, and ourselves on five invariants nobody else
>    benchmarks: point-in-time recall, provable erasure, audit snapshots,
>    stale-revision suppression, lookahead guards. Open harness, runnable
>    adapters — you can re-run any column with your own keys. {link}
> 2. A **reproducible lookahead-bias demo**: the same agent backtest run with
>    naive vs point-in-time retrieval — Sharpe 4.6 vs −0.6, every leaked
>    retrieval logged with timestamps. 30 seconds, no API keys. {link}
>
> If you update your piece, we'd love to be scored with the same knife as
> everyone else — and happy to provide anything that makes that easy
> (adapter code, a hosted sandbox, or direct answers).
>
> Either way, feel free to use the eval data with attribution.
> — E, Lians ({site})

Rules of engagement:
- Give data, never ask for placement.
- Never criticize the incumbent tools in the pitch — the eval table does it.
- Follow up exactly once, after 7 days, with one new fact (e.g. HN thread link).

## Tracking

Keep a sheet with: target · contact found (Y/N) · pitched date · response ·
included (Y/N) · which artifact they used. Success gate at 90 days: 3+
inclusions. If 10+ pitches produce 0 inclusions, the artifacts aren't landing —
revisit the material before pitching wave 3.
