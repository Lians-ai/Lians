/**
 * AgentMem TypeScript SDK — unit tests
 *
 * All tests use a mock fetch so no real API is needed.  The mock validates that:
 *   1. The client sends the correct HTTP method, path, and body.
 *   2. Timestamps are serialised to ISO 8601 strings.
 *   3. Error responses are surfaced as AgentMemError with status + body.
 *   4. Admin endpoints include the X-Admin-Secret header.
 *   5. Query parameters are serialised correctly for GET requests.
 */
import { describe, it, expect, beforeEach, jest } from "@jest/globals";
import { AgentMemClient, AgentMemError } from "./client.js";

// ── Mock fetch ───────────────────────────────────────────────────────────────

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

const MEMORY_FIXTURE = {
  id: "mem-uuid-1",
  namespace: "test-ns",
  agent_id: "agent-1",
  content: "The equity desk target for AAPL is $210",
  subject_id: null,
  event_time: "2026-06-01T12:00:00Z",
  ingestion_time: "2026-06-01T12:00:01Z",
  valid_from: "2026-06-01T12:00:00Z",
  valid_to: null,
  superseded_by: null,
  supersession_confidence: null,
  barrier_group: null,
  importance: 0.8,
  source: "analyst-note",
  content_hash: "abc123",
  erased_at: null,
  metadata: {},
};

// ── Setup ────────────────────────────────────────────────────────────────────

let client: AgentMemClient;

beforeEach(() => {
  client = new AgentMemClient({
    baseUrl: "https://mem.example.com",
    apiKey: "test-key",
    adminSecret: "admin-secret",
  });
  jest.restoreAllMocks();
});

// ── Client construction ──────────────────────────────────────────────────────

describe("AgentMemClient construction", () => {
  it("strips trailing slash from baseUrl", async () => {
    const c = new AgentMemClient({ baseUrl: "https://mem.example.com/", apiKey: "k" });
    const fetchMock = mockFetch({ ok: true, status: 200, body: MEMORY_FIXTURE });
    await c.addMemory({ agent_id: "a", content: "x", event_time: "2026-01-01T00:00:00Z" });
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://mem.example.com/v1/memories");
  });

  it("sends X-API-Key header on every request", async () => {
    const fetchMock = mockFetch({ ok: true, status: 200, body: MEMORY_FIXTURE });
    await client.addMemory({ agent_id: "a", content: "x", event_time: "2026-01-01T00:00:00Z" });
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>)["X-API-Key"]).toBe("test-key");
  });
});

// ── addMemory ────────────────────────────────────────────────────────────────

describe("addMemory()", () => {
  it("POST /v1/memories with correct body", async () => {
    const fetchMock = mockFetch({ ok: true, status: 200, body: MEMORY_FIXTURE });

    const result = await client.addMemory({
      agent_id: "agent-1",
      content: "The equity desk target for AAPL is $210",
      event_time: "2026-06-01T12:00:00Z",
      source: "analyst-note",
      importance: 0.8,
    });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://mem.example.com/v1/memories");
    expect(init.method).toBe("POST");

    const body = JSON.parse(init.body as string);
    expect(body.agent_id).toBe("agent-1");
    expect(body.event_time).toBe("2026-06-01T12:00:00Z");
    expect(body.importance).toBe(0.8);

    expect(result.id).toBe("mem-uuid-1");
    expect(result.content).toBe("The equity desk target for AAPL is $210");
  });
});

// ── batchAdd ─────────────────────────────────────────────────────────────────

describe("batchAdd()", () => {
  it("POST /v1/memories/batch with memories array", async () => {
    const batchResponse = {
      added: 2,
      memories: [MEMORY_FIXTURE, { ...MEMORY_FIXTURE, id: "mem-uuid-2" }],
    };
    const fetchMock = mockFetch({ ok: true, status: 200, body: batchResponse });

    const result = await client.batchAdd([
      { agent_id: "a", content: "first", event_time: "2026-06-01T10:00:00Z" },
      { agent_id: "a", content: "second", event_time: "2026-06-01T11:00:00Z" },
    ]);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://mem.example.com/v1/memories/batch");
    expect(init.method).toBe("POST");

    const body = JSON.parse(init.body as string);
    expect(body.memories).toHaveLength(2);
    expect(body.memories[0].content).toBe("first");

    expect(result.added).toBe(2);
    expect(result.memories).toHaveLength(2);
  });
});

// ── recall ───────────────────────────────────────────────────────────────────

describe("recall()", () => {
  it("POST /v1/recall with correct body", async () => {
    const recallResponse = { memories: [MEMORY_FIXTURE], as_of: null, total_candidates: 1 };
    const fetchMock = mockFetch({ ok: true, status: 200, body: recallResponse });

    const result = await client.recall({
      agent_id: "agent-1",
      query: "AAPL price target",
      k: 3,
    });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://mem.example.com/v1/recall");
    expect(init.method).toBe("POST");

    const body = JSON.parse(init.body as string);
    expect(body.agent_id).toBe("agent-1");
    expect(body.query).toBe("AAPL price target");
    expect(body.k).toBe(3);

    expect(result.memories).toHaveLength(1);
    expect(result.total_candidates).toBe(1);
  });
});

// ── eraseSubject ──────────────────────────────────────────────────────────────

