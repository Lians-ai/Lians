package lians

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

// APIError is returned when the Lians server responds with a non-2xx status.
type APIError struct {
	StatusCode int
	Body       string
}

func (e *APIError) Error() string {
	return fmt.Sprintf("lians: HTTP %d: %s", e.StatusCode, e.Body)
}

// Client is a synchronous HTTP client for the Lians memory API. It is safe for
// concurrent use by multiple goroutines.
type Client struct {
	baseURL     string
	apiKey      string
	adminSecret string
	httpClient  *http.Client
}

// Option configures a Client.
type Option func(*Client)

// WithAdminSecret sets the admin secret used for /v1/admin/* audit endpoints.
func WithAdminSecret(secret string) Option {
	return func(c *Client) { c.adminSecret = secret }
}

// WithTimeout sets the per-request timeout (default 30s).
func WithTimeout(d time.Duration) Option {
	return func(c *Client) { c.httpClient.Timeout = d }
}

// WithHTTPClient supplies a custom *http.Client (for proxies, mTLS, tracing, …).
func WithHTTPClient(h *http.Client) Option {
	return func(c *Client) { c.httpClient = h }
}

// NewClient creates a client for the given base URL and API key.
//
//	c := lians.NewClient("https://agentmem-lotus.fly.dev", os.Getenv("LIANS_API_KEY"),
//	    lians.WithAdminSecret(os.Getenv("LIANS_ADMIN_SECRET")))
func NewClient(baseURL, apiKey string, opts ...Option) *Client {
	c := &Client{
		baseURL:    strings.TrimRight(baseURL, "/"),
		apiKey:     apiKey,
		httpClient: &http.Client{Timeout: 30 * time.Second},
	}
	for _, o := range opts {
		o(c)
	}
	return c
}

func (c *Client) do(ctx context.Context, method, path string, body any, params url.Values, admin bool, out any) error {
	var reqBody io.Reader
	hasBody := false
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			return fmt.Errorf("lians: marshal request body: %w", err)
		}
		reqBody = bytes.NewReader(b)
		hasBody = true
	}

	u := c.baseURL + path
	if len(params) > 0 {
		u += "?" + params.Encode()
	}

	req, err := http.NewRequestWithContext(ctx, method, u, reqBody)
	if err != nil {
		return fmt.Errorf("lians: new request: %w", err)
	}
	req.Header.Set("X-API-Key", c.apiKey)
	if hasBody {
		req.Header.Set("Content-Type", "application/json")
	}
	if admin && c.adminSecret != "" {
		req.Header.Set("X-Admin-Secret", c.adminSecret)
	}

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("lians: %s %s: %w", method, path, err)
	}
	defer resp.Body.Close()

	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("lians: read response: %w", err)
	}

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return &APIError{StatusCode: resp.StatusCode, Body: string(data)}
	}
	if out != nil && len(data) > 0 {
		if err := json.Unmarshal(data, out); err != nil {
			return fmt.Errorf("lians: decode response: %w", err)
		}
	}
	return nil
}

func iso(t time.Time) string {
	return t.UTC().Format(time.RFC3339)
}

// ── Write ──────────────────────────────────────────────────────────────────

// AddMemoryRequest is the input to AddMemory.
type AddMemoryRequest struct {
	AgentID    string
	Content    string
	EventTime  time.Time      // BUSINESS time the fact became true (not now)
	Metadata   map[string]any // structured keys (e.g. {"ticker":"NVDA","metric":"eps"})
	Source     string
	SubjectID  string
	Importance float64 // 0..1; left at 0 it defaults to 0.5
}

