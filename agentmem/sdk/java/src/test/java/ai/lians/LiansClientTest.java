package ai.lians;

import com.fasterxml.jackson.databind.JsonNode;
import com.sun.net.httpserver.HttpServer;
import ai.lians.model.MemoryOut;
import ai.lians.model.RecallResult;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/**
 * Tests the client against Java's built-in HttpServer — exercises request
 * construction (headers, JSON body, query params) and response parsing with no
 * external dependency or live Lians server.
 */
class LiansClientTest {

    private HttpServer server;
    private LiansClient client;

    // Captured from the most recent request.
    volatile String lastMethod;
    volatile String lastPath;
    volatile String lastQuery;
    volatile String lastBody;
    volatile String lastApiKey;
    volatile String lastAdminSecret;

    @BeforeEach
    void setUp() throws Exception {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", exchange -> {
            lastMethod = exchange.getRequestMethod();
            lastPath = exchange.getRequestURI().getPath();
            lastQuery = exchange.getRequestURI().getQuery();
            lastApiKey = exchange.getRequestHeaders().getFirst("X-API-Key");
            lastAdminSecret = exchange.getRequestHeaders().getFirst("X-Admin-Secret");
            lastBody = readAll(exchange.getRequestBody());

            String path = lastPath;
            int status = 200;
            String resp;

            if (path.equals("/v1/memories") && lastBody.contains("\"BOOM\"")) {
                status = 422;
                resp = "{\"detail\":\"boom\"}";
            } else if (path.equals("/v1/memories")) {
                resp = "{\"id\":\"m-1\",\"namespace\":\"ns\",\"agent_id\":\"desk\","
                        + "\"content\":\"NVDA guidance $40B\",\"event_time\":\"2025-11-19T16:00:00Z\","
                        + "\"valid_to\":null,\"importance\":0.5,\"content_hash\":\"h\","
                        + "\"metadata\":{\"ticker\":\"NVDA\"}}";
            } else if (path.equals("/v1/recall")) {
                resp = "{\"memories\":[{\"id\":\"m-1\",\"content\":\"NVDA guidance $40B\","
                        + "\"event_time\":\"2025-11-19T16:00:00Z\",\"metadata\":{\"ticker\":\"NVDA\"}}],"
                        + "\"as_of\":null,\"total_candidates\":1}";
            } else if (path.equals("/v1/graph/path")) {
                resp = "{\"src\":\"Attorney\",\"dst\":\"PartyY\",\"connected\":true,"
                        + "\"hops\":2,\"as_of\":null,\"path\":[]}";
            } else if (path.equals("/v1/admin/audit/verify")) {
                resp = "{\"status\":\"ok\",\"rows_checked\":7}";
            } else {
                resp = "{}";
            }

            byte[] bytes = resp.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(status, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        });
        server.start();

        String baseUrl = "http://127.0.0.1:" + server.getAddress().getPort();
        client = new LiansClient(LiansClientOptions.builder()
                .baseUrl(baseUrl)
                .apiKey("test-key")
                .adminSecret("admin-secret")
                .build());
    }

    @AfterEach
    void tearDown() {
        server.stop(0);
    }

    @Test
    void addMemorySendsKeyAndBodyAndParsesResult() {
        MemoryOut m = client.addMemory("desk", "NVDA FY2026 guidance raised to $40B",
                Instant.parse("2025-11-19T16:00:00Z"),
                Map.of("ticker", "NVDA", "metric", "revenue_guidance"));

        assertEquals("POST", lastMethod);
        assertEquals("/v1/memories", lastPath);
        assertEquals("test-key", lastApiKey);
        assertTrue(lastBody.contains("\"agent_id\":\"desk\""));
        assertTrue(lastBody.contains("\"event_time\":\"2025-11-19T16:00:00Z\""));
        assertTrue(lastBody.contains("\"ticker\":\"NVDA\""));

        assertEquals("m-1", m.id);
        assertEquals("NVDA guidance $40B", m.content);
        assertEquals("NVDA", m.metadata.get("ticker").asText());
    }

    @Test
    void recallParsesMemories() {
        RecallResult r = client.recall("desk", "NVDA guidance", 5);
        assertEquals("/v1/recall", lastPath);
        assertEquals(1, r.memories.size());
        assertEquals("NVDA guidance $40B", r.memories.get(0).content);
        assertEquals(1, r.totalCandidates);
    }

    @Test
    void recallAtSendsAsOf() {
        client.recallAt("desk", "NVDA guidance", Instant.parse("2025-09-01T00:00:00Z"), 5);
        assertTrue(lastBody.contains("\"as_of\":\"2025-09-01T00:00:00Z\""));
    }

    @Test
    void recallNearAddsProximityFilters() {
        client.recallNear("desk", "earnings", "FundA", "ticker", 5);
        assertTrue(lastBody.contains("\"_near_entity\":\"FundA\""));
        assertTrue(lastBody.contains("\"_near_key\":\"ticker\""));
    }

    @Test
    void graphPathParsesConnectivity() {
        JsonNode res = client.path("desk", "Attorney", "PartyY", 4, null);
        assertEquals("GET", lastMethod);
        assertEquals("/v1/graph/path", lastPath);
        assertTrue(lastQuery.contains("src=Attorney"));
        assertTrue(lastQuery.contains("dst=PartyY"));
        assertTrue(res.get("connected").asBoolean());
        assertEquals(2, res.get("hops").asInt());
    }

    @Test
    void verifyChainSendsAdminSecret() {
        JsonNode res = client.verifyChain("ns");
        assertEquals("/v1/admin/audit/verify", lastPath);
        assertEquals("admin-secret", lastAdminSecret);
        assertEquals("ok", res.get("status").asText());
    }

    @Test
    void nonSuccessThrowsLiansException() {
        LiansException ex = assertThrows(LiansException.class, () ->
                client.addMemory("desk", "BOOM", Instant.parse("2026-01-01T00:00:00Z"), Map.of()));
        assertEquals(422, ex.status());
        assertTrue(ex.body().contains("boom"));
    }

    private static String readAll(InputStream in) {
        try (in) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[4096];
            int n;
            while ((n = in.read(buf)) != -1) {
                out.write(buf, 0, n);
            }
            return out.toString(StandardCharsets.UTF_8);
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }
}