describe("eraseSubject()", () => {
  it("POST /v1/erase with subject_id and request_ref", async () => {
    const eraseResponse = { subject_id: "sub-123", memories_erased: 5, request_ref: "DSAR-2026-001" };
    const fetchMock = mockFetch({ ok: true, status: 200, body: eraseResponse });

    const result = await client.eraseSubject({
      subject_id: "sub-123",
      request_ref: "DSAR-2026-001",
    });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://mem.example.com/v1/erase");
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body as string);
    expect(body.subject_id).toBe("sub-123");

    expect(result.memories_erased).toBe(5);
  });
});

// ── listConflicts ─────────────────────────────────────────────────────────────

describe("listConflicts()", () => {
  it("GET /v1/conflicts with no params", async () => {
    const response = { conflicts: [], total: 0, status_filter: null };
    const fetchMock = mockFetch({ ok: true, status: 200, body: response });

    await client.listConflicts();

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://mem.example.com/v1/conflicts");
    expect(init.method).toBe("GET");
  });

  it("appends status and limit as query params", async () => {
    const response = { conflicts: [], total: 0, status_filter: "open" };
    const fetchMock = mockFetch({ ok: true, status: 200, body: response });

    await client.listConflicts({ status: "open", limit: 20 });

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://mem.example.com/v1/conflicts?status=open&limit=20");
  });
});

// ── reviewSupersessions ───────────────────────────────────────────────────────

describe("reviewSupersessions()", () => {
  it("GET /v1/supersessions/review with threshold and limit", async () => {
    const response = { items: [], total: 0, confidence_threshold: 0.5 };
    const fetchMock = mockFetch({ ok: true, status: 200, body: response });

    await client.reviewSupersessions({ threshold: 0.5, limit: 10 });

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://mem.example.com/v1/supersessions/review?threshold=0.5&limit=10");
  });
});

// ── confirmSupersession ───────────────────────────────────────────────────────

describe("confirmSupersession()", () => {
  it("PATCH /v1/supersessions/:id with action=confirm", async () => {
    const response = { memory_id: "mem-uuid-1", action: "confirm", applied_at: "2026-06-18T10:00:00Z" };
    const fetchMock = mockFetch({ ok: true, status: 200, body: response });

    await client.confirmSupersession("mem-uuid-1", "Confirmed by compliance team");

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://mem.example.com/v1/supersessions/mem-uuid-1");
    expect(init.method).toBe("PATCH");
    const body = JSON.parse(init.body as string);
    expect(body.action).toBe("confirm");
    expect(body.reviewer_note).toBe("Confirmed by compliance team");
  });
});

// ── rejectSupersession ────────────────────────────────────────────────────────

describe("rejectSupersession()", () => {
  it("PATCH /v1/supersessions/:id with action=reject", async () => {
    const response = { memory_id: "mem-uuid-1", action: "reject", applied_at: "2026-06-18T10:00:00Z" };
    const fetchMock = mockFetch({ ok: true, status: 200, body: response });

    await client.rejectSupersession("mem-uuid-1");

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(init.body as string);
    expect(body.action).toBe("reject");
    expect(body.reviewer_note).toBeUndefined();
  });
});

// ── auditExport ───────────────────────────────────────────────────────────────

describe("auditExport()", () => {
  it("GET /v1/admin/audit/export sends X-Admin-Secret", async () => {
    const response = {
      namespace: "test-ns",
      from_: null,
      to: null,
      total_rows: 0,
      chain_status: "ok",
      chain_violations: null,
      events: [],
    };
    const fetchMock = mockFetch({ ok: true, status: 200, body: response });

    await client.auditExport({ namespace: "test-ns", verify: true });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/v1/admin/audit/export");
    expect(url).toContain("namespace=test-ns");
    expect(url).toContain("verify_chain=true");
    expect((init.headers as Record<string, string>)["X-Admin-Secret"]).toBe("admin-secret");
  });
});

// ── verifyChain ───────────────────────────────────────────────────────────────

describe("verifyChain()", () => {
  it("GET /v1/admin/audit/verify sends X-Admin-Secret", async () => {
    const response = { status: "ok", rows_checked: 42 };
    const fetchMock = mockFetch({ ok: true, status: 200, body: response });

    await client.verifyChain("test-ns");

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://mem.example.com/v1/admin/audit/verify?namespace=test-ns");
    expect((init.headers as Record<string, string>)["X-Admin-Secret"]).toBe("admin-secret");
  });
});

// ── AgentMemError ─────────────────────────────────────────────────────────────

describe("AgentMemError", () => {
  it("is thrown with status and body on 4xx", async () => {
    mockFetch({ ok: false, status: 401, body: { detail: "Invalid or missing X-API-Key" } });

    let caught: unknown;
    try {
      await client.recall({ agent_id: "a", query: "q" });
    } catch (e) {
      caught = e;
    }

    expect(caught).toBeInstanceOf(AgentMemError);
    const err = caught as AgentMemError;
    expect(err.status).toBe(401);
    expect(err.message).toMatch(/401/);
  });

  it("is thrown on 500", async () => {
    mockFetch({ ok: false, status: 500, body: { detail: "Internal server error" } });

    await expect(
      client.addMemory({ agent_id: "a", content: "x", event_time: "2026-01-01T00:00:00Z" }),
    ).rejects.toBeInstanceOf(AgentMemError);
  });
});
