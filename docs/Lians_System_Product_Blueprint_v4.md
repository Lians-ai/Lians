# Lians System Product Blueprint (v4)

Updated: July 26, 2026
Supersedes: Lians System Product Blueprint (v3), July 26, 2026

---

## Direction in four sentences

Lians helps people see what their AI remembers, decide whether it is trustworthy, and understand what could change before they correct or delete it. The first universal experience is a Personal Memory Checkup that explains stale, conflicting, duplicate, unsourced, or broken memories in everyday language. Underneath it, the same Memory Graph connects users, sources, documents, API calls, agents, decisions, actions, and outcomes for developers and regulated organizations. We will validate the personal checkup with one small user group while continuing the financial-services Decision Explorer as the focused enterprise path.

---

## What changed in this revision

| Change | Reason |
| --- | --- |
| Expanded the long-term audience | Lians can make decision systems understandable to learners while retaining the depth required by regulated institutions and expert architects. |
| Preserved financial services as the commercial wedge | A universal product vision does not require an unfocused launch. Regulated financial services still has the clearest immediate need and willingness to pay. |
| Reframed Lians as a decision-systems platform | Evidence is valuable not only for compliance, but also for education, debugging, reproducible science, safety, testing, and collaboration. |
| Added five progressive experiences | Learn, Build, Operate, Govern, and Architect provide different levels of complexity over one shared platform. |
| Introduced a universal decision model | The core model now works across classroom experiments, personal projects, production agents, and regulated systems. |
| Separated reconstruction, simulation, and comparison | These capabilities have different determinism and trust properties and should not be hidden under an ambiguous replay claim. |
| Added capture completeness and provenance strength | Lians must prove what it actually observed and clearly disclose what it could not observe. |
| Added memory lineage and impact analysis | Every memory should connect to its sources, consumers, decisions, and outcomes so users can see what a change could affect. |
| Added transparent memory-health diagnostics | Duplicate, conflicting, stale, unsourced, and broken memories should be detected with explainable findings and corrective actions. |
| Added Personal Memory Checkup | The universal vision now begins with one concrete experience that a nontechnical user can understand. |
| Added a consumer validation gate | Broad development starts only after users demonstrate comprehension, trust, successful correction, and repeat value. |
| Separated entry motions | Personal adoption and regulated-enterprise sales share a core but not positioning, onboarding, or release timing. |
| Retained the immediate five-screen build | The expanded vision does not change the near-term engineering commitment. |

---

## How to use this document

Sections 1 through 8 describe the long-term platform and should guide product language, architecture, hiring, partnerships, and investment decisions.

Section 9 defines validation and the immediate builds. It is the active product and engineering plan. Nothing else in this document is automatically authorized for implementation.

Sections 10 through 13 describe expansion triggers, commercial strategy, success measures, and guardrails. New experiences begin only when the shared core works and real users validate the next need.

The company is pursuing broad usefulness through a narrow personal prototype and a narrow enterprise workflow. Both must prove recurring value before the platform expands.

---

# Part One: Product

## 1. Definition

### Universal one sentence

Lians shows what your AI remembers, whether you can trust it, and what could be affected when it changes.

### Personal promise

Know what your AI knows about you—and fix it before it causes a bad answer.

### Enterprise promise

Prove what information was available to an AI system, what it used, and what it did at the time of a consequential decision.

### Category

AI memory intelligence and decision-systems platform.

### What Lians is

Lians is a shared memory, record, and exploration layer for intelligent decisions. It connects users, memories, documents, sources, API calls, agents, rules, models, tools, decisions, actions, and outcomes into a history people can understand and machines can verify.

The platform serves four complementary purposes:

1. **Personal purpose:** help ordinary people understand and control what AI remembers about them.
2. **Universal purpose:** help people learn, build, debug, test, and understand decision systems.
3. **Consequential-systems purpose:** help organizations operate, review, and defend decisions that affect money, rights, safety, access, or opportunity.
4. **Continuous-quality purpose:** keep AI memory accurate, connected, current, and safe before defects become bad decisions.

### What Lians is not

Lians is not a general chat application, an AI model provider, a generic observability dashboard, or a compliance certificate. It does not claim access to hidden model cognition. It records and explains observable decision context and behavior.

### Initial commercial target

The first paying customers are regulated financial organizations where an examiner, auditor, insurer, customer, opposing party, or court may compel an explanation.

Likely initial users include:

- Risk and compliance reviewers
- AI and quantitative developers
- Model validation teams
- Internal auditors
- Legal and investigation teams
- AI product and operations managers

The first demonstrated workflow is detecting future-information contamination in a quantitative backtest, reconstructing the correct historical context, comparing outcomes, and exporting a verifiable receipt.