// AddMemory stores a fact with its event-time. Supersession, audit-chain append,
// and per-subject encryption all happen server-side.
func (c *Client) AddMemory(ctx context.Context, req AddMemoryRequest) (*MemoryOut, error) {
	importance := req.Importance
	if importance == 0 {
		importance = 0.5
	}
	body := map[string]any{
		"agent_id":   req.AgentID,
		"content":    req.Content,
		"event_time": iso(req.EventTime),
		"importance": importance,
	}
	if req.Source != "" {
		body["source"] = req.Source
	}
	if req.SubjectID != "" {
		body["subject_id"] = req.SubjectID
	}
	if req.Metadata != nil {
		body["metadata"] = req.Metadata
	}
	var out MemoryOut
	if err := c.do(ctx, http.MethodPost, "/v1/memories", body, nil, false, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// ── Read ───────────────────────────────────────────────────────────────────

// RecallRequest is the input to Recall.
type RecallRequest struct {
	AgentID string
	Query   string
	K       int        // defaults to 5
	AsOf    *time.Time // point-in-time recall when non-nil
	Filters map[string]any
	IncludeContext bool
	Strategy string // standard or adaptive
	MaxQueryVariants int
	Mode string // fast, deep, or reconstruct
}

// Recall returns the current (non-stale) memories relevant to the query.
func (c *Client) Recall(ctx context.Context, req RecallRequest) (*RecallResult, error) {
	k := req.K
	if k <= 0 {
		k = 5
	}
	body := map[string]any{"agent_id": req.AgentID, "query": req.Query, "k": k}
	if req.IncludeContext {
		body["include_context"] = true
	}
	if req.Strategy != "" {
		body["strategy"] = req.Strategy
	}
	if req.MaxQueryVariants > 0 {
		body["max_query_variants"] = req.MaxQueryVariants
	}
	if req.Mode != "" {
		body["mode"] = req.Mode
	}
	if req.AsOf != nil {
		body["as_of"] = iso(*req.AsOf)
	}
	if req.Filters != nil {
		body["filters"] = req.Filters
	}
	var out RecallResult
	if err := c.do(ctx, http.MethodPost, "/v1/recall", body, nil, false, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// RecallAt is point-in-time recall - "what did the agent know on this date?".
func (c *Client) RecallAt(ctx context.Context, agentID, query string, asOf time.Time, k int) (*RecallResult, error) {
	return c.Recall(ctx, RecallRequest{AgentID: agentID, Query: query, K: k, AsOf: &asOf})
}

// RecallNear recalls with graph-proximity reranking around nearEntity.
func (c *Client) RecallNear(ctx context.Context, agentID, query, nearEntity, nearKey string, k int) (*RecallResult, error) {
	if nearKey == "" {
		nearKey = "ticker"
	}
	return c.Recall(ctx, RecallRequest{
		AgentID: agentID, Query: query, K: k,
		Filters: map[string]any{"_near_entity": nearEntity, "_near_key": nearKey},
	})
}

// Snapshot reconstructs the exhaustive knowledge state of an agent at asOf.
func (c *Client) Snapshot(ctx context.Context, agentID string, asOf time.Time, limit int) (json.RawMessage, error) {
	params := url.Values{}
	params.Set("agent_id", agentID)
	params.Set("as_of", iso(asOf))
	params.Set("limit", strconv.Itoa(limit))
	var out json.RawMessage
	if err := c.do(ctx, http.MethodGet, "/v1/snapshot", nil, params, false, &out); err != nil {
		return nil, err
	}
	return out, nil
}

// BacktestCheck detects lookahead bias relative to simulationAsOf.
func (c *Client) BacktestCheck(ctx context.Context, agentID string, simulationAsOf time.Time) (json.RawMessage, error) {
	body := map[string]any{"agent_id": agentID, "simulation_as_of": iso(simulationAsOf)}
	var out json.RawMessage
	if err := c.do(ctx, http.MethodPost, "/v1/backtest/check", body, nil, false, &out); err != nil {
		return nil, err
	}
	return out, nil
}

// FactHistory returns the time-series of a structured fact (ticker + metric).
func (c *Client) FactHistory(ctx context.Context, agentID, ticker, metric string, limit int) (json.RawMessage, error) {
	params := url.Values{}
	params.Set("agent_id", agentID)
	params.Set("ticker", ticker)
	params.Set("metric", metric)
	params.Set("limit", strconv.Itoa(limit))
	var out json.RawMessage
	if err := c.do(ctx, http.MethodGet, "/v1/facts/history", nil, params, false, &out); err != nil {
		return nil, err
	}
	return out, nil
}

// ── Compliance / erasure ───────────────────────────────────────────────────

// EraseSubject performs a GDPR/HIPAA crypto-shred of a data subject.
func (c *Client) EraseSubject(ctx context.Context, subjectID, requestRef string) (json.RawMessage, error) {
	body := map[string]any{"subject_id": subjectID, "request_ref": requestRef}
	var out json.RawMessage
	if err := c.do(ctx, http.MethodPost, "/v1/erase", body, nil, false, &out); err != nil {
		return nil, err
	}
	return out, nil
}

// VerifyChain verifies the tamper-evident hash chain (requires admin secret).
func (c *Client) VerifyChain(ctx context.Context, namespace string) (json.RawMessage, error) {
	params := url.Values{}
	params.Set("namespace", namespace)
	var out json.RawMessage
	if err := c.do(ctx, http.MethodGet, "/v1/admin/audit/verify", nil, params, true, &out); err != nil {
		return nil, err
	}
	return out, nil
}

// ── Relationship graph ─────────────────────────────────────────────────────

// RelateRequest is the input to Relate.
type RelateRequest struct {
	AgentID   string
	SrcEntity string
	RelType   string
	DstEntity string
	EventTime time.Time
	Exclusive bool // invalidate other live src--relType--> edges
	Normalize bool // collapse company/ISIN/CUSIP to canonical ticker
	SubjectID string
	Source    string
	Metadata  map[string]any
}

// Relate asserts a relationship edge src --relType--> dst.
func (c *Client) Relate(ctx context.Context, req RelateRequest) (json.RawMessage, error) {
	body := map[string]any{
		"agent_id":   req.AgentID,
		"src_entity": req.SrcEntity,
		"rel_type":   req.RelType,
		"dst_entity": req.DstEntity,
		"event_time": iso(req.EventTime),
		"exclusive":  req.Exclusive,
		"normalize":  req.Normalize,
	}
	if req.SubjectID != "" {
		body["subject_id"] = req.SubjectID
	}
	if req.Source != "" {
		body["source"] = req.Source
	}
	if req.Metadata != nil {
		body["metadata"] = req.Metadata
	}
	var out json.RawMessage
	if err := c.do(ctx, http.MethodPost, "/v1/graph/relate", body, nil, false, &out); err != nil {
		return nil, err
	}
	return out, nil
}

// Unrelate invalidates a live edge (sets valid_to).
func (c *Client) Unrelate(ctx context.Context, agentID, srcEntity, relType, dstEntity string) (json.RawMessage, error) {
	body := map[string]any{
		"agent_id":   agentID,
		"src_entity": srcEntity,
		"rel_type":   relType,
		"dst_entity": dstEntity,
	}
	var out json.RawMessage
	if err := c.do(ctx, http.MethodPost, "/v1/graph/unrelate", body, nil, false, &out); err != nil {
		return nil, err
	}
	return out, nil
}

// Neighbors returns entities within depth hops of entity. direction is
// "any" (default), "in", or "out"; asOf may be nil for present-time.
func (c *Client) Neighbors(ctx context.Context, agentID, entity string, depth int, direction string, asOf *time.Time) (json.RawMessage, error) {
	if direction == "" {
		direction = "any"
	}
	params := url.Values{}
	params.Set("agent_id", agentID)
	params.Set("entity", entity)
	params.Set("depth", strconv.Itoa(depth))
	params.Set("direction", direction)
	if asOf != nil {
		params.Set("as_of", iso(*asOf))
	}
	var out json.RawMessage
	if err := c.do(ctx, http.MethodGet, "/v1/graph/neighbors", nil, params, false, &out); err != nil {
		return nil, err
	}
	return out, nil
}

// Path returns the shortest connection between two entities - the
// conflict-of-interest / related-party reachability query.
func (c *Client) Path(ctx context.Context, agentID, srcEntity, dstEntity string, maxDepth int, asOf *time.Time) (json.RawMessage, error) {
	params := url.Values{}
	params.Set("agent_id", agentID)
	params.Set("src", srcEntity)
	params.Set("dst", dstEntity)
	params.Set("max_depth", strconv.Itoa(maxDepth))
	if asOf != nil {
		params.Set("as_of", iso(*asOf))
	}
	var out json.RawMessage
	if err := c.do(ctx, http.MethodGet, "/v1/graph/path", nil, params, false, &out); err != nil {
		return nil, err
	}
	return out, nil
}
