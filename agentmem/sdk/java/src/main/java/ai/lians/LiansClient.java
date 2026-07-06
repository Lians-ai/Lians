package ai.lians;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import ai.lians.model.MemoryOut;
import ai.lians.model.RecallResult;

import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Synchronous HTTP client for the Lians financial-grade memory API.
 *
 * <p>Lians is a memory layer for AI agents built for regulated environments
 * (financial institutions, healthcare, legal). Unlike a plain vector store it
 * uses a bitemporal model — superseded facts are excluded at the database layer,
 * every write lands in a tamper-evident SHA-256 audit chain (SEC 17a-4),
 * per-subject keys give GDPR/HIPAA crypto-shred, and information barriers are
 * enforced at PostgreSQL row-level security. It also exposes a bitemporal
 * relationship graph for conflict-of-interest / related-party / care-network
 * reachability queries.
 *
 * <pre>{@code
 * LiansClient client = new LiansClient(LiansClientOptions.builder()
 *     .baseUrl("https://api.lians.dev")
 *     .apiKey(System.getenv("LIANS_API_KEY"))
 *     .build());
 *
 * client.addMemory("equity-desk", "NVDA FY2026 guidance raised to $40B",
 *     Instant.parse("2025-11-19T16:00:00Z"),
 *     Map.of("ticker", "NVDA", "metric", "revenue_guidance"));
 *
 * RecallResult r = client.recall("equity-desk", "NVDA guidance", 5);
 * for (MemoryOut m : r.memories) System.out.println(m.eventTime + "  " + m.content);
 * }</pre>
 *
 * Instances are thread-safe and may be shared.
 */
public final class LiansClient {

    private final String baseUrl;
    private final String apiKey;
    private final String adminSecret;
    private final HttpClient http;
    private final java.time.Duration timeout;
    private final ObjectMapper mapper = new ObjectMapper();