### Initial personal target

The first personal users are people who repeatedly use AI for school, work, planning, research, or organization but cannot easily answer:

- What does it remember about me?
- Which facts are wrong or outdated?
- Where did those facts come from?
- Which memories disagree?
- What will change if I correct or remove one?

Start with one bounded research group rather than “everyone.” Good candidates are college students, independent professionals, or people who actively use more than one AI assistant. Middle-school deployment comes later because it requires stronger privacy, consent, safety, and classroom controls.

### Long-term audience

The long-term platform supports:

- Middle-school and high-school students
- Teachers and curriculum designers
- University students and researchers
- Hobbyists and independent builders
- Software and AI developers
- Product and operations teams
- Risk, compliance, legal, and audit teams
- Advanced systems and security architects

### Product structure

| Layer | Product | Purpose |
| --- | --- | --- |
| Shared record layer | Lians Core | Capture and preserve decision context, behavior, and outcomes |
| Memory intelligence layer | Lians Memory Graph | Connect memories to their origins, consumers, decisions, and downstream effects |
| Personal layer | Personal Memory Checkup | Help nontechnical users review and safely correct AI memory |
| Learning layer | Lians Learn | Teach decision systems through visual, safe experiments |
| Creation layer | Lians Build | Build, debug, test, and compare intelligent systems |
| Production layer | Lians Operate | Monitor decisions, quality, incidents, and human review |
| Assurance layer | Lians Govern | Apply controls and produce defensible evidence |
| Expert layer | Lians Architect | Configure schemas, policies, topology, storage, and trust boundaries |
| Distribution layer | Lians Cloud and integrations | Make installation, collaboration, deployment, and procurement practical |

---

## 2. Product principles

### 2.1 Start with the decision

The primary object is a decision: a recommendation, classification, selection, approval, denial, escalation, or action. Records, traces, memories, prompts, spans, and tool calls exist to explain the decision.

### 2.2 One core, progressive depth

A student, developer, and auditor should examine the same underlying event without being forced into the same interface. Complexity is progressively disclosed rather than removed from the record.

### 2.3 Make the causal path visible

Every decision should connect:

```text
Goal → Input → Information → Process → Decision → Action → Outcome
```

Lians must distinguish information that was available from information that was retrieved, passed to a model, cited, evaluated by policy, or used by a tool.

### 2.4 Preserve technical truth

Simple presentation must not weaken temporal accuracy, source lineage, permissions, provenance, integrity, or capture-status disclosure.

### 2.5 Never overstate observation

Lians proves what it recorded within a defined capture boundary. It must never imply that it observed hidden model cognition or uninstrumented activity.

### 2.6 Separate evidence from explanation

Observed events, supplied evidence, model-generated explanations, human statements, and derived analysis are visibly distinct.

### 2.7 Make experimentation safe

Users should be able to change a source, fact, model, policy, or cutoff in an isolated environment without modifying the original record or triggering real-world actions.

### 2.8 Make every screen useful

Every important screen should answer a question, enable an experiment, resolve an issue, compare outcomes, assign work, or produce a verifiable artifact.

### 2.9 Support adoption before procurement

Learners and developers should be able to run Lians locally and understand a real decision without a sales conversation. Enterprise governance and collaboration remain paid outcomes.

### 2.10 Treat compliance as an outcome, never a claim

Lians provides evidence and controls that support review. It does not make an organization compliant.

### 2.11 Broad platform, narrow launch

The architecture may serve many audiences, but each release pursues one audience and one complete workflow at a time.

### 2.12 Diagnose, do not gamify

Health scores summarize evidence-backed diagnostics. Every score must expose its contributing findings, confidence, affected objects, and recommended action. Lians should never use an opaque number to create artificial urgency.

### 2.13 Show downstream impact before change

Before a memory is edited, superseded, or deleted, Lians should show the users, agents, tests, decisions, actions, and derived memories that may be affected. Historical records remain intact even when active memory changes.

### 2.14 Earn trust before asking for data

Personal Lians must clearly explain what it imports, where it is stored, what leaves the device, and how deletion works. Local processing should be the default where practical, and everyone should be able to try a realistic sample before connecting private information.

### 2.15 Design for return value

A one-time cleanup may not support a durable product. Lians should earn repeat use through meaningful change alerts, periodic checkups, pre-change impact previews, and demonstrably better AI answers—not artificial notifications or gamification.

---

## 3. Progressive experiences

### 3.0 Personal Memory Checkup

**Audience:** nontechnical people who regularly use AI for everyday tasks.

**Purpose:** provide a safe, understandable place to inspect and improve the information AI uses about the user and their projects.

**Core experience:**

