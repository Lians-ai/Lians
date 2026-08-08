# Lians Agent Improvement Infrastructure Roadmap

Planning baseline: August 7, 2026

Status: phases 0–4 implemented August 8, 2026; robotics phases 5–6 deferred

## Implementation checkpoint

The seven pre-robotics build workstreams are now represented in the product:

1. Immutable agent/component/version manifests and Recorder v0.2 measurements.
2. Decision-derived cases, suites, repeated trials, statistical comparisons,
   protected metrics, and signed Evaluation Attestations.
3. Exact context compilation, traceable compression, governed tool registries,
   permission-aware selection, schema slimming, and advisory optimization.
4. Attestation-constrained routing, exact cache decisions, budgets, fallbacks,
   retries, and dependency-aware concurrency plans.
5. Signed release eligibility and Gate approval binding.
6. Shadow, canary, production, and rollback evidence.
7. Outcome, feedback, drift, incident-to-eval, and customer-approval-only
   learning proposals.

The robotics vocabulary, edge recorder, simulation, HIL, and mission-control
work remain intentionally out of scope until a named partner and supported
hardware profile exist. Production claims still require customer pilots and
measured acceptance evidence; implementation alone does not satisfy the claim
ladder.

## 1. Strategic decision

Lians will continue selling independent decision evidence now while expanding into
a provider-neutral improvement control plane for production AI agents.

The current commercial message remains:

> Lians provides independent decision evidence for consequential AI agents.

The expansion message is:

> Lians measures, improves, and proves every agent change across models,
> frameworks, and environments.

The eventual category position is:

> Lians is the improvement control plane for production AI agents.

The company will architect horizontally but sell vertically. The platform should
support many models, frameworks, industries, and eventually physical agents. The
first paid deployments should remain narrow consequential workflows with a named
buyer, a real agent, and a measurable failure or release problem.

The north-star metric is **verified improvements deployed per customer per
month**. An improvement counts only when:

1. A customer-approved primary metric improves.
2. Protected quality, evidence, safety, and policy metrics remain inside their
   approved tolerances.
3. The evaluated agent configuration is immutable and identifiable.
4. Lians produces verifiable evidence connecting the baseline, change,
   evaluation, approval, and deployment.

## 2. What "improve an agent" means

Lians will not use one opaque universal intelligence score. Each workflow gets a
versioned improvement contract with explicit metrics and tradeoffs.

| Promise | Measured definition |
| --- | --- |
| Smarter | Higher task success or domain quality on approved holdouts |
| Faster | Lower p50 and p95 end-to-end completion time |
| More concise | Fewer input and output tokens per successful task |
| Cheaper | Lower model, tool, infrastructure, and human cost per successful task |
| More reliable | Fewer failed tools, retries, timeouts, incomplete runs, and rollbacks |
| Better grounded | Higher evidence coverage, citation fidelity, and temporal correctness |
| Safer | Fewer policy violations, unsafe actions, and escaped regressions |
| More autonomous | Fewer human interventions without reducing protected quality or safety |
| More robust | Lower failure rate under approved perturbations and changed conditions |

Optimization is multi-objective. Cutting tokens is not an improvement if task
quality, evidence completeness, safety, or reliability falls outside the
customer's approved margin.

## 3. The shared platform loop

```text
Record -> Reconstruct -> Evaluate -> Optimize -> Verify -> Deploy -> Monitor
   ^                                                               |
   +------------------ outcomes and incidents ---------------------+
```

The existing Lians foundation maps directly into this loop:

- Universal Recorder provides normalized capture.
- Decision Record and Decision Receipt bind the historical evidence boundary.
- Evidence graph and Investigator provide reconstruction and change impact.
- Gate and the execution mediator provide enforcement.
- Bitemporal memory and token-budgeted context provide a starting point for
  context correctness and efficiency.

The new work adds immutable agent versions, product-grade evaluation,
optimization studies, release assurance, runtime efficiency, outcome learning,
and an edge domain pack for robotics.

## 4. One product with six planes

Do not create six separate companies or brands. These are interoperable modules
inside Lians and share identifiers, manifests, evidence, policy, and tenancy.

### 4.1 Evidence Plane, sell now

- Universal Recorder and native or OTLP ingestion
- Decision Records and independently verifiable Decision Receipts
- Historical source, prompt, model, permission, policy, and tool versions
- Evidence completeness and capture-gap disclosure
- Investigator, decision reconstruction, and reverse impact analysis
- Bitemporal memory, supersession, retention, and erasure evidence

### 4.2 Evaluation Plane, build first

