/* Unit tests for the pure JSON/string helpers — no network, no libcurl. */
#include "lians_json.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int failures = 0;

#define CHECK(cond, msg)                                  \
    do {                                                  \
        if (!(cond)) {                                    \
            printf("FAIL: %s\n", (msg));                  \
            failures++;                                   \
        }                                                 \
    } while (0)

int main(void) {
    /* Quotes, backslash, newline, tab. */
    char *e = lians_json_escape("he said \"hi\"\n\tend\\");
    CHECK(e != NULL, "escape returns non-NULL");
    CHECK(e && strcmp(e, "he said \\\"hi\\\"\\n\\tend\\\\") == 0, "escape content");
    free(e);

    /* Control character -> \u00xx. */
    char in[2];
    in[0] = 0x01;
    in[1] = '\0';
    char *ctrl = lians_json_escape(in);
    CHECK(ctrl && strcmp(ctrl, "\\u0001") == 0, "control char escapes to \\u0001");
    free(ctrl);

    /* NULL escapes to empty string. */
    char *nul = lians_json_escape(NULL);
    CHECK(nul != NULL && nul[0] == '\0', "NULL escapes to empty string");
    free(nul);

    /* String builder + quoted JSON string. */
    lians_sb sb;
    CHECK(lians_sb_init(&sb) == 0, "sb_init");
    lians_sb_append(&sb, "{\"k\":");
    lians_sb_append_json_string(&sb, "a\"b");
    lians_sb_append(&sb, "}");
    CHECK(strcmp(sb.data, "{\"k\":\"a\\\"b\"}") == 0, "sb builds escaped JSON object");
    lians_sb_free(&sb);

    /* Growth past the initial capacity. */
    lians_sb big;
    lians_sb_init(&big);
    for (int i = 0; i < 1000; i++) {
        lians_sb_append(&big, "x");
    }
    CHECK(big.len == 1000 && strlen(big.data) == 1000, "sb grows correctly");
    lians_sb_free(&big);

    if (failures == 0) {
        printf("OK: all JSON helper tests passed\n");
        return 0;
    }
    printf("%d failure(s)\n", failures);
    return 1;
}
