#!/usr/bin/env python3
"""Make FAQPage JSON-LD agree with the visible page.

Google requires the question and answer in FAQPage markup to be visible on the
page. Where they disagree the markup is at best ignored and at worst treated as
misleading, so the rich result is lost.

This does NOT invent content. It only ever rewrites a schema question to text
that already exists on the page, and only when the match is confident. Anything
it cannot match confidently is reported for a human, never silently deleted.

  --root DIR   repo root (default ~/crestbrick-consult)
  --apply      write changes (default is a dry run)
  --min RATIO  similarity floor for an automatic rewrite (default 0.82)
"""
import argparse, difflib, html, json, os, re, sys, collections

LD = re.compile(r'(<script[^>]+ld\+json[^>]*>)(.*?)(</script>)', re.S | re.I)
TAGS = re.compile(r"<[^>]+>")
# Elements that plausibly hold a visible question.
CAND = re.compile(
    r"<(summary|h2|h3|h4|h5|dt|strong|b)\b[^>]*>(.*?)</\1>", re.S | re.I)


def norm(s):
    s = html.unescape(TAGS.sub(" ", s))
    s = s.replace("’", "'").replace("‘", "'")
    s = s.replace("“", '"').replace("”", '"')
    s = re.sub(r"\s+", " ", s).strip()
    # Accordion toggles ("+", "−", "▾") are UI affordances, not part of the
    # question. Copying them into schema would publish widget chrome to Google.
    return re.sub(r"[\s+\-−–—▸▾▼›»]+$", "", s).strip()


def key(s):
    """Comparison form: lowercase, punctuation flattened, spacing collapsed.

    Collapsing AFTER stripping punctuation is essential. "leasehold -- which"
    strips to "leasehold  which" with a double space, which then fails to match
    the identical visible "leasehold which". That single omission accounted for
    most of the apparent schema mismatches on the site.
    """
    s = re.sub(r"[^a-z0-9 ]+", " ", norm(s).lower())
    return re.sub(r"\s+", " ", s).strip()


# Function words carry no domain meaning, so swapping them is safe.
STOP = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being", "do",
    "does", "did", "can", "could", "should", "would", "will", "shall", "may",
    "might", "must", "have", "has", "had", "what", "when", "where", "which",
    "who", "whom", "whose", "why", "how", "much", "many", "if", "then", "than",
    "and", "or", "but", "for", "to", "of", "in", "on", "at", "by", "with",
    "from", "about", "into", "over", "under", "you", "your", "yours", "i",
    "me", "my", "we", "our", "it", "its", "this", "that", "these", "those",
    "there", "here", "as", "so", "not", "no", "yes", "still", "get", "got",
    "actually", "really", "work", "works", "mean", "means", "explained",
}


def content_words(s):
    out = set()
    for w in key(s).split():
        if w in STOP or w.isdigit():
            continue
        out.add(w[:-1] if w.endswith("s") and len(w) > 4 else w)  # crude plural fold
    return out


def safe_rewrite(old, new):
    """True only when the rewrite is pure normalisation.

    The meaning bearing words must be identical as a set. Only case, punctuation,
    leading numbering, whitespace and function words may differ.

    A subset test is not enough. "Is a trust a better option" versus "Is a Will a
    better option" passed a subset test, because "will" reads as an auxiliary verb
    and sits in the stopword list, so the swap looked free while it silently
    replaced one legal instrument with another. Requiring equality catches it.
    """
    return content_words(old) == content_words(new)


def visible_text(src):
    body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", src,
                  flags=re.S | re.I)
    return norm(body)


def candidates(src):
    body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", src,
                  flags=re.S | re.I)
    out, seen = [], set()
    for _, inner in CAND.findall(body):
        t = norm(inner)
        if 8 <= len(t) <= 300 and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.expanduser("~/crestbrick-consult"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--min", type=float, default=0.82)
    a = ap.parse_args()
    pub = os.path.join(a.root, "public")

    stats = collections.Counter()
    rewrites, unmatched = [], []

    for dp, _, names in os.walk(pub):
        for n in sorted(names):
            if not n.endswith(".html"):
                continue
            path = os.path.join(dp, n)
            rel = os.path.relpath(path, a.root)
            src = open(path, encoding="utf-8", errors="replace").read()
            if "FAQPage" not in src:
                continue
            vis_key = key(visible_text(src))
            cands = candidates(src)
            cand_keys = [key(c) for c in cands]
            changed_file = False

            def fix_block(m):
                nonlocal changed_file
                open_t, blk, close_t = m.groups()
                try:
                    data = json.loads(blk)
                except Exception:
                    stats["block_unparseable"] += 1
                    return m.group(0)
                touched = False

                def walk(node):
                    nonlocal touched
                    if isinstance(node, list):
                        for x in node:
                            walk(x)
                        return
                    if not isinstance(node, dict):
                        return
                    if node.get("@type") == "FAQPage":
                        for qa in node.get("mainEntity", []) or []:
                            q = norm(str(qa.get("name", "")))
                            if not q:
                                continue
                            stats["questions"] += 1
                            qk = key(q)
                            if qk and qk in vis_key:
                                stats["already_matching"] += 1
                                continue
                            # 1. exact match against a visible heading
                            if qk in cand_keys:
                                new = cands[cand_keys.index(qk)]
                            else:
                                # 2. closest visible heading, if confident
                                best, ratio = None, 0.0
                                for c, ck in zip(cands, cand_keys):
                                    r = difflib.SequenceMatcher(None, qk, ck).ratio()
                                    if r > ratio:
                                        best, ratio = c, r
                                if best is None or ratio < a.min:
                                    stats["unmatched"] += 1
                                    unmatched.append((rel, q, round(ratio, 2),
                                                      best or ""))
                                    continue
                                new = best
                            if norm(new) == q:
                                continue
                            if not safe_rewrite(q, new):
                                stats["rejected_meaning_change"] += 1
                                unmatched.append((rel, q, -1.0, new))
                                continue
                            qa["name"] = new
                            touched = True
                            stats["rewritten"] += 1
                            rewrites.append((rel, q, new))
                    for v in node.values():
                        walk(v)

                walk(data)
                if not touched:
                    return m.group(0)
                changed_file = True
                return open_t + json.dumps(data, ensure_ascii=False, indent=2) + close_t

            out = LD.sub(fix_block, src)
            if changed_file and a.apply:
                open(path, "w", encoding="utf-8").write(out)
                stats["files_written"] += 1
            elif changed_file:
                stats["files_would_change"] += 1

    print(f"{'APPLIED' if a.apply else 'DRY RUN'}")
    for k, v in stats.most_common():
        print(f"  {k:<22} {v}")
    print(f"\n-- sample rewrites ({len(rewrites)}) --")
    for rel, old, new in rewrites[:12]:
        print(f"  {rel}\n     was: {old[:95]!r}\n     now: {new[:95]!r}")
    print(f"\n-- unmatched, need a human ({len(unmatched)}) --")
    for rel, q, r, best in unmatched[:15]:
        print(f"  {rel}  (best {r})\n     Q:    {q[:90]!r}\n     near: {best[:90]!r}")
    if unmatched:
        os.makedirs(os.path.join(a.root, "tmp"), exist_ok=True)
        with open(os.path.join(a.root, "tmp", "faq-schema-unmatched.json"), "w") as fh:
            json.dump([{"file": f, "question": q, "best_ratio": r,
                        "nearest_visible": b} for f, q, r, b in unmatched],
                      fh, indent=1)
        print(f"\n  full list -> tmp/faq-schema-unmatched.json")


if __name__ == "__main__":
    main()
