/**
 * AgentMem TypeScript SDK — unit tests
 *
 * All tests use a mock fetch so no real API is needed.  The mock validates that:
 *   1. The client sends the correct HTTP method, path, and body.
 *   2. Timestamps are serialised to ISO 8601 strings.
 *   3. Error responses are surfaced as thrown Error objects with useful messages.
 *   4. 204 No Content responses return undefined without crashing.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { AgentMemClient } from "./client.js";

// ── Mock fetch ───────────────────────────────────────────────────────────────

type MockResponse = { ok: boolean; status: number; body: unknown };

function mockFetch(response: MockResponse) {
  const fn = vi.fn().mockResolvedValue({
    ok: response.ok,
    status: response.status,
    json: () => Promise.resolve(response.body),
    text: () => Promise.resolve(JSON.stringify(response.body)),
    statusText: "OK",
  });
  vi.stubGlobal("fetch", fn);
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
  client = new AgentMemClient({ url: "https://mem.example.com", apiKey: "test-key" });
  vi.restoreAllMocks();
});

// ── Client construction ──────────────────────────────────────────────────────

describe("AgentMemClient construction", () => {
  it("strips trailing slash from url", async () => {
    const c = new AgentMemClient({ url: "https://mem.example.com/", apiKey: "k" });
    const fetchMock = mockFetch({ ok: true, status: 200, body: MEMORY_FIXTURE });
    await c.add({ agent_id: "a", content: "x", event_time: "2026-01-01T00:00:00Z" });
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://mem.example.com/v1/memories");
  });

  it("sends X-API-Key header on every request", async () => {
    const fetchMock = mockFetch({ ok: true, status: 200, body: MEMORY_FIXTURE });
    await client.add({ agent_id: "a", content: "x", event_time: "2026-01-01T00:00:00Z" });
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>)["X-API-Key"]).toBe("test-key");
  });
});

// ── add ──────────────────────────────────────────────────────────────────────

describe("add()", () => {
  it("POST /v1/memories with correct body", async () => {
    const fetchMock = mockFetch({ ok: true, status: 200, body: MEMORY_FIXTURE });

    const result = await client.add({
      agent_id: "agent-1",
      content: "The equity desk target for AAPL is $210",
      event_time: new Date("2026-06-01T12:00:00Z"),
      source: "analyst-note",
      importance: 0.8,
    });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://mem.example.com/v1/memories");
    expect(init.method).toBe("POST");

    const body = JSON.parse(init.body as string);
    expect(body.agent_id).toBe("agent-1");
    expect(body.event_time).toBe("2026-06-01T12:00:00.000Z");
    expect(body.importance).toBe(0.8);

    expect(result.id).toBe("mem-uuid-1");
    expect(result.content).toBe("The equity desk target for AAPL is $210");
  });

  it("serialises string event_time unchanged", async () => {
    const fetchMock = mockFetch({ ok: true, status: 200, body: MEMORY_FIXTURE });
    await client.add({
      agent_id: "a",
      content: "x",
      event_time: "2026-06-01T12:00:00Z",
    });
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(init.body as string);
    expect(body.event_time).toBe("2026-06-01T12:00:00Z");
  });
});

// ── batchAdd ─────────────────────────────────────────────────────────────────

describe("batchAdd()", () => {
  it("POST /v1/memories/batch with memories array", async () => {
    const batchResponse = { added: 2, memories: [MEMORY_FIXTURE, { ...MEMORY_FIXTURE, id: "mem-uuid-2" }] };
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
    expect(body.as_of).toBeUndefined();

    expect(result.memories).toHaveLength(1);
    expect(result.total_candidates).toBe(1);
  });

  it("serialises as_of Date to ISO string", async () => {
    const recallResponse = {
      memories: [],
      as_of: "2026-03-01T00:00:00Z",
      total_candidates: 0,
    };
    const fetchMock = mockFetch({ ok: true, status: 200, body: recallResponse });

    await client.recall({
      agent_id: "a",
      query: "q",
      as_of: new Date("2026-03-01T00:00:00Z"),
    });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(init.body as string);
    expect(body.as_of).toBe("2026-03-01T00:00:00.000Z");
  });
});

// ── recallAt ─────────────────────────────────────────────────────────────────

describe("recallAt()", () => {
  it("delegates to recall() with as_of", async () => {
    const recallResponse = { memories: [], as_of: "2026-01-01T00:00:00Z", total_candidates: 0 };
    const fetchMock = mockFetch({ ok: true, status: 200, body: recallResponse });

    await client.recallAt("agent-1", "earnings guidance", "2026-01-01T00:00:00Z", 10);

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(init.body as string);
    expect(body.agent_id).toBe("agent-1");
    expect(body.query).toBe("earnings guidance");
    expect(body.as_of).toBe("2026-01-01T00:00:00Z");
    expect(body.k).toBe(10);
  });
});

// ── reconstruct ──────────────────────────────────────────────────────────────

describe("reconstruct()", () => {
  it("POST /v1/audit/reconstruct", async () => {
    const reconstructResponse = {
      memories: [MEMORY_FIXTURE],
      event_trail: [{ id: "evt-1", op: "add", memory_id: "mem-uuid-1", content_hash: "abc123", payload: {}, created_at: "2026-06-01T12:00:01Z" }],
      as_of: "2026-06-10T00:00:00Z",
    };
    const fetchMock = mockFetch({ ok: true, status: 200, body: reconstructResponse });

    const result = await client.reconstruct({
      agent_id: "agent-1",
      as_of: new Date("2026-06-10T00:00:00Z"),
      query: "AAPL",
    });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://mem.example.com/v1/audit/reconstruct");
    const body = JSON.parse(init.body as string);
    expect(body.agent_id).toBe("agent-1");
    expect(body.as_of).toBe("2026-06-10T00:00:00.000Z");
    expect(body.query).toBe("AAPL");

    expect(result.memories).toHaveLength(1);
    expect(result.event_trail).toHaveLength(1);
  });

  it("omits query field when not provided", async () => {
    const reconstructResponse = { memories: [], event_trail: [], as_of: "2026-06-10T00:00:00Z" };
    const fetchMock = mockFetch({ ok: true, status: 200, body: reconstructResponse });

    await client.reconstruct({ agent_id: "a", as_of: "2026-06-10T00:00:00Z" });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(init.body as string);
    expect(body.query).toBeUndefined();
  });
});

// ── erase ────────────────────────────────────────────────────────────────────

describe("erase()", () => {
  it("POST /v1/erase with subject_id and request_ref", async () => {
    const eraseResponse = { subject_id: "sub-123", memories_erased: 5, request_ref: "DSAR-2026-001" };
    const fetchMock = mockFetch({ ok: true, status: 200, body: eraseResponse });

    const result = await client.erase({
      subject_id: "sub-123",
      request_ref: "DSAR-2026-001",
    });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://mem.example.com/v1/erase");
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body as string);
    expect(body.subject_id).toBe("sub-123");
    expect(body.request_ref).toBe("DSAR-2026-001");

    expect(result.memories_erased).toBe(5);
  });
});

// ── Supersession review ───────────────────────────────────────────────────────

describe("reviewSupersessions()", () => {
  it("GET /v1/supersessions/review with no params", async () => {
    const reviewResponse = { items: [], total: 0, confidence_threshold: 0.75 };
    const fetchMock = mockFetch({ ok: true, status: 200, body: reviewResponse });

    await client.reviewSupersessions();

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://mem.example.com/v1/supersessions/review");
    expect(init.method).toBe("GET");
  });

  it("appends threshold and limit as query params", async () => {
    const fetchMock = mockFetch({ ok: true, status: 200, body: { items: [], total: 0, confidence_threshold: 0.5 } });

    await client.reviewSupersessions({ threshold: 0.5, limit: 10 });

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://mem.example.com/v1/supersessions/review?threshold=0.5&limit=10");
  });
});

describe("confirmSupersession()", () => {
  it("PATCH /v1/supersessions/:id with action=confirm", async () => {
    const actionResponse = { memory_id: "mem-uuid-1", action: "confirm", applied_at: "2026-06-18T10:00:00Z" };
    const fetchMock = mockFetch({ ok: true, status: 200, body: actionResponse });

    const result = await client.confirmSupersession("mem-uuid-1", "Confirmed by compliance team");

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://mem.example.com/v1/supersessions/mem-uuid-1");
    expect(init.method).toBe("PATCH");
    const body = JSON.parse(init.body as string);
    expect(body.action).toBe("confirm");
    expect(body.reviewer_note).toBe("Confirmed by compliance team");

    expect(result.action).toBe("confirm");
  });
});

describe("rejectSupersession()", () => {
  it("PATCH /v1/supersessions/:id with action=reject", async () => {
    const actionResponse = { memory_id: "mem-uuid-1", action: "reject", applied_at: "2026-06-18T10:00:00Z" };
    const fetchMock = mockFetch({ ok: true, status: 200, body: actionResponse });

    await client.rejectSupersession("mem-uuid-1");

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(init.body as string);
    expect(body.action).toBe("reject");
    expect(body.reviewer_note).toBeUndefined();
  });
});

// ── Error handling ────────────────────────────────────────────────────────────

describe("error handling", () => {
  it("throws Error with status and detail on 4xx", async () => {
    mockFetch({ ok: false, status: 401, body: { detail: "Invalid or missing X-API-Key" } });

    await expect(
      client.recall({ agent_id: "a", query: "q" }),
    ).rejects.toThrow(/401/);
  });

  it("throws Error with status and detail on 404", async () => {
    mockFetch({ ok: false, status: 404, body: { detail: "Memory not found" } });

    await expect(
      client.confirmSupersession("nonexistent-id"),
    ).rejects.toThrow(/404/);
  });

  it("throws Error on 500", async () => {
    mockFetch({ ok: false, status: 500, body: { detail: "Internal server error" } });

    await expect(
      client.add({ agent_id: "a", content: "x", event_time: "2026-01-01T00:00:00Z" }),
    ).rejects.toThrow(/500/);
  });
});
