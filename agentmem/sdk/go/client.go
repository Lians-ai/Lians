package lians

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
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
	RequestID  string
}

func (e *APIError) Error() string {
	if e.RequestID != "" {
		return fmt.Sprintf("lians: HTTP %d (request %s)", e.StatusCode, e.RequestID)
	}
	return fmt.Sprintf("lians: HTTP %d", e.StatusCode)
}

// Client is a synchronous HTTP client for the Lians memory API. It is safe for
// concurrent use by multiple goroutines.
type Client struct {
	baseURL          string
	apiKey           string
	adminSecret      string
	httpClient       *http.Client
	timeout          time.Duration
	maxRetries       int
	maxRetryDelay    time.Duration
	maxResponseBytes int64
	initErr          error
}

// Option configures a Client.
type Option func(*Client)

// WithAdminSecret sets the admin secret used for /v1/admin/* audit endpoints.
func WithAdminSecret(secret string) Option {
	return func(c *Client) { c.adminSecret = secret }
}

// WithTimeout sets the total operation deadline, including retries (default 30s).
func WithTimeout(d time.Duration) Option {
	return func(c *Client) { c.timeout = d }
}

// WithHTTPClient supplies a custom *http.Client (for proxies, mTLS, tracing, …).
func WithHTTPClient(h *http.Client) Option {
	return func(c *Client) { c.httpClient = h }
}

// WithMaxRetries sets the number of retries after the first attempt (default 2,
// maximum 5). Retries are limited to GETs and writes protected by an
// Idempotency-Key; one-time and otherwise unsafe mutations are never retried.
func WithMaxRetries(n int) Option {
	return func(c *Client) { c.maxRetries = n }
}

// WithMaxRetryDelay caps server Retry-After and exponential backoff (default 2s).
func WithMaxRetryDelay(d time.Duration) Option {
	return func(c *Client) { c.maxRetryDelay = d }
}

// WithMaxResponseBytes caps response bodies retained in memory (default 16 MiB).
func WithMaxResponseBytes(n int64) Option {
	return func(c *Client) { c.maxResponseBytes = n }
}

// NewClient creates a client for the given base URL and API key.
//
//	c := lians.NewClient("https://api.lians.dev", os.Getenv("LIANS_API_KEY"),
//	    lians.WithAdminSecret(os.Getenv("LIANS_ADMIN_SECRET")))
func NewClient(baseURL, apiKey string, opts ...Option) *Client {
	c := &Client{
		baseURL:          baseURL,
		apiKey:           apiKey,
		httpClient:       &http.Client{},
		timeout:          30 * time.Second,
		maxRetries:       2,
		maxRetryDelay:    2 * time.Second,
		maxResponseBytes: 16 << 20,
	}
	for _, o := range opts {
		if o != nil {
			o(c)
		}
	}
	if normalized, err := normalizeBaseURL(c.baseURL); err != nil {
		c.initErr = err
	} else {
		c.baseURL = normalized
	}
	if err := validateCredential("API key", c.apiKey, true); err != nil && c.initErr == nil {
		c.initErr = err
	}
	if err := validateCredential("admin secret", c.adminSecret, false); err != nil && c.initErr == nil {
		c.initErr = err
	}
	if c.httpClient == nil && c.initErr == nil {
		c.initErr = errors.New("lians: HTTP client must not be nil")
	}
	if c.timeout <= 0 || c.timeout > 10*time.Minute {
		c.initErr = errors.New("lians: timeout must be greater than zero and at most 10 minutes")
	}
	if c.maxRetries < 0 || c.maxRetries > 5 {
		c.initErr = errors.New("lians: max retries must be between 0 and 5")
	}
	if c.maxRetryDelay <= 0 || c.maxRetryDelay > 30*time.Second {
		c.initErr = errors.New("lians: max retry delay must be greater than zero and at most 30 seconds")
	}
	if c.maxResponseBytes < 1024 || c.maxResponseBytes > 256<<20 {
		c.initErr = errors.New("lians: max response size must be between 1 KiB and 256 MiB")
	}
	if c.httpClient != nil {
		// Never forward Lians credentials through redirects. Copying preserves the
		// caller's transport while avoiding mutation of their shared http.Client.
		h := *c.httpClient
		h.CheckRedirect = func(_ *http.Request, _ []*http.Request) error {
			return http.ErrUseLastResponse
		}
		c.httpClient = &h
	}
	return c
}

