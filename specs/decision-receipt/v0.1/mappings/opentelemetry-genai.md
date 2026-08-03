# OpenTelemetry GenAI to Decision Receipt v0.1

This mapping explains how evidence normalized from OpenTelemetry GenAI spans can
support a Decision Receipt. It does not turn every span into a decision and does
not treat trace availability as proof of causal use.

The producer SHOULD propagate `lians.decision.id` (or an equivalent explicit
Decision ID in the Recorder correlation object). Lians binds only events that
reference the same authenticated namespace, visible information-barrier scope,
and authoritative DecisionRecord. A trace without that binding remains execution
evidence, not automatically cited decision evidence.

| OpenTelemetry evidence | Receipt location | Required interpretation |
|---|---|---|
| trace ID, span ID, conversation ID | `correlation` | Identifies recorded execution context; it does not authenticate the actor. |
| `gen_ai.request.model`, `gen_ai.response.model`, provider attributes | `model` | Preserve the observed identifier/version distinction and disclose missing versions. |
| system-instruction/configuration digest | `model.system_instruction_hash` or open model metadata | Store a digest or reference; never imply hidden instructions were captured. |
| structured input/output messages | `artifacts.input_hash`, `artifacts.output_hash` | Hash after mandatory secret redaction under the declared capture mode. |
| tool spans and tool-call/result identifiers | `tools` plus normalized evidence links | A tool is `used` only when a call/result was recorded and explicitly linked. |
| retrieval/source attributes | `sources` | Availability or retrieval is not citation; relation and match basis remain explicit. |
| `lians.policy.version` and policy result attributes | `policy` | A version label alone is weaker than a recorded policy evaluation. |
| end-user/principal attributes | open authorization metadata | Caller values are claims. `actor.recorded_by` comes only from authenticated Lians provenance. |
| span status and terminal output | `decision.outcome` and reconstruction evidence | The authoritative DecisionRecord owns the decision outcome. A span status is supporting evidence. |

Receipt completeness MUST name every field that could not be established. In
particular, a complete OTLP trace does not by itself establish source validity,
permission scope, human review, or a trusted policy evaluation.

See the [Universal Recorder OpenTelemetry mapping](../../../universal-recorder/v0.1/mappings/opentelemetry-genai.md)
for the wire-level normalization rules.