1. Start with a guided example or import a supported memory file.
2. See remembered facts as plain-language cards.
3. Review stale, duplicate, conflicting, unsourced, or broken items.
4. See where each memory came from and where it may be used.
5. Preview the likely effects of a correction or deletion.
6. Approve a correction, supersession, or removal.
7. Receive an updated health summary and change record.

**Everyday categories:**

- Preferences and communication style
- School and learning
- Work and projects
- Goals and plans
- People and relationships
- Documents and research
- Sensitive information

Sensitive categories require stronger warnings and should never be inferred casually.

**Plain-language examples:**

- “Your AI remembers two different graduation dates.”
- “This preference came from an old conversation and may be outdated.”
- “Three study recommendations used this learning goal.”
- “This memory has no source, so Lians cannot verify where it came from.”
- “Removing this instruction may change how two assistants format their answers.”

**Not in the first version:**

- A universal connector for every AI platform
- Autonomous deletion from third-party services
- Medical, legal, or financial recommendations
- Public social profiles
- Classroom or child accounts
- An unexplained score presented as truth

### 3.1 Lians Learn

**Audience:** students, teachers, first-time programmers, and curious nontechnical users.

**Purpose:** make intelligent decisions visible and teach users how information, rules, models, tools, and human choices affect outcomes.

**Core experiences:**

- Visual decision blocks
- Visual memory maps
- Animated decision timelines
- Guided experiments and lessons
- Safe, simulated tools
- Change-one-thing comparisons
- Bias, privacy, provenance, and uncertainty exercises
- Reproducible experiment sharing
- Teacher assignments and classroom-safe project controls

**Example lesson:** An agent decides which plants need water. The learner discovers that tomorrow's weather was accidentally included in today's information, removes it, and compares the result.

**Beginner language:** goal, information, rule, decision, action, and result.

### 3.2 Lians Build

**Audience:** students moving into code, hobbyists, researchers, and professional developers.

**Purpose:** create, inspect, debug, evaluate, and reproduce intelligent applications.

**Core experiences:**

- Visual and code-based project creation
- Python and TypeScript SDKs
- Local-first development
- Decision debugger
- Prompt, memory, source, policy, and tool inspection
- Memory-health diagnostics
- Change-impact previews
- Recorded and mocked tool responses
- Scenario and regression tests
- Dataset, model, prompt, and policy comparisons
- Shareable decision receipts
- Templates for common systems

### 3.3 Lians Operate

**Audience:** AI product managers, production engineers, reviewers, and operations teams.

**Purpose:** understand and improve intelligent systems running in production.

**Core experiences:**

- Decision feeds and operational summaries
- Capture-health monitoring
- Memory-health and dependency monitoring
- Quality, consistency, latency, and cost views
- Human review queues
- Alerts and escalation
- Version-change analysis
- Incident investigation
- Production-safe comparisons

### 3.4 Lians Govern

**Audience:** risk, compliance, security, legal, audit, and investigation teams.

**Purpose:** control and defend consequential intelligent decisions.

**Core experiences:**

- Bitemporal evidence
- Memory lineage and downstream impact evidence
- Policy and approval enforcement
- Access controls and information barriers
- Retention and legal holds
- Tamper-evident records
- Investigations
- Evidence Packs
- Independent package verification
- Enterprise identity, keys, and private deployment

### 3.5 Lians Architect

**Audience:** advanced systems, data, security, and platform architects.

**Purpose:** configure and extend the platform for complex or high-assurance systems.

**Core experiences:**

- Custom event, decision, and evidence schemas
- Policy-as-code
- Temporal queries
- Lineage and system-topology views
- Dependency queries and impact-analysis policies
- Trust-boundary definitions
- Replay and simulation sandbox policies
- Pluggable storage and execution
- Cryptographic signing configuration
- Distributed capture diagnostics
- Extension and integration framework

---

## 4. Shared experience

### 4.1 Onboarding

Personal onboarding begins with value before configuration:

1. Run a sample checkup.
2. Choose local-only or clearly explained hosted processing.
3. Import a supported file or create a few memories manually.
4. Review the first three understandable findings.
5. Correct one item and see what improved.

Professional onboarding adapts its language and depth to the selected experience but asks the same underlying questions:

1. What system or experiment are you creating or connecting?
2. What goal is it trying to achieve?
3. What decisions can it make?
4. What information, rules, models, memories, and tools can it use?
5. Can it take actions, and which actions require human approval?
6. Who needs to learn from, review, or be alerted about its behavior?

**Initial connection options:** guided sample, JSON or CSV import, Python SDK, REST API, MCP host, and OpenTelemetry bridge.

