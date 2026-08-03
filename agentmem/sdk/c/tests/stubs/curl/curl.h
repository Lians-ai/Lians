/*
 * Compile-only libcurl surface for offline -fsyntax-only checks. Production
 * builds must use the real libcurl headers discovered by CMake.
 */
#ifndef LIANS_TEST_STUB_CURL_H
#define LIANS_TEST_STUB_CURL_H

#include <stddef.h>

#define LIBCURL_VERSION_NUM 0x080000

typedef struct CURL CURL;
typedef int CURLcode;
typedef int CURLoption;
typedef int CURLINFO;

struct curl_slist {
    char *data;
    struct curl_slist *next;
};

#define CURL_GLOBAL_DEFAULT 0L
#define CURLPROTO_HTTP 1L
#define CURLPROTO_HTTPS 2L

#define CURLE_OK 0
#define CURLE_COULDNT_RESOLVE_HOST 6
#define CURLE_COULDNT_CONNECT 7
#define CURLE_PARTIAL_FILE 18
#define CURLE_WRITE_ERROR 23
#define CURLE_OPERATION_TIMEDOUT 28
#define CURLE_GOT_NOTHING 52
#define CURLE_SEND_ERROR 55
#define CURLE_RECV_ERROR 56

#define CURLOPT_URL 10002
#define CURLOPT_CUSTOMREQUEST 10036
#define CURLOPT_HTTPHEADER 10023
#define CURLOPT_WRITEFUNCTION 20011
#define CURLOPT_WRITEDATA 10001
#define CURLOPT_NOSIGNAL 99
#define CURLOPT_FOLLOWLOCATION 52
#define CURLOPT_SSL_VERIFYPEER 64
#define CURLOPT_SSL_VERIFYHOST 81
#define CURLOPT_USERAGENT 10018
#define CURLOPT_PROTOCOLS_STR 10318
#define CURLOPT_PROTOCOLS 181
#define CURLOPT_POSTFIELDS 10015
#define CURLOPT_POSTFIELDSIZE 60
#define CURLOPT_TIMEOUT_MS 155
#define CURLOPT_CONNECTTIMEOUT_MS 156

#define CURLINFO_RESPONSE_CODE 0x200002
#define CURLINFO_TOTAL_TIME 0x300003

CURLcode curl_global_init(long flags);
void curl_global_cleanup(void);
CURL *curl_easy_init(void);
void curl_easy_cleanup(CURL *handle);
CURLcode curl_easy_setopt(CURL *handle, CURLoption option, ...);
CURLcode curl_easy_perform(CURL *handle);
CURLcode curl_easy_getinfo(CURL *handle, CURLINFO info, ...);
const char *curl_easy_strerror(CURLcode code);
struct curl_slist *curl_slist_append(struct curl_slist *list, const char *value);
void curl_slist_free_all(struct curl_slist *list);

#endif
