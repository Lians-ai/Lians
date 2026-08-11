export type IncidentEventId = "decision" | "income-correction" | "policy-retirement";
export type ImpactLabel = "Direct reference" | "Reachable" | "Estimated";

export type TimelineEvent = {
  id: IncidentEventId;
  step: string;
  date: string;
  time: string;
  eyebrow: string;
  title: string;
  summary: string;
  tone: "ink" | "amber" | "blue";
};

export type BoundaryItem = {
  kind: string;
  title: string;
  value: string;
  detail: string;
  evidenceUse: "Direct reference" | "Recorded context";
  reference: string;
};

export type ImpactedDecision = {
  id: string;
  subject: string;
  outcome: string;
  reason: string;
  label: ImpactLabel;
  queueStatus: string;
  risk: "High" | "Medium" | "Low";
};

export const incident = {
  id: "INC-2026-0718-04",
  applicationId: "8127",
  decisionId: "7f4eac31-8df0-4e7d-9d2a-812700000001",
  decidedAt: "2026-07-12T18:32:14Z",
  decidedAtLabel: "July 12, 2026 at 2:32 PM ET",
  outcome: "Declined",
  reasonCode: "DTI_HIGH",
  policy: "Consumer Lending Policy 4.2",
  model: "credit-risk-v3.2",
  originalIncome: "$72,000",
  correctedIncome: "$96,000",
  originalDti: "47.8%",
  correctedDti: "35.9%",
  completeness: {
    grade: "A",
    score: "12 / 12",
    status: "Complete within declared boundary",
    exclusion:
      "Provider-internal calculations and hidden model cognition were outside the declared capture boundary.",
  },
  timeline: [
    {
      id: "decision",
      step: "01",
      date: "JUL 12",
      time: "2:32 PM",
      eyebrow: "RECORDED DECISION",
      title: "Application 8127 was declined",
      summary:
        "The agent cited a 47.8% debt-to-income ratio under policy 4.2. The receipt froze the evidence available at that moment.",
      tone: "ink",
    },
    {
      id: "income-correction",
      step: "02",
      date: "JUL 18",
      time: "9:14 AM",
      eyebrow: "SOURCE CORRECTION",
      title: "Income was corrected to $96,000",
      summary:
        "The provider superseded its original $72,000 response. The original receipt remains intact and the correction is linked as a later event.",
      tone: "amber",
    },
    {
      id: "policy-retirement",
      step: "03",
      date: "JUL 20",
      time: "4:05 PM",
      eyebrow: "POLICY CHANGE",
      title: "Policy 4.2 was retired",
      summary:
        "Version 4.3 replaced the underwriting rule set. Lians can identify recorded references and separately label broader reachability.",
      tone: "blue",
    },
  ] satisfies TimelineEvent[],
  boundary: [
    {
      kind: "SOURCE",
      title: "Verified income",
      value: "$72,000 annual income",
      detail: "income.verify response received July 12 at 2:31:48 PM ET",
      evidenceUse: "Direct reference",
      reference: "income_record inc_8843 · version 1",
    },
    {
      kind: "MODEL",
      title: "Risk classifier",
      value: "credit-risk-v3.2",
      detail: "Configuration and output hashes captured; internal reasoning not claimed",
      evidenceUse: "Recorded context",
      reference: "model_config sha256:9be4…d271",
    },
    {
      kind: "POLICY",
      title: "Underwriting rule",
      value: "Consumer Lending Policy 4.2",
      detail: "Automatic decline when verified DTI exceeds 43%",
      evidenceUse: "Direct reference",
      reference: "policy LND-4.2 · signed June 30",
    },
    {
      kind: "PERMISSIONS",
      title: "Acting principal",
      value: "underwriting-agent-prod",
      detail: "application:read · income:verify · decision:write",
      evidenceUse: "Recorded context",
      reference: "barrier consumer-lending · scope snapshot 8c41",
    },
    {
      kind: "TOOL RESULT",
      title: "Income verification call",
      value: "income.verify → verified",
      detail: "Result payload and tool definition hashes were captured",
      evidenceUse: "Direct reference",
      reference: "tool_call tc_9A12 · 384 ms",
    },
    {
      kind: "HUMAN REVIEW",
      title: "Review state at decision time",
      value: "Not requested",
      detail: "Policy 4.2 permitted automatic disposition for this reason code",
      evidenceUse: "Recorded context",
      reference: "oversight rule HR-02 · evaluated",
    },
  ] satisfies BoundaryItem[],
  impacts: [
    {
      id: "APP-8127",
      subject: "Application 8127",
      outcome: "Declined",
      reason: "Cited income record inc_8843 v1 and policy 4.2",
      label: "Direct reference",
      queueStatus: "Review required",
      risk: "High",
    },
    {
      id: "APP-8096",
      subject: "Application 8096",
      outcome: "Manual review",
      reason: "Receipt directly references retired policy 4.2",
      label: "Direct reference",
      queueStatus: "Policy review",
      risk: "Medium",
    },
    {
      id: "WF-UNDERWRITE-06",
      subject: "6 pending applications",
      outcome: "Not yet decided",
      reason: "Workflow can retrieve the corrected source and still loads policy 4.2",
      label: "Reachable",
      queueStatus: "Monitor",
      risk: "Medium",
    },
    {
      id: "SIM-8127-R1",
      subject: "Application 8127 scenario",
      outcome: "May differ",
      reason: "A sandboxed comparison estimates a different DTI result; no outcome is guaranteed",
      label: "Estimated",
      queueStatus: "Scenario only",
      risk: "Low",
    },
  ] satisfies ImpactedDecision[],
} as const;