**Later options:** TypeScript SDK, LangChain, LlamaIndex, webhooks, agent frameworks, and direct model-provider integrations.

**First-run success:** within ten minutes, a user sees one decision, its timeline, the information used, a safe comparison, and a shareable receipt.

**Personal first-run success:** within three minutes, a user understands one real memory problem and safely fixes or dismisses it.

### 4.2 Decision Explorer

The shared application begins with a searchable feed of decisions.

Common fields include decision ID, time, system, goal, action, subject, outcome, evidence status, capture status, model or rule version, approval status, risk status, and reviewer.

Views adapt by experience:

- Personal shows memory health and simple affected-result explanations.
- Learn shows experiments and outcomes.
- Build shows runs, tests, and debugging status.
- Operate shows production health and review queues.
- Govern shows control exceptions and evidence completeness.
- Architect shows capture topology, schemas, and trust boundaries.

For personal users, Decisions are secondary. The home screen is Memory Checkup; a decision appears only when it helps explain how a memory affected an answer, recommendation, plan, or action.

### 4.3 Decision detail

Every decision answers:

1. What was the goal?
2. What information was available?
3. What information was actually used?
4. Where did it come from?
5. What model, code, or rule processed it?
6. What decision and action followed?
7. What was the result?
8. What changed later?

The page contains:

- Plain-language summary
- Visual timeline
- Inputs and evidence
- Processing steps
- Decision and action
- Outcome
- Capture completeness
- Provenance labels
- Changes after the decision
- Available experiments or reviews

### 4.4 Memory Graph and health

Every memory is connected to:

- The user, system, or process it belongs to
- Its originating document, API response, tool result, human statement, or derived record
- Earlier memories it replaces, duplicates, conflicts with, or depends upon
- The agents, prompts, policies, and workflows that can retrieve it
- The decisions, actions, and outcomes in which it participated
- Tests, investigations, receipts, and exports that reference it

The Memory Graph supports two everyday questions:

1. **Can I trust this memory?**
2. **What might change if I edit, supersede, or delete it?**

Lians continuously checks for:

- Exact and semantic duplicates
- Conflicting facts
- Stale or expired information
- Missing or weak sources
- Broken references
- Invalid access scope
- Unreachable or orphaned memories
- Unexpectedly high downstream dependence
- Missing replacement records
- Facts used outside their valid time range

Each finding includes the evidence, severity, confidence, affected objects, and suggested correction.

### 4.5 Memory quality score

Lians may present an overall health score out of 100 for quick orientation, but the number is never authoritative by itself.

The score is composed from visible dimensions:

- Source and provenance quality
- Freshness and validity
- Consistency
- Reference integrity
- Duplication
- Access correctness
- Downstream risk
- Capture completeness

Users can always inspect the formula, underlying findings, uncertainty, and score history. Scores are calculated separately by project, system, agent, source, and memory collection so one harmless defect does not distort the entire organization.

The preferred interaction is:

```text
Health: 78/100
Why: 2 conflicting facts, 5 stale memories, 1 broken source
Impact: 3 active agents and 14 recent decisions may be affected
Next action: Review the highest-impact conflict
```

Personal users should see findings before scores. The first personal prototype may omit the number entirely and add it only if research shows that it improves understanding without creating false confidence.

### 4.6 Change-impact analysis

When a user proposes a memory change, Lians previews:

- Direct references
- Derived memories
- Agents and workflows that may retrieve it
- Tests whose fixtures or expected results depend on it
- Historical decisions that used it
- Future decisions whose context may change
- Receipts, investigations, or exports that reference it

Deletion never rewrites historical evidence. The active memory may be revoked or cryptographically erased under policy, while the history records that a change occurred and preserves permitted integrity metadata.

Users may run an isolated simulation to estimate behavioral effects before applying the change. Estimated effects must be labeled separately from observed historical effects.

### 4.7 Reconstruction, simulation, and comparison

These are separate capabilities.

**Reconstruction** rebuilds the recorded context at a selected historical cutoff. It should be deterministic when the underlying record is complete.

**Simulation** runs a model, rule, policy, or tool configuration against a reconstructed or modified context. Its output may be nondeterministic.

**Comparison** shows how two records or simulations differ across evidence, cutoff, model, policy, permissions, approval, decision, action, and outcome.

The interface must label:

- Which components were pinned
- Which tool outputs were replayed from records
- Which components were re-executed
- Which components were nondeterministic
- Whether current information entered the simulation
- Whether the original context was completely reconstructed

### 4.8 Receipts and Evidence Packs

A basic receipt can be shared by any user. It summarizes the decision, context, versions, capture status, comparison, and integrity information.

Enterprise Evidence Packs add:

