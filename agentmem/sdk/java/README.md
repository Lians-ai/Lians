<p align="center">
  <a href="https://github.com/Lians-ai/Lians">
    <img src="https://raw.githubusercontent.com/Lians-ai/Lians/HEAD/docs/images/logo.png" width="340" alt="Lians logo">
  </a>
</p>

# Lians Java SDK

**Bitemporal long-term memory for JVM agents.** Keep current facts clean, reconstruct what an agent knew at a past time, retain tamper-evident audit records, and query relationship graphs for conflict-of-interest and related-party workflows.

Java 11+, one runtime dependency on Jackson, and HTTP through the JDK.

## Install

Maven:

```xml
<dependency>
  <groupId>ai.lians</groupId>
  <artifactId>lians-sdk</artifactId>
  <version>0.4.1</version>
</dependency>
```

Gradle:

```groovy
implementation "ai.lians:lians-sdk:0.4.1"
```

Version 0.4.1 is available from [Maven Central](https://central.sonatype.com/artifact/ai.lians/lians-sdk/0.4.1). Release JARs are also attached to [GitHub Releases](https://github.com/Lians-ai/Lians/releases).

## Quickstart

```java
import ai.lians.LiansClient;
import ai.lians.LiansClientOptions;
import ai.lians.model.RecallResult;
import java.time.Instant;
import java.util.Map;

LiansClient client = new LiansClient(LiansClientOptions.builder()
        .baseUrl("https://mem.yourfirm.internal")
        .apiKey(System.getenv("LIANS_API_KEY"))
        .build());

client.addMemory("equity-desk", "NVDA FY2026 revenue guidance raised to $40B",
        Instant.parse("2025-11-19T16:00:00Z"),
        Map.of("ticker", "NVDA", "metric", "revenue_guidance"));

RecallResult current = client.recall("equity-desk", "NVDA revenue guidance", 5);
RecallResult past = client.recallAt("equity-desk", "NVDA revenue guidance",
        Instant.parse("2025-09-01T00:00:00Z"), 5);
```

## Audit and graph surfaces

```java
client.snapshot("equity-desk", Instant.parse("2026-03-01T00:00:00Z"), 1000);
client.backtestCheck("equity-desk", Instant.parse("2026-01-01T00:00:00Z"));
client.eraseSubject("subject-42", "REQUEST-2026-001");
client.verifyChain("your-namespace");

client.relate("matter-7", "Attorney", "represented", "ClientX",
        Instant.parse("2026-01-01T00:00:00Z"), false, false);
var path = client.path("matter-7", "Attorney", "PartyY", 4, null);
```

## Why Java and Lians

Java is the backbone of many financial, insurance, healthcare, and legal systems. This SDK brings Lians point-in-time recall, deterministic supersession, audit history, crypto-erasure workflow, and relationship graph to the JVM.

See the [published benchmark results](https://github.com/Lians-ai/Lians/blob/master/docs/benchmark.md), [regulated-memory evaluation](https://github.com/Lians-ai/Lians/blob/master/docs/regulated-eval-results.md), and [public correction ledger](https://github.com/Lians-ai/Lians/blob/master/docs/gtm/public-right-of-reply-2026-07-17.md). The evaluation includes runnable adapters so results can be reproduced and challenged.

## Build and test

```bash
cd agentmem/sdk/java
mvn test
mvn package
```
