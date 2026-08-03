package lians

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

type capture struct {
	method, path, query, body, apiKey, adminSecret, idempotencyKey, userAgent string
}

func newServer(cap *capture) *httptest.Server {
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		cap.method = r.Method
		cap.path = r.URL.Path
		cap.query = r.URL.RawQuery
		cap.body = string(b)
		cap.apiKey = r.Header.Get("X-API-Key")
		cap.adminSecret = r.Header.Get("X-Admin-Secret")
		cap.idempotencyKey = r.Header.Get("Idempotency-Key")
		cap.userAgent = r.Header.Get("User-Agent")

		switch {
		case r.URL.Path == "/v1/memories" && strings.Contains(cap.body, `"BOOM"`):
			w.WriteHeader(http.StatusUnprocessableEntity)
			io.WriteString(w, `{"detail":"boom"}`)
		case r.URL.Path == "/v1/memories":
			io.WriteString(w, `{"id":"m-1","namespace":"ns","agent_id":"desk",`+
				`"content":"NVDA guidance $40B","event_time":"2025-11-19T16:00:00Z",`+
				`"valid_to":null,"importance":0.5,"metadata":{"ticker":"NVDA"}}`)
		case r.URL.Path == "/v1/recall":
			io.WriteString(w, `{"memories":[{"id":"m-1","content":"NVDA guidance $40B",`+
				`"event_time":"2025-11-19T16:00:00Z"}],"as_of":null,"total_candidates":1}`)
		case r.URL.Path == "/v1/graph/path":
			io.WriteString(w, `{"src":"Attorney","dst":"PartyY","connected":true,"hops":2,"path":[]}`)
		case r.URL.Path == "/v1/admin/audit/verify":
			io.WriteString(w, `{"status":"ok","rows_checked":7}`)
		default:
			io.WriteString(w, `{}`)
		}
	}))
}

func newClient(srv *httptest.Server) *Client {
	return NewClient(srv.URL, "test-key", WithAdminSecret("admin-secret"))
}

func TestAddMemory(t *testing.T) {
	cap := &capture{}
	srv := newServer(cap)
	defer srv.Close()

	m, err := newClient(srv).AddMemory(context.Background(), AddMemoryRequest{
		AgentID:   "desk",
		Content:   "NVDA FY2026 revenue guidance raised to $40B",
		EventTime: time.Date(2025, 11, 19, 16, 0, 0, 0, time.UTC),
		Metadata:  map[string]any{"ticker": "NVDA", "metric": "revenue_guidance"},
	})
	if err != nil {
		t.Fatalf("AddMemory: %v", err)
	}
	if cap.method != http.MethodPost || cap.path != "/v1/memories" {
		t.Fatalf("unexpected request: %s %s", cap.method, cap.path)
	}
	if cap.apiKey != "test-key" {
		t.Fatalf("missing api key, got %q", cap.apiKey)
	}
	if cap.idempotencyKey == "" || cap.userAgent != UserAgent {
		t.Fatalf("missing SDK identity headers: idempotency=%q user-agent=%q", cap.idempotencyKey, cap.userAgent)
	}
	if !strings.Contains(cap.body, `"agent_id":"desk"`) ||
		!strings.Contains(cap.body, `"event_time":"2025-11-19T16:00:00Z"`) ||
		!strings.Contains(cap.body, `"ticker":"NVDA"`) {
		t.Fatalf("unexpected body: %s", cap.body)
	}
	if m.ID != "m-1" || m.Content == nil || *m.Content != "NVDA guidance $40B" {
		t.Fatalf("unexpected result: %+v", m)
	}
}

func TestClientConfigurationAndErrorRedaction(t *testing.T) {
	if _, err := NewClientWithError("https://user:secret@example.com", "key"); err == nil {
		t.Fatal("expected embedded URL credentials to be rejected")
	}
	if _, err := NewClientWithError("https://example.com", "key\r\ninjected"); err == nil {
		t.Fatal("expected header control characters to be rejected")
	}
	apiErr := &APIError{StatusCode: 422, Body: `{"detail":"sensitive"}`}
	if strings.Contains(apiErr.Error(), "sensitive") {
		t.Fatalf("APIError string leaked body: %q", apiErr.Error())
	}
}