- Investigation summary
- Complete event timeline
- Source and policy inventories
- Model and system versions
- Tool calls and permission checks
- Human approvals
- Reconstruction and simulation results
- Reviewer findings
- Redactions and disclosure history
- Signed export manifest

A recipient must be able to verify a package without accessing the originating workspace. Lians should publish an open package specification and independent verifier.

### 4.9 Accessibility and age-appropriate design

Learn must support keyboard navigation, screen readers, plain-language summaries, color-safe visualizations, adjustable reading levels, and classroom privacy controls.

The platform must avoid manipulative engagement design. Student accounts should minimize collected personal information and disable public sharing by default.

Personal Lians uses a no-graph default. Relationships appear as short statements such as “used by three study plans”; a graph appears only when it makes the answer materially clearer.

---

# Part Two: Architecture

## 5. Universal model

```text
Project
  System
    Run
      Decision
        Goal
        Context
        Memory
        Evidence
        Process
        Action
        Outcome
        Receipt
```

Optional domain layers extend the core:

```text
Learning: Lesson, Experiment, Assignment, Submission
Team: Organization, Workspace, Review, Alert, Incident
Governance: Policy, Approval, Investigation, Legal Hold, Evidence Pack
Architecture: Schema, Trust Boundary, Capture Node, Signing Policy
```

### Core definitions

**Project:** a classroom activity, personal build, research project, team application, or enterprise program.

**System:** the intelligent or automated system being observed.

**Run:** one bounded execution or interaction.

**Decision:** a consequential recommendation, classification, selection, approval, denial, escalation, or action.

**Goal:** the intended objective and success conditions.

**Context:** the state available to the system at a point in time.

**Memory:** a versioned unit of retained context connected to its origin, validity, access scope, consumers, and downstream effects.

**Evidence:** a source, fact, memory, policy, tool result, approval, or system state connected to a decision.

**Process:** the observable model, code, rule, tool, and human steps that transformed context into a decision.

**Action:** an attempted or completed effect following a decision.

**Outcome:** the observed result and later feedback.

**Receipt:** a portable account of the decision and its verification status.

### Evidence-use states

Lians distinguishes:

1. Available to the surrounding system
2. Retrieved or recalled
3. Included in model or rule input
4. Evaluated by a policy or tool
5. Cited by an output
6. Asserted as influential
7. Confirmed by a deterministic rule path

The platform must not infer causality merely because evidence existed in the context.

### Provenance classes

Every record is labeled as:

- Directly observed by Lians
- Reported by an instrumented application
- Supplied by an external connector
- Attested by a human
- Generated by a model
- Derived by Lians

### Capture completeness

Every decision has a capture status:

- Complete within declared boundary
- Complete with known exclusions
- Partial
- Delayed
- Failed
- Unverifiable

The capture boundary, missing components, timestamp trust, and instrumentation version must be visible and exportable.

### Memory relationships

The shared graph uses typed, temporal relationships such as:

- `sourced_from`
- `belongs_to`
- `supersedes`
- `duplicates`
- `conflicts_with`
- `derived_from`
- `retrieved_by`
- `included_in`
- `used_by`
- `affected`
- `referenced_by`
- `restricted_to`

Every relationship records when it was valid, when Lians learned it, how it was established, and its confidence. Deterministic relationships and inferred semantic relationships are never presented as equivalent.

---

## 6. System overview

```text
Lessons, applications, agents, models, rules, and tools
                         |
     Visual builder, SDKs, APIs, MCP, OTel, and imports
                         |
              Capture and ingestion gateway
                         |
       Validation, identity, policy, and provenance
                         |
             Event and evidence pipeline
                         |
                   Lians Core
                         |
 Query, diagnose, analyze impact, reconstruct, simulate, compare, and export
                         |
 Learn | Build | Operate | Govern | Architect
```

### 6.1 Capture layer

The capture layer authenticates clients, validates schemas, normalizes timestamps, declares capture boundaries, attaches tenant and environment identity, preserves idempotency, applies rate limits, and reports capture failures.

Actions may continue when capture fails only under an explicit workflow policy. Consequential workflows should support fail-closed capture requirements.

### 6.2 Event envelope

```json
{
  "event_id": "evt_...",
  "project_id": "prj_...",
  "organization_id": "org_...",
  "workspace_id": "ws_...",
  "environment": "production",
  "event_type": "decision.completed",
  "event_time": "2026-07-26T14:30:00Z",
  "recorded_time": "2026-07-26T14:30:01Z",
  "actor": {},
  "subject": {},
  "system": {},
  "run": {},
  "decision": {},
  "payload": {},
  "evidence_refs": [],
  "policy_refs": [],
  "approval_refs": [],
  "security_context": {},
  "capture": {},
  "provenance": {},
  "integrity": {},
  "schema_version": "1.0"
}
```

