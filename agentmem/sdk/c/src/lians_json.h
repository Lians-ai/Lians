/*
 * Internal helpers for the Lians C SDK: a growable string buffer and JSON string
 * escaping. These are pure (no I/O), so they are unit-tested without a server.
 */
#ifndef LIANS_JSON_H
#define LIANS_JSON_H

#include <stddef.h>

/* Growable string buffer. */
typedef struct {
    char  *data;
    size_t len;
    size_t cap;
} lians_sb;

/* Initialize an empty buffer. Returns 0 on success, -1 on allocation failure. */
int  lians_sb_init(lians_sb *sb);

/* Append a NUL-terminated string. Returns 0 on success, -1 on allocation failure. */
int  lians_sb_append(lians_sb *sb, const char *s);

/* Append `n` bytes. Returns 0 on success, -1 on allocation failure. */
int  lians_sb_append_n(lians_sb *sb, const char *s, size_t n);

/*
 * Append `s` as a quoted, escaped JSON string (including the surrounding quotes).
 * Returns 0 on success, -1 on allocation failure.
 */
int  lians_sb_append_json_string(lians_sb *sb, const char *s);

/* Free the buffer's storage. Safe to call on a zero-initialized buffer. */
void lians_sb_free(lians_sb *sb);

/*
 * Return a freshly malloc'd JSON-escaped copy of `s` WITHOUT surrounding quotes.
 * Caller frees. Returns NULL on allocation failure.
 */
char *lians_json_escape(const char *s);

#endif /* LIANS_JSON_H */
