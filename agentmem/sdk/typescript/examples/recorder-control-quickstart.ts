/** Synthetic Universal Recorder + Gate + attested-remediation workflow. */

import { createHash } from "node:crypto";

import {
  LiansClient,
  a2aEvent,
  liansEvent,
  mcpJsonRpcEvent,
  otlpGenAiSpan,
} from "../src/index.js";

const baseUrl = process.env.LIANS_URL ?? "http://localhost:8000";
const apiKey = process.env.LIANS_API_KEY;
const accessToken = process.env.LIANS_ACCESS_TOKEN;
const mediatorApiKey = process.env.LIANS_MEDIATOR_API_KEY;
// Without a separate mediator credential the zero-UUID fallback is
// intentionally unredeemable; the evaluator never consumes its own permit.
const configuredMediatorPrincipalId =
  process.env.LIANS_MEDIATOR_PRINCIPAL_REF ??
  "lians:principal:v1:api-key:00000000-0000-0000-0000-000000000000";
if (!apiKey && !accessToken) {
  throw new Error("Set LIANS_API_KEY or LIANS_ACCESS_TOKEN; credentials are never printed.");
}
if (mediatorApiKey && apiKey && mediatorApiKey === apiKey) {
  throw new Error("LIANS_MEDIATOR_API_KEY must be a separate credential.");
}

