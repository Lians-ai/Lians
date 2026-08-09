import concurrent.futures
import html
import json
import re
import ssl
import sys
import urllib.parse
import urllib.request


EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
HREF_RE = re.compile(r"href=[\"']([^\"']+)[\"']", re.I)
RELEVANT_RE = re.compile(
    r"contact|partner|alliance|about|team|leadership|company|invest|pitch|founder|venture|get-in-touch",
    re.I,
)
BAD_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".zip", ".mp4", ".mp3")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; LiansPublicContactResearch/1.0; +https://lians.ai)"
}


def fetch(url, timeout=5):
    request = urllib.request.Request(url, headers=HEADERS)
    context = ssl.create_default_context()
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        content_type = response.headers.get("Content-Type", "")
        if "text" not in content_type and "html" not in content_type and "json" not in content_type:
            return "", response.geturl()
        raw = response.read(2_000_000)
        charset = response.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace"), response.geturl()


def normalized_host(url):
    host = urllib.parse.urlsplit(url).hostname or ""
    return host.lower().removeprefix("www.")


def same_company_host(host, domain):
    host = host.removeprefix("www.")
    domain = domain.removeprefix("www.")
    return host == domain or host.endswith("." + domain)


def extract_emails(text):
    decoded = html.unescape(text).replace("[at]", "@").replace("(at)", "@")
    emails = set()
    for email in EMAIL_RE.findall(decoded):
        email = email.lower().strip(".,;:()[]{}<>\"'")
        if email.endswith((".png", ".jpg", ".jpeg", ".svg", ".webp")):
            continue
        if email.split("@")[-1] in {"example.com", "email.com", "domain.com"}:
            continue
        emails.add(email)
    return emails


def crawl_one(item):
    domain = item["domain"].lower().removeprefix("www.")
    seeds = [f"https://{domain}/", f"https://www.{domain}/"]
    fetched = {}
    home_text = ""
    home_url = ""
    for seed in seeds:
        try:
            home_text, home_url = fetch(seed)
            if home_text:
                fetched[home_url] = home_text
                break
        except Exception:
            pass

    links = []
    if home_text:
        for href in HREF_RE.findall(home_text):
            absolute = urllib.parse.urljoin(home_url, html.unescape(href))
            parsed = urllib.parse.urlsplit(absolute)
            if parsed.scheme not in {"http", "https"}:
                continue
            if not same_company_host((parsed.hostname or "").lower(), domain):
                continue
            clean = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))
            if clean.lower().endswith(BAD_EXTENSIONS):
                continue
            if RELEVANT_RE.search(clean):
                links.append(clean)

    common = [
        "/contact", "/contact-us", "/about", "/about-us", "/team", "/leadership",
        "/partners", "/partnerships", "/company", "/invest", "/pitch", "/imprint",
    ]
    base = home_url or f"https://{domain}/"
    for path in common:
        links.append(urllib.parse.urljoin(base, path))

    deduped = []
    seen = set(fetched)
    for link in links:
        if link not in seen:
            seen.add(link)
            deduped.append(link)
        if len(deduped) >= 4:
            break

    for link in deduped:
        try:
            text, final_url = fetch(link)
            if text:
                fetched[final_url] = text
        except Exception:
            pass

    evidence = {}
    for url, text in fetched.items():
        for email in extract_emails(text):
            evidence.setdefault(email, []).append(url)

    return {
        "account": item["account"],
        "domain": domain,
        "type": item.get("type"),
        "role": item.get("role"),
        "emails": [
            {"email": email, "source_urls": sorted(set(urls))[:4]}
            for email, urls in sorted(evidence.items())
        ],
        "pages_fetched": len(fetched),
    }


def main():
    items = json.loads(sys.stdin.read())
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=24) as executor:
        futures = {executor.submit(crawl_one, item): item for item in items}
        for future in concurrent.futures.as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:
                item = futures[future]
                results.append({"account": item["account"], "domain": item["domain"], "error": str(exc)})
    results.sort(key=lambda row: next(i for i, item in enumerate(items) if item["account"] == row["account"]))
    json.dump(results, sys.stdout, ensure_ascii=True)


if __name__ == "__main__":
    main()
