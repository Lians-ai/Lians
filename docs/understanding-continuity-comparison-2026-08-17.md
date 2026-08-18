# Lians understanding and continuity comparison

Date checked: 2026-08-17

This is a capability comparison based on current official documentation. It is
not a head-to-head quality benchmark. The only executed result in this update
is Lians against prompt-only and full-replay strategies on the checked local
fixture in
`packages/lians-easy/benchmarks/understanding-continuity-report.json`.

## The position

**Continuity is the product. Deeper understanding is the promise.**

Lians should not become another chat interface, hosted model proxy, generic
vector database, or code graph. It should sit inside the agents a person
already uses and give each one the same compact understanding of:

1. who the user is;
2. what they are trying to achieve now;
3. what changed since the last session;
4. what evidence and constraints matter;
5. which single missing answer would change the next action.

The memory system is the mechanism. Reduced replay is an economic benefit.
The customer outcome is that their agent understands the work sooner and keeps
understanding it across tools and time.

## Current capability map

| Product | Strongest documented capability | Where it is ahead of Lians | Where Lians is intentionally different |
| --- | --- | --- | --- |
| Letta | Stateful agent runtime with always-visible editable memory blocks, files, archival memory, external RAG, tools, and a full agent development environment | More mature agent runtime, context hierarchy, continual-learning story, and developer platform | Lians stays underneath Claude, Codex, Cursor, and other existing agents instead of asking the user to adopt a new agent runtime. Its local Understanding Brief, task contract, approval policy, and cross-agent receipt are the differentiator |
| Mem0 | Managed memory extraction and retrieval with automatic entity graph links, semantic, keyword, and graph ranking | More mature automatic extraction, semantic retrieval, entity linking, hosted APIs, and graph view | Lians local mode needs no account, provider key, model proxy, or graph service. It exposes exactly why bounded context was selected and keeps prompt understanding local |
| Supermemory | Integrated memory, RAG, user profiles, connectors, file processing, contradiction handling, and multimodal extraction | Broader context stack, connectors, automated profiles, multimodal processing, and published memory benchmark focus | Lians is a native companion and control layer for individual users across existing agent apps. It adds relevant-question gating, task completion evidence, and local policy controls rather than becoming a hosted context API |
| Graphiti | Incremental temporal context graphs with provenance, validity windows, hybrid semantic, keyword, and graph retrieval | Far richer entity and relationship extraction, temporal graph traversal, ontologies, and large-scale retrieval | Lians uses a smaller encrypted temporal store that installs with the desktop companion. Its graph is a user-facing map of work, agents, tasks, evidence, and memory rather than an enterprise knowledge-graph engine |
| Graphify | Deterministic local AST extraction, explainable extracted vs inferred edges, code and document maps, path queries, and generated project graphs | Much stronger codebase understanding, structural parsing, and code relationship visualization | Lians maps continuity across tasks and agents. Graphify maps the codebase. They are complementary, and a Graphify subgraph can become evidence inside a Lians task rather than being reimplemented |
| Pieces | On-device long-term memory running in the background with desktop, browser, IDE, and MCP integrations plus per-app capture controls | More mature ambient capture and broad day-to-day activity history | Lians should remain deliberate and bounded. It stores explicit durable facts, current state, task evidence, and content-free observations instead of recording everything a user does |

## What shipped in this beta update

- `understand_request`: classifies the work, uses bounded current memory, reports
  what is already known, and returns at most three questions. The request is
  not stored and no external model is called.
- Hook-level question gating: clear requests proceed. A connected agent is
  instructed to ask one question only when the request lacks a reliable
  outcome and no task contract already resolves it.
- Four memory layers: identity, working state, episodic handoffs, and knowledge.
- Diversity-aware context selection: one small identity lane cannot consume an
  entire three-memory pack.
- Query stopword removal and small intent expansions for research, writing,
  building, learning, and planning.
- `memory_health`: read-only duplicate, scope, size, staleness, and versioning
  diagnostics. It never silently consolidates or deletes user memory.
- Desktop **Understand** view with the same local brief and a memory health
  score.

## Executed strategy comparison

Checked fixture result:

| Strategy | Estimated context tokens | Essential fact recall | Useful question precision |
| --- | ---: | ---: | ---: |
| Prompt only | 0 | 0% | N/A |
| Full replay | 2,913 | 100% | N/A |
| Lians bounded understanding | 736 | 100% | 100% |

On this fixture, Lians used 25.3% of full-replay context. The fixture is small,
deterministic, and model-free. It does not establish provider-billed savings,
answer quality, or superiority over another memory product. The next fair gate
is a blinded provider-backed run with real user tasks and the same model,
prompts, budgets, and judge across strategies.

## Sources checked

- Letta context hierarchy: <https://docs.letta.com/guides/core-concepts/memory/context-hierarchy>
- Letta memory blocks: <https://docs.letta.com/guides/core-concepts/memory/memory-blocks>
- Letta platform and agent surfaces: <https://docs.letta.com/guides/get-started/intro>
- Mem0 Graph Memory: <https://docs.mem0.ai/platform/features/graph-memory>
- Mem0 memory history: <https://docs.mem0.ai/api-reference/memory/history-memory>
- Mem0 self-hosted feature overview: <https://docs.mem0.ai/open-source/features/overview>
- Supermemory repository: <https://github.com/supermemoryai/supermemory>
- Graphiti repository: <https://github.com/getzep/graphiti>
- Graphify repository: <https://github.com/Graphify-Labs/graphify>
- Graphify implementation notes: <https://github.com/Graphify-Labs/graphify/blob/v8/docs/how-it-works.md>
- Pieces product documentation: <https://docs.pieces.app/>
- PiecesOS access controls: <https://docs.pieces.app/products/core-dependencies/pieces-os/quick-menu>
