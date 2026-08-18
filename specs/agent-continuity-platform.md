# Lians Personal Agent Control and Intelligence Platform

## North star

Lians gives individuals a living map and direct control over AI-assisted work inside the
agents they already use. It helps people complete longer, higher-quality work with less
repetition, fewer stale assumptions, clearer decisions, visible evidence, and explicit control
over what an agent receives and which actions require approval.

Token reduction is one measurable benefit. It is not the product boundary.

Lians is not a hosted model proxy, replacement chat client, or model reseller. It integrates
through native MCP, hook, rule, and extension surfaces. The user continues working in their
chosen agent while Lians supplies an independent local control, continuity, and observability
layer.

## The beta control contract

- **Observe** maps content-free local agent activity and does not inject Lians context.
- **Guide** supplies bounded current state and the applicable execution contract.
- **Protect** adds explicit user approval boundaries for selected high-impact actions.
- Every mode remains exportable, inspectable, and reversible.
- Lians reports whether an integration can technically enforce an action or can only provide
  explicit guidance. It never describes prompt guidance as host enforcement.
- The 3D Work Graph exposes projects, tasks, sessions, decisions, memories, evidence, blockers,
  lineage, and recall receipts. Verified edges remain distinct from optional inferred edges.

## The first problem to own

Agents do not share a durable definition of done. Across long sessions and client changes they
lose the original goal, silently drop constraints, repeat completed work, and may declare success
without evidence. Memory retrieval alone cannot decide whether work is complete, and a corpus
graph alone cannot represent the user's current execution contract.

Lians makes every substantial task carry one encrypted, temporal contract:

- the goal;
- explicit success criteria;
- constraints that must remain true;
- the current action and blockers;
- evidence for every completed criterion;
- signed bounded context for the next agent;
- a completion gate that remains closed while evidence is missing or a constraint is unresolved.

The dependency is earned through recovery and correctness. Removing Lians should mean losing the
ability to prove where a task stands, not merely losing a convenient chat summary.

## The user outcomes

1. **Start with the right context**
   - Continue an existing task in a new chat or another agent without reconstructing it.
   - Receive only the relevant preferences, constraints, decisions, evidence, and next steps.
   - See why every item was selected.

2. **Keep changing work correct**
   - Maintain one named current value for every important decision, fact, constraint, or plan.
   - Preserve prior versions without allowing stale updates to silently become current.
   - Answer both “what was true then?” and “what did the agent know then?”

3. **Turn sessions into durable progress**
   - Capture goals, completed work, unresolved questions, blockers, artifacts, and next actions.
   - Generate compact handoffs that work across Claude, Codex, Cursor, Gemini, Copilot, and
     other agent clients.
   - Resume from an interrupted or failed session without rereading the full transcript.

4. **Improve execution quality**
   - Attach success criteria and constraints to a task before an agent acts.
   - Compare the result with those criteria when the task ends.
   - Surface contradictions, missing evidence, repeated failures, and decisions that need review.

5. **Keep the human in control**
   - Inspect, correct, pause, rescope, export, or erase remembered information.
   - Treat all recalled content as untrusted evidence rather than system instructions.
   - Require explicit approval for high-impact actions and block secrets from memory.

6. **Make improvement measurable**
   - Report workload-scoped context delivered, repeated context avoided, successful handoffs,
     stale-state interceptions, corrections, and task outcomes.
   - Never imply that Lians enlarges a provider context window or subscription quota.
   - Separate estimates from directly observed provider measurements.

7. **Work for individuals and teams**
   - Keep local-first personal memory simple enough for a nontechnical user.
   - Add encrypted synchronization, access boundaries, review queues, and shared project state
     when a team chooses managed operation.

8. **Help people think and act better**
   - Turn an unclear request into a goal, constraints, success criteria, and a visible plan.
   - Suggest decomposition, parallel work, missing evidence, and decisions without silently taking
     control away from the user.
   - Show the connected work map so a person can inspect what their agents know, changed, used,
     and still need to finish.

## Technical planes

### Continuity plane

- Encrypted local memory
- Stable project identity
- Named current state
- Bitemporal history
- Cross-agent handoffs
- Bounded context compiler

### Execution plane

- Goal and success-criteria envelopes
- Active plan and next-action state
- Artifact and source references
- Failure recovery checkpoints
- Human approval boundaries

### Evidence plane

- Signed context receipts
- Recall reasons and exclusions
- Source lineage
- Correction and supersession chains
- Deletion and erasure evidence

### Improvement plane

- Workload-scoped token and context measurements
- Task outcome evaluation
- Repeated-failure and contradiction detection
- User feedback on useful or harmful recall
- Private, inspectable optimization suggestions