- Convert a production decision or incident into a versioned evaluation case
- Versioned datasets and suites
- Repeated baseline and candidate trials
- Deterministic, human, external, and calibrated model-based scorers
- Quality, evidence, safety, latency, token, cost, and outcome metrics
- Variance, confidence intervals, scorer provenance, and capture limitations
- Signed Evaluation Attestation referencing existing Decision Receipt hashes

Do not add evaluation fields to Decision Receipt v0.1. Create a separate,
versioned Evaluation Attestation so existing receipt verification remains closed
and compatible.

### 4.3 Optimization Plane

- Exact-token context compilation by provider and model tokenizer
- Relevance, redundancy, contradiction, freshness, and evidence-coverage analysis
- Traceable compression and summarization with source lineage
- Tool registry, tool shortlisting, schema slimming, and failed-loop detection
- Prompt, model, policy, context, and tool candidate generation
- Multi-objective optimization studies with hard protected constraints
- Human-approved recommendations supported by batch or shadow evidence

The first optimizer is advisory. It proposes changes and proves their measured
effects. It does not silently rewrite production agents.

### 4.4 Runtime Plane

- Constrained provider and model routing under a declared quality floor
- Exact-response, provider prompt, and permission-aware tool-result caching
- Request budgets, output-length contracts, timeout and retry policies
- Safe tool parallelism where dependencies allow it
- Fallback and degradation policies
- Online telemetry linked to the exact approved agent version

Semantic caching is opt-in and read-only for low-risk use cases. Consequential
actions must not be semantically replayed by default.

### 4.5 Release and Control Plane

- Immutable agent and release manifests
- CI regression checks
- Signed Release Attestations
- Shadow evaluation and canary releases
- Policy and human approval gates
- Deployment evidence, rollback, and change blast-radius analysis
- Gate enforcement for protected releases and consequential actions

### 4.6 Outcome and Learning Plane

- Business outcome, correction, dispute, human override, and incident ingestion
- Drift and change detection
- Production failure to regression-case automation
- Ranked, evidence-backed improvement proposals
- Customer-approved learning queues
- Longitudinal history of which changes worked for which workloads

No production change is self-approved. Candidate generation, evaluation,
approval, deployment, and monitoring remain separate recorded actions.

## 5. Canonical records and API families

New records should be immutable or append-only where they make consequential
claims:

- `AgentDefinition`, `AgentVersion`, and `ComponentArtifact`
- `EvalCase`, `EvalSuite`, `EvalRun`, `Trial`, `MetricResult`, and `Comparison`
- `EvaluationAttestation`
- `OptimizationStudy`, `Candidate`, and `Recommendation`
- `ContextBundle`, `ToolRegistryVersion`, `RoutingDecision`, and `CacheDecision`
- `Outcome`, `Feedback`, `DriftSignal`, and `LearningProposal`
- `ReleaseCandidate`, `ReleaseAttestation`, `Deployment`, and `Rollback`
- Later: `EdgeDevice`, `WorldStateSnapshot`, `ActionIntent`, `SafetyEnvelope`,
  and `EdgeSyncCursor`

Initial API families:

```text
/v1/agents/{id}/versions
/v1/eval/cases/from-decision
/v1/eval/suites
/v1/eval/runs
/v1/eval/comparisons
/v1/eval/attestations
/v1/optimization/studies
/v1/context/compile
/v1/tools/select
/v1/routing/decide
/v1/outcomes
/v1/feedback
/v1/drift
/v1/releases
/v1/deployments
/v1/rollback
/v1/edge/events/batch
```

Universal Recorder v0.2 should add normalized, optional fields for provider,
runtime framework, operation, prompt hash, toolset hash, request-configuration
hash, release reference, input and output tokens, cached tokens, latency, finish
reason, error code, cost attribution, and outcome correlation. Each metric must
state whether it was provider-reported, workload-reported, client-measured,
deterministic, human-authored, model-judged, or estimated.

## 6. Adoption modes

Customers should be able to adopt Lians without immediately placing it on a
critical path.

1. **Observer:** record, reconstruct, compare, and diagnose.
2. **Advisor:** recommend agent, model, context, prompt, tool, and policy changes.
3. **Controller:** enforce approved releases or protected actions through Gate.

Observer proves value with the lowest integration and operational risk. Advisor
creates the improvement workflow. Controller becomes the high-retention control
point only after the customer trusts the recorded evidence and evaluations.

## 7. Build sequence and acceptance gates

