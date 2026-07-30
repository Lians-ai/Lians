package lians

import "encoding/json"

// MemoryOut is a single memory returned by recall, snapshot, or fact-history.
//
// Content is empty (and ContentErased true) when the memory was crypto-shredded
// (GDPR/HIPAA erasure): its existence and metadata survive, the content does not.
type MemoryOut struct {
	ID           string          `json:"id"`
	Namespace    string          `json:"namespace"`
	AgentID      string          `json:"agent_id"`
	Content      *string         `json:"content"` // nil if erased
	SubjectID    string          `json:"subject_id,omitempty"`
	EventTime    string          `json:"event_time"`
	ValidFrom    string          `json:"valid_from,omitempty"`
	ValidTo      *string         `json:"valid_to"` // nil = currently valid
	SupersededBy *string         `json:"superseded_by,omitempty"`
	Importance   float64         `json:"importance"`
	Source       *string         `json:"source,omitempty"`
	ContentHash  string          `json:"content_hash,omitempty"`
	ErasedAt     *string         `json:"erased_at,omitempty"`
	Metadata     json.RawMessage `json:"metadata,omitempty"`
}

// RecallResult is the set of current (non-stale) memories relevant to a query.
type RecallResult struct {
	Memories        []MemoryOut `json:"memories"`
	AsOf            *string     `json:"as_of"` // set when recall used a point-in-time checkpoint
	TotalCandidates int         `json:"total_candidates"`
	RetrievalDegraded bool      `json:"retrieval_degraded"`
	TokenEstimate     int       `json:"token_estimate"`
	Strategy          string    `json:"strategy"`
	QueryVariants     []string  `json:"query_variants"`
	RetrievalConfidence float64 `json:"retrieval_confidence"`
	LatencyMS         float64   `json:"latency_ms"`
	Mode              string    `json:"mode"`
	LatencyBudgetMS   float64   `json:"latency_budget_ms"`
	DeadlineExceeded bool      `json:"deadline_exceeded"`
	ReceiptSHA256    string    `json:"receipt_sha256"`
	Receipt          json.RawMessage `json:"receipt"`
	ProvenanceCoverage float64 `json:"provenance_coverage"`
}