// NewClientWithError is the eager-validation form of NewClient. NewClient is
// retained for source compatibility and reports configuration errors on use.
func NewClientWithError(baseURL, apiKey string, opts ...Option) (*Client, error) {
	c := NewClient(baseURL, apiKey, opts...)
	if c.initErr != nil {
		return nil, c.initErr
	}
	return c, nil
}

type requestPolicy struct {
	idempotencyKey string
	retrySafe      bool
	noContent      *bool
}

func (c *Client) do(ctx context.Context, method, path string, body any, params url.Values, admin bool, out any) error {
	return c.doWithPolicy(ctx, method, path, body, params, admin, out, requestPolicy{
		retrySafe: method == http.MethodGet || method == http.MethodHead,
	})
}

func (c *Client) doWithPolicy(ctx context.Context, method, path string, body any, params url.Values, admin bool, out any, policy requestPolicy) error {
	if c == nil {
		return errors.New("lians: nil client")
	}
	if c.initErr != nil {
		return c.initErr
	}
	if ctx == nil {
		return errors.New("lians: context must not be nil")
	}
	if admin && c.adminSecret == "" {
		return errors.New("lians: admin secret is required for this operation")
	}
	var bodyBytes []byte
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			return fmt.Errorf("lians: marshal request body: %w", err)
		}
		bodyBytes = b
	}

	u := c.baseURL + path
	if len(params) > 0 {
		u += "?" + params.Encode()
	}

	operationCtx, cancel := context.WithTimeout(ctx, c.timeout)
	defer cancel()
	for attempt := 0; ; attempt++ {
		var reqBody io.Reader
		if bodyBytes != nil {
			reqBody = bytes.NewReader(bodyBytes)
		}
		req, err := http.NewRequestWithContext(operationCtx, method, u, reqBody)
		if err != nil {
			return fmt.Errorf("lians: build request: %w", err)
		}
		req.Header.Set("X-API-Key", c.apiKey)
		req.Header.Set("User-Agent", UserAgent)
		if bodyBytes != nil {
			req.Header.Set("Content-Type", "application/json")
		}
		if policy.idempotencyKey != "" {
			req.Header.Set("Idempotency-Key", policy.idempotencyKey)
		}
		if admin {
			req.Header.Set("X-Admin-Secret", c.adminSecret)
		}

		resp, err := c.httpClient.Do(req)
		if err != nil {
			if policy.retrySafe && attempt < c.maxRetries && operationCtx.Err() == nil {
				if waitErr := waitForRetry(operationCtx, retryDelay(attempt, "", c.maxRetryDelay)); waitErr == nil {
					continue
				}
			}
			return fmt.Errorf("lians: %s %s failed: %w", method, path, err)
		}

		data, readErr := readBounded(resp.Body, c.maxResponseBytes)
		_ = resp.Body.Close()
		if readErr != nil {
			if policy.retrySafe && !errors.Is(readErr, errResponseTooLarge) && attempt < c.maxRetries && operationCtx.Err() == nil {
				if waitErr := waitForRetry(operationCtx, retryDelay(attempt, "", c.maxRetryDelay)); waitErr == nil {
					continue
				}
			}
			return fmt.Errorf("lians: read response: %w", readErr)
		}
		if policy.retrySafe && attempt < c.maxRetries && retryableStatus(resp.StatusCode) {
			if waitErr := waitForRetry(operationCtx, retryDelay(attempt, resp.Header.Get("Retry-After"), c.maxRetryDelay)); waitErr == nil {
				continue
			}
		}
		if resp.StatusCode < 200 || resp.StatusCode >= 300 {
			bodyText := string(data)
			if len(bodyText) > 64<<10 {
				bodyText = bodyText[:64<<10]
			}
			return &APIError{
				StatusCode: resp.StatusCode,
				Body:       bodyText,
				RequestID:  safeRequestID(resp.Header.Get("X-Request-ID")),
			}
		}
		if resp.StatusCode == http.StatusNoContent || resp.StatusCode == http.StatusResetContent {
			if policy.noContent != nil {
				*policy.noContent = true
			}
			return nil
		}
		if out != nil && len(data) > 0 && resp.StatusCode != http.StatusNoContent && resp.StatusCode != http.StatusResetContent {
			if err := json.Unmarshal(data, out); err != nil {
				return fmt.Errorf("lians: decode response: %w", err)
			}
		}
		return nil
	}
}

