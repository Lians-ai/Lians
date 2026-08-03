// Package lians is the Go SDK for Lians, provider-neutral decision evidence
// infrastructure for consequential AI systems.
//
// Lians records and reconstructs consequential AI evidence across providers. Its
// bitemporal model excludes superseded facts from present recall while preserving
// historical state; tamper-evident records, per-subject crypto-shredding,
// PostgreSQL row-level security, and relationship reachability provide the
// governed memory substrate beneath the decision-evidence control plane.
//
// The client uses only the standard library (net/http, encoding/json) and is safe
// for concurrent use. Every method takes a context.Context.
//
//	c := lians.NewClient("https://api.lians.dev", os.Getenv("LIANS_API_KEY"))
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
