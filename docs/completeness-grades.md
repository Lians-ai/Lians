# Lians Decision Evidence Completeness Grades

Version 1.0

This document is the normative definition of the Lians decision-evidence
completeness vocabulary. The grades describe what the captured record can
support. They do not certify that a decision was correct, fair, lawful, or
compliant.

## Rules that apply to every grade

1. Grades are cumulative. A decision must satisfy its current grade and every
   lower grade.
2. A missing required check stops advancement. Lians reports the highest
   satisfied grade, the next grade, and every named gap.
3. A profile may add requirements, but it cannot remove a base requirement.
4. An open envelope without a sealed decision may have `grade=null`. It is not
   Recorded.
5. The numeric completeness score is the fraction of required checks currently
   satisfied. It is diagnostic metadata, not a substitute for the grade.
6. A grade describes evidence completeness at the evaluation time. Appending
   new evidence can raise a grade; it cannot rewrite the historical artifacts.

## Recorded

**Normative claim:** Lians has a sealed, append-only decision record with an
integrity commitment.

Required base check:

- `decision_record`: the envelope has been sealed into a decision whose
  `record_hash` commits to the decision body.

Recorded does not claim that Lians can recover the complete prior context,
validate every supporting artifact, or replay the execution.

## Reconstructable

**Normative claim:** Lians can assemble the material point-in-time context and
influence evidence captured for the decision.

Required base checks, in addition to Recorded:

- `temporal_context`: the decision or envelope records `knowledge_as_of`.
- `influence_evidence`: at least one material influence is linked with a role
  such as `retrieved`, `used`, `governed`, `executed`, or `reviewed`. Supported
  influences include recall receipts, memories, traces, policy decisions, tool
  results, reviews, models, prompts, inputs, outputs, and external artifacts.

The `regulated_recordkeeping` profile also requires:

- `trace_context`: a content-addressed OTLP trace or span is linked.
- `policy_context`: the governing policy decision or exact policy version is
  linked.

Reconstructable is intentionally broader than Replayable. External APIs,
stochastic model behavior, or retired runtimes may make exact replay impossible
without preventing an honest reconstruction of the captured record.

## Verifiable

**Normative claim:** A recipient can independently check the integrity of the
decision input, output, and every material evidence edge required by the active
profile.

Required base checks, in addition to Reconstructable:

- `input_integrity`: the input has a SHA-256 commitment.
- `output_integrity`: the output has a SHA-256 commitment.
- `evidence_integrity`: at least one material evidence edge exists and every
  material edge carries an artifact hash.

The `regulated_recordkeeping` profile also requires:

- `model_identity`: both the model identifier and exact version are recorded.

The `human_review` profile also requires:

- `human_oversight`: the reviewer and review outcome are recorded.

Verifiable means the committed artifacts can be checked against the record. It
does not mean that Lians endorses the decision or that every real-world fact was
true.

## Replayable

**Normative claim:** The record contains the identity and dependency commitments
required to attempt deterministic replay under the active profile.

Required base checks, in addition to Verifiable:

- `model_identity`: model identifier and exact version.
- `prompt_identity`: prompt identifier and exact version.
- `trace_context`: a content-addressed OTLP trace or span.
- `replay_manifest`: a SHA-256 commitment to the replay manifest covering the
  runtime and external dependency closure.

Profiles may additionally require:

- `runtime_identity`: exact agent runtime version.
- `tool_context`: material tool calls and corresponding results.

Replayable does not promise that a third-party service remains available or
that a nondeterministic provider will return identical output. It means the
captured record satisfies the declared replay contract. A replay attempt and
its result should be recorded separately.

## Gap reporting

Each missing check is returned with:

- `code`: stable machine-readable check name.
- `label`: short human-readable name.
- `blocks`: first grade the check prevents.
- `message`: concrete remediation.

Unknown custom checks and unknown grade names are rejected. This prevents a
misspelling from silently weakening the standard.

## Profiles

| Profile | Additional requirements |
|---|---|
| `standard` | No additions to the base grade definitions. |
| `regulated_recordkeeping` | Trace and policy evidence for Reconstructable; model identity for Verifiable. |
| `human_review` | Recorded human oversight for Verifiable. |

Organizations can add recognized checks through `required_checks`. The
resulting profile must be preserved in the Decision Envelope and Evidence Pack
so a recipient can evaluate the grade against the same contract.
