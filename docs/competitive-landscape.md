# Lians Guard competitive landscape

Last reviewed: 2026-08-19 against current official product documentation.

## Bottom line

Generic agent memory and session continuity are no longer defensible categories
by themselves. Claude Code, Cursor, GitHub Copilot, Codex, Entire, and Factory
already provide important parts of memory, checkpoints, context, or session
recovery. Lians should use local recovery as distribution, then compete on a
narrower contract:

> Recover the current task. Reject stale evidence. Block unsupported done.

The product wins only if it becomes the neutral evidence boundary across agent,
repository, CI, and human review. It loses if it becomes another transcript
viewer, generic memory database, or agent dashboard.

## Direct pressure

| Product | Current strength | Pressure on Lians | Required Lians answer |
|---|---|---|---|
| Claude Code | Project memory, automatic checkpoints, rewind, and lifecycle hooks including `PreCompact` and `SessionEnd` | Native distribution and no extra install | Capture only supported lifecycle events, cover current repository state, and treat Bash or external changes as evidence gaps |
| GitHub Copilot | Repository memories with code citations that are revalidated against the current branch | Native repository distribution and source-grounded freshness | Match citation and freshness discipline, then add task-level readiness and cross-agent evidence |
| Cursor | Project rules, automatic memories, and background agents | Strong native workflow and automatic context | Make Lians useful in minutes without replacing Cursor, and keep the completion boundary provider-neutral |
| OpenAI Codex | Parallel agent work, skills, automations, and durable teammate workflows | Strong task orchestration inside the product | Complement Codex with inspectable local state and attested external evidence, not a replacement chat interface |
| Entire | Git-linked checkpoints with prompts, tool calls, session history, and resume across agents | Strongest direct pressure on session capture and continuity | Avoid competing on recording alone; own stale-state invalidation and evidence-backed readiness |
| Factory Droids | Model-neutral agents across terminal, IDE, browser, and collaboration tools with persistent context | Strong cross-tool execution and enterprise posture | Own the evidence gate around work performed by any agent |
| Graphite with Cursor | AI code creation and review in one integrated workflow | Review is moving closer to generation and distribution is consolidating | Feed reliable CI and task evidence into review rather than trying to replace the review surface |

Official references:

- Claude Code [hooks](https://code.claude.com/docs/en/hooks),
  [checkpointing](https://code.claude.com/docs/en/checkpointing), and
  [memory](https://code.claude.com/docs/en/memory)
- GitHub Copilot [memory](https://docs.github.com/en/copilot/concepts/agents/copilot-memory)
- Cursor [memories](https://docs.cursor.com/en/context/memories),
  [rules](https://docs.cursor.com/context/rules), and
  [background agents](https://docs.cursor.com/background-agent)
- OpenAI Codex [use cases](https://developers.openai.com/codex/use-cases)
- Entire [product](https://entire.io/) and [overview](https://docs.entire.io/overview)
- Factory [Droids](https://factory.ai/product/droids)
- Graphite [AI reviews](https://graphite.com/docs/ai-reviews) and
  [joining Cursor](https://graphite.com/blog/graphite-joins-cursor)

## Table stakes

These capabilities are necessary but cannot carry the category claim:

- persistent memory;
- transcript or tool-call capture;
- MCP compatibility;
- a Git checkpoint;
- a project instruction file;
- a context summary;
- a dashboard;
- local-first storage by itself; and
- an agent saying that tests passed.

## Scarce differentiation

Lians can build a defensible wedge from five connected behaviors:

1. **A hard trust boundary.** Agent-facing tools cannot promote their own text to
   measured or human-confirmed evidence.
2. **Current-state binding.** Evidence is tied to an exact repository state and
   becomes stale when that state changes.
3. **Provider neutrality.** Claude Code, Codex, GitHub Actions, and future agents
   write into one bounded task contract without becoming the authority over it.
4. **Inspectable readiness.** The user sees why a task is recovered, stale,
   blocked, or ready for human review.
5. **A failure corpus.** Repeated real-world stale states, false completion
   claims, recoveries, and review corrections become a private evaluation asset.

The moat is not stored text. It is the normalized evidence model, invalidation
logic, trusted issuers, customer policies, and the growing corpus of failures
that Lians catches across tools.

## Expansion path

The repository already contains advanced temporal, audit, erasure, isolation,
and regulated-memory capabilities. Those can become an enterprise expansion
path after the Guard loop earns repeated use. They should not make the first
install, first message, or first sales conversation harder to understand.

## Positioning line

Use this in product and sales material:

> Lians Guard is the neutral current-state and completion guard for AI coding
> agents. It recovers the current task, rejects stale evidence, and sends only
> supported work to human review.
