# Grafana Cloud campaign

Updated: July 27, 2026

## Objective

Convert teams already using Grafana Cloud, OpenTelemetry, and production AI
agents into paid Lians design partnerships.

The campaign does not compete with Grafana AI Observability. Grafana owns live
performance, conversations, traces, evaluations, cost, and operational
response. Lians owns historical knowledge boundaries, memory provenance,
decision evidence, downstream impact, investigation, and defensible exports.

## Positioning

### One sentence

Keep using Grafana to observe how your AI is performing; add Lians when you need
to prove what it knew and relied on when a consequential decision was made.

### Short headline

Your AI trace says what happened. Lians preserves what can be proven.

### Supporting copy

Lians connects to the same OpenTelemetry stream already flowing through
Grafana Alloy. Grafana keeps the operational trace. Lians reconstructs the
decision-time record: memory versions, source provenance, policy state,
permissions, approvals, historical cutoff, and evidence integrity.

### Contrast

| Grafana Cloud AI Observability | Lians |
| --- | --- |
| Is the agent healthy? | What did it know when it acted? |
| Latency, errors, tokens, cost | Memory, sources, policy, approvals |
| Conversations and live evaluations | Historical reconstruction |
| Operational alerts | Evidence and control exceptions |
| Trace investigation | Decision investigation |
| Production troubleshooting | Audit, dispute, and examiner response |

## Ideal customer profile

All conditions should normally be present:

1. Uses Grafana Cloud, Tempo, Grafana Alloy, or an OTLP pipeline.
2. Runs an AI agent or LLM-supported workflow in production or late pilot.
3. The system recommends, approves, denies, escalates, or executes something
   consequential.
4. An auditor, regulator, insurer, customer, court, or internal model-risk team
   can demand an explanation.
5. Memory, retrieved documents, changing policies, or tool results influence
   the outcome.

### Priority segments

1. Trading, investment research, surveillance, and risk
2. Lending and adverse-action workflows
3. Insurance underwriting and claims
4. Fraud, AML, and financial-crime investigation
5. Legal investigation and privilege-sensitive agents
6. Healthcare only after the financial-services motion is proven

### Buyer and user

- Buyer: Head of AI Risk, Model Risk, Compliance Engineering, Internal Audit
- Technical champion: Staff/Principal AI Platform Engineer, Observability Lead
- Operational user: AI Product Manager, Investigation Lead, Risk Reviewer
- Blocker: Security, privacy, or platform engineering

## Qualification signals

Strong public or discovery signals:

- Grafana Cloud or Alloy is named in engineering material or job descriptions.
- OpenTelemetry and Tempo are part of the platform.
- The company discusses production agents, RAG, underwriting, surveillance,
  fraud, risk, or automated recommendations.
- Model governance, AI assurance, auditability, or adverse action is named.
- The team retains prompts and traces but cannot reconstruct changing memory,
  source, or policy state.

Disqualify when:

- AI is only an internal writing assistant.
- No consequential action or external review exists.
- The team only needs latency, cost, or prompt evaluation.
- There is no production or near-production workflow.

## Offer

### Twenty-minute evidence gap review

We inspect one existing GenAI trace and answer:

1. Can the team identify the exact decision?
2. Can it reconstruct the memory and retrieved sources at that time?
3. Can it distinguish information available then from facts learned later?
4. Can it verify policy and approval state?
5. Can an outside reviewer validate the exported record?

Deliverable: a one-page gap map. No procurement required.

### Paid eight-week design partnership

- One workflow
- Existing Grafana/OTEL pipeline
- Synthetic or sanitized data first
- Alloy fan-out
- Trace-to-decision correlation
- Historical-cutoff and source-revision tests
- Capture-health dashboard and alerts
- Evidence receipt
- Production recommendation

## Landing-page copy

### Hero

**Your AI trace says what happened. Lians preserves what can be proven.**

Already using Grafana Cloud and OpenTelemetry? Add Lians to the same Alloy
pipeline to preserve the memory, source, policy, permission, and approval state
behind consequential AI decisions.

Primary CTA: **Run an evidence gap review**
Secondary CTA: **See the Alloy integration**

Trust line:

`One OTLP stream · No replacement of Grafana · Unsampled evidence path · Open verifier`

### Problem

Grafana can show the model call, latency, tokens, tools, and conversation. But a
dispute six months later asks different questions:

- Which version of the customer fact was valid?
- Was that document available before the decision?
- Which memory was superseded afterward?
- Which policy and permissions applied?
- Can a reviewer verify the record independently?

### How it works

1. Applications continue sending OTLP to Grafana Alloy.
2. Alloy fans the stream to Grafana Cloud and Lians.
3. Grafana provides operational AI observability.
4. Lians creates the decision-time evidence record.
5. Each Grafana trace links to the corresponding Lians decision.

