package ai.lians;

import ai.lians.model.MemoryOut;
import ai.lians.model.RecallResult;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.Flow;

/**
 * Synchronous HTTP client for Lians decision evidence and governed memory.
 *
 * <p>Lians records and reconstructs consequential AI evidence across providers.
 * Its bitemporal model excludes superseded facts from present recall while
 * preserving historical state; tamper-evident records, per-subject
 * crypto-shredding, PostgreSQL row-level security, and relationship reachability
 * provide the governed memory substrate beneath the decision-evidence control
 * plane.
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

    /** Version of this Java SDK. */
    public static final String VERSION = "0.5.0";

    /** User-Agent value attached to requests made by this SDK. */
    public static final String USER_AGENT = "lians-java-sdk/" + VERSION;

    private final String baseUrl;
    private final String apiKey;
    private final String adminSecret;
    private final HttpClient http;
    private final Duration timeout;
    private final int maxRetries;
    private final Duration maxRetryDelay;
    private final int maxResponseBytes;
    private final ObjectMapper mapper = new ObjectMapper();

    /**
     * Creates a client from validated connection and transport options.
     *
     * @param options connection, credential, timeout, retry, and response-size options
     */
    public LiansClient(LiansClientOptions options) {
        if (options == null) {
            throw new IllegalArgumentException("options must not be null");
        }
        this.baseUrl = normalizeBaseUrl(options.baseUrl());
        this.apiKey = validateCredential("API key", options.apiKey(), true);
        this.adminSecret = validateCredential("admin secret", options.adminSecret(), false);
        this.timeout = options.timeout();
        this.maxRetries = options.maxRetries();
        this.maxRetryDelay = options.maxRetryDelay();
        this.maxResponseBytes = options.maxResponseBytes();
        // Pin the SDK transport to HTTP/1.1 so cleartext development servers do
        // not receive an h2c upgrade attempt. Production HTTPS proxies remain
        // fully supported over HTTP/1.1.
        this.http = HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_1_1)
                .followRedirects(HttpClient.Redirect.NEVER)
                .connectTimeout(options.timeout())
                .build();
    }

    /**
     * Creates a client with no admin secret and the default transport settings.
     *
     * @param baseUrl the absolute HTTP or HTTPS URL of the Lians server
     * @param apiKey the API key sent with requests
     */
    public LiansClient(String baseUrl, String apiKey) {
        this(LiansClientOptions.builder().baseUrl(baseUrl).apiKey(apiKey).build());
    }

    // ── Write ───────────────────────────────────────────────────────────────

    /**
     * Stores a fact with its business event time.
     *
     * @param agentId the agent that owns the memory
     * @param content the plaintext fact to store
     * @param eventTime the business event time
     * @param metadata optional structured metadata
     * @return the stored memory returned by the server
     */
    public MemoryOut addMemory(String agentId, String content, Instant eventTime,
                               Map<String, ?> metadata) {
        return addMemory(agentId, content, eventTime, metadata, null, null, 0.5);
    }

    /**
     * Stores a fact with a caller-controlled replay key and default importance.
     *
     * @param agentId the agent that owns the memory
     * @param content the plaintext fact to store
     * @param eventTime the business event time
     * @param metadata optional structured metadata
     * @param idempotencyKey a stable key for safe replay of the same write
     * @return the stored memory returned by the server
     */
    public MemoryOut addMemory(String agentId, String content, Instant eventTime,
                               Map<String, ?> metadata, String idempotencyKey) {
        return addMemory(agentId, content, eventTime, metadata, null, null, 0.5,
                idempotencyKey);
    }

    /**
     * Stores a fact with full control over provenance, subject, and importance.
     *
     * @param agentId the agent that owns the memory
     * @param content the plaintext fact to store
     * @param eventTime the business event time
     * @param metadata optional structured metadata
     * @param source optional provenance label
     * @param subjectId optional data-subject identifier for governed erasure
     * @param importance caller-supplied importance score
     * @return the stored memory returned by the server
     */
    public MemoryOut addMemory(String agentId, String content, Instant eventTime,
                               Map<String, ?> metadata, String source, String subjectId,
                               double importance) {
        return addMemory(agentId, content, eventTime, metadata, source, subjectId,
                importance, UUID.randomUUID().toString());
    }

    /**
     * Store a fact with a caller-controlled replay key. Reuse the same key when
     * retrying the same business write; a key must not be reused for different content.
     *
     * @param agentId the agent that owns the memory
     * @param content the plaintext fact to store
     * @param eventTime the business event time
     * @param metadata optional structured metadata
     * @param source optional provenance label
     * @param subjectId optional data-subject identifier for governed erasure
     * @param importance caller-supplied importance score
     * @param idempotencyKey a stable key for safe replay of the same write
     * @return the stored memory returned by the server
     */
    public MemoryOut addMemory(String agentId, String content, Instant eventTime,
                               Map<String, ?> metadata, String source, String subjectId,
                               double importance, String idempotencyKey) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("agent_id", agentId);
        body.put("content", content);
        body.put("event_time", iso(eventTime));
        body.put("importance", importance);
        putIfPresent(body, "source", source);
        putIfPresent(body, "subject_id", subjectId);
        putIfPresent(body, "metadata", metadata);
        return request("POST", "/v1/memories", body, null, false, MemoryOut.class,
                RequestPolicy.idempotent(validateIdempotencyKey(idempotencyKey)));
    }

    /**
     * Adds multiple memories in one request, processed sequentially by the server.
     *
     * @param memories serialized memory request objects
     * @return the batch result returned by the server
     */
    public JsonNode batchAdd(List<Map<String, ?>> memories) {
        return batchAdd(memories, UUID.randomUUID().toString());
    }

    /**
     * Adds multiple memories with a stable replay key for the whole batch.
     *
     * @param memories serialized memory request objects
     * @param idempotencyKey a stable key for safe replay of the same batch
     * @return the batch result returned by the server
     */
    public JsonNode batchAdd(List<Map<String, ?>> memories, String idempotencyKey) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("memories", memories);
        return request("POST", "/v1/memories/batch", body, null, false, JsonNode.class,
                RequestPolicy.idempotent(validateIdempotencyKey(idempotencyKey)));
    }

    // ── Read ────────────────────────────────────────────────────────────────

    /**
     * Retrieves the current, non-stale memories relevant to a query.
     *
     * @param agentId the agent whose memories should be searched
     * @param query the natural-language recall query
     * @param k the maximum number of memories to return
     * @return the recall result returned by the server
     */
    public RecallResult recall(String agentId, String query, int k) {
        return recall(agentId, query, k, null, null);
    }

    /**
     * Recall with optional point-in-time ({@code asOf}) and metadata {@code filters}.
     * Pass {@code asOf} to ask "what did the agent know on this date?" — the
     * compliance query mem0 and Zep cannot answer.
     *
     * @param agentId the agent whose memories should be searched
     * @param query the natural-language recall query
     * @param k the maximum number of memories to return
     * @param asOf optional point-in-time knowledge checkpoint
     * @param filters optional metadata filters
     * @return the recall result returned by the server
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

    /**
     * Performs point-in-time recall.
     *
     * @param agentId the agent whose memories should be searched
     * @param query the natural-language recall query
     * @param asOf the point-in-time knowledge checkpoint
     * @param k the maximum number of memories to return
     * @return the recall result returned by the server
     * @see #recall(String, String, int, Instant, Map)
     */
    public RecallResult recallAt(String agentId, String query, Instant asOf, int k) {
        return recall(agentId, query, k, asOf, null);
    }

    /**
     * Retrieves the time series of a structured fact, oldest first.
     *
     * @param agentId the agent that owns the facts
     * @param ticker the fact's ticker metadata value
     * @param metric the fact's metric metadata value
     * @param limit the maximum number of facts to return
     * @return the fact-history response returned by the server
     */
    public JsonNode factHistory(String agentId, String ticker, String metric, int limit) {
        Map<String, Object> p = new LinkedHashMap<>();
        p.put("agent_id", agentId);
        p.put("ticker", ticker);
        p.put("metric", metric);
        p.put("limit", limit);
        return requestJson("GET", "/v1/facts/history", null, p, false);
    }

    /**
     * Retrieves the bounded supersession graph for a memory.
     *
     * @param memoryId the memory whose lineage should be retrieved
     * @return the lineage response, including completeness fields
     */
    public JsonNode getLineage(String memoryId) {
        return requestJson("GET", "/v1/memories/" + enc(memoryId) + "/lineage", null, null, false);
    }

    /**
     * Retrieves a bounded knowledge-state page at a point in time.
     *
     * @param agentId the agent whose knowledge state should be retrieved
     * @param asOf the point-in-time knowledge checkpoint
     * @param limit the maximum number of memories to return
     * @return the snapshot response, including completeness fields
     */
    public JsonNode snapshot(String agentId, Instant asOf, int limit) {
        Map<String, Object> p = new LinkedHashMap<>();
        p.put("agent_id", agentId);
        p.put("as_of", iso(asOf));
        p.put("limit", limit);
        return requestJson("GET", "/v1/snapshot", null, p, false);
    }

    /**
     * Checks visible recorded facts for lookahead contamination.
     *
     * @param agentId the agent whose facts should be checked
     * @param simulationAsOf the simulated point-in-time boundary
     * @return the backtest-check response returned by the server
     */
    public JsonNode backtestCheck(String agentId, Instant simulationAsOf) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("agent_id", agentId);
        body.put("simulation_as_of", iso(simulationAsOf));
        return requestJson("POST", "/v1/backtest/check", body, null, false);
    }

    // ── Compliance / erasure ─────────────────────────────────────────────────

    /**
     * Crypto-shreds a data subject's per-subject key for governed erasure.
     *
     * @param subjectId the data-subject identifier to erase
     * @param requestRef the caller's erasure-request reference
     * @return the erasure result returned by the server
     */
    public JsonNode eraseSubject(String subjectId, String requestRef) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("subject_id", subjectId);
        body.put("request_ref", requestRef);
        return requestJson("POST", "/v1/erase", body, null, false);
    }

    /**
     * Retrieves the proof-of-erasure certificate for a data subject.
     *
     * @param subjectId the erased data-subject identifier
     * @return the proof-of-erasure certificate returned by the server
     */
    public JsonNode erasureCertificate(String subjectId) {
        return requestJson("GET", "/v1/erase/" + enc(subjectId) + "/certificate", null, null, false);
    }

    /**
     * Retrieves a compliance report for the caller's namespace.
     *
     * @param from optional inclusive start of the report interval
     * @param to optional inclusive end of the report interval
     * @param verify whether the server should verify tamper evidence
     * @return the compliance report returned by the server
     */
    public JsonNode complianceReport(Instant from, Instant to, boolean verify) {
        Map<String, Object> p = new LinkedHashMap<>();
        putIfPresent(p, "from", from == null ? null : iso(from));
        putIfPresent(p, "to", to == null ? null : iso(to));
        p.put("verify", verify);
        return requestJson("GET", "/v1/compliance/report", null, p, false);
    }

    // ── Conflicts ─────────────────────────────────────────────────────────────

    /**
     * Lists detected same-time contradictions awaiting review.
     *
     * @param status optional conflict-status filter
     * @param limit the maximum number of conflicts to return
     * @return the conflict list returned by the server
     */
    public JsonNode listConflicts(String status, int limit) {
        Map<String, Object> p = new LinkedHashMap<>();
        putIfPresent(p, "status", status);
        p.put("limit", limit);
        return requestJson("GET", "/v1/conflicts", null, p, false);
    }

    /**
     * Resolves a conflict as {@code accept_a}, {@code accept_b}, or {@code dismiss}.
     *
     * @param conflictId the conflict to resolve
     * @param resolution the resolution action
     * @param note an optional reviewer note
     * @return the resolved conflict returned by the server
     */
    public JsonNode resolveConflict(String conflictId, String resolution, String note) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("resolution", resolution);
        putIfPresent(body, "note", note);
        return requestJson("POST", "/v1/conflicts/" + enc(conflictId) + "/resolve", body, null, false);
    }

    // ── Relationship graph ────────────────────────────────────────────────────

    /**
     * Asserts a relationship edge {@code src --relType--&gt; dst}.
     *
     * @param agentId the agent that owns the relationship
     * @param srcEntity the source entity
     * @param relType the relationship type
     * @param dstEntity the destination entity
     * @param eventTime the business event time of the relationship
     * @param exclusive whether the relationship supersedes conflicting live edges
     * @param normalize whether entity identifiers should be normalized by the server
     * @return the created relationship returned by the server
     */
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

    /**
     * Invalidates a live relationship edge by setting its {@code valid_to} value.
     *
     * @param agentId the agent that owns the relationship
     * @param srcEntity the source entity
     * @param relType the relationship type
     * @param dstEntity the destination entity
     * @return the invalidated relationship returned by the server
     */
    public JsonNode unrelate(String agentId, String srcEntity, String relType, String dstEntity) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("agent_id", agentId);
        body.put("src_entity", srcEntity);
        body.put("rel_type", relType);
        body.put("dst_entity", dstEntity);
        return requestJson("POST", "/v1/graph/unrelate", body, null, false);
    }

    /**
     * Retrieves entities within a bounded number of hops of an entity.
     *
     * @param agentId the agent whose relationship graph should be searched
     * @param entity the entity at the center of the search
     * @param depth the maximum traversal depth
     * @param direction traversal direction, or {@code null} for any direction
     * @param asOf optional point-in-time graph checkpoint
     * @return the neighboring-entities response returned by the server
     */
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
     *
     * @param agentId the agent whose relationship graph should be searched
     * @param srcEntity the source entity
     * @param dstEntity the destination entity
     * @param maxDepth the maximum traversal depth
     * @param asOf optional point-in-time graph checkpoint
     * @return the shortest-path response returned by the server
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

    /**
     * Recalls memories with graph-proximity reranking around an entity.
     *
     * @param agentId the agent whose memories should be searched
     * @param query the natural-language recall query
     * @param nearEntity the entity used as the graph-proximity anchor
     * @param nearKey metadata key containing entity identifiers, or {@code null} for {@code ticker}
     * @param k the maximum number of memories to return
     * @return the reranked recall result returned by the server
     */
    public RecallResult recallNear(String agentId, String query, String nearEntity, String nearKey, int k) {
        Map<String, Object> filters = new LinkedHashMap<>();
        filters.put("_near_entity", nearEntity);
        filters.put("_near_key", nearKey == null ? "ticker" : nearKey);
        return recall(agentId, query, k, null, filters);
    }

    // ── Admin / audit chain ───────────────────────────────────────────────────

    /**
     * Verifies the SEC 17a-4 tamper-evidence hash chain.
     *
     * @param namespace the namespace whose audit chain should be verified
     * @return the verification response returned by the server
     */
    public JsonNode verifyChain(String namespace) {
        Map<String, Object> p = new LinkedHashMap<>();
        p.put("namespace", namespace);
        return requestJson("GET", "/v1/admin/audit/verify", null, p, true);
    }

    /**
     * Exports the audit log for a namespace.
     *
     * @param namespace the namespace whose audit log should be exported
     * @param from optional inclusive start of the export interval
     * @param to optional inclusive end of the export interval
     * @param limit the maximum number of audit entries to return
     * @param verify whether the server should verify the exported hash chain
     * @return the audit export returned by the server
     */
    public JsonNode auditExport(String namespace, Instant from, Instant to, int limit, boolean verify) {
        Map<String, Object> p = new LinkedHashMap<>();
        p.put("namespace", namespace);
        putIfPresent(p, "from", from == null ? null : iso(from));
        putIfPresent(p, "to", to == null ? null : iso(to));
        p.put("limit", limit);
        p.put("verify", verify);
        return requestJson("GET", "/v1/admin/audit/export", null, p, true);
    }

    // ── Internals ─────────────────────────────────────────────────────────────

    private JsonNode requestJson(String method, String path, Object body,
                                 Map<String, Object> params, boolean admin) {
        return request(method, path, body, params, admin, JsonNode.class);
    }

    private <T> T request(String method, String path, Object body,
                          Map<String, Object> params, boolean admin, Class<T> type) {
        return request(method, path, body, params, admin, type,
                ("GET".equals(method) || "HEAD".equals(method))
                        ? RequestPolicy.safeRead() : RequestPolicy.unsafe());
    }

    private <T> T request(String method, String path, Object body,
                          Map<String, Object> params, boolean admin, Class<T> type,
                          RequestPolicy policy) {
        if (admin && (adminSecret == null || adminSecret.isEmpty())) {
            throw new IllegalStateException("admin secret is required for this operation");
        }
        URI uri = URI.create(baseUrl + path + queryString(params));
        byte[] requestBody = null;
        if (body != null) {
            try {
                requestBody = mapper.writeValueAsBytes(body);
            } catch (IOException e) {
                throw new LiansException("Failed to serialize request body", e);
            }
            if (requestBody.length > 16 * 1024 * 1024) {
                throw new IllegalArgumentException("request body exceeds 16 MiB");
            }
        }

        long deadline = System.nanoTime() + timeout.toNanos();
        for (int attempt = 0; ; attempt++) {
            long remainingNanos = deadline - System.nanoTime();
            if (remainingNanos <= 0) {
                throw new LiansException("Lians request deadline exceeded", null);
            }
            HttpRequest.Builder rb = HttpRequest.newBuilder(uri)
                    .timeout(Duration.ofNanos(remainingNanos))
                    .header("User-Agent", USER_AGENT)
                    .header("X-API-Key", apiKey);
            if (admin) {
                rb.header("X-Admin-Secret", adminSecret);
            }
            if (policy.idempotencyKey != null) {
                rb.header("Idempotency-Key", policy.idempotencyKey);
            }
            HttpRequest.BodyPublisher publisher;
            if (requestBody != null) {
                publisher = HttpRequest.BodyPublishers.ofByteArray(requestBody);
                rb.header("Content-Type", "application/json");
            } else {
                publisher = HttpRequest.BodyPublishers.noBody();
            }
            rb.method(method, publisher);

            HttpResponse<byte[]> resp;
            try {
                resp = http.send(rb.build(), limitedBodyHandler(maxResponseBytes));
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new LiansException("Lians request interrupted", e);
            } catch (IOException e) {
                if (policy.retrySafe && !causedByResponseTooLarge(e) && attempt < maxRetries
                        && waitBeforeRetry(deadline, retryDelay(attempt, null))) {
                    continue;
                }
                throw new LiansException("Lians request failed", e);
            }

            int code = resp.statusCode();
            if (policy.retrySafe && attempt < maxRetries && retryableStatus(code)
                    && waitBeforeRetry(deadline,
                    retryDelay(attempt, resp.headers().firstValue("Retry-After").orElse(null)))) {
                continue;
            }
            byte[] responseBody = resp.body() == null ? new byte[0] : resp.body();
            if (code < 200 || code >= 300) {
                int errorLength = Math.min(responseBody.length, 64 * 1024);
                String errorBody = new String(responseBody, 0, errorLength, StandardCharsets.UTF_8);
                String requestId = sanitizeRequestId(
                        resp.headers().firstValue("X-Request-ID").orElse(""));
                String message = requestId.isEmpty()
                        ? "Lians request returned HTTP " + code
                        : "Lians request returned HTTP " + code + " (request " + requestId + ")";
                throw new LiansException(code, errorBody, requestId, message);
            }
            if (code == 204 || code == 205 || responseBody.length == 0) {
                return null;
            }
            try {
                if (type == JsonNode.class) {
                    return type.cast(mapper.readTree(responseBody));
                }
                return mapper.readValue(responseBody, type);
            } catch (IOException e) {
                throw new LiansException("Failed to parse Lians response", e);
            }
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
            parts.add(encQuery(e.getKey()) + "=" + encQuery(String.valueOf(e.getValue())));
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
        if (s == null) {
            throw new IllegalArgumentException("path segment must not be null");
        }
        return encQuery(s).replace("+", "%20");
    }

    private static String encQuery(String s) {
        return URLEncoder.encode(s, StandardCharsets.UTF_8);
    }

    private static String normalizeBaseUrl(String raw) {
        if (raw == null || raw.isEmpty() || raw.length() > 8192) {
            throw new IllegalArgumentException("baseUrl must be between 1 and 8192 characters");
        }
        final URI uri;
        try {
            uri = URI.create(raw);
        } catch (IllegalArgumentException e) {
            throw new IllegalArgumentException("baseUrl must be an absolute HTTP(S) URL");
        }
        String scheme = uri.getScheme();
        if (scheme == null || !(scheme.equalsIgnoreCase("http") || scheme.equalsIgnoreCase("https"))
                || uri.getHost() == null || uri.getRawUserInfo() != null
                || uri.getRawQuery() != null || uri.getRawFragment() != null) {
            throw new IllegalArgumentException(
                    "baseUrl must be an absolute HTTP(S) URL without credentials, query, or fragment");
        }
        String normalized = raw;
        while (normalized.endsWith("/")) {
            normalized = normalized.substring(0, normalized.length() - 1);
        }
        return normalized;
    }

    private static String validateCredential(String name, String value, boolean required) {
        if (required && (value == null || value.isEmpty())) {
            throw new IllegalArgumentException(name + " must not be empty");
        }
        if (value == null) {
            return null;
        }
        if (value.length() > 8192) {
            throw new IllegalArgumentException(name + " is too long");
        }
        for (int i = 0; i < value.length(); i++) {
            char ch = value.charAt(i);
            if (ch < 0x20 || ch == 0x7f) {
                throw new IllegalArgumentException(name + " contains a control character");
            }
        }
        return value;
    }

    private static String validateIdempotencyKey(String value) {
        String key = validateCredential("idempotency key", value, true);
        if (key.length() > 255) {
            throw new IllegalArgumentException("idempotency key must be 1-255 bytes");
        }
        for (int i = 0; i < key.length(); i++) {
            char ch = key.charAt(i);
            if (ch < 0x21 || ch > 0x7e) {
                throw new IllegalArgumentException(
                        "idempotency key must use visible ASCII without whitespace");
            }
        }
        return key;
    }

    private static String sanitizeRequestId(String value) {
        if (value.length() > 128) {
            value = value.substring(0, 128);
        }
        StringBuilder safe = new StringBuilder(value.length());
        for (int i = 0; i < value.length(); i++) {
            char ch = value.charAt(i);
            if ((ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z')
                    || (ch >= '0' && ch <= '9') || ch == '-' || ch == '_' || ch == '.') {
                safe.append(ch);
            }
        }
        return safe.toString();
    }

    private static boolean causedByResponseTooLarge(Throwable error) {
        Throwable current = error;
        for (int depth = 0; current != null && depth < 8; depth++) {
            if (current instanceof ResponseTooLargeException) {
                return true;
            }
            current = current.getCause();
        }
        return false;
    }

    private static boolean retryableStatus(int code) {
        return code == 408 || code == 429 || code == 500 || code == 502 || code == 503 || code == 504;
    }

    private Duration retryDelay(int attempt, String retryAfter) {
        if (retryAfter != null) {
            String value = retryAfter.trim();
            try {
                long seconds = Long.parseLong(value);
                if (seconds >= 0) {
                    return minDuration(Duration.ofSeconds(seconds), maxRetryDelay);
                }
            } catch (NumberFormatException | ArithmeticException ignored) {
                // Try the HTTP-date form below.
            }
            try {
                Instant when = ZonedDateTime.parse(value, DateTimeFormatter.RFC_1123_DATE_TIME).toInstant();
                Duration delay = Duration.between(Instant.now(), when);
                return minDuration(delay.isNegative() ? Duration.ZERO : delay, maxRetryDelay);
            } catch (RuntimeException ignored) {
                // Fall back to bounded exponential delay.
            }
        }
        long multiplier = 1L << Math.min(attempt, 5);
        return minDuration(Duration.ofMillis(100L * multiplier), maxRetryDelay);
    }

    private static Duration minDuration(Duration left, Duration right) {
        return left.compareTo(right) <= 0 ? left : right;
    }

    private static boolean waitBeforeRetry(long deadline, Duration requestedDelay) {
        long remaining = deadline - System.nanoTime();
        if (remaining <= 0) {
            return false;
        }
        Duration delay = minDuration(requestedDelay, Duration.ofNanos(remaining));
        if (delay.isZero() || delay.isNegative()) {
            return true;
        }
        try {
            long millis = delay.toMillis();
            int nanos = (int) (delay.minusMillis(millis).toNanos());
            Thread.sleep(millis, nanos);
            return deadline - System.nanoTime() > 0;
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return false;
        }
    }

    private static HttpResponse.BodyHandler<byte[]> limitedBodyHandler(int maxBytes) {
        return ignored -> new LimitedBodySubscriber(maxBytes);
    }

    private static final class LimitedBodySubscriber implements HttpResponse.BodySubscriber<byte[]> {
        private final int maxBytes;
        private final ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        private final CompletableFuture<byte[]> result = new CompletableFuture<>();
        private Flow.Subscription subscription;
        private int received;

        private LimitedBodySubscriber(int maxBytes) {
            this.maxBytes = maxBytes;
        }

        @Override
        public CompletionStage<byte[]> getBody() {
            return result;
        }

        @Override
        public void onSubscribe(Flow.Subscription value) {
            if (subscription != null) {
                value.cancel();
                return;
            }
            subscription = value;
            value.request(Long.MAX_VALUE);
        }

        @Override
        public void onNext(List<ByteBuffer> items) {
            for (ByteBuffer item : items) {
                int size = item.remaining();
                if (size > maxBytes - received) {
                    subscription.cancel();
                    result.completeExceptionally(
                            new ResponseTooLargeException("Lians response exceeds " + maxBytes + " bytes"));
                    return;
                }
                byte[] chunk = new byte[size];
                item.get(chunk);
                bytes.write(chunk, 0, chunk.length);
                received += size;
            }
        }

        @Override
        public void onError(Throwable error) {
            result.completeExceptionally(error);
        }

        @Override
        public void onComplete() {
            result.complete(bytes.toByteArray());
        }
    }

    private static final class ResponseTooLargeException extends IOException {
        private static final long serialVersionUID = 1L;

        private ResponseTooLargeException(String message) {
            super(message);
        }
    }

    private static final class RequestPolicy {
        private final boolean retrySafe;
        private final String idempotencyKey;

        private RequestPolicy(boolean retrySafe, String idempotencyKey) {
            this.retrySafe = retrySafe;
            this.idempotencyKey = idempotencyKey;
        }

        private static RequestPolicy safeRead() {
            return new RequestPolicy(true, null);
        }

        private static RequestPolicy idempotent(String key) {
            return new RequestPolicy(true, key);
        }

        private static RequestPolicy unsafe() {
            return new RequestPolicy(false, null);
        }
    }
}