const completenessChecks = [
  {
    id: "decision.identity",
    label: "Decision and agent identity",
    weight: 8,
    status: "present",
    evidence: "decision.id, decision.agent_id, decision.decision_type",
  },
  {
    id: "decision.time",
    label: "Decision-time boundary",
    weight: 7,
    status: "present",
    evidence:
      "decision.decided_at, decision.knowledge_as_of, decision.knowledge_recorded_as_of",
  },
  {
    id: "model.identity",
    label: "Model identity and version",
    weight: 10,
    status: "present",
    evidence: "model.id, model.version",
  },
  {
    id: "instructions.hash",
    label: "System instruction/configuration hash",
    weight: 7,
    status: "present",
    evidence: "model.system_instruction_hash",
  },
  {
    id: "artifacts.hashes",
    label: "Input and output hashes",
    weight: 10,
    status: "present",
    evidence: "artifacts.input_hash, artifacts.output_hash",
  },
  {
    id: "sources.provenance",
    label: "Versioned cited sources",
    weight: 12,
    status: "present",
    evidence: "sources[].source, content_hash, valid_from",
  },
  {
    id: "policy.evaluation",
    label: "Policy version and evaluation",
    weight: 10,
    status: "present",
    evidence: "policy.version, policy.evaluation",
  },
  {
    id: "authorization.context",
    label: "Principal and authorization context",
    weight: 10,
    status: "present",
    evidence: "actor.principal, authorization",
  },
  {
    id: "tools.provenance",
    label: "Tool definitions and results",
    weight: 8,
    status: "present",
    evidence: "tools[].definition_hash, tools[].result_hash",
  },
  {
    id: "review.status",
    label: "Human-review status",
    weight: 6,
    status: "present",
    evidence: "human_review.status",
  },
  {
    id: "integrity.audit_chain",
    label: "Audit-chain verification",
    weight: 7,
    status: "present",
    evidence: "audit_chain.status",
  },
  {
    id: "integrity.signature",
    label: "Deployment signature",
    weight: 5,
    status: "present",
    evidence: "integrity.signature",
  },
] as const;