    public LiansClient(LiansClientOptions options) {
        this.baseUrl = stripTrailingSlash(options.baseUrl());
        this.apiKey = options.apiKey();
        this.adminSecret = options.adminSecret();
        this.timeout = options.timeout();
        // Pin HTTP/1.1: the default HttpClient policy is HTTP/2, and against a
        // cleartext HTTP/1.1 server (uvicorn) the h2c upgrade attempt is rejected
        // as "Invalid HTTP request received". The Lians API speaks HTTP/1.1.
        this.http = HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_1_1)
                .connectTimeout(options.timeout())
                .build();
    }

    /** Convenience constructor for the common case (no admin secret, default timeout). */
    public LiansClient(String baseUrl, String apiKey) {
        this(LiansClientOptions.builder().baseUrl(baseUrl).apiKey(apiKey).build());
    }

    // ── Write ───────────────────────────────────────────────────────────────

    /** Store a fact with its business event-time. */
    public MemoryOut addMemory(String agentId, String content, Instant eventTime,
                               Map<String, ?> metadata) {
        return addMemory(agentId, content, eventTime, metadata, null, null, 0.5);
    }

    /** Store a fact with full control over provenance, subject, and importance. */
    public MemoryOut addMemory(String agentId, String content, Instant eventTime,
                               Map<String, ?> metadata, String source, String subjectId,
                               double importance) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("agent_id", agentId);
        body.put("content", content);
        body.put("event_time", iso(eventTime));
        body.put("importance", importance);
        putIfPresent(body, "source", source);
        putIfPresent(body, "subject_id", subjectId);
        putIfPresent(body, "metadata", metadata);
        return request("POST", "/v1/memories", body, null, false, MemoryOut.class);
    }

    /** Add multiple memories in one request (processed sequentially). */
    public JsonNode batchAdd(List<Map<String, ?>> memories) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("memories", memories);
        return requestJson("POST", "/v1/memories/batch", body, null, false);
    }

    // ── Read ────────────────────────────────────────────────────────────────

    /** Retrieve the current (non-stale) memories relevant to {@code query}. */
    public RecallResult recall(String agentId, String query, int k) {
        return recall(agentId, query, k, null, null);
    }

    /**
     * Recall with optional point-in-time ({@code asOf}) and metadata {@code filters}.
     * Pass {@code asOf} to ask "what did the agent know on this date?" — the
     * compliance query mem0 and Zep cannot answer.
     */
    public RecallResult recall(String agentId, String query, int k, Instant asOf,
                               Map<String, ?> filters) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("agent_id", agentId);
        body.put("query", query);
        body.put("k", k);
        putIfPresent(body, "as_of", asOf == null ? null : iso(asOf));
        putIfPresent(body, "filters", filters);
        return request("POST", "/v1/recall", body, null, false, RecallResult.class);
    }

    /** Point-in-time recall — sugar for {@link #recall(String, String, int, Instant, Map)}. */
    public RecallResult recallAt(String agentId, String query, Instant asOf, int k) {
        return recall(agentId, query, k, asOf, null);
    }

    /** Time-series of a structured fact (ticker + metric), oldest first. */
    public JsonNode factHistory(String agentId, String ticker, String metric, int limit) {
        Map<String, Object> p = new LinkedHashMap<>();
        p.put("agent_id", agentId);
        p.put("ticker", ticker);
        p.put("metric", metric);
        p.put("limit", limit);
        return requestJson("GET", "/v1/facts/history", null, p, false);
    }

    /** Full supersession lineage for a memory. */
    public JsonNode getLineage(String memoryId) {
        return requestJson("GET", "/v1/memories/" + enc(memoryId) + "/lineage", null, null, false);
    }

    /** Exhaustive knowledge-state reconstruction at {@code asOf} (audit/regulator demo). */
    public JsonNode snapshot(String agentId, Instant asOf, int limit) {
        Map<String, Object> p = new LinkedHashMap<>();
        p.put("agent_id", agentId);
        p.put("as_of", iso(asOf));
        p.put("limit", limit);
        return requestJson("GET", "/v1/snapshot", null, p, false);
    }

    /** Detect lookahead bias — facts the agent held that it couldn't have known. */
    public JsonNode backtestCheck(String agentId, Instant simulationAsOf) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("agent_id", agentId);
        body.put("simulation_as_of", iso(simulationAsOf));
        return requestJson("POST", "/v1/backtest/check", body, null, false);
    }

    // ── Compliance / erasure ─────────────────────────────────────────────────

    /** GDPR/HIPAA crypto-shred: destroy a data subject's per-subject key. */
    public JsonNode eraseSubject(String subjectId, String requestRef) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("subject_id", subjectId);
        body.put("request_ref", requestRef);
        return requestJson("POST", "/v1/erase", body, null, false);
    }

    /** Proof-of-erasure certificate for a data subject (admin scope). */
    public JsonNode erasureCertificate(String subjectId) {
        return requestJson("GET", "/v1/erase/" + enc(subjectId) + "/certificate", null, null, false);
    }

    /** Compliance report for the caller's namespace. */
    public JsonNode complianceReport(Instant from, Instant to, boolean verify) {
        Map<String, Object> p = new LinkedHashMap<>();
        putIfPresent(p, "from", from == null ? null : iso(from));
        putIfPresent(p, "to", to == null ? null : iso(to));
        p.put("verify", verify);
        return requestJson("GET", "/v1/compliance/report", null, p, false);
    }

    // ── Conflicts ─────────────────────────────────────────────────────────────

    /** List detected conflicts (same-time contradictions awaiting review). */
    public JsonNode listConflicts(String status, int limit) {
        Map<String, Object> p = new LinkedHashMap<>();
        putIfPresent(p, "status", status);
        p.put("limit", limit);
        return requestJson("GET", "/v1/conflicts", null, p, false);
    }

    /** Resolve a conflict: {@code accept_a}, {@code accept_b}, or {@code dismiss}. */
    public JsonNode resolveConflict(String conflictId, String resolution, String note) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("resolution", resolution);
        putIfPresent(body, "note", note);
        return requestJson("POST", "/v1/conflicts/" + enc(conflictId) + "/resolve", body, null, false);
    }

    // ── Relationship graph ────────────────────────────────────────────────────

    /** Assert a relationship edge {@code src --relType--> dst}. */
    public JsonNode relate(String agentId, String srcEntity, String relType, String dstEntity,
                           Instant eventTime, boolean exclusive, boolean normalize) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("agent_id", agentId);
        body.put("src_entity", srcEntity);
        body.put("rel_type", relType);
        body.put("dst_entity", dstEntity);
        body.put("event_time", iso(eventTime));
        body.put("exclusive", exclusive);
        body.put("normalize", normalize);
        return requestJson("POST", "/v1/graph/relate", body, null, false);
    }

    /** Invalidate a live edge (sets {@code valid_to}). */
    public JsonNode unrelate(String agentId, String srcEntity, String relType, String dstEntity) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("agent_id", agentId);
        body.put("src_entity", srcEntity);
        body.put("rel_type", relType);
        body.put("dst_entity", dstEntity);
        return requestJson("POST", "/v1/graph/unrelate", body, null, false);
    }

    /** Entities within {@code depth} hops of {@code entity} (optional point-in-time {@code asOf}). */
    public JsonNode neighbors(String agentId, String entity, int depth, String direction, Instant asOf) {
        Map<String, Object> p = new LinkedHashMap<>();
        p.put("entity", entity);
        p.put("agent_id", agentId);
        p.put("depth", depth);
        p.put("direction", direction == null ? "any" : direction);
        putIfPresent(p, "as_of", asOf == null ? null : iso(asOf));
        return requestJson("GET", "/v1/graph/neighbors", null, p, false);
    }

    /**
     * Shortest connection between two entities — the conflict-of-interest /
     * related-party reachability query. {@code "connected": false} is the clean result.
     */
    public JsonNode path(String agentId, String srcEntity, String dstEntity, int maxDepth, Instant asOf) {
        Map<String, Object> p = new LinkedHashMap<>();
        p.put("src", srcEntity);
        p.put("dst", dstEntity);
        p.put("agent_id", agentId);
        p.put("max_depth", maxDepth);
        putIfPresent(p, "as_of", asOf == null ? null : iso(asOf));
        return requestJson("GET", "/v1/graph/path", null, p, false);
    }

    /** Recall with graph-proximity reranking around {@code nearEntity}. */
    public RecallResult recallNear(String agentId, String query, String nearEntity, String nearKey, int k) {
        Map<String, Object> filters = new LinkedHashMap<>();
        filters.put("_near_entity", nearEntity);
        filters.put("_near_key", nearKey == null ? "ticker" : nearKey);
        return recall(agentId, query, k, null, filters);
    }

    // ── Admin / audit chain ───────────────────────────────────────────────────

    /** Verify the SEC 17a-4 tamper-evidence hash chain (requires admin secret). */
    public JsonNode verifyChain(String namespace) {
        Map<String, Object> p = new LinkedHashMap<>();
        p.put("namespace", namespace);
        return requestJson("GET", "/v1/admin/audit/verify", null, p, true);
    }

    /** Export the full audit log for a namespace (requires admin secret). */
    public JsonNode auditExport(String namespace, Instant from, Instant to, int limit, boolean verify) {
        Map<String, Object> p = new LinkedHashMap<>();
        p.put("namespace", namespace);
        putIfPresent(p, "from_", from == null ? null : iso(from));
        putIfPresent(p, "to", to == null ? null : iso(to));
        p.put("limit", limit);
        p.put("verify_chain", verify);
        return requestJson("GET", "/v1/admin/audit/export", null, p, true);
    }

    // ── Internals ─────────────────────────────────────────────────────────────

    private JsonNode requestJson(String method, String path, Object body,
                                 Map<String, Object> params, boolean admin) {
        return request(method, path, body, params, admin, JsonNode.class);
    }

    private <T> T request(String method, String path, Object body,
                          Map<String, Object> params, boolean admin, Class<T> type) {
        URI uri = URI.create(baseUrl + path + queryString(params));
        HttpRequest.Builder rb = HttpRequest.newBuilder(uri)
                .timeout(timeout)
                .header("X-API-Key", apiKey);
        if (admin && adminSecret != null) {
            rb.header("X-Admin-Secret", adminSecret);
        }

        HttpRequest.BodyPublisher pub;
        if (body != null) {
            byte[] json;
            try {
                json = mapper.writeValueAsBytes(body);
            } catch (IOException e) {
                throw new LiansException("Failed to serialize request body", e);
            }
            pub = HttpRequest.BodyPublishers.ofByteArray(json);
            rb.header("Content-Type", "application/json");
        } else {
            pub = HttpRequest.BodyPublishers.noBody();
        }
        rb.method(method, pub);

        HttpResponse<String> resp;
        try {
            resp = http.send(rb.build(), HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
        } catch (IOException | InterruptedException e) {
            if (e instanceof InterruptedException) {
                Thread.currentThread().interrupt();
            }
            throw new LiansException("Request to " + method + " " + path + " failed", e);
        }

        int code = resp.statusCode();
        if (code < 200 || code >= 300) {
            throw new LiansException(code, resp.body(),
                    "Lians " + method + " " + path + " -> " + code + ": " + resp.body());
        }
        if (code == 204 || resp.body() == null || resp.body().isEmpty()) {
            return null;
        }
        try {
            if (type == JsonNode.class) {
                return type.cast(mapper.readTree(resp.body()));
            }
            return mapper.readValue(resp.body(), type);
        } catch (IOException e) {
            throw new LiansException("Failed to parse response from " + method + " " + path, e);
        }
    }

    private String queryString(Map<String, Object> params) {
        if (params == null || params.isEmpty()) {
            return "";
        }
        List<String> parts = new ArrayList<>();
        for (Map.Entry<String, Object> e : params.entrySet()) {
            if (e.getValue() == null) {
                continue;
            }
            parts.add(enc(e.getKey()) + "=" + enc(String.valueOf(e.getValue())));
        }
        return parts.isEmpty() ? "" : "?" + String.join("&", parts);
    }

    private static void putIfPresent(Map<String, Object> m, String key, Object value) {
        if (value != null) {
            m.put(key, value);
        }
    }

    private static String iso(Instant instant) {
        return instant.toString();
    }

    private static String enc(String s) {
        return URLEncoder.encode(s, StandardCharsets.UTF_8);
    }

    private static String stripTrailingSlash(String s) {
        return s.endsWith("/") ? s.substring(0, s.length() - 1) : s;
    }
}
