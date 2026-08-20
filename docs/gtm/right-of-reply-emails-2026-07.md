# Right-of-Reply Emails - Final Copy (send 2026-07-03)

*Ready to send. Fill only the recipient address (verify on their site/GitHub;
a GitHub issue on their repo is the documented public fallback). Reply window:
**end of day Friday, July 17, 2026** (10 business days from July 3).
Send from your personal address, signed with your name. After sending, log
date + channel in `vendor-right-of-reply.md` - the log is part of the
published methodology.*

Common links (already public on master):

- Results + methodology + live-run appendix: <https://github.com/Lians-ai/Lians/blob/master/docs/regulated-eval-results.md>
- Harness + adapters: <https://github.com/Lians-ai/Lians/tree/master/agentmem/benchmarks>

---

## 1. Mem0

**To:** (verify - founders@mem0.ai / GitHub) · **Subject:** `Fact-check request: how we scored mem0 in an open compliance benchmark`

> Hi Mem0 team,
>
> I'm Ethan, the author of Lians - an open-source agent-memory layer aimed at
> regulated deployments (finance/healthcare/legal). Honest context first: your
> ecosystem is the reason "agent memory" is a category a buyer can search for
> at all - our own comparison docs say so explicitly, and our migration guide
> assumes people start with mem0 for good reasons.
>
> We're publishing an evaluation that scores memory systems on five
> compliance-specific invariants that LoCoMo/LongMemEval don't cover:
> stale-revision suppression, point-in-time (as-of) recall, provable erasure,
> lookahead/backtest guards, and audit-state reconstruction. Lians is scored
> by the same harness, same knife.
>
> Your column was **executed live** - mem0 OSS 2.0.11 in its default
> configuration (OpenAI LLM + embeddings), not scored from docs. Two things
> from that run you should see before anyone else does:
>
> 1. The results, methodology, per-cell evidence, and your column's exact
>    adapter code:
>    https://github.com/Lians-ai/Lians/blob/master/docs/regulated-eval-results.md
>    https://github.com/Lians-ai/Lians/blob/master/agentmem/benchmarks/adapters/mem0_adapter.py
> 2. While setting up the run we hit a bug in the OSS defaults (default model
>    resolves to gpt-5-mini, which rejects the default temperature=0.1, so
>    default-config add() silently stores nothing). We filed it with a repro
>    and workaround: https://github.com/mem0ai/mem0/issues/6085 - and pinned
>    gpt-4o-mini in our run so your column wasn't scored on a broken default.
>
> Two asks, both optional:
>
> - If any cell is wrong - an API we missed, a configuration that changes the
>   outcome - tell me and I'll fix it before wider publication and credit the
>   correction to your team by name.
> - If you'd rather run the harness yourselves, the adapter interface is six
>   methods; PRs against your own adapter are welcome and will be merged.
>
> If I don't hear back by **Friday, July 17**, we'll publish with the column
> as-is and a note that you were offered review on July 3. Either way the
> harness stays open and your column stays re-runnable - corrections are
> welcome after publication too.
>
> Respect for what you've built,
> Ethan Beirne
> https://github.com/Lians-ai/Lians · ethan.g.beirne@gmail.com

---

## 2. Zep

**To:** (verify - founders@getzep.com / GitHub) · **Subject:** `Fact-check request: how we scored Graphiti in an open compliance benchmark`

> Hi Zep team,
>
> I'm Ethan, the author of Lians - an open-source agent-memory layer aimed at
> regulated deployments. Honest context first: Graphiti's bitemporal edge
> model is genuinely good engineering - our own relationship graph credits it
> as the direct inspiration, in our docs, by name.
>
> We're publishing an evaluation that scores memory systems on five
> compliance-specific invariants that LoCoMo/LongMemEval don't cover:
> stale-revision suppression, point-in-time (as-of) recall, provable erasure,
> lookahead/backtest guards, and audit-state reconstruction. Lians is scored
> by the same harness, same knife.
>
> Your column was **executed live** - Graphiti OSS 0.29.2 in its default
> OpenAI configuration on embedded Kuzu - and I want to flag that the run
> *confirmed* your documented strengths rather than discounting them: your
> contradiction invalidation fired and correctly backdated `invalid_at` to
> the revision's reference time, which we credit in print. The gap we scored
> is that default search returns invalidated edges, so suppression isn't
> turnkey for a caller assembling agent context. Results, per-cell evidence,
> and your column's exact adapter code:
>
> https://github.com/Lians-ai/Lians/blob/master/docs/regulated-eval-results.md
> https://github.com/Lians-ai/Lians/blob/master/agentmem/benchmarks/adapters/graphiti_oss_adapter.py
>
> While setting up the run we also hit the two Kuzu-driver issues already
> tracked in getzep/graphiti#1258 and confirmed they persist in 0.29.2, with
> repros and complete workarounds:
> https://github.com/getzep/graphiti/issues/1258#issuecomment-4880328136
>
> Two asks, both optional:
>
> - If any cell is wrong - an API we missed, a search configuration that
>   filters invalidated edges by default, anything - tell me and I'll fix it
>   before wider publication and credit the correction to your team by name.
>   I'd particularly value your read on whether we've drawn the line between
>   validity windows and two-axis point-in-time recall fairly.
> - If you'd rather run the harness yourselves, the adapter interface is six
>   methods; PRs against your own adapter are welcome and will be merged.
>
> If I don't hear back by **Friday, July 17**, we'll publish with the column
> as-is and a note that you were offered review on July 3. Either way the
> harness stays open and your column stays re-runnable.
>
> Respect for what you've built,
> Ethan Beirne
> https://github.com/Lians-ai/Lians · ethan.g.beirne@gmail.com