// This fixture is the exact Decision Receipt v0.1 shape produced by
// agentmem/src/lians/decision_receipt.py. The hash covers every field except
// integrity using json-sort-keys-utf8-v1; the Ed25519 signature covers the raw
// 32-byte SHA-256 digest. Local review interactions never rewrite this object.
export const decisionReceipt = {
  $schema: "https://lians.ai/specs/decision-receipt/v0.1/schema.json",
  receipt_version: "0.1",
  receipt_id:
    "urn:lians:decision-receipt:7f4eac31-8df0-4e7d-9d2a-812700000001",
  issued_at: "2026-07-12T18:32:15Z",
  issuer: {
    name: "Lians",
    category: "decision_evidence_infrastructure",
    key_id: "lians-synthetic-demo-2026-01",
  },
  decision: {
    id: "7f4eac31-8df0-4e7d-9d2a-812700000001",
    namespace: "synthetic-lending-demo",
    type: "credit_application",
    outcome: "declined",
    reason_codes: ["DTI_HIGH"],
    regime: "ECOA_REG_B",
    subject_id: "application-8127",
    decided_at: "2026-07-12T18:32:14Z",
    recorded_at: "2026-07-12T18:32:15Z",
    knowledge_as_of: "2026-07-12T18:32:14Z",
    knowledge_recorded_as_of: "2026-07-12T18:32:13Z",
    record_hash:
      "2e511d1b09619726d534e0eedf3e6e76b40fd5d26c870f6506dcf41b76ffeabb",
    supersedes_id: null,
  },
  actor: {
    agent_id: "underwriting-agent-prod",
    principal: {
      id: "underwriting-agent-prod",
      type: "service",
    },
  },
  model: {
    provider: "provider-neutral-demo",
    id: "credit-risk",
    version: "3.2",
    system_instruction_hash:
      "8063c7a5c6fad6e05fde104a3cc01a68fe8fbecb6441dd51cfbd2ae4881de5f3",
    configuration_hash:
      "0a93ac401bc0cdf7f4957696433d767a85e89bc80e445fa084de3ad9140663ec",
  },
  artifacts: {
    input_hash:
      "b2c1e24c6b39b3fe52976d75aa75f5db3777951d4d9190aa3e64797aae986b0a",
    output_hash:
      "67d67240d98cab6152a3b93ebd2e70ac5ee0fc5b2e74d99531c37b6987c2218f",
  },
  tools: [
    {
      name: "income.verify",
      call_id: "tc_9A12",
      definition_hash:
        "6a87e268b11d382de005d326ff1be3868ddc332794c94316225342ade14705ba",
      result_hash:
        "48c636bc4259f55274461ed3172921de3a78c1c56c1b751e508fa3ca7ce3f7b5",
      duration_ms: 384,
    },
  ],
  sources: [
    {
      memory_id: "5e12ea1f-5ca2-41d7-bab8-812700000001",
      source: "verified-income-service",
      source_version: "inc_8843:v1",
      content: "Applicant verified annual income is USD 72000.",
      content_hash:
        "65f9d6386fdfdeffb8074d91d83729c1fda5a6b8c49e10c45f573568bf676083",
      valid_from: "2026-07-12T18:31:48Z",
      valid_to: null,
      recorded_at: "2026-07-12T18:31:49Z",
      erased_at: null,
    },
  ],
  policy: {
    version: "LND-4.2",
    evaluation: {
      decision: "decline",
      rule_ids: ["dti-threshold"],
      evaluated_dti: 0.478,
      maximum_dti: 0.43,
    },
  },
  authorization: {
    decision: "allow",
    scopes: ["application:read", "income:verify", "decision:write"],
    barrier_group: "consumer-lending",
  },
  human_review: {
    status: "not_requested",
    reviewer: null,
    reviewed_at: null,
  },
  correlation: {
    session_id: "sess-8127-20260712",
    trace_id: "1234567890abcdef1234567890abcdef",
    span_id: "9a12000000008127",
  },
  reconstruction: {
    knowledge_as_of: "2026-07-12T18:32:14Z",
    knowledge_recorded_as_of: "2026-07-12T18:32:13Z",
    snapshot_count: 3,
    cited_source_count: 1,
    snapshot_manifest: [
      {
        memory_id: "5e12ea1f-5ca2-41d7-bab8-812700000003",
        content_hash:
          "eebfb93063133007c4b5c8a3cb0387bf1bb23a53e2d240bd3b0230da618fa2b5",
        valid_from: "2026-07-12T18:30:00Z",
        valid_to: null,
      },
      {
        memory_id: "5e12ea1f-5ca2-41d7-bab8-812700000002",
        content_hash:
          "2e360a95d51513f6372554a445b9ddffb00e077861bef96e36b667124499e6fe",
        valid_from: "2026-07-12T18:31:32Z",
        valid_to: null,
      },
      {
        memory_id: "5e12ea1f-5ca2-41d7-bab8-812700000001",
        content_hash:
          "65f9d6386fdfdeffb8074d91d83729c1fda5a6b8c49e10c45f573568bf676083",
        valid_from: "2026-07-12T18:31:48Z",
        valid_to: null,
      },
    ],
  },
  audit_chain: {
    status: "ok",
    rows_checked: 18,
    violations: [],
    hash_version: 2,
  },
  completeness: {
    score: 100,
    grade: "A",
    status: "complete",
    checks: completenessChecks,
    missing: [],
  },
  integrity: {
    hash_algorithm: "sha-256",
    canonicalization: "json-sort-keys-utf8-v1",
    receipt_hash:
      "6cd905cc1012e3c37861128785d8321fd717a684f6fbdfcd8512fe183644950e",
    signature: {
      algorithm: "ed25519",
      key_id: "lians-synthetic-demo-2026-01",
      public_key: "A6EHv/POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg=",
      value:
        "XwwNtpTRfl0UzBtEb1GSnrawkyxZMsx1B/tjPsZ3wNlzdXUZtxFVs2EZdyKkhR2tBginInMd5sxccOQE+jwpDg==",
    },
  },
} as const;