Event types include project, run, context, memory, source, model, tool, policy, approval, decision, action, outcome, review, incident, reconstruction, simulation, comparison, export, and erasure events.

### 6.3 Time model

Lians records:

- When something was true in the represented world
- When the system observed or received it
- When it became available to a decision
- When it was used
- When Lians recorded it
- When it was revised or invalidated

Server receipt time is authoritative for ingestion. Client event time includes clock source, precision, and skew metadata.

### 6.4 Storage

Start with:

- PostgreSQL for structured platform records
- Object storage for immutable snapshots and large artifacts
- PostgreSQL-native search where adequate
- Vector search only for semantic discovery, never authorization or historical availability

Add dedicated search infrastructure only when measured tenant-scale volume requires it.

### 6.5 Integrity and privacy

Core controls include append-only records, content hashes, chain linkage, immutable snapshots, signed manifests, idempotency, verifiable transformations, encryption, retention policies, and crypto-shred erasure where appropriate.

Integrity does not override privacy. Redaction, deletion, and disclosure must be represented as authorized transformations with verifiable records.

### 6.6 Reconstruction and simulation engine

A reconstruction package includes:

- Original goal and request
- Historical cutoff
- Source and memory versions
- Policy and model configuration
- Recorded tool results
- Permission context
- Approval state
- Capture and provenance status

Simulation runs in isolation, blocks unapproved external calls, avoids real-world actions, records all differences, and clearly labels nondeterministic components.

### 6.7 Memory intelligence engine

The memory intelligence engine:

- Resolves typed and temporal dependencies
- Detects exact duplicates deterministically
- Suggests semantic duplicates with confidence labels
- Detects conflicting claims without automatically deciding which is true
- Evaluates freshness and validity policies
- Verifies source and reference integrity
- Computes explainable health dimensions
- Traverses downstream dependencies
- Produces impact previews for proposed changes
- Recomputes only affected graph regions after updates

Impact analysis must distinguish:

- **Observed impact:** a historical decision actually referenced the memory.
- **Reachable impact:** an agent or workflow is able to retrieve the memory.
- **Estimated impact:** a simulation predicts that behavior may change.

### 6.8 Application architecture

Begin as a modular monolith with clean boundaries for identity, projects, ingestion, memory, lineage, diagnostics, decisions, evidence, search, impact analysis, reconstruction, simulation, comparison, learning, review, alerts, investigations, exports, billing, and integrations.

Do not split services until operational scale or isolation requirements justify it.

### 6.9 Extension architecture

The platform should eventually support:

- Versioned custom schemas
- Experience-specific interface extensions
- Connector and tool adapters
- Policy plug-ins
- Storage adapters
- Export renderers
- Curriculum and experiment packages
- Locally hosted execution sandboxes

Extensions must not bypass provenance, capture, authorization, or integrity controls.

---

## 7. Interface strategy

### One record, multiple levels

The same decision can be presented as:

**Learn:** “The agent used yesterday's temperature and predicted rain.”

**Build:** `weather.json → retrieval → prompt context → model response → action`

**Architect:** event time, recorded time, validity interval, content hash, schema version, policy version, access decision, execution environment, and chain signature.

### Navigation

Shared navigation:

- Projects
- Systems
- Decisions
- Experiments or Comparisons
- Receipts

Capability-based navigation appears as needed:

- Lessons
- Tests
- Reviews
- Alerts
- Investigations
- Policies
- Architecture
- Administration

### Design rule

Experience modes change vocabulary, defaults, and available actions. They do not create incompatible data formats or isolated product silos.

---

## 8. Trust claims

Lians may claim:

- It can reconstruct the recorded context at a historical cutoff when capture is complete.
- It can show the provenance and version history of recorded information.
- It can verify the integrity of a receipt or Evidence Pack.
- It can compare recorded or simulated decision contexts and outcomes.

Lians must qualify:

- Whether capture was complete
- Whether timestamps were trusted
- Whether an external system supplied the record
- Whether a model or tool was pinned
- Whether a simulation was deterministic
- Whether an explanation was generated or human-authored

Lians must not claim:

- Access to hidden model reasoning
- Proof that every available fact causally influenced an output
- Exact outcome reproduction when components are nondeterministic
- Regulatory compliance merely because Lians is installed

---

# Part Three: Execution

## 9. Immediate validation and builds

**This is the active product and engineering plan. Everything else is direction.**

### Track P0: validate Personal Memory Checkup before building it

Use a clickable prototype and manually prepared memory examples with 8 to 12 people from one audience. Do not begin with account connectors or a production graph.

