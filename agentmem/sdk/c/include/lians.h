/*
 * Lians C SDK — financial-grade agent memory over the REST API.
 *
 * A thin libcurl-based client for the Lians memory layer: bitemporal recall,
 * SEC 17a-4 audit chain, GDPR/HIPAA crypto-shred, information barriers, and a
 * relationship graph (conflict-of-interest / related-party / care-network).
 *
 * Built for the native, low-latency, and embedded world — HFT, market-data
 * gateways, and on-prem systems in finance, healthcare, and legal — where Python
 * and JVM SDKs don't reach. Responses are returned as raw JSON strings; pair with
 * your JSON parser of choice.
 *
 * Thread-safety: a lians_client_t is immutable after creation and may be shared
 * across threads; each call uses its own libcurl easy handle.
 *
 * Memory: every call returns a lians_response_t whose `body` you must release
 * with lians_response_free().
 */
#ifndef LIANS_H
#define LIANS_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Opaque client handle. */
typedef struct lians_client lians_client_t;

/* Result of a request. status<0 means the request never completed (network/setup);
 * otherwise status is the HTTP status code. body is a malloc'd NUL-terminated
 * string (may be NULL on hard failure) that the caller frees via
 * lians_response_free(). */
typedef struct {
    long  status;
    char *body;
} lians_response_t;

/* One-time global init/cleanup (wraps curl_global_init/cleanup). Call lians_global_init
 * once before creating clients in a multi-threaded program; optional otherwise. */
int  lians_global_init(void);
void lians_global_cleanup(void);

/* Create a client. base_url and api_key are required; admin_secret may be NULL
 * (needed only for /v1/admin/* audit endpoints). Returns NULL on failure. */
lians_client_t *lians_client_new(const char *base_url, const char *api_key,
                                 const char *admin_secret);

/* Set the per-request timeout in milliseconds (default 30000). */
void lians_client_set_timeout_ms(lians_client_t *client, long timeout_ms);

/* Free a client. */
void lians_client_free(lians_client_t *client);

/* Free a response body. Safe on a zero-initialized response. */
void lians_response_free(lians_response_t *resp);

/* ── Write ─────────────────────────────────────────────────────────────────── */

/* Store a fact. event_time is ISO-8601 UTC (the BUSINESS time, not now).
 * metadata_json/source/subject_id may be NULL. metadata_json must be a raw JSON
 * object literal, e.g. "{\"ticker\":\"NVDA\",\"metric\":\"eps\"}". */
lians_response_t lians_add(lians_client_t *client, const char *agent_id,
                           const char *content, const char *event_time,
                           const char *metadata_json, const char *source,
                           const char *subject_id, double importance);

/* ── Read ──────────────────────────────────────────────────────────────────── */

/* Recall current (non-stale) facts. as_of/filters_json may be NULL. Pass as_of
 * (ISO-8601 UTC) for point-in-time recall. */
lians_response_t lians_recall(lians_client_t *client, const char *agent_id,
                              const char *query, int k, const char *as_of,
                              const char *filters_json);

/* Exhaustive knowledge-state reconstruction at as_of (ISO-8601 UTC). */
lians_response_t lians_snapshot(lians_client_t *client, const char *agent_id,
                                const char *as_of, int limit);

/* Detect lookahead bias relative to simulation_as_of (ISO-8601 UTC). */
lians_response_t lians_backtest_check(lians_client_t *client, const char *agent_id,
                                      const char *simulation_as_of);

/* Time-series of a structured fact (ticker + metric), oldest first. */
lians_response_t lians_fact_history(lians_client_t *client, const char *agent_id,
                                    const char *ticker, const char *metric, int limit);

/* ── Compliance / erasure ──────────────────────────────────────────────────── */

/* GDPR/HIPAA crypto-shred a data subject. */
lians_response_t lians_erase(lians_client_t *client, const char *subject_id,
                             const char *request_ref);

/* Verify the SEC 17a-4 tamper-evidence hash chain (requires admin secret). */
lians_response_t lians_verify_chain(lians_client_t *client, const char *namespace_);

/* ── Relationship graph ────────────────────────────────────────────────────── */

/* Assert an edge src --rel_type--> dst. event_time is ISO-8601 UTC. */
lians_response_t lians_relate(lians_client_t *client, const char *agent_id,
                              const char *src_entity, const char *rel_type,
                              const char *dst_entity, const char *event_time,
                              int exclusive, int normalize);

/* Invalidate a live edge (sets valid_to). */
lians_response_t lians_unrelate(lians_client_t *client, const char *agent_id,
                                const char *src_entity, const char *rel_type,
                                const char *dst_entity);

/* Entities within `depth` hops of `entity`. direction is "any"|"in"|"out"
 * (NULL => "any"); as_of (ISO-8601 UTC) may be NULL for present-time. */
lians_response_t lians_neighbors(lians_client_t *client, const char *agent_id,
                                 const char *entity, int depth,
                                 const char *direction, const char *as_of);

/* Shortest connection between two entities — the conflict-of-interest /
 * related-party reachability query. as_of may be NULL. */
lians_response_t lians_path(lians_client_t *client, const char *agent_id,
                            const char *src_entity, const char *dst_entity,
                            int max_depth, const char *as_of);

#ifdef __cplusplus
}
#endif

#endif /* LIANS_H */
