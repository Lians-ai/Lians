#include "lians.h"

/* Compile-only fixture: no network request or process entry point. */
void lians_api_compile_fixture(lians_client_t *client) {
    lians_client_set_timeout_ms(client, 30000);
    lians_client_set_max_retries(client, 2);
    lians_client_set_max_response_bytes(client, 16L * 1024L * 1024L);

    lians_response_t response = lians_add_idempotent(
        client,
        "agent",
        "content",
        "2026-01-01T00:00:00Z",
        "{}",
        NULL,
        NULL,
        0.5,
        "business-operation:v1"
    );
    lians_response_free(&response);
}
