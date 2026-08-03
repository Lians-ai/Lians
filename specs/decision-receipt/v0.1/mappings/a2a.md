# Agent2Agent Protocol to Decision Receipt v0.1

A2A Tasks, Messages, status updates, and Artifacts can contribute cross-agent
evidence to a Decision Receipt. They do not by themselves authenticate a remote
agent, prove that a message history is exhaustive, or establish that an artifact
was causally used by the final decision.

| A2A evidence | Receipt location | Required interpretation |
|---|---|---|
| task ID and context ID | `correlation` | Stable task scope; propagate the authoritative Decision ID separately. |
| message ID, role, and parts | input/output `artifacts` or open evidence metadata | Communication is not automatically a decision outcome. Hash/redact according to capture policy. |
| terminal task status and timestamp | reconstruction timeline and outcome evidence | The DecisionRecord remains authoritative for the decision outcome. |
| Task Artifact or artifact update | `sources`, `tools`, or output evidence according to its declared role | Preserve artifact identity/version/hash and an explicit evidence relation. |
| remote agent/card identity | claimed actor or open metadata | Trust requires an independently authenticated identity binding; self-asserted names are not principals. |
| extensions carrying model or policy versions | `model` or `policy` | Preserve observed values and disclose when no signed evaluation/version evidence exists. |

Streaming and push updates may be delivered at least once. Producers SHOULD
preserve message/task/context identities and supply a stable event or idempotency
key for repeated artifact chunks. Receipt construction uses only normalized,
integrity-checked events explicitly bound to the decision and its namespace and
barrier view.

See the [Universal Recorder A2A mapping](../../../universal-recorder/v0.1/mappings/a2a.md)
for the wire-level normalization rules.
