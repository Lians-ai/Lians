#include "lians.h"
#include "lians_json.h"

#include <curl/curl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct lians_client {
    char *base_url;     /* no trailing slash */
    char *api_key;
    char *admin_secret; /* may be NULL */
    long  timeout_ms;
};

/* ── small utilities ───────────────────────────────────────────────────────── */

static char *dupstr(const char *s) {
    if (!s) {
        return NULL;
    }
    size_t n = strlen(s);
    char *p = (char *)malloc(n + 1);
    if (p) {
        memcpy(p, s, n + 1);
    }
    return p;
}

static char *concat2(const char *a, const char *b) {
    size_t na = strlen(a), nb = strlen(b);
    char *p = (char *)malloc(na + nb + 1);
    if (!p) {
        return NULL;
    }
    memcpy(p, a, na);
    memcpy(p + na, b, nb + 1);
    return p;
}

struct membuf {
    char  *data;
    size_t len;
};

static size_t write_cb(char *ptr, size_t size, size_t nmemb, void *userdata) {
    size_t n = size * nmemb;
    struct membuf *m = (struct membuf *)userdata;
    char *p = (char *)realloc(m->data, m->len + n + 1);
    if (!p) {
        return 0; /* signals error to libcurl */
    }
    m->data = p;
    memcpy(m->data + m->len, ptr, n);
    m->len += n;
    m->data[m->len] = '\0';
    return n;
}

/* Append "?key=val" / "&key=val" with URL-encoded value; skips NULL values. */
static void qadd(lians_sb *url, int *first, const char *key, const char *val) {
    if (!val) {
        return;
    }
    char *ev = curl_easy_escape(NULL, val, 0);
    if (!ev) {
        return;
    }
    lians_sb_append(url, *first ? "?" : "&");
    *first = 0;
    lians_sb_append(url, key);
    lians_sb_append(url, "=");
    lians_sb_append(url, ev);
    curl_free(ev);
}

static void qadd_int(lians_sb *url, int *first, const char *key, long val) {
    char buf[32];
    snprintf(buf, sizeof(buf), "%ld", val);
    qadd(url, first, key, buf);
}

/* ── core request ──────────────────────────────────────────────────────────── */

static lians_response_t do_request(lians_client_t *c, const char *method,
                                   const char *url, const char *body, int admin) {
    lians_response_t resp;
    resp.status = -1;
    resp.body = NULL;

    CURL *h = curl_easy_init();
    if (!h) {
        resp.body = dupstr("curl_easy_init failed");
        return resp;
    }

    struct membuf mb;
    mb.data = NULL;
    mb.len = 0;

    struct curl_slist *hdrs = NULL;
    char *apihdr = concat2("X-API-Key: ", c->api_key);
    if (apihdr) {
        hdrs = curl_slist_append(hdrs, apihdr);
        free(apihdr);
    }
    if (body) {
        hdrs = curl_slist_append(hdrs, "Content-Type: application/json");
    }
    if (admin && c->admin_secret) {
        char *adm = concat2("X-Admin-Secret: ", c->admin_secret);
        if (adm) {
            hdrs = curl_slist_append(hdrs, adm);
            free(adm);
        }
    }

    curl_easy_setopt(h, CURLOPT_URL, url);
    curl_easy_setopt(h, CURLOPT_CUSTOMREQUEST, method);
    curl_easy_setopt(h, CURLOPT_HTTPHEADER, hdrs);
    curl_easy_setopt(h, CURLOPT_WRITEFUNCTION, write_cb);
    curl_easy_setopt(h, CURLOPT_WRITEDATA, &mb);
    curl_easy_setopt(h, CURLOPT_TIMEOUT_MS, c->timeout_ms);
    curl_easy_setopt(h, CURLOPT_USERAGENT, "lians-c-sdk/0.2.0");
    if (body) {
        curl_easy_setopt(h, CURLOPT_POSTFIELDS, body);
        curl_easy_setopt(h, CURLOPT_POSTFIELDSIZE, (long)strlen(body));
    }

    CURLcode rc = curl_easy_perform(h);
    if (rc == CURLE_OK) {
        long code = 0;
        curl_easy_getinfo(h, CURLINFO_RESPONSE_CODE, &code);
        resp.status = code;
        resp.body = mb.data ? mb.data : dupstr("");
    } else {
        resp.status = -1;
        free(mb.data);
        resp.body = dupstr(curl_easy_strerror(rc));
    }

    curl_slist_free_all(hdrs);
    curl_easy_cleanup(h);
    return resp;
}