The timing below assumes a funded team of roughly three to five focused
engineers. With the current founder team, use the acceptance gates rather than
promising the dates.

| Phase | Estimated window | Deliverable | Mandatory acceptance gate |
| --- | --- | --- | --- |
| 0. Stabilize and version | Weeks 0 to 4 | Deploy the current evidence stack; add immutable AgentVersion and normalized token, cost, latency, and outcome fields | One OpenAI-based and one Anthropic, LangGraph, or Google-based app produce independently verifiable receipts tied to exact versions; tenancy and conformance tests pass |
| 1. Evaluate and attest | Weeks 4 to 10 | Decision-to-eval conversion, repeated trials, scorer provenance, comparisons, and signed Evaluation Attestation | A real decision becomes an eval in under 10 minutes; every trial is pinned; critical invariants pass 100 percent; variance is disclosed |
| 2. Optimize context and tools | Weeks 8 to 16 | Exact-token compiler, traceable compression, tool registry, tool shortlisting, and schema reduction | At least 25 percent input-token reduction on one paid workflow without breaching its quality margin; every retained or compressed fact remains traceable |
| 3. Optimize runtime | Weeks 14 to 24 | Constrained routing, exact and prompt caching, permission-aware tool caching, budgets, and safe concurrency | Router overhead under 25 ms p95 excluding provider latency; zero cross-tenant cache or invalidation failures; at least 20 percent cost or latency improvement at the approved quality floor |
| 4. Control releases | Weeks 20 to 32 | CI gate, shadow, canary, rollback, outcomes, drift, and incident-to-eval automation | Every protected production release links to a signed evaluation pack; a seeded regression is blocked; rollback drill passes; no unapproved automatic change occurs |
| 5. Robotics domain pack | Weeks 32 to 52, only after partner gate | Edge Recorder, ROS 2 and Open-RMF support, offline signed spool, mission manifests, simulator and HIL evaluation | Lians never enters the hard real-time servo loop; reconnect is lossless and idempotent within declared capacity; clock uncertainty and capture loss are disclosed; independent hardware safety remains authoritative |
| 6. Ecosystem scale | After repeatable production use | Vertical packs, integration certification, partner marketplace, private deployment, and improvement benchmark program | At least three unrelated workloads show repeatable verified improvements and customers regularly deploy Lians-approved changes |

Phases may overlap only when their preceding data contracts are stable. No
customer-facing optimizer should precede immutable versions and product-grade
evaluation.

## 8. Robotics and physical AI track

Robotics is a domain pack, not a separate operating system.

```text
LLM or learned planner
        |
Mission and task decision
        |
Lians evidence, evaluation, comparison, and high-level Gate
        |
ROS 2, Open-RMF, or vendor fleet API
        |
Local navigation, controller, safety PLC, and emergency stop
```

Lians may record, reconstruct, test, compare, and sometimes authorize a mission
or software release. It must not replace certified safety functions, local
obstacle avoidance, motor control, braking, or emergency-stop systems.

### Minimum robotics package

1. A read-only `lians_ros2` lifecycle node on the robot computer or edge gateway.
2. Allowlisted topic, action, service, parameter, lifecycle, diagnostic, and
   safety-state observation.
3. Explicit planner instrumentation for proposed, committed, requested,
   accepted, rejected, completed, cancelled, failed, safety, and human-override
   events.
4. Stable decision, mission, task, trace, robot, and fleet identifiers.
5. A bounded encrypted local spool for disconnected operation.
6. A triggered flight recorder using local rosbag2 or MCAP windows around
   incidents rather than continuous raw sensor upload.
7. An evidence manifest covering model, prompt, planner, policy, container,
   package, firmware, launch, parameters, map, calibration, coordinate frame,
   sensor, controller, ROS distribution, middleware, QoS, clock, simulator,
   seed, and hardware versions.
8. Separate ROS, monotonic, UTC, and simulated clocks with uncertainty.
9. A read-only Open-RMF observer for task allocation, itinerary, schedule,
   battery, replanning, interruptions, shared resources, and outcomes.
10. Simulation, processor-in-loop, hardware-in-loop, shadow, canary, and
    production stages using the same evaluation contract.

The first robotics wedge should be warehouse AMR mission planning, inspection,
or fleet coordination. Do not begin with surgery, autonomous driving, or direct
safety-critical motion control.

Robotics improvement metrics include mission completion, collisions and near
misses, safety-zone violations, intervention, recovery time, localization error,
path length, energy, congestion, deadline misses, perception error, inference
latency, tokens, cost, outcome variance, and capture coverage.

## 9. What Lians owns and what it integrates

