/*
 * Lians C SDK example.
 *
 * Build:  cmake -B build && cmake --build build
 * Run:    LIANS_URL=https://api.lians.dev LIANS_API_KEY=lians_... ./build/lians_example
 */
#include "lians.h"

#include <stdio.h>
#include <stdlib.h>

static void show(const char *label, lians_response_t *r) {
    printf("%-12s -> %ld  %s\n", label, r->status, r->body ? r->body : "(no body)");
    lians_response_free(r);
}

int main(void) {
    const char *url = getenv("LIANS_URL");
    const char *key = getenv("LIANS_API_KEY");
    if (!url || !key) {
        printf("Set LIANS_URL and LIANS_API_KEY to run this example.\n");
        return 0;
    }

    lians_global_init();
    lians_client_t *c = lians_client_new(url, key, getenv("LIANS_ADMIN_SECRET"));
    if (!c) {
        fprintf(stderr, "client init failed\n");
        lians_global_cleanup();
        return 1;
    }

    /* Store a fact with its business event-time. */
    lians_response_t r = lians_add(
        c, "equity-desk", "NVDA FY2026 revenue guidance raised to $40B",
        "2025-11-19T16:00:00Z",
        "{\"ticker\":\"NVDA\",\"metric\":\"revenue_guidance\"}",
        "analyst", NULL, 0.6);
    show("add", &r);

    /* Recall current facts. */
    r = lians_recall(c, "equity-desk", "NVDA revenue guidance", 5, NULL, NULL);
    show("recall", &r);

    /* Point-in-time recall. */
    r = lians_recall(c, "equity-desk", "NVDA revenue guidance", 5,
                     "2025-09-01T00:00:00Z", NULL);
    show("recall_at", &r);

    /* Relationship graph: conflict-of-interest reachability. */
    r = lians_relate(c, "matter-7", "Attorney", "represented", "ClientX",
                     "2026-01-01T00:00:00Z", 0, 0);
    show("relate", &r);
    r = lians_relate(c, "matter-7", "ClientX", "adverse_to", "PartyY",
                     "2026-01-01T00:00:00Z", 0, 0);
    show("relate", &r);
    r = lians_path(c, "matter-7", "Attorney", "PartyY", 4, NULL);
    show("coi path", &r);

    lians_client_free(c);
    lians_global_cleanup();
    return 0;
}