Test whether users can:

1. Explain what the product does in their own words.
2. Identify an incorrect, stale, conflicting, or unsourced memory.
3. Understand where the memory came from.
4. Predict the consequence of changing it.
5. Correct or dismiss it confidently.
6. Name a reason they would return.

Proceed to a coded prototype only if most participants complete the core task without instruction, trust the explanation, and demonstrate a recurring use case.

### Track P1: Personal Memory Checkup prototype

If validation passes, build five simple screens:

1. Welcome and guided sample
2. Memory list
3. Checkup findings
4. Memory detail and source
5. Change-impact preview and confirmation

The prototype supports sample data, manual entry, and one documented import format. It detects deterministic duplicates, explicit conflicts, dates that violate freshness rules, missing sources, and broken references. Semantic findings are suggestions with confidence labels.

It does not initially connect to every AI service, autonomously delete third-party data, or expose the full graph.

### Track E0: enterprise Decision Explorer

Continue the existing hosted Decision Explorer around the lookahead-bias demonstration:

1. Import deterministic backtest events.
2. Show every decision.
3. Open a contaminated decision.
4. Display future information that entered its context.
5. Reconstruct the correct historical cutoff.
6. Compare contaminated and historically valid outcomes.
7. Export and independently verify the receipt.

Required additions are capture completeness, provenance labels, available-versus-used evidence, honest reconstruction terminology, simulation labels, and an open receipt format.

### Shared-core rule

Personal and enterprise interfaces reuse versioned memory, provenance, conflicts, lineage, temporal validity, and change records. They do not share UI language, onboarding, or release gates merely because they share infrastructure.

---

## 10. Expansion roadmap

Expansion is triggered by evidence, not calendar dates.

| Phase | Scope | Trigger |
| --- | --- | --- |
| P0 | Personal Memory Checkup research prototype | Current validation work |
| P1 | Coded personal checkup with sample/manual/imported memories | P0 comprehension, trust, and return-value thresholds pass |
| E0 | Financial backtest Decision Explorer | Current enterprise commitment |
| 0.5 | Determinism and reconstruction spike | Before model-based simulation |
| A | Domain-neutral Decision Explorer and local sample projects | Core workflow proven with design partners |
| B | Lians Build: SDK workflow, debugger, scenario tests | Developers repeatedly use the explorer to diagnose real systems |
| C | Production Memory Graph, health diagnostics, and impact preview | Personal or enterprise prototype proves repeated value |
| D | Lians Learn pilot with three guided experiments | Core language is understandable and an education partner commits |
| E | Lians Operate: capture health, memory health, alerts, review queues | A production partner asks to be notified rather than investigate manually |
| F | Lians Govern: investigations and Evidence Packs | A partner faces a real review or external request |
| G | Enterprise controls and private deployment | A qualified deal is blocked by security requirements |
| H | Lians Architect and extension framework | Multiple deployments require safe customization |

### Phase 0.5 questions

1. Which providers and components can be pinned?
2. What variance occurs across identical calls?
3. Which tool results can be replayed from records?
4. What context can be reconstructed exactly?
5. How does the interface disclose uncertainty?
6. Which launch workflows support deterministic comparison?

The likely initial claim is deterministic evidence reconstruction, not universal outcome reproduction.

### Initial Learn experiments

When Learn begins, start with only three:

1. Future weather accidentally enters today's plant-watering decision.
2. A recommendation changes when one source is outdated.
3. A private fact crosses an information boundary.

Each experiment should teach the same core concepts used by professional systems: time, provenance, permissions, comparison, and accountability.

---

## 11. Commercial strategy

### Market sequence

The platform vision is universal. Regulated financial services remains the initial sales motion. Personal Memory Checkup begins as product research and an adoption experiment, not an assumed revenue line.

Public enterprise positioning:

> Prove what information your AI used and what it did when a consequential decision was made.

Public platform positioning:

> Know what your AI remembers—and fix it before it causes a bad answer.

### Personal

- Free guided sample
- Local-first memory checkup
- Plain-language findings
- Manual correction and change history
- Clear export and deletion controls

Do not set consumer pricing until research establishes recurring value. Plausible later value includes continuous monitoring, multiple assistants, encrypted synchronization, family or project spaces, and advanced impact previews.

### Community and Learn

- Free local and hosted learning projects
- Classroom-safe defaults
- Guided lessons and public curriculum
- Open receipt verifier
- No advertising or sale of student data

### Developer

- Local open-source core
- Development projects
- Decision Explorer and debugging
- Manual reconstruction and comparison
- Basic receipts

Limits should be based on active projects, collaboration, environments, or compute—not intentionally shortened evidence history.

### Team

