<p align="center">
  <a href="https://github.com/Lians-ai/Lians">
    <img src="https://raw.githubusercontent.com/Lians-ai/Lians/HEAD/docs/images/logo.png" width="340" alt="Lians logo">
  </a>
</p>

# Lians Java SDK

Financial-grade agent memory for the JVM — bitemporal recall, SEC 17a-4 audit
chain, GDPR/HIPAA crypto-shred, information barriers, and a relationship graph for
conflict-of-interest / related-party / care-network queries.

Built for where Java already runs: bank and insurer risk systems, healthcare
platforms, and legal tech. Java 11+, one dependency (Jackson); HTTP is the JDK's
`java.net.http`.

## Install

Maven:

```xml
<dependency>
  <groupId>dev.lians</groupId>
  <artifactId>lians-sdk</artifactId>
  <version>0.2.0</version>
</dependency>
```

Gradle:

```groovy
implementation "dev.lians:lians-sdk:0.2.0"
```

## Quick start

```java
import dev.lians.LiansClient;
import dev.lians.LiansClientOptions;
import dev.lians.model.MemoryOut;
import dev.lians.model.RecallResult;
import java.time.Instant;
import java.util.Map;

LiansClient client = new LiansClient(LiansClientOptions.builder()
        .baseUrl("https://api.lians.dev")              // or your self-hosted server
        .apiKey(System.getenv("LIANS_API_KEY"))
        .adminSecret(System.getenv("LIANS_ADMIN_SECRET"))   // only for /v1/admin/* audit calls
        .build());

// Store a fact with its BUSINESS event-time (not now)
client.addMemory("equity-desk", "NVDA FY2026 revenue guidance raised to $40B",
        Instant.parse("2025-11-19T16:00:00Z"),
        Map.of("ticker", "NVDA", "metric", "revenue_guidance"));

// Recall current (non-stale) facts
RecallResult r = client.recall("equity-desk", "NVDA revenue guidance", 5);
for (MemoryOut m : r.memories) {
    System.out.println(m.eventTime + "  " + m.content);
}

// Point-in-time — what did we know on a past date?
RecallResult past = client.recallAt("equity-desk", "NVDA revenue guidance",
        Instant.parse("2025-09-01T00:00:00Z"), 5);
```

## Compliance & graph surfaces

```java
// Exhaustive knowledge state at a date (regulator demo)
client.snapshot("equity-desk", Instant.parse("2026-03-01T00:00:00Z"), 1000);

// Lookahead-bias proof before trusting a backtest
client.backtestCheck("equity-desk", Instant.parse("2026-01-01T00:00:00Z"));

// GDPR/HIPAA crypto-shred + verify the tamper-evident chain
client.eraseSubject("MRN-00042", "GDPR-REQ-2026-001");
client.verifyChain("your-namespace");   // requires adminSecret

// Relationship graph — conflict-of-interest / related-party reachability
client.relate("matter-7", "Attorney", "represented", "ClientX",
        Instant.parse("2026-01-01T00:00:00Z"), false, false);
client.relate("matter-7", "ClientX", "adverse_to", "PartyY",
        Instant.parse("2026-01-01T00:00:00Z"), false, false);
var coi = client.path("matter-7", "Attorney", "PartyY", 4, null);
// -> {"connected": true, "hops": 2, "path": [...]}

// Graph-proximity reranking
client.recallNear("equity-desk", "earnings", "FundA", "ticker", 5);
```

## Notes

- Timestamps are `java.time.Instant` (serialized as ISO-8601 UTC).
- Errors (non-2xx) throw `LiansException` exposing `status()` and `body()`.
- Responses with rich schemas (snapshot, graph, conflicts, audit) return Jackson
  `JsonNode`; `addMemory`/`recall` return typed `MemoryOut` / `RecallResult`.
- `LiansClient` is thread-safe — create one and share it.

## Why Java + Lians

mem0 ships Python/TypeScript only; Zep adds Go. Neither offers a Java SDK — yet
Java is the backbone of regulated back-offices. This SDK brings the full
compliance memory layer (and the relationship graph that neither competitor pairs
with an open compliance spine) to the JVM. See the
[mem0](../../../docs/compare-mem0.md) and [Zep/Graphiti](../../../docs/compare-zep.md)
comparisons.

## Build & test

```bash
cd agentmem/sdk/java
mvn test      # JUnit tests run against an in-process mock server — no live Lians needed
mvn package
```
