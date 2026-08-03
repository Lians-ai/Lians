# Confluent meeting brief

**Meeting:** Aldo Mendoza Besarez, Kafka Advisor
**Time:** Tuesday, July 28, 2026, 12:00–12:30 PM ET
**Invite evidence:** Gmail invitation `19fa45b0fd43f013`, sent to `info@lians.ai`; Zoom meeting ID `952 0039 1907`. The event is not visible on Ethan's connected primary calendar, so verify access and RSVP from the `info@lians.ai` calendar before the meeting.
**Primary outcome:** A named buyer with a consequential AI workflow, budget authority, and permission for an introduction.
**Secondary outcome:** A dated session with a Confluent solutions architect to validate the smallest production-relevant ingestion path.

## Confirmed context

- Aldo initiated contact after Lians received Confluent Cloud credits.
- He said the use case was interesting and offered to coordinate the right technical counterpart for a simple pilot architecture.
- This is currently a vendor onboarding/technical conversation, not evidence that Confluent has purchasing intent.
- The commercial conversion path is either Confluent explicitly owning the sprint or Aldo making a named introduction to a customer with a consequential workflow and budget.

## Thirty-second explanation

Lians preserves the evidence behind an AI action so it can still be reconstructed after documents, policies, models, permissions, memories, or API results change. Kafka can carry the decision, evidence, tool-call, approval, and downstream-effect events. Grafana shows how the system behaved; Lians shows what it knew, why it acted, and what would break if a dependency changed or were deleted.

## The commercial offer

The first engagement is a paid two-week AI Evidence Readiness Sprint for one sanitized workflow:

- point-in-time reconstruction of one consequential AI decision;
- dependency and downstream-impact mapping;
- stale, conflicting, duplicated, missing-source, and broken-reference checks;
- Kafka ingestion and OpenTelemetry correlation where relevant;
- hashed evidence report and technical walkthrough.

**Price:** $4,500 fixed.
**Payment:** $2,250 after signature and before kickoff; $2,250 within five business days of delivery.
**Boundary:** no free pilot or unpaid evaluation.

Confluent should only receive an order form if it explicitly becomes the buyer. Otherwise, ask for one qualified customer introduction and send that customer the one-page scope after confirming its workflow and budget owner.

## Pilot architecture to validate

1. Producers emit `decision`, `evidence`, `tool_call`, `approval`, and `effect` events.
2. Confluent Cloud transports them with ordering keyed by `decision_id`.
3. Schema Registry enforces compatible contracts.
4. Lians resolves temporal relationships and immutable evidence references.
5. OpenTelemetry trace IDs connect the reconstructed decision to Grafana traces and metrics.
6. A replay query reconstructs the original decision and previews downstream effects before a source or memory is changed or deleted.

## Meeting control

1. **0–4 minutes:** establish that the goal is a buyer-backed evidence sprint, not a general Kafka tutorial.
2. **4–10 minutes:** explain one concrete workflow: an AI decision whose sources later change.
3. **10–16 minutes:** validate topic structure, keying, schema, replay, and retention.
4. **16–23 minutes:** identify one Confluent customer or internal team with consequential AI, an evidence gap, and budget authority.
5. **23–28 minutes:** request a warm introduction by name and ask permission to mention Confluent's technical fit.
6. **28–30 minutes:** place the next meeting on the calendar or record an explicit no-fit reason.

## Discovery questions

- Which existing Confluent customer teams are operating regulated, high-impact, or customer-facing AI agents?
- Which one already needs decision replay, audit evidence, deletion-impact analysis, or historical source reconstruction?
- Who owns that problem and can approve a $4,500 two-week engagement?
- Would you introduce us by email if we provide a three-sentence forwardable note during this call?
- Which solutions architect can validate the event model with us, and can we schedule that session now?
- What proof would Confluent need before it could list or recommend this integration?

## Direct closing language

> The architecture is useful only if it reaches a real workflow. Who is one customer or internal AI owner you would feel comfortable introducing us to for a paid $4,500 evidence-readiness sprint?

If Aldo says he cannot introduce customers:

> Understood. Can you name the partner, field engineering, or industry owner who can evaluate that request, and can we add them to a dated follow-up now?

If Aldo offers only architecture help:

> That technical validation is useful, but it does not establish a paid use case. Can we pair the architect with one internal or customer workflow owner who has the evidence problem and can authorize the $4,500 sprint?

If Confluent itself may fund the work:

> Great. Before I send the order form, can we confirm the internal workflow, success criterion, approving owner, legal entity, and billing contact? The first $2,250 is due after signature and before kickoff.

If budget is the only objection:

> We can reduce scope to a one-week paid assessment for $2,500, still with payment before kickoff. We do not run unpaid pilots.

## Forwardable customer introduction

> Ethan is the co-founder of Lians, an evidence layer that reconstructs exactly what an AI workflow knew when it acted and shows downstream impact when its sources, policies, permissions, or memories change. Lians is looking for one team operating a consequential AI workflow for a paid two-week Evidence Readiness Sprint: $4,500 fixed, with $2,250 due before kickoff. I thought this might be relevant to your work on [workflow] and am introducing you both to assess fit directly.

## Qualification gates

A lead is commercially qualified only if the conversation identifies:

- one specific consequential AI workflow;
- one named business or product owner;
- a plausible budget path;
- a dated next action;
- acceptance that the engagement is paid.

Technical enthusiasm, cloud credits, an architecture review, or a vague offer to “keep in touch” is not a paid buying signal.

## Materials to have open

- `output/pdf/lians-paid-design-partnership.pdf`
- `output/pdf/lians-paid-design-partnership-order-form.pdf`
- `docs/gtm/paid-design-partnership-objection-card.md`
- `docs/gtm/paid-deal-outcome-template.md`
- `docs/gtm/meeting-follow-up-templates.md`
