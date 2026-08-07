#!/usr/bin/env python3
"""Render FAQPage JSON-LD that exists only in markup as visible page content.

Google requires FAQ rich result markup to be visible to the reader. Many pages
here carry a large FAQPage block whose questions and answers appear nowhere on
the page, so the markup is ignored and a body of already written Q&A is invisible
to both readers and search.

This publishes that existing content. It writes NOTHING new: every question and
answer rendered is copied verbatim from the page's own schema.

  --root DIR   repo root (default ~/crestbrick-consult)
  --apply      write changes (default is a dry run)
  --limit N    only process the first N files (for a supervised trial)
"""
import argparse, collections, html as H, json, os, re

LD = re.compile(r'<script[^>]+ld\+json[^>]*>(.*?)</script>', re.S | re.I)
TAGS = re.compile(r"<[^>]+>")
HEADING = '<h2 id="frequently-asked-questions">Frequently asked questions</h2>'


def norm(s):
    return re.sub(r"\s+", " ", H.unescape(TAGS.sub(" ", s))).strip()


def key(s):
    s = re.sub(r"[^a-z0-9 ]+", " ", norm(s).lower())
    return re.sub(r"\s+", " ", s).strip()


def visible_key(src):
    body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", src,
                  flags=re.S | re.I)
    return key(body)


def faq_entries(src):
    """Every (question, answer) pair in this page's FAQPage blocks."""
    out = []
    for blk in LD.findall(src):
        try:
            data = json.loads(blk)
        except Exception:
            continue

        def walk(n):
            if isinstance(n, list):
                for x in n:
                    walk(x)
                return
            if not isinstance(n, dict):
                return
            if n.get("@type") == "FAQPage":
                for qa in n.get("mainEntity", []) or []:
                    q = norm(str(qa.get("name", "")))
                    a = norm(str((qa.get("acceptedAnswer") or {}).get("text", "")))
                    if q and a:
                        out.append((q, a))
            for v in n.values():
                walk(v)
        walk(data)
    return out


def insertion_point(src):
    """Where the FAQ section should go: end of the article body.

    Ordered by preference. Returns an index, or None when the page has no
    structure we recognise, in which case we skip it rather than guess.
    """
    for pat in (r"</article>", r"</main>", r"<footer\b"):
        m = None
        for m in re.finditer(pat, src, re.I):
            pass                      # take the LAST occurrence
        if m:
            return m.start()
    return None


def render(pairs, has_section=False):
    # A page that already carries the FAQ heading must not get a second element
    # with the same id: duplicate ids are invalid HTML and break anchor links.
    rows = ['<h2 id="more-questions">More questions</h2>'] if has_section else [HEADING]
    for q, a in pairs:
        rows.append(f"  <h3>{H.escape(q, quote=False)}</h3>")
        rows.append(f"  <p>{H.escape(a, quote=False)}</p>")
    return "\n" + "\n".join(rows) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.expanduser("~/crestbrick-consult"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    pub = os.path.join(a.root, "public")

    stats = collections.Counter()
    touched, skipped = [], []

    files = []
    for dp, _, names in os.walk(pub):
        for n in sorted(names):
            if n.endswith(".html"):
                files.append(os.path.join(dp, n))

    for path in sorted(files):
        rel = os.path.relpath(path, a.root)
        src = open(path, encoding="utf-8", errors="replace").read()
        if "FAQPage" not in src:
            continue
        pairs = faq_entries(src)
        if not pairs:
            continue
        stats["pages_with_faq_schema"] += 1

        vk = visible_key(src)
        missing = [(q, an) for q, an in pairs if key(q) not in vk]
        if not missing:
            stats["already_fully_visible"] += 1
            continue

        # Only render answers that are ALSO absent. If the answer text is already
        # in the prose, the page covers it and a duplicate block would read badly.
        missing = [(q, an) for q, an in missing if key(an)[:80] not in vk]
        if not missing:
            stats["answers_already_in_prose"] += 1
            continue

        at = insertion_point(src)
        if at is None:
            stats["no_insertion_point"] += 1
            skipped.append((rel, "no </article>, </main> or <footer>"))
            continue

        has_section = 'id="frequently-asked-questions"' in src
        if has_section:
            stats["appended_to_existing_section"] += 1
        else:
            stats["new_section"] += 1

        block = render(missing, has_section)
        out = src[:at] + block + src[at:]

        stats["questions_published"] += len(missing)
        touched.append((rel, len(missing)))
        if a.apply:
            open(path, "w", encoding="utf-8").write(out)
            stats["files_written"] += 1
        if a.limit and len(touched) >= a.limit:
            break

    print(f"{'APPLIED' if a.apply else 'DRY RUN'}")
    for k, v in stats.most_common():
        print(f"  {k:<28} {v}")
    print(f"\n-- pages gaining visible FAQ ({len(touched)}) --")
    for rel, n in sorted(touched, key=lambda x: -x[1])[:15]:
        print(f"  {n:>3} questions  {rel}")
    if skipped:
        print(f"\n-- skipped ({len(skipped)}) --")
        for rel, why in skipped[:10]:
            print(f"  {rel}: {why}")


if __name__ == "__main__":
    main()
