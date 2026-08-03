/** Decision Evidence API surface tests. */
import { beforeEach, describe, expect, it, jest } from "@jest/globals";
import {
  LiansClient,
  type DecisionCreate,
  type DecisionImpactResult,
  type DecisionOut,
  type DecisionReceipt,
  type DecisionReceiptVerificationResult,
} from "./index.js";

type MockResponse = { ok: boolean; status: number; body: unknown };

function mockFetch(response: MockResponse) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const fn = (jest.fn() as any).mockResolvedValue({
    ok: response.ok,
    status: response.status,
    json: () => Promise.resolve(response.body),
    text: () => Promise.resolve(JSON.stringify(response.body)),
    statusText: "OK",
  });
  global.fetch = fn as unknown as typeof fetch;
  return fn;
}

const NOW = "2026-08-02T04:00:00Z";
const HASH = "a".repeat(64);

const DECISION: DecisionOut = {
  id: "decision-42",
  namespace: "underwriting",
  agent_id: "underwriter-1",
  recorded_by_principal_ref: "lians:principal:v1:api-key:00000000-0000-0000-0000-000000000042",
  recorded_by_auth_method: "api_key",
  recorded_by_credential_ref: `lians:credential:v1:sha256:${HASH}`,
  recorded_by_principal_type: null,
  recorded_by_role: null,
  recorded_by_scopes: [],
  decision_type: "credit_application",
  outcome: "manual_review",
  reason_codes: ["DTI_NEAR_LIMIT"],
  regime: "ECOA_REG_B",
  subject_id: "applicant-42",
  session_id: "session-42",
  model_id: "credit-model",
  model_version: "2026-08-01",
  policy_version: "credit-policy-17",
  decided_at: NOW,
  recorded_at: NOW,
  knowledge_as_of: NOW,
  knowledge_recorded_as_of: NOW,
  evidence_memory_ids: ["memory-42"],
  input_hash: HASH,
  output_hash: HASH,
  human_review_status: "requested",
  human_reviewer: null,
  human_reviewed_at: null,
  supersedes_id: null,
  metadata: {},
  record_hash_version: 2,
  record_integrity_status: "verified",
  record_hash: HASH,
};

const RECEIPT: DecisionReceipt = {
  $schema: "https://lians.ai/specs/decision-receipt/v0.1/schema.json",
  receipt_version: "0.1",
  receipt_id: "urn:lians:decision-receipt:decision-42",
  issued_at: NOW,
  issuer: {
    name: "Lians",
    category: "decision_evidence_infrastructure",
    key_id: null,
  },
  decision: {
    id: DECISION.id,
    namespace: DECISION.namespace,
    type: DECISION.decision_type,
    outcome: DECISION.outcome,
    reason_codes: DECISION.reason_codes,
    regime: DECISION.regime,
    subject_id: DECISION.subject_id,
    decided_at: DECISION.decided_at,
    recorded_at: DECISION.recorded_at,
    knowledge_as_of: DECISION.knowledge_as_of,
    knowledge_recorded_as_of: DECISION.knowledge_recorded_as_of,
    record_hash: DECISION.record_hash,
    record_hash_version: DECISION.record_hash_version,
    record_integrity_status: DECISION.record_integrity_status,
    supersedes_id: null,
  },
  actor: {
    agent_id: DECISION.agent_id,
    claimed_agent_id: DECISION.agent_id,
    principal: {
      id: DECISION.recorded_by_principal_ref,
      auth_method: DECISION.recorded_by_auth_method,
      credential_ref: DECISION.recorded_by_credential_ref,
    },
    recorded_by: {
      principal_ref: DECISION.recorded_by_principal_ref,
      auth_method: DECISION.recorded_by_auth_method,
      credential_ref: DECISION.recorded_by_credential_ref,
    },
  },
  model: {
    provider: null,
    id: DECISION.model_id,
    version: DECISION.model_version,
    system_instruction_hash: null,
    configuration_hash: null,
  },
  artifacts: { input_hash: HASH, output_hash: HASH },
  tools: [],
  sources: [],
  policy: { version: DECISION.policy_version, evaluation: null },
  authorization: null,
  human_review: { status: "requested", reviewer: null, reviewed_at: null },
  correlation: { session_id: "session-42", trace_id: null, span_id: null },
  reconstruction: {
    knowledge_as_of: NOW,
    knowledge_recorded_as_of: NOW,
    snapshot_count: 0,
    cited_source_count: 0,
    snapshot_manifest: [],
  },
  audit_chain: { status: "ok", rows_checked: 3, violations: [] },
  completeness: {
    score: 50,
    grade: "C",
    status: "incomplete",
    checks: [],
    missing: ["sources.provenance"],
  },
  integrity: {
    hash_algorithm: "sha-256",
    canonicalization: "json-sort-keys-utf8-v1",
    receipt_hash: HASH,
    signature: null,
  },
};