Lians should own:

- The normalized decision and change schema
- Point-in-time provenance and evidence completeness
- The production failure-to-regression workflow
- Cross-version and cross-provider comparison
- Signed proof of improvement
- Reverse change impact and blast-radius analysis
- Release assurance and consequential-action enforcement
- The longitudinal improvement history for each customer

Lians should integrate with:

- Model providers and agent frameworks
- OpenTelemetry and existing observability systems
- Existing gateways and provider caches
- Human and external evaluation systems
- Vector databases and retrieval systems
- CI/CD, identity, policy, and deployment systems
- ROS 2, Open-RMF, Gazebo, Isaac Sim, and vendor fleet APIs

Lians should not build:

- A foundation model or general training platform
- A GPU scheduler or general model-serving cloud
- Another generic LLM gateway
- A full agent orchestration framework
- A new vector database
- A generic APM or tracing clone
- A chain-of-thought recorder
- An autonomous self-modifying production system
- A robotics operating system or safety controller
- Default semantic caching of consequential actions

The rule is to own the differentiating improvement and proof loop while
integrating commodity infrastructure.

## 10. Commercial sequence

### Entry offer, sell immediately

**Decision Evidence Sprint**

- $7,500 fixed price
- 10 business days
- 50 percent upfront
- One consequential workflow
- One captured decision or incident
- One reconstruction and Evidence Pack
- Baseline report for quality, latency, tokens, cost, and capture completeness
- One scoped proposal for the next improvement experiment

### Expansion offers, pricing hypotheses to validate

| Offer | Initial pricing hypothesis |
| --- | --- |
| Production Evidence | $24,000 to $60,000 annually |
| Agent Improvement pilot | $15,000 to $25,000 for four to six weeks |
| Evidence plus Improve platform | $40,000 to $100,000 annually |
| Private Control deployment | $100,000 to $250,000 annually plus implementation |
| Robotics design partnership | $25,000 to $75,000 paid |
| Fleet or enterprise Edge | $100,000 to $300,000 annually after validation |

Charge for protected workflows, consequential decisions, evaluation runs,
release boundaries, retention, deployment, and support. Do not mark up model
tokens because that conflicts with the promise of reducing token cost.

The expansion motion is:

```text
Evidence Sprint
  -> production recording
  -> regression and comparison
  -> optimization
  -> release Gate
  -> additional workflows, business units, or fleets
```

Initial buyers are AI-native vendors in lending, fraud, KYC, insurance, legal
operations, regulated support, and payments. Target live or near-production
agents, a recent incident or migration, 20 to 500 employees, and a technical
buyer with authority over reliability or AI infrastructure.

Clouds, marketplaces, consultancies, ServiceNow, Pega, NVIDIA, NayaOne, and
OVHcloud are channels or implementation partners. They count as validation only
when they fund a deployment or introduce a funded customer.

## 11. Revenue and validation gates

| Milestone | Commercial gate |
| --- | --- |
| Evidence foundation | First paid sprint with real run data and a named buyer |
| Evaluation Plane | Three paid sprints and at least two customers reusing an eval |
| Optimization Plane | Verified improvement on three distinct workloads and at least one annual conversion |
| Release and Control | Customers require Lians evidence before a production change and at least 60 percent of qualified pilots convert |
| Robotics prototype | Two paid robotics design partners with simulator, recorded fleet data, or named hardware access |
| Continuous optimization | Customers repeatedly deploy Lians-verified changes without bespoke founder intervention |

Stop or change direction when:

- Thirty qualified live buyer conversations yield no paid Evidence Sprint.
- Fewer than two of five paid sprints convert to recurring use.
- A proposed module has fewer than two paying customers requesting the same
  workflow.
- An optimizer cannot beat a simple baseline on approved holdouts.
- Integration cannot be reduced to one working day for a supported stack.
- An adapter has fewer than two active users after six months and carries
  meaningful maintenance cost.
- Twenty qualified robotics conversations yield fewer than two paid partners.
- A robotics opportunity requires Lians inside a certified hard real-time motion
  loop.

## 12. Team, capital, and machine constraints

Before financing, founders should close and deliver the first customers. Avoid
full-time hiring and use narrowly scoped contractors only when a paid deployment
requires it.

Hiring gates:

1. Senior distributed-systems or infrastructure engineer after two paid pilots
   or financing closes.
2. Evaluation and ML-systems engineer as the Evaluation Plane becomes a paid
   product.
3. Solutions and integrations engineer after three annual conversions or
   approximately $150,000 in contracted ARR.
