#include "lians.h"
#include "lians_json.h"

#include <curl/curl.h>
#include <ctype.h>
#include <limits.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if defined(_WIN32)
#include <windows.h>
#else
#include <sys/select.h>
#endif

#if defined(_MSC_VER)
#include <intrin.h>
#endif

#define LIANS_DEFAULT_TIMEOUT_MS 30000L
#define LIANS_MAX_TIMEOUT_MS 600000L
#define LIANS_DEFAULT_MAX_RETRIES 2L
#define LIANS_MAX_RETRIES 5L
#define LIANS_DEFAULT_MAX_RESPONSE_BYTES (16L * 1024L * 1024L)
#define LIANS_MAX_RESPONSE_BYTES (256L * 1024L * 1024L)

struct lians_client {
    char *base_url;     /* no trailing slash */
    char *api_key;
    char *admin_secret; /* may be NULL */
    volatile long timeout_ms;
    volatile long max_retries;
    volatile long max_response_bytes;
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
    if (na > SIZE_MAX - nb - 1) {
        return NULL;
    }
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
    size_t max_len;
    int limit_exceeded;
};

static size_t write_cb(char *ptr, size_t size, size_t nmemb, void *userdata) {
    if (size != 0 && nmemb > SIZE_MAX / size) {
        return 0;
    }
    size_t n = size * nmemb;
    struct membuf *m = (struct membuf *)userdata;
    if (m->len > m->max_len || n > m->max_len - m->len) {
        m->limit_exceeded = 1;
        return 0;
    }
    if (m->len > SIZE_MAX - n - 1) {
        return 0; /* bounded response or size overflow */
    }
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

static long atomic_load_long(volatile long *value) {
#if defined(_MSC_VER)
    return _InterlockedCompareExchange(value, 0, 0);
#else
    return __atomic_load_n(value, __ATOMIC_RELAXED);
#endif
}

static void atomic_store_long(volatile long *value, long next) {
#if defined(_MSC_VER)
    (void)_InterlockedExchange(value, next);
#else
    __atomic_store_n(value, next, __ATOMIC_RELAXED);
#endif
}

static lians_response_t local_error(const char *message) {
    lians_response_t response;
    response.status = -1;
    response.body = dupstr(message ? message : "local SDK error");
    return response;
}

static void secure_free(char *value) {
    if (value) {
        volatile unsigned char *p = (volatile unsigned char *)value;
        size_t len = strlen(value);
        while (len--) {
            *p++ = 0;
        }
        free(value);
    }
}

static int valid_header_value(const char *value, int required, size_t max_len) {
    if (!value) {
        return !required;
    }
    size_t len = strlen(value);
    if ((required && len == 0) || len > max_len) {
        return 0;
    }
    for (const unsigned char *p = (const unsigned char *)value; *p; ++p) {
        if (*p < 0x20 || *p == 0x7f) {
            return 0;
        }
    }
    return 1;
}

static int valid_idempotency_key(const char *value) {
    if (!value) {
        return 0;
    }
    size_t len = strlen(value);
    if (len == 0 || len > 255) {
        return 0;
    }
    for (const unsigned char *p = (const unsigned char *)value; *p; ++p) {
        if (*p < 0x21 || *p > 0x7e) {
            return 0;
        }
    }
    return 1;
}

static int ascii_prefix_equal(const char *value, const char *prefix) {
    while (*prefix) {
        if (!*value || tolower((unsigned char)*value) != tolower((unsigned char)*prefix)) {
            return 0;
        }
        ++value;
        ++prefix;
    }
    return 1;
}

static int valid_base_url(const char *value) {
    if (!value) {
        return 0;
    }
    size_t len = strlen(value);
    if (len == 0 || len > 8192
            || (!ascii_prefix_equal(value, "http://") && !ascii_prefix_equal(value, "https://"))) {
        return 0;
    }
    const char *authority = strstr(value, "://") + 3;
    const char *authority_end = strchr(authority, '/');
    if (!authority_end) {
        authority_end = value + len;
    }
    if (authority == authority_end) {
        return 0;
    }
    for (const unsigned char *p = (const unsigned char *)value; *p; ++p) {
        if (*p <= 0x20 || *p == 0x7f || *p == '\\' || *p == '?' || *p == '#') {
            return 0;
        }
    }
    for (const char *p = authority; p < authority_end; ++p) {
        if (*p == '@') {
            return 0; /* never accept credentials in a URL */
        }
    }
    return 1;
}

static void sleep_ms(long milliseconds) {
    if (milliseconds <= 0) {
        return;
    }
#if defined(_WIN32)
    Sleep((DWORD)milliseconds);
#else
    struct timeval delay;
    delay.tv_sec = milliseconds / 1000;
    delay.tv_usec = (milliseconds % 1000) * 1000;
    (void)select(0, NULL, NULL, NULL, &delay);
#endif
}

/* Append an RFC 3986 percent-encoded query value; skips NULL values. */
static int qadd(lians_sb *url, int *first, const char *key, const char *val) {
    if (!val) {
        return 0;
    }
    if (lians_sb_append(url, *first ? "?" : "&") != 0
            || lians_sb_append(url, key) != 0 || lians_sb_append(url, "=") != 0) {
        return -1;
    }
    *first = 0;
    for (const unsigned char *p = (const unsigned char *)val; *p; ++p) {
        if ((*p >= 'a' && *p <= 'z') || (*p >= 'A' && *p <= 'Z')
                || (*p >= '0' && *p <= '9')
                || *p == '-' || *p == '.' || *p == '_' || *p == '~') {
            if (lians_sb_append_n(url, (const char *)p, 1) != 0) {
                return -1;
            }
        } else {
            char encoded[4];
            snprintf(encoded, sizeof(encoded), "%%%02X", *p);
            if (lians_sb_append_n(url, encoded, 3) != 0) {
                return -1;
            }
        }
    }
    return 0;
}

static int qadd_int(lians_sb *url, int *first, const char *key, long val) {
    char buf[32];
    snprintf(buf, sizeof(buf), "%ld", val);
    return qadd(url, first, key, buf);
}

/* ── core request ──────────────────────────────────────────────────────────── */

static int append_header(struct curl_slist **headers, const char *value) {
    struct curl_slist *next = curl_slist_append(*headers, value);
    if (!next) {
        return -1;
    }
    *headers = next;
    return 0;
}

static int retryable_status(long code) {
    return code == 408 || code == 429 || code == 500 || code == 502 || code == 503 || code == 504;
}

static int retryable_curl_error(CURLcode code) {
    switch (code) {
        case CURLE_COULDNT_RESOLVE_HOST:
        case CURLE_COULDNT_CONNECT:
        case CURLE_OPERATION_TIMEDOUT:
        case CURLE_SEND_ERROR:
        case CURLE_RECV_ERROR:
        case CURLE_GOT_NOTHING:
        case CURLE_PARTIAL_FILE:
            return 1;
        default:
            return 0;
    }
}

static lians_response_t do_request_policy(lians_client_t *c, const char *method,
                                          const char *url, const char *body, int admin,
                                          const char *idempotency_key, int retry_safe) {
    lians_response_t resp;
    resp.status = -1;
    resp.body = NULL;

    if (!c || !method || !url || (admin && (!c->admin_secret || !*c->admin_secret))) {
        return local_error(admin ? "admin secret is required" : "invalid request arguments");
    }

    CURL *h = curl_easy_init();
    if (!h) {
        resp.body = dupstr("curl_easy_init failed");
        return resp;
    }

    struct membuf mb;
    mb.data = NULL;
    mb.len = 0;
    mb.max_len = (size_t)atomic_load_long(&c->max_response_bytes);
    mb.limit_exceeded = 0;

    struct curl_slist *hdrs = NULL;
    char *apihdr = concat2("X-API-Key: ", c->api_key);
    if (!apihdr || append_header(&hdrs, apihdr) != 0) {
        secure_free(apihdr);
        curl_easy_cleanup(h);
        return local_error("failed to allocate request headers");
    }
    secure_free(apihdr);
    if (body) {
        if (append_header(&hdrs, "Content-Type: application/json") != 0) {
            curl_slist_free_all(hdrs);
            curl_easy_cleanup(h);
            return local_error("failed to allocate request headers");
        }
    }
    if (idempotency_key) {
        char *idem = concat2("Idempotency-Key: ", idempotency_key);
        if (!idem || append_header(&hdrs, idem) != 0) {
            secure_free(idem);
            curl_slist_free_all(hdrs);
            curl_easy_cleanup(h);
            return local_error("failed to allocate request headers");
        }
        secure_free(idem);
    }
    if (admin) {
        char *adm = concat2("X-Admin-Secret: ", c->admin_secret);
        if (!adm || append_header(&hdrs, adm) != 0) {
            secure_free(adm);
            curl_slist_free_all(hdrs);
            curl_easy_cleanup(h);
            return local_error("failed to allocate request headers");
        }
        secure_free(adm);
    }

    curl_easy_setopt(h, CURLOPT_URL, url);
    curl_easy_setopt(h, CURLOPT_CUSTOMREQUEST, method);
    curl_easy_setopt(h, CURLOPT_HTTPHEADER, hdrs);
    curl_easy_setopt(h, CURLOPT_WRITEFUNCTION, write_cb);
    curl_easy_setopt(h, CURLOPT_WRITEDATA, &mb);
    curl_easy_setopt(h, CURLOPT_NOSIGNAL, 1L);
    curl_easy_setopt(h, CURLOPT_FOLLOWLOCATION, 0L);
    curl_easy_setopt(h, CURLOPT_SSL_VERIFYPEER, 1L);
    curl_easy_setopt(h, CURLOPT_SSL_VERIFYHOST, 2L);
    curl_easy_setopt(h, CURLOPT_USERAGENT, LIANS_SDK_USER_AGENT);
#if LIBCURL_VERSION_NUM >= 0x075500
    curl_easy_setopt(h, CURLOPT_PROTOCOLS_STR, "http,https");
#else
    curl_easy_setopt(h, CURLOPT_PROTOCOLS, CURLPROTO_HTTP | CURLPROTO_HTTPS);
#endif
    if (body) {
        if (strlen(body) > 16L * 1024L * 1024L || strlen(body) > LONG_MAX) {
            curl_slist_free_all(hdrs);
            curl_easy_cleanup(h);
            return local_error("request body exceeds 16 MiB");
        }
        curl_easy_setopt(h, CURLOPT_POSTFIELDS, body);
        curl_easy_setopt(h, CURLOPT_POSTFIELDSIZE, (long)strlen(body));
    }

    long remaining_ms = atomic_load_long(&c->timeout_ms);
    long retries = retry_safe ? atomic_load_long(&c->max_retries) : 0;
    CURLcode rc = CURLE_OK;
    for (long attempt = 0; ; ++attempt) {
        curl_easy_setopt(h, CURLOPT_TIMEOUT_MS, remaining_ms);
        curl_easy_setopt(h, CURLOPT_CONNECTTIMEOUT_MS, remaining_ms < 10000 ? remaining_ms : 10000);
        rc = curl_easy_perform(h);
        long code = 0;
        if (rc == CURLE_OK) {
            curl_easy_getinfo(h, CURLINFO_RESPONSE_CODE, &code);
        }
        int should_retry = attempt < retries
                && ((rc == CURLE_OK && retryable_status(code))
                    || (rc != CURLE_OK && retryable_curl_error(rc)));
        if (!should_retry) {
            if (rc == CURLE_OK) {
                resp.status = code;
                resp.body = mb.data ? mb.data : dupstr("");
                mb.data = NULL;
            } else {
                resp.status = -1;
                if (rc == CURLE_WRITE_ERROR && mb.limit_exceeded) {
                    resp.body = dupstr("response body exceeded configured limit");
                } else {
                    resp.body = dupstr(curl_easy_strerror(rc));
                }
            }
            break;
        }

        double elapsed_seconds = 0.0;
        (void)curl_easy_getinfo(h, CURLINFO_TOTAL_TIME, &elapsed_seconds);
        long elapsed_ms = (long)(elapsed_seconds * 1000.0) + 1;
        long delay_ms = 100L << (attempt < 4 ? attempt : 4);
        if (elapsed_ms >= remaining_ms || delay_ms >= remaining_ms - elapsed_ms) {
            resp.status = rc == CURLE_OK ? code : -1;
            resp.body = rc == CURLE_OK
                    ? (mb.data ? mb.data : dupstr(""))
                    : dupstr(curl_easy_strerror(rc));
            mb.data = NULL;
            break;
        }
        remaining_ms -= elapsed_ms + delay_ms;
        free(mb.data);
        mb.data = NULL;
        mb.len = 0;
        mb.limit_exceeded = 0;
        sleep_ms(delay_ms);
    }

    free(mb.data);
    curl_slist_free_all(hdrs);
    curl_easy_cleanup(h);
    return resp;
}

/* Build "<base_url><path>" into a fresh lians_sb (caller must lians_sb_free). */
static void url_begin(lians_sb *url, lians_client_t *c, const char *path) {
    lians_sb_init(url);
    if (c) {
        lians_sb_append(url, c->base_url);
        lians_sb_append(url, path);
    } else {
        url->failed = 1;
    }
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
    if (!valid_base_url(base_url) || !valid_header_value(api_key, 1, 8192)
            || !valid_header_value(admin_secret, 0, 8192)) {
        return NULL;
    }
    lians_client_t *c = (lians_client_t *)calloc(1, sizeof(*c));
    if (!c) {
        return NULL;
    }
    /* strip trailing slashes without changing the validated authority */
    size_t n = strlen(base_url);
    while (n > 0 && base_url[n - 1] == '/') {
        --n;
    }
    if (n != strlen(base_url)) {
        c->base_url = (char *)malloc(n + 1);
        if (c->base_url) {
            memcpy(c->base_url, base_url, n);
            c->base_url[n] = '\0';
        }
    } else {
        c->base_url = dupstr(base_url);
    }
    c->api_key = dupstr(api_key);
    c->admin_secret = dupstr(admin_secret); /* NULL stays NULL */
    c->timeout_ms = LIANS_DEFAULT_TIMEOUT_MS;
    c->max_retries = LIANS_DEFAULT_MAX_RETRIES;
    c->max_response_bytes = LIANS_DEFAULT_MAX_RESPONSE_BYTES;

    if (!c->base_url || !c->api_key) {
        lians_client_free(c);
        return NULL;
    }
    return c;
}

void lians_client_set_timeout_ms(lians_client_t *client, long timeout_ms) {
    if (client && timeout_ms > 0 && timeout_ms <= LIANS_MAX_TIMEOUT_MS) {
        atomic_store_long(&client->timeout_ms, timeout_ms);
    }
}

void lians_client_set_max_retries(lians_client_t *client, long max_retries) {
    if (client && max_retries >= 0 && max_retries <= LIANS_MAX_RETRIES) {
        atomic_store_long(&client->max_retries, max_retries);
    }
}

void lians_client_set_max_response_bytes(lians_client_t *client, long max_bytes) {
    if (client && max_bytes >= 1024 && max_bytes <= LIANS_MAX_RESPONSE_BYTES) {
        atomic_store_long(&client->max_response_bytes, max_bytes);
    }
}

void lians_client_free(lians_client_t *client) {
    if (!client) {
        return;
    }
    free(client->base_url);
    secure_free(client->api_key);
    secure_free(client->admin_secret);
    free(client);
}

void lians_response_free(lians_response_t *resp) {
    if (resp && resp->body) {
        secure_free(resp->body);
        resp->body = NULL;
    }
}

static int required_text(const char *value) {
    return value && *value && strlen(value) <= 8192;
}

static int required_content(const char *value) {
    return value && *value && strlen(value) <= 16L * 1024L * 1024L;
}

static int optional_text(const char *value) {
    return !value || strlen(value) <= 16L * 1024L * 1024L;
}

static int optional_query(const char *value) {
    return !value || strlen(value) <= 8192;
}

static lians_response_t buffered_request(lians_client_t *client, const char *method,
                                         lians_sb *url, lians_sb *body, int admin,
                                         const char *idempotency_key, int retry_safe) {
    if (!url || url->failed || (body && body->failed)) {
        return local_error("failed to allocate request");
    }
    return do_request_policy(client, method, url->data, body ? body->data : NULL,
                             admin, idempotency_key, retry_safe);
}

/* ── write ─────────────────────────────────────────────────────────────────── */

static lians_response_t add_internal(lians_client_t *client, const char *agent_id,
                                     const char *content, const char *event_time,
                                     const char *metadata_json, const char *source,
                                     const char *subject_id, double importance,
                                     const char *idempotency_key) {
    if (!client || !required_text(agent_id) || !required_content(content)
            || !required_text(event_time) || !optional_text(metadata_json)
            || !optional_text(source) || !optional_text(subject_id)
            || !isfinite(importance) || importance < 0.0 || importance > 1.0) {
        return local_error("invalid lians_add arguments");
    }
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
    lians_response_t r = buffered_request(client, "POST", &url, &b, 0,
                                          idempotency_key, idempotency_key != NULL);
    lians_sb_free(&url);
    lians_sb_free(&b);
    return r;
}

lians_response_t lians_add(lians_client_t *client, const char *agent_id,
                           const char *content, const char *event_time,
                           const char *metadata_json, const char *source,
                           const char *subject_id, double importance) {
    return add_internal(client, agent_id, content, event_time, metadata_json,
                        source, subject_id, importance, NULL);
}

lians_response_t lians_add_idempotent(lians_client_t *client, const char *agent_id,
                                      const char *content, const char *event_time,
                                      const char *metadata_json, const char *source,
                                      const char *subject_id, double importance,
                                      const char *idempotency_key) {
    if (!valid_idempotency_key(idempotency_key)) {
        return local_error("invalid idempotency key");
    }
    return add_internal(client, agent_id, content, event_time, metadata_json,
                        source, subject_id, importance, idempotency_key);
}

/* ── read ──────────────────────────────────────────────────────────────────── */

lians_response_t lians_recall(lians_client_t *client, const char *agent_id,
                              const char *query, int k, const char *as_of,
                              const char *filters_json) {
    if (!client || !required_text(agent_id) || !required_text(query)
            || !optional_query(as_of) || !optional_text(filters_json)) {
        return local_error("invalid lians_recall arguments");
    }
    if (k <= 0) {
        k = 5;
    }
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
    lians_response_t r = buffered_request(client, "POST", &url, &b, 0, NULL, 0);
    lians_sb_free(&url);
    lians_sb_free(&b);
    return r;
}

lians_response_t lians_snapshot(lians_client_t *client, const char *agent_id,
                                const char *as_of, int limit) {
    if (!client || !required_text(agent_id) || !required_text(as_of)) {
        return local_error("invalid lians_snapshot arguments");
    }
    lians_sb url;
    url_begin(&url, client, "/v1/snapshot");
    int first = 1;
    qadd(&url, &first, "agent_id", agent_id);
    qadd(&url, &first, "as_of", as_of);
    qadd_int(&url, &first, "limit", limit);
    lians_response_t r = buffered_request(client, "GET", &url, NULL, 0, NULL, 1);
    lians_sb_free(&url);
    return r;
}

lians_response_t lians_backtest_check(lians_client_t *client, const char *agent_id,
                                      const char *simulation_as_of) {
    if (!client || !required_text(agent_id) || !required_text(simulation_as_of)) {
        return local_error("invalid lians_backtest_check arguments");
    }
    lians_sb b;
    lians_sb_init(&b);
    lians_sb_append(&b, "{\"agent_id\":");
    lians_sb_append_json_string(&b, agent_id);
    lians_sb_append(&b, ",\"simulation_as_of\":");
    lians_sb_append_json_string(&b, simulation_as_of);
    lians_sb_append(&b, "}");

    lians_sb url;
    url_begin(&url, client, "/v1/backtest/check");
    lians_response_t r = buffered_request(client, "POST", &url, &b, 0, NULL, 0);
    lians_sb_free(&url);
    lians_sb_free(&b);
    return r;
}

lians_response_t lians_fact_history(lians_client_t *client, const char *agent_id,
                                    const char *ticker, const char *metric, int limit) {
    if (!client || !required_text(agent_id) || !required_text(ticker) || !required_text(metric)) {
        return local_error("invalid lians_fact_history arguments");
    }
    lians_sb url;
    url_begin(&url, client, "/v1/facts/history");
    int first = 1;
    qadd(&url, &first, "agent_id", agent_id);
    qadd(&url, &first, "ticker", ticker);
    qadd(&url, &first, "metric", metric);
    qadd_int(&url, &first, "limit", limit);
    lians_response_t r = buffered_request(client, "GET", &url, NULL, 0, NULL, 1);
    lians_sb_free(&url);
    return r;
}

/* ── compliance / erasure ──────────────────────────────────────────────────── */

lians_response_t lians_erase(lians_client_t *client, const char *subject_id,
                             const char *request_ref) {
    if (!client || !required_text(subject_id) || !required_text(request_ref)) {
        return local_error("invalid lians_erase arguments");
    }
    lians_sb b;
    lians_sb_init(&b);
    lians_sb_append(&b, "{\"subject_id\":");
    lians_sb_append_json_string(&b, subject_id);
    lians_sb_append(&b, ",\"request_ref\":");
    lians_sb_append_json_string(&b, request_ref);
    lians_sb_append(&b, "}");

    lians_sb url;
    url_begin(&url, client, "/v1/erase");
    /* Erasure is one-time and intentionally never retried automatically. */
    lians_response_t r = buffered_request(client, "POST", &url, &b, 0, NULL, 0);
    lians_sb_free(&url);
    lians_sb_free(&b);
    return r;
}

lians_response_t lians_verify_chain(lians_client_t *client, const char *namespace_) {
    if (!client || !required_text(namespace_)) {
        return local_error("invalid lians_verify_chain arguments");
    }
    lians_sb url;
    url_begin(&url, client, "/v1/admin/audit/verify");
    int first = 1;
    qadd(&url, &first, "namespace", namespace_);
    lians_response_t r = buffered_request(client, "GET", &url, NULL, 1, NULL, 1);
    lians_sb_free(&url);
    return r;
}

/* ── relationship graph ────────────────────────────────────────────────────── */

lians_response_t lians_relate(lians_client_t *client, const char *agent_id,
                              const char *src_entity, const char *rel_type,
                              const char *dst_entity, const char *event_time,
                              int exclusive, int normalize) {
    if (!client || !required_text(agent_id) || !required_text(src_entity)
            || !required_text(rel_type) || !required_text(dst_entity)
            || !required_text(event_time)) {
        return local_error("invalid lians_relate arguments");
    }
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
    lians_response_t r = buffered_request(client, "POST", &url, &b, 0, NULL, 0);
    lians_sb_free(&url);
    lians_sb_free(&b);
    return r;
}

lians_response_t lians_unrelate(lians_client_t *client, const char *agent_id,
                                const char *src_entity, const char *rel_type,
                                const char *dst_entity) {
    if (!client || !required_text(agent_id) || !required_text(src_entity)
            || !required_text(rel_type) || !required_text(dst_entity)) {
        return local_error("invalid lians_unrelate arguments");
    }
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
    lians_response_t r = buffered_request(client, "POST", &url, &b, 0, NULL, 0);
    lians_sb_free(&url);
    lians_sb_free(&b);
    return r;
}

lians_response_t lians_neighbors(lians_client_t *client, const char *agent_id,
                                 const char *entity, int depth,
                                 const char *direction, const char *as_of) {
    if (!client || !required_text(agent_id) || !required_text(entity)
            || !optional_query(as_of)
            || (direction && strcmp(direction, "any") != 0
                && strcmp(direction, "in") != 0 && strcmp(direction, "out") != 0)) {
        return local_error("invalid lians_neighbors arguments");
    }
    lians_sb url;
    url_begin(&url, client, "/v1/graph/neighbors");
    int first = 1;
    qadd(&url, &first, "agent_id", agent_id);
    qadd(&url, &first, "entity", entity);
    qadd_int(&url, &first, "depth", depth);
    qadd(&url, &first, "direction", direction ? direction : "any");
    qadd(&url, &first, "as_of", as_of);
    lians_response_t r = buffered_request(client, "GET", &url, NULL, 0, NULL, 1);
    lians_sb_free(&url);
    return r;
}

lians_response_t lians_path(lians_client_t *client, const char *agent_id,
                            const char *src_entity, const char *dst_entity,
                            int max_depth, const char *as_of) {
    if (!client || !required_text(agent_id) || !required_text(src_entity)
            || !required_text(dst_entity) || !optional_query(as_of)) {
        return local_error("invalid lians_path arguments");
    }
    lians_sb url;
    url_begin(&url, client, "/v1/graph/path");
    int first = 1;
    qadd(&url, &first, "agent_id", agent_id);
    qadd(&url, &first, "src", src_entity);
    qadd(&url, &first, "dst", dst_entity);
    qadd_int(&url, &first, "max_depth", max_depth);
    qadd(&url, &first, "as_of", as_of);
    lians_response_t r = buffered_request(client, "GET", &url, NULL, 0, NULL, 1);
    lians_sb_free(&url);
    return r;
}
