<p align="center">
  <a href="https://github.com/Lians-ai/Lians">
    <img src="https://raw.githubusercontent.com/Lians-ai/Lians/HEAD/docs/images/logo.png" width="340" alt="Lians logo">
  </a>
</p>

# Lians C SDK

Financial-grade agent memory for native, low-latency, and embedded systems —
bitemporal recall, SEC 17a-4 audit chain, GDPR/HIPAA crypto-shred, information
barriers, and a relationship graph (conflict-of-interest / related-party /
care-network).

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
    lians_response_t r = lians_add(c, "equity-desk",
        "NVDA FY2026 revenue guidance raised to $40B",
        "2025-11-19T16:00:00Z",
        "{\"ticker\":\"NVDA\",\"metric\":\"revenue_guidance\"}",
        "analyst", NULL, 0.6);
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
| `lians_add` | Store a fact (with event-time, metadata, subject) |
| `lians_recall` | Recall current facts; pass `as_of` for point-in-time |
| `lians_snapshot` | Exhaustive knowledge state at a date |
| `lians_backtest_check` | Lookahead-bias detection |
| `lians_fact_history` | Time-series of a ticker+metric |
| `lians_erase` | GDPR/HIPAA crypto-shred a subject |
| `lians_verify_chain` | Verify the SEC 17a-4 audit chain (admin) |
| `lians_relate` / `lians_unrelate` | Assert / invalidate a graph edge |
| `lians_neighbors` | N-hop neighbors of an entity |
| `lians_path` | Connection between two entities (COI / related-party) |

Every call returns a `lians_response_t { long status; char *body; }`:
- `status` is the HTTP status code, or `< 0` if the request never completed.
- `body` is a malloc'd JSON string — release it with `lians_response_free()`.

## Memory & threading

- Free every response body with `lians_response_free()`.
- A `lians_client_t` is immutable after creation and safe to share across threads;
  each call uses its own libcurl easy handle. Call `lians_global_init()` once at
  startup in multi-threaded programs.

## Why C + Lians

mem0 ships Python/TypeScript only; Zep adds Go. Neither offers a C SDK — yet the
lowest-latency and most regulated systems (HFT, exchange gateways, on-prem medical
and legal devices) are native. This SDK brings the full compliance memory layer and
the relationship graph to them. See the
[mem0](../../../docs/compare-mem0.md) and [Zep/Graphiti](../../../docs/compare-zep.md)
comparisons.