/* Build "<base_url><path>" into a fresh lians_sb (caller must lians_sb_free). */
static void url_begin(lians_sb *url, lians_client_t *c, const char *path) {
    lians_sb_init(url);
    lians_sb_append(url, c->base_url);
    lians_sb_append(url, path);
}

/* ── lifecycle ─────────────────────────────────────────────────────────────── */

int lians_global_init(void) {
    return curl_global_init(CURL_GLOBAL_DEFAULT) == CURLE_OK ? 0 : -1;
}

void lians_global_cleanup(void) {
    curl_global_cleanup();
}

lians_client_t *lians_client_new(const char *base_url, const char *api_key,
                                 const char *admin_secret) {
    if (!base_url || !api_key) {
        return NULL;
    }
    lians_client_t *c = (lians_client_t *)calloc(1, sizeof(*c));
    if (!c) {
        return NULL;
    }
    /* strip a single trailing slash */
    size_t n = strlen(base_url);
    if (n > 0 && base_url[n - 1] == '/') {
        c->base_url = (char *)malloc(n);
        if (c->base_url) {
            memcpy(c->base_url, base_url, n - 1);
            c->base_url[n - 1] = '\0';
        }
    } else {
        c->base_url = dupstr(base_url);
    }
    c->api_key = dupstr(api_key);
    c->admin_secret = dupstr(admin_secret); /* NULL stays NULL */
    c->timeout_ms = 30000;

    if (!c->base_url || !c->api_key) {
        lians_client_free(c);
        return NULL;
    }
    return c;
}

void lians_client_set_timeout_ms(lians_client_t *client, long timeout_ms) {
    if (client && timeout_ms > 0) {
        client->timeout_ms = timeout_ms;
    }
}

void lians_client_free(lians_client_t *client) {
    if (!client) {
        return;
    }
    free(client->base_url);
    free(client->api_key);
    free(client->admin_secret);
    free(client);
}

void lians_response_free(lians_response_t *resp) {
    if (resp && resp->body) {
        free(resp->body);
        resp->body = NULL;
    }
}

/* ── write ─────────────────────────────────────────────────────────────────── */

lians_response_t lians_add(lians_client_t *client, const char *agent_id,
                           const char *content, const char *event_time,
                           const char *metadata_json, const char *source,
                           const char *subject_id, double importance) {
    lians_sb b;
    lians_sb_init(&b);
    lians_sb_append(&b, "{\"agent_id\":");
    lians_sb_append_json_string(&b, agent_id);
    lians_sb_append(&b, ",\"content\":");
    lians_sb_append_json_string(&b, content);
    lians_sb_append(&b, ",\"event_time\":");
    lians_sb_append_json_string(&b, event_time);
    char imp[40];
    snprintf(imp, sizeof(imp), ",\"importance\":%g", importance);
    lians_sb_append(&b, imp);
    if (source) {
        lians_sb_append(&b, ",\"source\":");
        lians_sb_append_json_string(&b, source);
    }
    if (subject_id) {
        lians_sb_append(&b, ",\"subject_id\":");
        lians_sb_append_json_string(&b, subject_id);
    }
    if (metadata_json) {
        lians_sb_append(&b, ",\"metadata\":");
        lians_sb_append(&b, metadata_json);
    }
    lians_sb_append(&b, "}");

    lians_sb url;
    url_begin(&url, client, "/v1/memories");
    lians_response_t r = do_request(client, "POST", url.data, b.data, 0);
    lians_sb_free(&url);
    lians_sb_free(&b);
    return r;
}