let client: LiansClient;

beforeEach(() => {
  jest.restoreAllMocks();
  client = new LiansClient({
    baseUrl: "https://mem.example.com",
    apiKey: "test-key",
  });
});

describe("recordDecision()", () => {
  it("POSTs both event-time and recording-time knowledge cutoffs", async () => {
    const fetchMock = mockFetch({ ok: true, status: 200, body: DECISION });
    const request: DecisionCreate = {
      agent_id: "underwriter-1",
      decision_type: "credit_application",
      outcome: "manual_review",
      reason_codes: ["DTI_NEAR_LIMIT"],
      decided_at: NOW,
      knowledge_as_of: "2026-08-01T23:59:59Z",
      knowledge_recorded_as_of: "2026-08-02T00:00:00Z",
      evidence_memory_ids: ["memory-42"],
      input_hash: HASH,
      output_hash: HASH,
    };

    const result = await client.recordDecision(request);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://mem.example.com/v1/decisions");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual(request);
    expect(result.knowledge_recorded_as_of).toBe(NOW);
  });
});

describe("decisionReceipt()", () => {
  it("GETs a typed v0.1 receipt and forwards the verify option", async () => {
    const fetchMock = mockFetch({ ok: true, status: 200, body: RECEIPT });

    const result = await client.decisionReceipt("decision-42", { verify: false });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(
      "https://mem.example.com/v1/decisions/decision-42/receipt?verify=false",
    );
    expect(init.method).toBe("GET");
    expect(result.receipt_version).toBe("0.1");
    expect(result.reconstruction.knowledge_recorded_as_of).toBe(NOW);
  });

  it("uses the server's verification default when no option is supplied", async () => {
    const fetchMock = mockFetch({ ok: true, status: 200, body: RECEIPT });

    await client.decisionReceipt("decision-42");

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://mem.example.com/v1/decisions/decision-42/receipt");
  });
});

describe("verifyDecisionReceipt()", () => {
  it("POSTs the receipt and trust policy to the database-independent verifier", async () => {
    const response: DecisionReceiptVerificationResult = {
      valid: true,
      hash_valid: true,
      signature_present: true,
      signature_valid: true,
      trusted_key: true,
      receipt_hash: HASH,
      errors: [],
    };
    const fetchMock = mockFetch({ ok: true, status: 200, body: response });

    const result = await client.verifyDecisionReceipt({
      receipt: RECEIPT,
      trusted_public_key: "ab".repeat(32),
      require_signature: true,
    });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://mem.example.com/v1/receipts/verify");
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body as string);
    expect(body.receipt.receipt_id).toBe(RECEIPT.receipt_id);
    expect(body.require_signature).toBe(true);
    expect(result.trusted_key).toBe(true);
  });
});

describe("assessDecisionImpact()", () => {
  it("POSTs a dependency change and returns ranked affected decisions", async () => {
    const response: DecisionImpactResult = {
      dependency: { kind: "policy", value: "credit-policy-17" },
      change_type: "retired",
      assessed_at: NOW,
      total: 1,
      direct_count: 1,
      reachable_count: 0,
      search_truncated: false,
      change_event_id: "event-42",
      analysis_mode: "indexed",
      indexed_decisions_matched: 1,
      legacy_decisions_matched: 0,
      legacy_candidates_scanned: 0,
      legacy_fallback_truncated: false,
      total_is_lower_bound: false,
      legacy_fallback_scope: "incomplete_kind_coverage",
      items: [
        {
          decision: DECISION,
          match_basis: ["decision.policy_version"],
          impact_status: "direct_reference",
          risk_score: 85,
          priority: "critical",
        },
      ],
    };
    const fetchMock = mockFetch({ ok: true, status: 200, body: response });

    const result = await client.assessDecisionImpact({
      dependency_kind: "policy",
      dependency_value: "credit-policy-17",
      change_type: "retired",
      occurred_at: NOW,
      note: "Policy was superseded",
      record_event: true,
      limit: 25,
    });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://mem.example.com/v1/decisions/impact");
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body as string);
    expect(body.dependency_kind).toBe("policy");
    expect(body.record_event).toBe(true);
    expect(result.items[0]?.priority).toBe("critical");
  });
});