---

## 3. Letta

**To:** (verify - contact@letta.com / GitHub) · **Subject:** `Fact-check request: how we scored Letta in an open compliance benchmark`

> Hi Letta team,
>
> I'm Ethan, the author of Lians - an open-source agent-memory layer aimed at
> regulated deployments. The MemGPT lineage is the most original architecture
> in this space, and I want to be upfront that our eval necessarily scores
> *turnkey compliance primitives* rather than agent-managed memory - if that
> framing misrepresents what Letta is for, I'd rather hear it from you than
> from your users after publication.
>
> The eval scores five invariants (stale-revision suppression, point-in-time
> recall, provable erasure, lookahead guards, audit-state reconstruction)
> across six systems, Lians included, same harness for everyone. Your column
> is currently capability-assessed from your public API surface, with a
> one-line justification per cell in the adapter:
>
> https://github.com/Lians-ai/Lians/blob/master/docs/regulated-eval-results.md
> https://github.com/Lians-ai/Lians/blob/master/agentmem/benchmarks/adapters/letta_adapter.py
>
> If any cell is wrong, tell me and I'll fix it before wider publication and
> credit your team by name. If you'd rather run it live, export LETTA_API_KEY
> and the harness executes your column; adapter PRs are welcome and will be
> merged. If I don't hear back by **Friday, July 17**, we'll publish with a
> note that you were offered review on July 3 - corrections stay welcome
> after publication too.
>
> Respect for what you've built,
> Ethan Beirne
> https://github.com/Lians-ai/Lians · ethan.g.beirne@gmail.com

---

## 4. Hindsight

**To:** (find via site/GitHub) · **Subject:** `Fact-check request: how we scored Hindsight in an open compliance benchmark`

> Hi Hindsight team,
>
> I'm Ethan, the author of Lians - an open-source agent-memory layer aimed at
> regulated deployments. Your temporal retrieval work is the closest in
> spirit to ours in this category, which is exactly why I want your column
> checked before anyone else sees it.
>
> The eval scores five compliance invariants (stale-revision suppression,
> point-in-time recall, provable erasure, lookahead guards, audit-state
> reconstruction) across six systems, Lians included, same harness for all.
> Your column is capability-assessed from your public API surface:
>
> https://github.com/Lians-ai/Lians/blob/master/docs/regulated-eval-results.md
> https://github.com/Lians-ai/Lians/blob/master/agentmem/benchmarks/adapters/hindsight_adapter.py
>
> The one cell I most want checked: **erasure**. As of our review we found no
> deletion API at all, so that cell is scored absent rather than partial - if
> that's wrong or has changed, I'd genuinely rather fix it than publish it.
>
> If any cell is wrong, I'll correct it before wider publication and credit
> your team by name; adapter PRs are welcome. If I don't hear back by
> **Friday, July 17**, we'll publish with a note that you were offered review
> on July 3.
>
> Respect for what you've built,
> Ethan Beirne
> https://github.com/Lians-ai/Lians · ethan.g.beirne@gmail.com

---

## 5. Supermemory

**To:** (find via site/GitHub) · **Subject:** `Fact-check request: how we scored Supermemory in an open compliance benchmark`

> Hi Supermemory team,
>
> I'm Ethan, the author of Lians - an open-source agent-memory layer aimed at
> regulated deployments. We're publishing an evaluation that scores memory
> systems on five compliance invariants (stale-revision suppression,
> point-in-time recall, provable erasure, lookahead guards, audit-state
> reconstruction) across six systems, ourselves included, same harness for
> everyone.
>
> Your column is capability-assessed from your public API surface - your
> profile consolidation earned the supersession partial-credit cell; the
> other cells are structural API checks, each with a one-line justification:
>
> https://github.com/Lians-ai/Lians/blob/master/docs/regulated-eval-results.md
> https://github.com/Lians-ai/Lians/blob/master/agentmem/benchmarks/adapters/supermemory_adapter.py
>
> If any cell is wrong, tell me and I'll fix it before wider publication and
> credit your team by name. To run it live: export SUPERMEMORY_API_KEY and
> the harness executes your column; adapter PRs are welcome. If I don't hear
> back by **Friday, July 17**, we'll publish with a note that you were
> offered review on July 3.
>
> Respect for what you've built,
> Ethan Beirne
> https://github.com/Lians-ai/Lians · ethan.g.beirne@gmail.com
