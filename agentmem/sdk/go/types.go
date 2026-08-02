package lians

import "encoding/json"

// MemoryRankingStage explains one deterministic reorder or score adjustment.
type MemoryRankingStage struct {
	Stage          string  `json:"stage"`
	InputScore     float64 `json:"input_score"`
	OutputScore    float64 `json:"output_score"`
	Method         string  `json:"method,omitempty"`
	Position       int     `json:"position,omitempty"`
	CandidateCount int     `json:"candidate_count,omitempty"`
}

// MemoryScoringLimits records the deterministic work bounds used for a score.
type MemoryScoringLimits struct {
	TextSampleChars int `json:"text_sample_chars"`
	TokenCap        int `json:"token_cap"`
	MetadataChars   int `json:"metadata_chars"`
	MetadataItems   int `json:"metadata_items"`
	MetadataDepth   int `json:"metadata_depth"`
	MetadataValueChars int `json:"metadata_value_chars"`
}

// MemoryScoreBreakdown is the typed, auditable explanation of a recall score.
type MemoryScoreBreakdown struct {
	ImportanceScore     float64              `json:"importance_score"`
	ConfidenceScore     float64              `json:"confidence_score"`
	TrustScore          float64              `json:"trust_score"`
	FreshnessScore      float64              `json:"freshness_score"`
	RelevanceScore      float64              `json:"relevance_score"`
	StabilityScore      float64              `json:"stability_score"`
	SafetyScore         float64              `json:"safety_score"`
	FinalScore          float64              `json:"final_score"`
	Eligible            bool                 `json:"eligible"`
	SafetyEligible      *bool                `json:"safety_eligible,omitempty"`
	TemporalEligible    *bool                `json:"temporal_eligible,omitempty"`
	Purpose             string               `json:"purpose"`
	ScoringPolicyVersion string              `json:"scoring_policy_version,omitempty"`
	ReferenceTime       string               `json:"reference_time,omitempty"`
	ScoringLimits       *MemoryScoringLimits `json:"scoring_limits,omitempty"`
	Weights             map[string]float64   `json:"weights,omitempty"`
	Reasons             []string             `json:"reasons,omitempty"`
	QualityScore        *float64              `json:"quality_score,omitempty"`
	PreFusionScore      *float64              `json:"pre_fusion_score,omitempty"`
	RankingWeights      map[string]float64   `json:"ranking_weights,omitempty"`
	RankingStages       []MemoryRankingStage `json:"ranking_stages,omitempty"`
	Fusion              json.RawMessage      `json:"fusion,omitempty"`
}

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
	Score        *float64        `json:"score,omitempty"`
	ScoreBreakdown *MemoryScoreBreakdown `json:"score_breakdown,omitempty"`
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