function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const object = value as Record<string, unknown>;
  return `{${Object.keys(object)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(object[key])}`)
    .join(",")}}`;
}

async function main(): Promise<void> {
const suffix = Date.now().toString(36);
const runId = `synthetic-ts-review-${suffix}`;
const traceId = "fedcba9876543210fedcba9876543210";
const occurredAt = new Date();
const client = new LiansClient({ baseUrl, apiKey, accessToken });

const events = [
  liansEvent(
    "decision.started",
    {
      name: "synthetic-ts-review",
      phase: "started",
      model_id: "synthetic-model",
      policy_version: "quickstart-ts-1",
      input: { order_id: "SYNTHETIC-TS-001" },
      evidence: ["synthetic-source:catalog-v1"],
    },
    {
      runId,
      traceId,
      spanId: "fedcba9876543210",
      agentId: "synthetic-ts-agent",
      occurredAt,
      idempotencyKey: `${runId}:decision-started`,
    },
  ),
  otlpGenAiSpan({
    runId,
    traceId,
    spanId: "edcba98765432101",
    operation: "chat",
    model: "synthetic-model",
    input: [{ role: "user", content: "Review a synthetic order" }],
    output: [{ role: "assistant", content: "Synthetic review complete" }],
    agentId: "synthetic-ts-agent",
    occurredAt,
    endedAt: occurredAt,
  }),
  mcpJsonRpcEvent(
    {
      jsonrpc: "2.0",
      id: "synthetic-ts-tool-1",
      method: "tools/call",
      params: { name: "synthetic_lookup", arguments: { sku: "SYNTHETIC-SKU" } },
    },
    { runId, toolName: "synthetic_lookup", agentId: "synthetic-ts-agent", occurredAt },
  ),
  a2aEvent(
    {
      kind: "task",
      id: "synthetic-ts-task-1",
      contextId: runId,
      status: { state: "completed", timestamp: occurredAt.toISOString() },
      artifacts: [{ name: "synthetic-review", parts: [{ text: "approved" }] }],
    },
    { runId, agentId: "synthetic-ts-agent" },
  ),
];

const principal = await client.whoami();
let mediatorPrincipalId = configuredMediatorPrincipalId;
if (mediatorApiKey) {
  const mediatorIdentity = await new LiansClient({
    baseUrl,
    apiKey: mediatorApiKey,
  }).whoami();
  mediatorPrincipalId = mediatorIdentity.principal_id ?? "";
}
if (
  !principal.principal_id ||
  !mediatorPrincipalId ||
  mediatorPrincipalId === principal.principal_id
) {
  throw new Error("The evaluator and mediator must be separate identities.");
}
const capabilities = await client.platformCapabilities();
const platform = await client.platformReadiness();
console.log("platform", {
  status: platform.status,
  recorder_version: capabilities.components.recorder?.version,
});
const batch = await client.ingestRecorderBatch(events);
const recorderRunId = batch.results[0]?.readiness.run_id;
if (!recorderRunId) throw new Error("Recorder accepted no events");
const readiness = await client.recorderRunReadiness(recorderRunId);
console.log("recorder", { accepted: batch.accepted, score: readiness.score, ready: readiness.receipt_ready });

let policy = await client.createGatePolicy({
  name: `synthetic-ts-release-gate-${suffix}`,
  version: "quickstart-ts-1",
  default_disposition: "deny",
  protected_actions: ["synthetic.order.release"],
  target_ref_prefixes: ["urn:lians:synthetic-order:"],
  enforcement_principal_ids: [mediatorPrincipalId],
  maximum_permit_ttl_seconds: 30,
  rules: [{
    name: "require-recorded-policy",
    applies_to_risk_levels: ["high", "critical"],
    require_policy_attached: true,
    action_on_failure: "deny",
  }],
  metadata: { synthetic: true },
});
policy = await client.activateGatePolicy(policy.id);
const decision = await client.recordDecision({
  agent_id: "synthetic-ts-agent",
  decision_type: "order_release",
  outcome: "approved",
  decided_at: occurredAt.toISOString(),
  policy_version: "quickstart-ts-1",
  metadata: { risk_level: "high", synthetic: true },
});
const providerRequest = {
  action: "synthetic.order.release",
  target_ref: "urn:lians:synthetic-order:SYNTHETIC-TS-001",
  decision_id: decision.id,
  arguments: { order_id: "SYNTHETIC-TS-001", synthetic: true },
};
const executionRequestHash = createHash("sha256")
  .update(canonicalJson(providerRequest))
  .digest("hex");
const verdict = await client.evaluateGate({
  action: "synthetic.order.release",
  target_ref: "urn:lians:synthetic-order:SYNTHETIC-TS-001",
  decision_id: decision.id,
  enforcement_principal_id: mediatorPrincipalId,
  permit_ttl_seconds: 30,
  execution_request_hash: executionRequestHash,
  risk_level: "high",
  policy_set_id: policy.id,
  context: { synthetic: true, recorder_run_id: recorderRunId },
});
const permit = verdict.execution_permit;
let permitConsumed = false;
if (permit && mediatorApiKey) {
  // A real mediator derives these claims from its normalized actual request
  // and recomputes the digest instead of trusting the evaluator's copy.
  const mediatorRequestHash = createHash("sha256")
    .update(canonicalJson(providerRequest))
    .digest("hex");
  const mediator = new LiansClient({ baseUrl, apiKey: mediatorApiKey });
  await mediator.consumeGateExecutionPermit({
    permit_id: permit.permit_id,
    token: permit.token,
    action: providerRequest.action,
    target_ref: providerRequest.target_ref,
    decision_id: providerRequest.decision_id,
    execution_request_hash: mediatorRequestHash,
  });
  permitConsumed = true;
}
console.log("gate", {
  principal: principal.principal_id,
  disposition: verdict.disposition,
  permit_id: permit?.permit_id,
  permit_consumed: permitConsumed,
});

const investigation = await client.createInvestigationCase({
  title: "Synthetic TypeScript control-plane exercise",
  severity: "low",
  gate_decision_id: verdict.id,
  metadata: { synthetic: true },
});
const task = await client.createRemediationTask(investigation.id, {
  expected_case_updated_at: investigation.updated_at,
  title: "Attest synthetic evidence review",
  owner_principal: principal.principal_id ?? undefined,
  metadata: { synthetic: true },
});
const taskInProgress = await client.updateRemediationTask(task.id, {
  expected_updated_at: task.updated_at,
  status: "in_progress",
});
const taskClosed = await client.closeRemediationTask(task.id, {
  expected_updated_at: taskInProgress.updated_at,
  statement: "Synthetic evidence review completed.",
  evidence_refs: [`gate:${verdict.id}`, `recorder-run:${recorderRunId}`],
});
const caseBeforeClose = await client.investigationCase(investigation.id);
const caseClosed = await client.closeInvestigationCase(investigation.id, {
  expected_updated_at: caseBeforeClose.updated_at,
  statement: "Synthetic exercise closed after the owned task was attested.",
  evidence_refs: [`attestation:${taskClosed.attestation.id}`],
  resolution_summary: "Synthetic control-plane path completed.",
});
console.log("investigation", { case_id: investigation.id, status: caseClosed.status });
}

main().catch((error: unknown) => {
  // The SDK never logs request bodies or credentials. Keep example failures terse.
  console.error(error instanceof Error ? error.message : "Synthetic quickstart failed");
  process.exitCode = 1;
});