### Intelligence plane

- Deterministic indexing and exact state resolution on the latency-critical path
- Optional on-device neural embeddings for semantic recall, clustering, and duplicate detection
- A small reranker that scores candidates but never overrides scope, deletion, or temporal truth
- Suggested semantic graph links kept visually and structurally separate from verified links
- Task routing between direct, bounded, parallel, and deep-reasoning paths
- Asynchronous learning from explicit user feedback rather than invisible model-side memory

### Visualization plane

- A local work map of projects, agents, current state, prior versions, topics, and recall receipts
- Provenance edges for created-by, supersedes, recalled-in, and belongs-to relationships
- Optional neural similarity edges with visible confidence and method labels
- Filters for current, historical, blocked, completed, and uncertain work
- No requirement to send the graph or its content to a hosted model

### Latency plane

- Exact lookups and client hook response remain deterministic and bounded
- Neural indexing, clustering, and evaluation run asynchronously or on idle resources
- Cache verified task briefs, tool schemas, and stable retrieval results by content hash
- Parallelize independent retrieval and validation steps under a shared deadline
- Route easy work to fast paths and reserve expensive reasoning for high-impact decisions
- Measure end-to-end task completion time, not just model response latency

### Integration plane

- MCP as the universal baseline
- Native hooks or rules where clients support them
- One shared encrypted store across clients
- Capability detection instead of hard-coded assumptions
- Fail-open host hooks with bounded latency and no secret leakage

## Product sequence

### P0: Trustworthy continuity

- Named current decisions and constraints
- Stale-update rejection
- Version history
- Factual-time and knowledge-time queries
- Signed lineage in every recall receipt
- Encrypted backup and synchronization preservation

### P1: Session intelligence

- Automatic end-of-session brief
- Completed work, open work, blockers, and next actions
- Resumable checkpoints for long browser, coding, and research workflows
- One-click continuation in another connected agent

### P2: Outcome intelligence

- Task goals and success criteria
- Result-versus-goal review
- Evidence coverage and uncertainty markers
- User feedback that improves future selection without hiding why

### P2.5: Visual and neural intelligence

- Interactive local work map backed by explicit provenance and temporal lineage
- On-device embedding model behind an optional, replaceable encoder interface
- Semantic clusters and duplicate suggestions shown as suggestions, never facts
- Latency budget with fast-path recall unaffected when the encoder is cold or unavailable

### P3: Safe action control

- Action risk classification
- Per-project approval rules
- Secret and sensitive-data boundaries
- Preview, execute, verify, and rollback receipts where the client permits them

### P4: Team continuity

- Shared project state with personal and team boundaries
- Conflicting update review
- Ownership and approval metadata
- Encrypted cross-device synchronization and auditable erasure

### P5: Universal usability

- Automatic client detection and configuration
- Plain-language desktop setup
- Accessible UI and keyboard navigation
- Import from transcripts, task logs, and compatible project knowledge systems

## Competitive boundary

Knowledge-graph products map the structure of a corpus. Lians carries the evolving state of
work across time and agents. Lians may ingest or link to a project graph, but it should not
make generic graph construction its primary identity.

The durable position is:

> Project tools describe what exists. Lians remembers what changed, why it changed, what is
> current, and what the next agent needs.

The performance promise is not “a slower provider model becomes a faster model.” Lians reduces
avoidable work around the model so a task can finish faster and with fewer restarts. Provider
inference speed, context-window size, and subscription quotas remain outside Lians' control.

### Patterns worth adopting without copying product identity

From [Graphify](https://github.com/Graphify-Labs/graphify):

- deterministic local extraction before expensive semantic work;
- explicit versus inferred relationship labels;
- content-hash caching and incremental updates;
- graph traversal, paths, communities, and inspectable local visualization;
- benchmark artifacts users can reproduce.

From [Mem0](https://github.com/mem0ai/mem0):

- user, agent, and run scoping;
- semantic, lexical, and entity retrieval signals fused instead of trusting one score;
- async variants for expensive work;
- pluggable storage, embedding, and reranking components;
- temporal retrieval and agent-generated facts as first-class inputs.

Lians must add what neither pattern provides by itself: current-state correctness, definitions of
done, evidence gates, stale-agent rejection, recovery checkpoints, action approval, and signed
handoffs across unrelated agent products.

## Release gates

No capability is marketed as production-ready until it has:

- a stable public contract;
- adversarial tests for stale, conflicting, scoped, and erased state;
- upgrade and backup preservation tests;
- bounded latency or an explicit asynchronous path;
- a documented claim boundary;
- a reproducible workload result or an honest “not yet measured” label.