- Shared projects
- Production environments
- Reviews and alerts
- Scenario test automation
- Collaboration and integrations
- Usage-based platform capacity

### Business and Enterprise

- Govern capabilities
- Investigations and Evidence Packs
- Advanced access controls
- Custom retention
- SSO and SCIM
- Customer-managed keys
- Private cloud or self-hosted deployment
- Legal holds and information barriers
- Implementation and support commitments

Enterprise pricing should reflect connected workflows, decision volume, deployment requirements, and assurance value rather than seats alone.

### Design partnership

The paid eight-week design partnership remains the primary near-term revenue motion:

- One consequential workflow
- One customer technical owner
- Synthetic or sanitized data initially
- Decision instrumentation
- Historical-cutoff and source-revision tests
- Reconstruction and comparison
- Evidence receipt
- Production recommendation

---

## 12. Success metrics

### Shared core

- Time to first visible decision
- Capture success and completeness
- Percentage of decisions with clear provenance
- Percentage of memories with valid sources
- Duplicate, conflict, stale, and broken-reference resolution rates
- Time to identify downstream impact
- Percentage of health findings with an actionable explanation
- Reconstruction success
- Receipt verification success

### Personal validation

- Percentage who explain the product correctly after one use
- Time to first understood finding
- Percentage who correct or dismiss a finding without help
- Confidence before and after reviewing provenance
- Percentage with a credible reason to return within a month
- Actual four-week return rate after a prototype exists
- Percentage unwilling to connect personal data and why
- False-positive and dismissed-finding rates

### Learn

- Learners who complete an experiment
- Learners who correctly predict how a change affects a decision
- Teacher reuse and assignment completion
- Accessibility and safety incidents

### Build

- Projects reaching first decision
- Scenario tests created
- Regressions discovered
- Developers returning to inspect real systems

### Operate

- Production decisions reviewed
- Alerts resolved
- Capture failures detected before consequential actions
- Incidents investigated

### Govern

- Evidence Packs independently verified
- External reviews supported
- Time required to reconstruct an incident
- Controls and approvals correctly enforced

### Commercial

- Design partnerships completed
- Evaluations converted to production
- Connected workflows expanded
- Recurring revenue

### The metric that matters now

Time from the first customer conversation to the customer proving one of its own decisions with its own data.

---

## 13. Guardrails

- Do not build all five experiences simultaneously.
- Do not abandon the financial-services wedge to market a vague product for everyone.
- Do not treat “everyone” as a customer segment.
- Do not build broad third-party connectors before the checkup workflow is validated.
- Do not require private account access before demonstrating value with a sample.
- Do not assume a one-time cleanup is a recurring product.
- Do not market estimated downstream effects as guaranteed outcomes.
- Do not launch for children before privacy, consent, moderation, and educator controls are complete.
- Do not let enterprise terminology define the universal core model.
- Do not create separate, incompatible data models for students and professionals.
- Do not reduce memory health to an opaque or gamified score.
- Do not claim that semantic similarity proves duplication or factual conflict.
- Do not delete or rewrite historical decision evidence when active memory changes.
- Do not present reachable or estimated impact as observed causal impact.
- Do not expose expert complexity by default to beginners.
- Do not hide capture gaps, uncertain timestamps, or supplied provenance.
- Do not call simulation deterministic reconstruction.
- Do not claim access to hidden model reasoning.
- Do not infer causal use merely from contextual availability.
- Do not let semantic similarity determine historical availability or authorization.
- Do not market Lians as a compliance certificate.
- Do not build a general chat application or model provider.
- Do not compete with generic observability products on dashboard breadth.
- Do not split into microservices before scale or isolation requires it.
- Do not build every integration before one workflow works end to end.
- Do not compromise student privacy or classroom safety for growth.
- Do not weaken information barriers for application convenience.
- Do not let this vision document drive sprint scope; Section 9 drives engineering.

---

## 14. Vision

Lians begins with a simple idea: every intelligent decision should be understandable.

A student uses Lians to see how changing one fact changes an outcome. A developer uses the same underlying model to debug an agent. An operations team uses it to detect unusual behavior. An investigator reconstructs an incident. An auditor verifies the resulting evidence. An architect defines the temporal, security, and integrity boundaries for the entire system.

The interface becomes more powerful as the user's needs grow, but the fundamental questions remain constant:

- What was the goal?
- What information was available?
- What was actually used?
- What processed it?
- What decision was made?
- What action followed?
- What happened as a result?
- What changes when we try something different?

The long-term product is the shared platform people use to learn about, create, operate, and govern intelligent decision systems.

The near-term product is still five screens and a backtest.

Both statements are true, and only the second is currently on the schedule.