func TestCallerIdempotencyKeyIsPreserved(t *testing.T) {
	cap := &capture{}
	srv := newServer(cap)
	defer srv.Close()

	_, err := newClient(srv).AddMemory(context.Background(), AddMemoryRequest{
		AgentID: "desk", Content: "stable", EventTime: time.Unix(0, 0),
		IdempotencyKey: "import:stable:v1",
	})
	if err != nil {
		t.Fatalf("AddMemory: %v", err)
	}
	if cap.idempotencyKey != "import:stable:v1" {
		t.Fatalf("idempotency key changed: %q", cap.idempotencyKey)
	}
}

func TestRecall(t *testing.T) {
	cap := &capture{}
	srv := newServer(cap)
	defer srv.Close()

	r, err := newClient(srv).Recall(context.Background(), RecallRequest{AgentID: "desk", Query: "NVDA guidance", K: 5})
	if err != nil {
		t.Fatalf("Recall: %v", err)
	}
	if cap.path != "/v1/recall" || len(r.Memories) != 1 || r.TotalCandidates != 1 {
		t.Fatalf("unexpected recall result: %+v", r)
	}
	if r.Memories[0].Content == nil || *r.Memories[0].Content != "NVDA guidance $40B" {
		t.Fatalf("unexpected content: %+v", r.Memories[0])
	}
}

func TestRecallAtSendsAsOf(t *testing.T) {
	cap := &capture{}
	srv := newServer(cap)
	defer srv.Close()

	_, err := newClient(srv).RecallAt(context.Background(), "desk", "NVDA guidance",
		time.Date(2025, 9, 1, 0, 0, 0, 0, time.UTC), 5)
	if err != nil {
		t.Fatalf("RecallAt: %v", err)
	}
	if !strings.Contains(cap.body, `"as_of":"2025-09-01T00:00:00Z"`) {
		t.Fatalf("missing as_of in body: %s", cap.body)
	}
}

func TestRecallNearAddsFilters(t *testing.T) {
	cap := &capture{}
	srv := newServer(cap)
	defer srv.Close()

	_, err := newClient(srv).RecallNear(context.Background(), "desk", "earnings", "FundA", "ticker", 5)
	if err != nil {
		t.Fatalf("RecallNear: %v", err)
	}
	if !strings.Contains(cap.body, `"_near_entity":"FundA"`) ||
		!strings.Contains(cap.body, `"_near_key":"ticker"`) {
		t.Fatalf("missing proximity filters: %s", cap.body)
	}
}

func TestPath(t *testing.T) {
	cap := &capture{}
	srv := newServer(cap)
	defer srv.Close()

	raw, err := newClient(srv).Path(context.Background(), "desk", "Attorney", "PartyY", 4, nil)
	if err != nil {
		t.Fatalf("Path: %v", err)
	}
	if cap.method != http.MethodGet || cap.path != "/v1/graph/path" {
		t.Fatalf("unexpected request: %s %s", cap.method, cap.path)
	}
	if !strings.Contains(cap.query, "src=Attorney") || !strings.Contains(cap.query, "dst=PartyY") {
		t.Fatalf("unexpected query: %s", cap.query)
	}
	var pr struct {
		Connected bool `json:"connected"`
		Hops      int  `json:"hops"`
	}
	if err := json.Unmarshal(raw, &pr); err != nil {
		t.Fatalf("decode path: %v", err)
	}
	if !pr.Connected || pr.Hops != 2 {
		t.Fatalf("unexpected path result: %+v", pr)
	}
}

func TestVerifyChainSendsAdminSecret(t *testing.T) {
	cap := &capture{}
	srv := newServer(cap)
	defer srv.Close()

	if _, err := newClient(srv).VerifyChain(context.Background(), "ns"); err != nil {
		t.Fatalf("VerifyChain: %v", err)
	}
	if cap.path != "/v1/admin/audit/verify" || cap.adminSecret != "admin-secret" {
		t.Fatalf("admin secret not sent: path=%s secret=%q", cap.path, cap.adminSecret)
	}
}

func TestAPIErrorOnNonSuccess(t *testing.T) {
	cap := &capture{}
	srv := newServer(cap)
	defer srv.Close()

	_, err := newClient(srv).AddMemory(context.Background(), AddMemoryRequest{
		AgentID:   "desk",
		Content:   "BOOM",
		EventTime: time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC),
	})
	if err == nil {
		t.Fatal("expected error")
	}
	var apiErr *APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("expected *APIError, got %T", err)
	}
	if apiErr.StatusCode != 422 || !strings.Contains(apiErr.Body, "boom") {
		t.Fatalf("unexpected APIError: %+v", apiErr)
	}
}