/* ── read ──────────────────────────────────────────────────────────────────── */

lians_response_t lians_recall(lians_client_t *client, const char *agent_id,
                              const char *query, int k, const char *as_of,
                              const char *filters_json) {
    lians_sb b;
    lians_sb_init(&b);
    lians_sb_append(&b, "{\"agent_id\":");
    lians_sb_append_json_string(&b, agent_id);
    lians_sb_append(&b, ",\"query\":");
    lians_sb_append_json_string(&b, query);
    char kbuf[32];
    snprintf(kbuf, sizeof(kbuf), ",\"k\":%d", k);
    lians_sb_append(&b, kbuf);
    if (as_of) {
        lians_sb_append(&b, ",\"as_of\":");
        lians_sb_append_json_string(&b, as_of);
    }
    if (filters_json) {
        lians_sb_append(&b, ",\"filters\":");
        lians_sb_append(&b, filters_json);
    }
    lians_sb_append(&b, "}");

    lians_sb url;
    url_begin(&url, client, "/v1/recall");
    lians_response_t r = do_request(client, "POST", url.data, b.data, 0);
    lians_sb_free(&url);
    lians_sb_free(&b);
    return r;
}

lians_response_t lians_snapshot(lians_client_t *client, const char *agent_id,
                                const char *as_of, int limit) {
    lians_sb url;
    url_begin(&url, client, "/v1/snapshot");
    int first = 1;
    qadd(&url, &first, "agent_id", agent_id);
    qadd(&url, &first, "as_of", as_of);
    qadd_int(&url, &first, "limit", limit);
    lians_response_t r = do_request(client, "GET", url.data, NULL, 0);
    lians_sb_free(&url);
    return r;
}

lians_response_t lians_backtest_check(lians_client_t *client, const char *agent_id,
                                      const char *simulation_as_of) {
    lians_sb b;
    lians_sb_init(&b);
    lians_sb_append(&b, "{\"agent_id\":");
    lians_sb_append_json_string(&b, agent_id);
    lians_sb_append(&b, ",\"simulation_as_of\":");
    lians_sb_append_json_string(&b, simulation_as_of);
    lians_sb_append(&b, "}");

    lians_sb url;
    url_begin(&url, client, "/v1/backtest/check");
    lians_response_t r = do_request(client, "POST", url.data, b.data, 0);
    lians_sb_free(&url);
    lians_sb_free(&b);
    return r;
}

lians_response_t lians_fact_history(lians_client_t *client, const char *agent_id,
                                    const char *ticker, const char *metric, int limit) {
    lians_sb url;
    url_begin(&url, client, "/v1/facts/history");
    int first = 1;
    qadd(&url, &first, "agent_id", agent_id);
    qadd(&url, &first, "ticker", ticker);
    qadd(&url, &first, "metric", metric);
    qadd_int(&url, &first, "limit", limit);
    lians_response_t r = do_request(client, "GET", url.data, NULL, 0);
    lians_sb_free(&url);
    return r;
}

/* ── compliance / erasure ──────────────────────────────────────────────────── */

lians_response_t lians_erase(lians_client_t *client, const char *subject_id,
                             const char *request_ref) {
    lians_sb b;
    lians_sb_init(&b);
    lians_sb_append(&b, "{\"subject_id\":");
    lians_sb_append_json_string(&b, subject_id);
    lians_sb_append(&b, ",\"request_ref\":");
    lians_sb_append_json_string(&b, request_ref);
    lians_sb_append(&b, "}");

    lians_sb url;
    url_begin(&url, client, "/v1/erase");
    lians_response_t r = do_request(client, "POST", url.data, b.data, 0);
    lians_sb_free(&url);
    lians_sb_free(&b);
    return r;
}

