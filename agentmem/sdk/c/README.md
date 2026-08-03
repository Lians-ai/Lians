<p align="center">
  <a href="https://github.com/Lians-ai/Lians">
    <img src="https://raw.githubusercontent.com/Lians-ai/Lians/HEAD/docs/images/logo.png" width="340" alt="Lians logo">
  </a>
</p>

# Lians C SDK

Provider-neutral decision evidence and governed memory for native, low-latency,
and embedded systems: bitemporal reconstruction, tamper-evident records,
subject crypto-shredding, information barriers, and relationship-graph queries.

A thin [libcurl](https://curl.se/libcurl/) client. Responses come back as raw
JSON strings, so it drops into HFT gateways, market-data plants, trading systems,
and on-prem C/C++ stacks where Python and JVM SDKs don't fit. Pair with your JSON
parser of choice (cJSON, jansson, RapidJSON, …).

## Requirements

- A C99 compiler and CMake ≥ 3.15
- libcurl development headers (`libcurl4-openssl-dev` on Debian/Ubuntu,
  `curl-devel` on RHEL, `brew install curl` on macOS)

## Build

```bash
cd agentmem/sdk/c
cmake -B build
cmake --build build
ctest --test-dir build --output-on-failure   # runs the pure-function unit tests
```

This produces the `lians` library, a `lians_example` binary, and the test runner.

## Usage

```c
#include "lians.h"
#include <stdio.h>

int main(void) {
    lians_global_init();
    lians_client_t *c = lians_client_new("https://api.lians.dev", getenv("LIANS_API_KEY"), NULL);

    /* Store a fact with its BUSINESS event-time (ISO-8601 UTC). */
    lians_response_t r = lians_add_idempotent(c, "equity-desk",
        "NVDA FY2026 revenue guidance raised to $40B",
        "2025-11-19T16:00:00Z",
        "{\"ticker\":\"NVDA\",\"metric\":\"revenue_guidance\"}",
        "analyst", NULL, 0.6, "guidance-import:nvda:2025-11-19:v1");
    printf("%ld %s\n", r.status, r.body);
    lians_response_free(&r);

    /* Recall current (non-stale) facts. */
    r = lians_recall(c, "equity-desk", "NVDA revenue guidance", 5, NULL, NULL);
    printf("%s\n", r.body);
    lians_response_free(&r);

    /* Point-in-time: what did we know on a past date? */
    r = lians_recall(c, "equity-desk", "NVDA revenue guidance", 5, "2025-09-01T00:00:00Z", NULL);
    lians_response_free(&r);

    /* Conflict-of-interest reachability via the relationship graph. */
    r = lians_path(c, "matter-7", "Attorney", "PartyY", 4, NULL);
    /* -> {"connected": true, "hops": 2, "path": [...]} */
    lians_response_free(&r);

    lians_client_free(c);
    lians_global_cleanup();
    return 0;
}
```

See [`examples/example.c`](examples/example.c) for a complete program.

## API

| Function | Purpose |
|----------|---------|
| `lians_add` | Store a fact once, without automatic retry |
| `lians_add_idempotent` | Store a fact with a caller-stable replay key and bounded retry |
| `lians_recall` | Recall current facts; pass `as_of` for point-in-time |
| `lians_snapshot` | Bounded knowledge-state page with JSON completeness metadata |
| `lians_backtest_check` | Lookahead-bias detection |
| `lians_fact_history` | Time-series of a ticker+metric |
| `lians_erase` | GDPR/HIPAA crypto-shred a subject |
| `lians_verify_chain` | Verify the tamper-evident audit chain (admin) |
| `lians_relate` / `lians_unrelate` | Assert / invalidate a graph edge |
| `lians_neighbors` | N-hop neighbors of an entity |
| `lians_path` | Connection between two entities (COI / related-party) |

Every call returns a `lians_response_t { long status; char *body; }`:
- `status` is the HTTP status code, or `< 0` if the request never completed.
- `body` is a malloc'd JSON string — release it with `lians_response_free()`.

## Memory & threading

- Free every response body with `lians_response_free()`.
- A `lians_client_t` is safe to share across threads; each call uses its own
  libcurl easy handle and runtime bounds are atomic. Do not free a client while a
  request is active. Call `lians_global_init()` once before starting worker
  threads and `lians_global_cleanup()` only after all clients and requests end.
- Base URLs accept HTTP(S) only and reject user-info, query strings, fragments,
  control characters, and backslashes. libcurl TLS verification remains enabled
  and redirects remain disabled. Use HTTPS outside local development.
- The 30-second default timeout is a total budget across retries and backoff.
  Safe GETs and `lians_add_idempotent` retry transient failures up to twice;
  recall, erasure, and graph mutations never retry automatically. Tune with
  `lians_client_set_timeout_ms`, `lians_client_set_max_retries`, and
  `lians_client_set_max_response_bytes`.
- Response bodies are capped at 16 MiB by default. Server error bodies are still
  returned to the caller as raw JSON and may contain sensitive details; avoid
  logging them indiscriminately.
- `metadata_json` and `filters_json` are caller-owned raw JSON object strings.
  The SDK escapes ordinary string arguments but does not parse those two inputs.

## Why C + Lians

Native services remain common in low-latency gateways, market-data plants, embedded
systems, and tightly controlled on-premises environments. This SDK exposes Lians'
bounded HTTP contracts without forcing those systems to embed a Python or JVM
runtime. It returns raw JSON intentionally; callers remain responsible for schema
validation, completeness fields, secret-safe logging, and the regulatory posture of
their deployment. The version-pinned [mem0](../../../docs/compare-mem0.md) and
[Zep/Graphiti](../../../docs/compare-zep.md) comparisons are evaluation snapshots,
not current exclusivity claims.
