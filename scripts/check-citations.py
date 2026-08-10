#!/usr/bin/env python3
"""Check every cited source URL, following redirects and catching soft 404s.

A status code alone is not enough. statecourts.gov.sg returned 200 for a page
that had been moved: the old path 301s to judiciary.gov.sg and then lands on a
"page not found" screen that answers 200. Any checker that only reads the status
line calls that link healthy.

This follows the redirect chain, then judges the page it actually landed on:
the final URL, the title, and the visible body.

Usage:
  check-citations.py <dir> [out.json] [--workers N]
"""
import concurrent.futures
import html
import json
import os
import re
import subprocess
import sys

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Cited sources live in the Sources & References block.
SOURCES_RX = re.compile(
    r'Sources\s*(?:&amp;|&)\s*References(.*?)(?:</section>|</div>\s*</div>|</article>)',
    re.S | re.I)
HREF_RX = re.compile(r'href=["\'](https?://[^"\']+)["\']', re.I)
TITLE_RX = re.compile(r'<title[^>]*>(.*?)</title>', re.S | re.I)
TAG_RX = re.compile(r'<(?:script|style)[^>]*>.*?</(?:script|style)>|<[^>]+>', re.S)

# Markers that mean "we served you an error page with a 200".
SOFT_404_URL = re.compile(r'page-not-found|/404|/error|notfound|aspxerrorpath', re.I)
SPA_RX = re.compile(r'<script|id=["\']root["\']|id=["\']app["\']|__NUXT__|ng-version|<noscript', re.I)
SOFT_404_TEXT = re.compile(
    r'page not found|page cannot be found|page you (?:are|were) looking for'
    r'|no longer available|page has moved|does not exist|404 error'
    r'|sorry,? (?:the|this) page', re.I)


def fetch(url, timeout=30):
    """Return (status, final_url, title, text). Status 0 means the request failed."""
    try:
        r = subprocess.run(
            ["curl", "-sL", "--max-time", str(timeout), "-A", UA,
             "-w", "\n__META__%{http_code}\t%{url_effective}", url],
            capture_output=True, timeout=timeout + 10)
        out = r.stdout.decode("utf-8", "replace")
    except Exception:
        return 0, url, "", "", ""
    if "__META__" not in out:
        return 0, url, "", "", ""
    body, meta = out.rsplit("__META__", 1)
    parts = meta.strip().split("\t")
    status = int(parts[0]) if parts and parts[0].isdigit() else 0
    final = parts[1] if len(parts) > 1 else url
    m = TITLE_RX.search(body)
    title = html.unescape(re.sub(r"\s+", " ", m.group(1))).strip() if m else ""
    text = html.unescape(TAG_RX.sub(" ", body))
    return status, final, title, re.sub(r"\s+", " ", text).strip(), body


def classify(url, status, final, title, text, raw=""):
    """Return (verdict, detail).

    Verdicts: ok, dead, soft404, moved, clientside, unreachable.

    Thin visible text is NOT evidence of a dead link. Calendly, CPF, EDB, CEA and
    the HDB portals all serve a small shell that hydrates in the browser, so a
    "body too short" rule flags perfectly good pages — including Winfred's own
    booking link. Only the final URL and explicit error wording are trusted.
    """
    if status == 0:
        return "unreachable", "request failed or timed out"
    if status >= 400:
        return "dead", f"HTTP {status}"
    head = text[:3000]
    if SOFT_404_URL.search(final):
        return "soft404", f"landed on {final}"
    if SOFT_404_TEXT.search(title) or SOFT_404_TEXT.search(head):
        hit = (SOFT_404_TEXT.search(title) or SOFT_404_TEXT.search(head)).group(0)
        return "soft404", f'HTTP {status} but page reads "{hit}"'
    if len(head) < 200:
        if SPA_RX.search(raw):
            return "clientside", f"HTTP {status}, renders in the browser — not verifiable here"
        return "soft404", f"HTTP {status} with an almost empty body and no scripts"
    # Same page, different address: worth updating the citation but not broken.
    if final.rstrip("/") != url.rstrip("/"):
        return "moved", f"redirects to {final}"
    return "ok", f"HTTP {status}"


def collect(root):
    """Map each cited URL to the files citing it."""
    cites = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith((".", "_"))]
        for fn in filenames:
            if not fn.endswith(".html"):
                continue
            p = os.path.join(dirpath, fn)
            try:
                t = open(p, encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            for block in SOURCES_RX.findall(t):
                for u in HREF_RX.findall(block):
                    cites.setdefault(u.rstrip('/'), set()).add(p)
    return {u: sorted(f) for u, f in cites.items()}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    root = args[0] if args else "public"
    out_path = args[1] if len(args) > 1 else None
    workers = 6
    for a in sys.argv[1:]:
        if a.startswith("--workers"):
            workers = int(a.split("=", 1)[1])

    cites = collect(root)
    print(f"{len(cites)} distinct cited URLs across {root}", flush=True)

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch, u): u for u in cites}
        for i, fut in enumerate(concurrent.futures.as_completed(futs), 1):
            u = futs[fut]
            status, final, title, text, raw = fut.result()
            verdict, detail = classify(u, status, final, title, text, raw)
            results[u] = {"verdict": verdict, "detail": detail, "status": status,
                          "final": final, "title": title[:120],
                          "cited_by": cites[u]}
            if i % 25 == 0:
                print(f"  checked {i}/{len(cites)}", flush=True)

    # Anything that looked broken gets one serial retry with a longer timeout.
    # Running 12 requests at once makes data.gov.sg and others rate limit, which
    # surfaces as HTTP 500 or a timeout on links that are perfectly healthy.
    suspect = [u for u, r in results.items()
               if r["verdict"] in ("dead", "unreachable", "soft404")]
    if suspect:
        print(f"\nretrying {len(suspect)} suspect URL(s) serially...", flush=True)
        for u in suspect:
            status, final, title, text, raw = fetch(u, timeout=45)
            verdict, detail = classify(u, status, final, title, text, raw)
            if verdict != results[u]["verdict"]:
                print(f"  {results[u]['verdict']} -> {verdict}: {u}")
            results[u].update(verdict=verdict, detail=detail, status=status, final=final)

    order = ["dead", "soft404", "unreachable", "clientside", "moved", "ok"]
    counts = {v: sum(1 for r in results.values() if r["verdict"] == v) for v in order}
    print("\n== summary ==")
    for v in order:
        if counts[v]:
            print(f"  {counts[v]:4}  {v}")

    for v in ("dead", "soft404", "unreachable"):
        bad = {u: r for u, r in results.items() if r["verdict"] == v}
        if not bad:
            continue
        print(f"\n== {v.upper()} ==")
        for u, r in sorted(bad.items()):
            print(f"  {u}\n      {r['detail']}  | cited by {len(r['cited_by'])} page(s)")

    if out_path:
        json.dump(results, open(out_path, "w"), indent=2)
        print(f"\nwrote {out_path}")
    return 1 if (counts["dead"] or counts["soft404"]) else 0


if __name__ == "__main__":
    sys.exit(main())