func normalizeBaseURL(raw string) (string, error) {
	if len(raw) == 0 || len(raw) > 8192 {
		return "", errors.New("lians: base URL must be between 1 and 8192 bytes")
	}
	u, err := url.ParseRequestURI(raw)
	if err != nil || u.Host == "" || (u.Scheme != "http" && u.Scheme != "https") || u.Opaque != "" {
		return "", errors.New("lians: base URL must be an absolute HTTP(S) URL")
	}
	if u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		return "", errors.New("lians: base URL must not contain credentials, a query, or a fragment")
	}
	return strings.TrimRight(raw, "/"), nil
}

func validateCredential(name, value string, required bool) error {
	if required && value == "" {
		return fmt.Errorf("lians: %s must not be empty", name)
	}
	if len(value) > 8192 {
		return fmt.Errorf("lians: %s is too long", name)
	}
	for _, r := range value {
		if r < 0x20 || r == 0x7f {
			return fmt.Errorf("lians: %s contains a control character", name)
		}
	}
	return nil
}

func safeRequestID(value string) string {
	if len(value) > 128 {
		value = value[:128]
	}
	var result strings.Builder
	result.Grow(len(value))
	for _, r := range value {
		if r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' || r == '-' || r == '_' || r == '.' {
			result.WriteRune(r)
		}
	}
	return result.String()
}

var errResponseTooLarge = errors.New("response exceeds configured limit")

func readBounded(r io.Reader, limit int64) ([]byte, error) {
	data, err := io.ReadAll(io.LimitReader(r, limit+1))
	if err != nil {
		return nil, err
	}
	if int64(len(data)) > limit {
		return nil, fmt.Errorf("%w (%d bytes)", errResponseTooLarge, limit)
	}
	return data, nil
}

func retryableStatus(code int) bool {
	switch code {
	case http.StatusRequestTimeout, http.StatusTooManyRequests, http.StatusInternalServerError,
		http.StatusBadGateway, http.StatusServiceUnavailable, http.StatusGatewayTimeout:
		return true
	default:
		return false
	}
}

func retryDelay(attempt int, retryAfter string, max time.Duration) time.Duration {
	if retryAfter != "" {
		if seconds, err := strconv.Atoi(strings.TrimSpace(retryAfter)); err == nil && seconds >= 0 {
			if int64(seconds) > int64(max/time.Second) {
				return max
			}
			d := time.Duration(seconds) * time.Second
			if d < max {
				return d
			}
			return max
		}
		if when, err := http.ParseTime(retryAfter); err == nil {
			d := time.Until(when)
			if d < 0 {
				return 0
			}
			if d < max {
				return d
			}
			return max
		}
	}
	d := 100 * time.Millisecond * time.Duration(1<<attempt)
	if d > max {
		return max
	}
	return d
}

