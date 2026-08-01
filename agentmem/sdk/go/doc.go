// Package lians is the Go SDK for Lians, a financial-grade memory layer for AI agents.
//
// Lians is built for regulated environments (financial institutions, healthcare,
// legal). Unlike a plain vector store it uses a bitemporal model — superseded
// facts are excluded at the database layer, every write lands in a tamper-evident
// SHA-256 tamper-evident audit chain, per-subject keys support crypto-shred erasure,
// and information barriers are enforced at PostgreSQL row-level security. It also
// exposes a bitemporal relationship graph for conflict-of-interest / related-party
// / care-network reachability queries.
//
// The client uses only the standard library (net/http, encoding/json) and is safe
// for concurrent use. Every method takes a context.Context.
//
//	c := lians.NewClient("https://agentmem-lotus.fly.dev", os.Getenv("LIANS_API_KEY"))
//
//	_, err := c.AddMemory(ctx, lians.AddMemoryRequest{
//	    AgentID:   "equity-desk",
//	    Content:   "NVDA FY2026 revenue guidance raised to $40B",
//	    EventTime: time.Date(2025, 11, 19, 16, 0, 0, 0, time.UTC),
//	    Metadata:  map[string]any{"ticker": "NVDA", "metric": "revenue_guidance"},
//	})
//
//	r, err := c.Recall(ctx, lians.RecallRequest{AgentID: "equity-desk", Query: "NVDA guidance"})
//	for _, m := range r.Memories {
//	    fmt.Println(m.EventTime, *m.Content)
//	}
package lians
