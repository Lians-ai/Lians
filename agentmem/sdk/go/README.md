<p align="center">
  <a href="https://github.com/Lians-ai/Lians">
    <img src="https://raw.githubusercontent.com/Lians-ai/Lians/HEAD/docs/images/logo.png" width="340" alt="Lians logo">
  </a>
</p>

# Lians Go SDK

Financial-grade agent memory for Go — bitemporal recall, SEC 17a-4 audit chain,
GDPR/HIPAA crypto-shred, information barriers, and a relationship graph for
conflict-of-interest / related-party / care-network queries.

Standard library only (`net/http` + `encoding/json`), `context`-aware, and safe
for concurrent use. This puts Lians on parity with Zep's Go SDK — while Lians also
ships Java and C, which neither mem0 nor Zep offers.

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
// Exhaustive knowledge state at a date (regulator demo)
c.Snapshot(ctx, "equity-desk", time.Date(2026, 3, 1, 0, 0, 0, 0, time.UTC), 1000)

// Lookahead-bias proof before trusting a backtest
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
- Errors from non-2xx responses are `*lians.APIError` (`errors.As` to inspect
  `StatusCode` / `Body`).
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