func waitForRetry(ctx context.Context, delay time.Duration) error {
	if delay <= 0 {
		return nil
	}
	timer := time.NewTimer(delay)
	defer timer.Stop()
	select {
	case <-timer.C:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func newIdempotencyKey() (string, error) {
	var raw [16]byte
	if _, err := rand.Read(raw[:]); err != nil {
		return "", errors.New("lians: generate idempotency key")
	}
	raw[6] = (raw[6] & 0x0f) | 0x40
	raw[8] = (raw[8] & 0x3f) | 0x80
	buf := make([]byte, 36)
	hex.Encode(buf[0:8], raw[0:4])
	buf[8] = '-'
	hex.Encode(buf[9:13], raw[4:6])
	buf[13] = '-'
	hex.Encode(buf[14:18], raw[6:8])
	buf[18] = '-'
	hex.Encode(buf[19:23], raw[8:10])
	buf[23] = '-'
	hex.Encode(buf[24:36], raw[10:16])
	return string(buf), nil
}

func iso(t time.Time) string {
	return t.UTC().Format(time.RFC3339Nano)
}

// ── Write ──────────────────────────────────────────────────────────────────

// AddMemoryRequest is the input to AddMemory.
type AddMemoryRequest struct {
	AgentID        string
	Content        string
	EventTime      time.Time      // BUSINESS time the fact became true (not now)
	Metadata       map[string]any // structured keys (e.g. {"ticker":"NVDA","metric":"eps"})
	Source         string
	SubjectID      string
	Importance     float64 // 0..1; left at 0 it defaults to 0.5
	IdempotencyKey string  // optional business-stable replay key; generated per call when empty
}

// AddMemory stores a fact with its event-time. Supersession, audit-chain append,
// and per-subject encryption all happen server-side.
func (c *Client) AddMemory(ctx context.Context, req AddMemoryRequest) (*MemoryOut, error) {
	idempotencyKey := req.IdempotencyKey
	if idempotencyKey == "" {
		var err error
		idempotencyKey, err = newIdempotencyKey()
		if err != nil {
			return nil, err
		}
	}
	if err := validateIdempotencyKey(idempotencyKey); err != nil {
		return nil, err
	}
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
	noContent := false
	if err := c.doWithPolicy(ctx, http.MethodPost, "/v1/memories", body, nil, false, &out, requestPolicy{
		idempotencyKey: idempotencyKey,
		retrySafe:      true,
		noContent:      &noContent,
	}); err != nil {
		return nil, err
	}
	if noContent {
		return nil, nil
	}
	return &out, nil
}

func validateIdempotencyKey(value string) error {
	if len(value) == 0 || len(value) > 255 {
		return errors.New("lians: idempotency key must be 1-255 bytes")
	}
	for i := 0; i < len(value); i++ {
		if value[i] < 0x21 || value[i] > 0x7e {
			return errors.New("lians: idempotency key must use visible ASCII without whitespace")
		}
	}
	return nil
}

// ── Read ───────────────────────────────────────────────────────────────────

// RecallRequest is the input to Recall.
type RecallRequest struct {
	AgentID string
	Query   string
	K       int        // defaults to 5
	AsOf    *time.Time // point-in-time recall when non-nil
	Filters map[string]any
}

// Recall returns the current (non-stale) memories relevant to the query.
func (c *Client) Recall(ctx context.Context, req RecallRequest) (*RecallResult, error) {
	k := req.K
	if k <= 0 {
		k = 5
	}
	body := map[string]any{"agent_id": req.AgentID, "query": req.Query, "k": k}
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

// RecallAt is point-in-time recall — "what did the agent know on this date?".
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

// Snapshot returns a bounded knowledge-state page; inspect response completeness.
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

// BacktestCheck checks visible recorded Lians data relative to simulationAsOf.
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

// VerifyChain verifies the SEC 17a-4 tamper-evidence hash chain (requires admin secret).
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

// Path returns the shortest connection between two entities — the
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