4. Robotics engineer only after two paid robotics partners or dedicated
   strategic funding.
5. Safety specialist or qualified advisor before any physical pilot that may
   affect hazardous motion.

The current development machine has an AMD Ryzen 9 7940HS, 15.2 GB RAM, and an
NVIDIA RTX 4050 Laptop GPU with 6 GB VRAM. It can build and test the control
plane, SDKs, adapters, small local models, and modest evaluation suites. Serious
multi-model load testing, model training, and complex robotics simulation should
use provider APIs, OVHcloud or other cloud capacity, customer compute, or
dedicated edge hardware.

For a $750,000 pre-seed, preserve roughly 18 months of runway. A planning
hypothesis is 50 to 55 percent for technical payroll, 15 percent for integration
and deployment contractors, 12 to 15 percent for legal, security, and compliance,
10 percent for compute and edge hardware, 5 percent for focused sales and travel,
and the remainder for contingency. Recalculate this against actual founder pay,
taxes, benefits, insurance, and signed contracts before hiring.

## 13. Claim ladder

Lians earns broader language in stages:

1. **Current:** independent decision evidence for consequential AI actions.
2. **After Evaluation validation:** provider-neutral agent evaluation and change
   assurance.
3. **After measured customer results:** reduces token cost or latency while
   preserving an approved quality and safety floor.
4. **After repeated controlled deployments:** continuously improves and safely
   releases production agents across supported providers.
5. **After simulator, HIL, and a named physical pilot:** mission-level evidence
   and change assurance for supported robotics configurations.
6. **Long-term category position:** the improvement control plane for production
   AI agents.

Never claim access to hidden reasoning, universal deterministic replay,
guaranteed correctness, automatic regulatory compliance, support for every
agent, hard real-time behavior without named hardware evidence, or robotics
safety certification merely because Lians is installed.

## 14. The next 30 days

### Sell

1. Continue selling the Decision Evidence Sprint.
2. Add a baseline quality, latency, token, cost, and evidence report to every
   proposal.
3. Ask prospects about their last agent incident, migration, delayed release,
   expensive workflow, or human override rather than asking whether they have a
   memory problem.
4. Require 50 percent upfront and access to one named workflow.

### Build

1. Deploy and verify the current Recorder, Receipt, Investigator, and Gate stack.
2. Specify immutable `AgentVersion` and `ComponentArtifact` records.
3. Specify Recorder v0.2 operational and cost fields.
4. Build the Evaluation Plane vertical slice:
   decision to case, suite, run, repeated trial, comparison, and separate signed
   Evaluation Attestation.
5. Demonstrate the same 20 to 50 cases against two model, prompt, or tool
   configurations.
6. Add exact tokenizer support to the context budgeter for the first two
   supported providers.

### Prove

1. Seed a known regression and show Lians detecting it.
2. Reduce tokens or latency on one workflow while meeting its protected quality
   and evidence limits.
3. Publish the measurement method, configuration hashes, variance, and claim
   limitations.
4. Turn the result into the first Agent Improvement case study.

### Prepare robotics without distracting the core

1. Write the ROS 2 event vocabulary and edge evidence manifest.
2. Build only a simulator design and compatibility matrix until a paid partner
   is present.
3. Seek a warehouse AMR, inspection, or fleet-coordination design partner with
   ROS 2 or Open-RMF access.

## 15. External technical anchors

- [OpenAI Agents SDK tracing](https://openai.github.io/openai-agents-python/tracing/)
- [OpenTelemetry semantic conventions](https://opentelemetry.io/docs/specs/semconv/)
- [ROS 2 real-time design](https://design.ros2.org/articles/realtime_proposal.html)
- [ROS 2 topics, services, and actions](https://docs.ros.org/en/ros2_documentation/rolling/Concepts/Basic/Interfaces-Topics-Services-Actions.html)
- [ROS 2 clock and time design](https://design.ros2.org/articles/clock_and_time.html)
- [Open-RMF repository and fleet integration](https://github.com/open-rmf/rmf)
- [Gazebo ROS 2 integration](https://gazebosim.org/docs/harmonic/ros2_integration/)
- [NVIDIA Isaac ROS NITROS](https://nvidia-isaac-ros.github.io/concepts/nitros/index.html)
- [W3C PROV data model](https://www.w3.org/TR/prov-dm/)
- [SLSA provenance](https://slsa.dev/spec/v1.2/provenance)

## Final operating rule

> Build the complete vision, but require every layer to earn the right to exist
> through customer money, repeated use, and measured improvement.
