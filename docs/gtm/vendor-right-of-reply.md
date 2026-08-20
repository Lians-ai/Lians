# Vendor Right-of-Reply - Regulated-Memory Eval

*Process + email templates. Send BEFORE the scores are promoted anywhere.
This email is doing double duty: it is methodological diligence, and it is the
first time each of these teams hears the name Lians. Write and send it as the
introduction it is.*

## Process (documented in public)

1. Send each scored vendor the methodology, the harness link, and **their
   column with per-cell justifications** - not the full comparison table.
   (The full table reads as an ad; their own column reads as a fact-check
   request.)
2. Reply window: **10 business days** before the results are promoted beyond
   the repo. Corrections received are applied and credited by name in the
   results doc ("corrected per feedback from the X team, 2026-07-xx").
3. Log in the table below: date sent, channel, response, action taken. This
   log is part of the published methodology - silence is also data, and
   documenting the outreach is what makes the benchmark defensible on HN.
4. If a vendor disputes a cell and is right → fix it and say so prominently.
   If they dispute it and the API still doesn't exist → the cell stands, and
   the exchange (with their permission) becomes an appendix note.

**Status:** final ready-to-send copies (real links, July 17 deadline) are in
[right-of-reply-emails-2026-07.md](right-of-reply-emails-2026-07.md).
Upstream bug reports filed **before** first contact, 2026-07-03, so a vendor
looking us up finds courteous engineering first:
[mem0ai/mem0#6085](https://github.com/mem0ai/mem0/issues/6085) ·
[getzep/graphiti#1258 (comment)](https://github.com/getzep/graphiti/issues/1258#issuecomment-4880328136).

| Vendor | Contact channel | Sent | Response | Action |
|---|---|---|---|---|
| Mem0 | founders@mem0.ai / GitHub issue | - | - | bug report filed 2026-07-03 (#6085) |
| Zep | founders@getzep.com / GitHub issue | - | - | confirming comment on #1258, 2026-07-03 |
| Letta | contact@letta.com | - | - | - |
| Hindsight | (find via site/GitHub) | - | - | - |
| Supermemory | (find via site/GitHub) | - | - | - |

*(Verify each address before sending; GitHub issue on their repo is an
acceptable public-channel fallback and has the advantage of being visibly
open.)*

---

## The email

**Subject:** `Fact-check request: how we scored {Product} in an open compliance benchmark`

> Hi {name / team},
>
> I'm E, the author of Lians - an open-source agent-memory layer aimed at
> regulated deployments (finance/healthcare/legal). First, the honest context:
> {Product} is one of the systems that defined this category, and parts of
> Lians exist because of ideas your team proved out. {personal_line - see
> per-vendor notes below.}
>
> We're publishing an evaluation that scores memory systems on five
> compliance-specific invariants that existing benchmarks (LoCoMo,
> LongMemEval) don't cover: stale-revision suppression, point-in-time (as-of)
> recall, provable erasure, lookahead/backtest guards, and audit-state
> reconstruction. Lians is scored by the same harness, same knife.
>
> Before we promote it anywhere, I want your column to be right.
> Attached/linked:
>
> 1. The methodology and the open harness (runnable, no keys needed for the
>    structural checks): {harness link}
> 2. **Your column** - five cells, each with the one-line justification and
>    the exact API call (or absence) it's based on: {column link}
>
> Two asks, both optional:
>
> - If any cell is wrong - an API we missed, a primitive that exists, a
>   configuration that changes the outcome - tell me and I'll fix it before
>   publication and credit the correction to your team by name.
> - If you'd rather run the harness yourselves, the adapter interface is six
>   methods; PRs against your own adapter are welcome and will be merged.
>
> If I don't hear back by {date, 10 business days out}, we'll publish with
> the column as-is and a note that you were offered review on {send date}.
> Either way, the harness stays open and your column stays re-runnable -
> corrections are welcome after publication too.
>
> Respect for what you've built,
> E
> {site} · {repo} · {email}

## Per-vendor personalization (`{personal_line}`)

- **Mem0:** "Your ecosystem breadth is the reason 'agent memory' is a category
  a buyer can search for at all - our comparison docs say so explicitly, and
  our migration guide assumes people start with mem0 for good reasons."
- **Zep:** "Graphiti's bitemporal edge model is genuinely good engineering -
  our own relationship graph credits it as the direct inspiration in our docs.
  The eval distinguishes validity windows from two-axis point-in-time recall,
  and I'd particularly value your read on whether we've drawn that line
  fairly."
- **Letta:** "The MemGPT lineage is the most original architecture in the
  space; the eval necessarily scores turnkey compliance primitives rather
  than agent-managed memory, and I want to be sure that framing doesn't
  misrepresent what Letta is for."
- **Hindsight:** "Your temporal retrieval work is the closest in spirit to
  ours; the one cell I most want checked is erasure - as of our review we
  found no deletion API, and if that's wrong or has changed I'd rather fix it
  than publish it."
- **Supermemory:** "Profile consolidation earned you the supersession
  partial-credit cell; the other cells are structural API checks and I'd
  welcome a correction on any of them."

## Rules

- Send from a personal address, signed with a name, not a brand account.
- No marketing language, no comparison-table screenshot, no CC lists.
- Do not time the send to a launch - the 10 days must be real.
- Every claim in the email must be true on the day it's sent (harness public,
  column linkable). Publish the methodology post first if it isn't.
