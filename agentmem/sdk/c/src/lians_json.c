#include "lians_json.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int lians_sb_init(lians_sb *sb) {
    sb->cap = 64;
    sb->len = 0;
    sb->data = (char *)malloc(sb->cap);
    if (!sb->data) {
        sb->cap = 0;
        return -1;
    }
    sb->data[0] = '\0';
    return 0;
}

static int lians_sb_reserve(lians_sb *sb, size_t extra) {
    size_t need = sb->len + extra + 1; /* +1 for NUL */
    if (need <= sb->cap) {
        return 0;
    }
    size_t cap = sb->cap ? sb->cap : 64;
    while (cap < need) {
        cap *= 2;
    }
    char *p = (char *)realloc(sb->data, cap);
    if (!p) {
        return -1;
    }
    sb->data = p;
    sb->cap = cap;
    return 0;
}

int lians_sb_append_n(lians_sb *sb, const char *s, size_t n) {
    if (lians_sb_reserve(sb, n) != 0) {
        return -1;
    }
    memcpy(sb->data + sb->len, s, n);
    sb->len += n;
    sb->data[sb->len] = '\0';
    return 0;
}

int lians_sb_append(lians_sb *sb, const char *s) {
    return lians_sb_append_n(sb, s, strlen(s));
}

void lians_sb_free(lians_sb *sb) {
    if (sb && sb->data) {
        free(sb->data);
        sb->data = NULL;
        sb->cap = 0;
        sb->len = 0;
    }
}

/* Append the escaped form of a single char to the buffer. */
static int append_escaped_char(lians_sb *sb, unsigned char c) {
    char buf[8];
    switch (c) {
        case '"':  return lians_sb_append_n(sb, "\\\"", 2);
        case '\\': return lians_sb_append_n(sb, "\\\\", 2);
        case '\b': return lians_sb_append_n(sb, "\\b", 2);
        case '\f': return lians_sb_append_n(sb, "\\f", 2);
        case '\n': return lians_sb_append_n(sb, "\\n", 2);
        case '\r': return lians_sb_append_n(sb, "\\r", 2);
        case '\t': return lians_sb_append_n(sb, "\\t", 2);
        default:
            if (c < 0x20) {
                snprintf(buf, sizeof(buf), "\\u%04x", c);
                return lians_sb_append_n(sb, buf, 6);
            }
            buf[0] = (char)c;
            return lians_sb_append_n(sb, buf, 1);
    }
}

int lians_sb_append_json_string(lians_sb *sb, const char *s) {
    if (lians_sb_append_n(sb, "\"", 1) != 0) {
        return -1;
    }
    if (s) {
        for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
            if (append_escaped_char(sb, *p) != 0) {
                return -1;
            }
        }
    }
    return lians_sb_append_n(sb, "\"", 1);
}

char *lians_json_escape(const char *s) {
    lians_sb sb;
    if (lians_sb_init(&sb) != 0) {
        return NULL;
    }
    if (s) {
        for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
            if (append_escaped_char(&sb, *p) != 0) {
                lians_sb_free(&sb);
                return NULL;
            }
        }
    }
    return sb.data; /* caller frees */
}