lians_response_t lians_verify_chain(lians_client_t *client, const char *namespace_) {
    lians_sb url;
    url_begin(&url, client, "/v1/admin/audit/verify");
    int first = 1;
    qadd(&url, &first, "namespace", namespace_);
    lians_response_t r = do_request(client, "GET", url.data, NULL, 1 /* admin */);
    lians_sb_free(&url);
    return r;
}

/* ── relationship graph ────────────────────────────────────────────────────── */

lians_response_t lians_relate(lians_client_t *client, const char *agent_id,
                              const char *src_entity, const char *rel_type,
                              const char *dst_entity, const char *event_time,
                              int exclusive, int normalize) {
    lians_sb b;
    lians_sb_init(&b);
    lians_sb_append(&b, "{\"agent_id\":");
    lians_sb_append_json_string(&b, agent_id);
    lians_sb_append(&b, ",\"src_entity\":");
    lians_sb_append_json_string(&b, src_entity);
    lians_sb_append(&b, ",\"rel_type\":");
    lians_sb_append_json_string(&b, rel_type);
    lians_sb_append(&b, ",\"dst_entity\":");
    lians_sb_append_json_string(&b, dst_entity);
    lians_sb_append(&b, ",\"event_time\":");
    lians_sb_append_json_string(&b, event_time);
    lians_sb_append(&b, exclusive ? ",\"exclusive\":true" : ",\"exclusive\":false");
    lians_sb_append(&b, normalize ? ",\"normalize\":true" : ",\"normalize\":false");
    lians_sb_append(&b, "}");

    lians_sb url;
    url_begin(&url, client, "/v1/graph/relate");
    lians_response_t r = do_request(client, "POST", url.data, b.data, 0);
    lians_sb_free(&url);
    lians_sb_free(&b);
    return r;
}

lians_response_t lians_unrelate(lians_client_t *client, const char *agent_id,
                                const char *src_entity, const char *rel_type,
                                const char *dst_entity) {
    lians_sb b;
    lians_sb_init(&b);
    lians_sb_append(&b, "{\"agent_id\":");
    lians_sb_append_json_string(&b, agent_id);
    lians_sb_append(&b, ",\"src_entity\":");
    lians_sb_append_json_string(&b, src_entity);
    lians_sb_append(&b, ",\"rel_type\":");
    lians_sb_append_json_string(&b, rel_type);
    lians_sb_append(&b, ",\"dst_entity\":");
    lians_sb_append_json_string(&b, dst_entity);
    lians_sb_append(&b, "}");

    lians_sb url;
    url_begin(&url, client, "/v1/graph/unrelate");
    lians_response_t r = do_request(client, "POST", url.data, b.data, 0);
    lians_sb_free(&url);
    lians_sb_free(&b);
    return r;
}

lians_response_t lians_neighbors(lians_client_t *client, const char *agent_id,
                                 const char *entity, int depth,
                                 const char *direction, const char *as_of) {
    lians_sb url;
    url_begin(&url, client, "/v1/graph/neighbors");
    int first = 1;
    qadd(&url, &first, "agent_id", agent_id);
    qadd(&url, &first, "entity", entity);
    qadd_int(&url, &first, "depth", depth);
    qadd(&url, &first, "direction", direction ? direction : "any");
    qadd(&url, &first, "as_of", as_of);
    lians_response_t r = do_request(client, "GET", url.data, NULL, 0);
    lians_sb_free(&url);
    return r;
}

lians_response_t lians_path(lians_client_t *client, const char *agent_id,
                            const char *src_entity, const char *dst_entity,
                            int max_depth, const char *as_of) {
    lians_sb url;
    url_begin(&url, client, "/v1/graph/path");
    int first = 1;
    qadd(&url, &first, "agent_id", agent_id);
    qadd(&url, &first, "src", src_entity);
    qadd(&url, &first, "dst", dst_entity);
    qadd_int(&url, &first, "max_depth", max_depth);
    qadd(&url, &first, "as_of", as_of);
    lians_response_t r = do_request(client, "GET", url.data, NULL, 0);
    lians_sb_free(&url);
    return r;
}