### CTA

**Bring one trace. Leave with an evidence gap map.**

## Demo script

Duration: seven minutes.

1. Open a GenAI trace in Grafana and show model, tool calls, tokens, and latency.
2. Follow the decision link into Lians.
3. Show the memory and source versions available at the historical cutoff.
4. Reveal one future or superseded fact that must not enter reconstruction.
5. Compare the original context with the corrected historical context.
6. Show capture completeness and provenance labels.
7. Export and verify the receipt.

Close with:

> Grafana helped us find the event. Lians made the decision defensible.

## Outreach sequences

### Technical champion email

Subject: Keep Grafana for AI observability—add the missing decision record

Hi {{first_name}},

If {{company}} is already sending AI traces through Grafana Alloy, Lians can use
that same OTLP stream without replacing Grafana.

Grafana shows model calls, tools, latency, tokens, and evaluations. Lians
preserves the memory versions, source provenance, policy state, permissions,
and approvals behind a consequential decision—so the team can reconstruct and
prove it later.

Would a 20-minute review of one sanitized trace be useful? We will return a
one-page map of what the trace proves today and which evidence would be missing
in an audit or dispute.

{{sender}}

### Risk or compliance email

Subject: Can your AI evidence survive a source or policy revision?

Hi {{first_name}},

Most AI tracing answers what the system did. It does not necessarily prove
which version of a fact, document, memory, or policy was available when the
decision occurred.

Lians connects to an existing Grafana/OpenTelemetry pipeline and preserves that
decision-time boundary. It is designed for teams that may need to answer an
examiner, auditor, insurer, customer, or court later.

We are offering a short evidence gap review using one sanitized decision. No
production access is required. Is there a workflow at {{company}} where that
would be relevant?

{{sender}}

### Follow-up

Subject: Re: AI decision evidence in Grafana

The quickest test is simple: take one historical AI decision and ask whether
the team can recover the exact memory, source, policy, and approval versions
that existed then—without leaking later information into the reconstruction.

If useful, I can send the seven-minute Grafana-to-Lians demo.

### LinkedIn connection note

We built an OTEL fan-out for teams using Grafana Cloud with consequential AI
workflows: Grafana keeps operational observability; Lians preserves the
decision-time memory and evidence record. Interested in comparing notes?

## Launch content

### Community or LinkedIn post

**Grafana can tell you what your AI did. What happens when someone asks what it
knew at the time?**

We built a Lians integration for teams already using Grafana Cloud and
OpenTelemetry.

One trace stream flows through Grafana Alloy:

- Grafana keeps operational visibility: latency, tokens, tools, conversations,
  evaluations, and incidents.
- Lians preserves decision evidence: historical memory, source versions,
  policy state, permissions, approvals, and capture integrity.

The point is not another LLM dashboard. It is being able to reconstruct a
consequential decision months later without accidentally using information
learned afterward.

We are looking for financial-services teams willing to test one sanitized
workflow. Bring one trace; we will return an evidence gap map.

### Technical article

Title: **Your AI trace is not yet a decision record**

Outline:

1. What OpenTelemetry captures well
2. Why operational traces and historical evidence differ
3. The future-information problem
4. Fan-out before sampling
5. Metadata-only privacy defaults
6. Trace-to-decision correlation
7. Reconstructing memory, source, and policy state
8. Verifiable evidence receipts

## Channel sequence

### Week 1

- Publish integration source and installation guide.
- Record the seven-minute demo.
- Create the Grafana plugin submission package.
- Recruit five warm technical reviewers.
- Run three evidence gap reviews manually.

### Week 2

- Publish the technical article.
- Post the integration in the appropriate Grafana community integration or
  plugin-development category after verifying forum rules.
- Begin narrowly researched outreach to 20 qualified technical champions.
- Contact five existing financial-services relationships.

### Weeks 3–4

- Publish one sanitized gap-review result.
- Hold a technical office hour.
- Expand to 50 researched accounts only if replies confirm the problem.
- Convert qualified reviews into paid design partnerships.

## Metrics

- Landing page → gap review request
- Qualified replies per researched account
- Reviews completed
- Reviews that reveal a material evidence gap
- Days from first reply to sanitized trace
- Design partnerships proposed and accepted
- Plugin installs after catalog acceptance

The most important early metric is not impressions. It is the percentage of
qualified teams that provide one sanitized trace for review.

## Claims guardrails

- Say “complements Grafana,” never “replaces Grafana.”
- Do not claim catalog availability until Grafana approves the plugin.
- Do not call application-level append-only storage certified WORM.
- Do not claim full capture when sampling occurs before Lians.
- Do not claim hidden chain-of-thought or internal model cognition.
- Do not imply that installing Lians makes a customer compliant.
- Do not name a company as a Grafana Cloud user without current evidence.
