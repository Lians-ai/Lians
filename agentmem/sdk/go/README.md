<p align="center">
  <a href="https://github.com/Lians-ai/Lians">
    <img src="https://raw.githubusercontent.com/Lians-ai/Lians/HEAD/docs/images/logo.png" width="340" alt="Lians logo">
  </a>
</p>

# Lians Go SDK

Provider-neutral decision evidence and governed memory for Go: reconstruct
historical context, preserve tamper-evident records, enforce information
barriers, crypto-shred subject data, and query bitemporal relationship graphs.

Standard library only (`net/http` + `encoding/json`), `context`-aware, and safe
for concurrent use. It connects native services to the same cross-provider
evidence and historical-truth boundary used by the rest of the Lians platform.

## Install

```bash
go get github.com/Lians-ai/Lians/agentmem/sdk/go
```

```go
import lians "github.com/Lians-ai/Lians/agentmem/sdk/go"
```

## Quick start

```go
package main

import (
	"context"
	"fmt"
	"os"
	"time"

	lians "github.com/Lians-ai/Lians/agentmem/sdk/go"
)

func main() {
	ctx := context.Background()
	c := lians.NewClient("https://api.lians.dev", os.Getenv("LIANS_API_KEY"),
		lians.WithAdminSecret(os.Getenv("LIANS_ADMIN_SECRET"))) // admin secret optional

	// Store a fact with its BUSINESS event-time (not now)
	if _, err := c.AddMemory(ctx, lians.AddMemoryRequest{
		AgentID:   "equity-desk",
		Content:   "NVDA FY2026 revenue guidance raised to $40B",
		EventTime: time.Date(2025, 11, 19, 16, 0, 0, 0, time.UTC),
		Metadata:  map[string]any{"ticker": "NVDA", "metric": "revenue_guidance"},
		// Optional: supply a business-stable key when replaying across processes.
		IdempotencyKey: "guidance-import:nvda:2025-11-19:v1",
	}); err != nil {
		panic(err)
	}

	// Recall current (non-stale) facts
	r, _ := c.Recall(ctx, lians.RecallRequest{AgentID: "equity-desk", Query: "NVDA guidance"})
	for _, m := range r.Memories {
		fmt.Println(m.EventTime, *m.Content)
	}

	// Point-in-time — what did we know on a past date?
	past, _ := c.RecallAt(ctx, "equity-desk", "NVDA guidance",
		time.Date(2025, 9, 1, 0, 0, 0, 0, time.UTC), 5)
	_ = past
}
```

## Compliance & graph

```go
// Bounded knowledge-state page at a date; retain completeness metadata
c.Snapshot(ctx, "equity-desk", time.Date(2026, 3, 1, 0, 0, 0, 0, time.UTC), 1000)

// Check recorded Lians data for lookahead contamination before trusting a backtest
c.BacktestCheck(ctx, "equity-desk", time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC))

// GDPR/HIPAA crypto-shred + verify the tamper-evident chain
c.EraseSubject(ctx, "MRN-00042", "GDPR-REQ-2026-001")
c.VerifyChain(ctx, "your-namespace") // requires admin secret

// Relationship graph — conflict-of-interest reachability
c.Relate(ctx, lians.RelateRequest{AgentID: "matter-7", SrcEntity: "Attorney",
	RelType: "represented", DstEntity: "ClientX", EventTime: t})
c.Relate(ctx, lians.RelateRequest{AgentID: "matter-7", SrcEntity: "ClientX",
	RelType: "adverse_to", DstEntity: "PartyY", EventTime: t})
raw, _ := c.Path(ctx, "matter-7", "Attorney", "PartyY", 4, nil)
// raw -> {"connected": true, "hops": 2, "path": [...]}

// Graph-proximity reranking
c.RecallNear(ctx, "equity-desk", "earnings", "FundA", "ticker", 5)
```

## Notes

- Timestamps are `time.Time` (serialized RFC3339 UTC).
- `NewClientWithError` validates configuration eagerly. Base URLs must be
  absolute HTTP(S) URLs and cannot contain user-info, query strings, or fragments.
  Use HTTPS outside local development.
- The 30-second default deadline covers all attempts. The client retries GETs and
  `AddMemory` only; each memory write carries one `Idempotency-Key` reused across
  its attempts. `Recall`, erasure, and graph mutations are never retried
  automatically. Configure bounds with `WithTimeout`, `WithMaxRetries`,
  `WithMaxRetryDelay`, and `WithMaxResponseBytes`.
- Redirects are returned as errors instead of forwarding API credentials. A
  custom `http.Client` is shallow-copied and its transport must not be mutated
  concurrently after construction.
- Errors from non-2xx responses are `*lians.APIError` (`errors.As` to inspect
  `StatusCode`, bounded `Body`, and `RequestID`). Error strings omit the raw body;
  treat the programmatic `Body` as potentially sensitive.
- `AddMemory` / `Recall` return typed `*MemoryOut` / `*RecallResult`; richer
  responses (snapshot, graph, conflicts, audit) return `json.RawMessage` for you to
  unmarshal into your own shape.

## Test

```bash
cd agentmem/sdk/go
go test ./...   # runs against an in-process httptest server — no live Lians needed
```

See the [mem0](../../../docs/compare-mem0.md) and [Zep/Graphiti](../../../docs/compare-zep.md)
comparisons.
