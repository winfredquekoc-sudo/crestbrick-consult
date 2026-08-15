#!/usr/bin/env python3
"""Scan and repair drift between visible FAQ content and FAQPage JSON-LD.

Finds visible questions missing from schema, schema entries no longer on the
page, and answers whose wording differs, then with --fix rebuilds each
FAQPage's mainEntity from the visible page (the source of truth), verbatim
and in visible order.

Asymmetric by design. Additions are evidence-driven from structurally
detected visible pairs (faq-q / h3 / details blocks scoped to a "Frequently
asked questions" or "more questions" heading). Removals need a stricter bar:
a schema entry no detected pair matches is deleted only if its question text
is not found ANYWHERE in the page's visible text -- a plain substring test.
Real FAQ questions live outside any FAQ section too: an /answers/ page's own
h1 IS a question, answered by the "Quick answer" lead; some articles carry
"common follow up questions" h3 blocks above the main FAQ section; some use a
question as an h2 heading. None match the structured patterns, so "unmatched"
must not mean "gone".

  --root DIR      repo root (default: parent of this script's scripts/ dir)
  --fix           write repairs (default is report only)
  --json PATH     also write a machine readable dump of the report
  --min-ratio F   similarity floor for pairing a near-miss question drift (default 0.6)
  --strict        also fail (exit 1) when report-only categories are non-empty
"""
import argparse, collections, difflib, html as H, json, os, re, sys
from pathlib import Path

TAG = re.compile(r"<[^>]+>")
SCRIPT_STYLE = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.S | re.I)
NOSCRIPT_COMMENT = re.compile(r"<noscript\b[^>]*>.*?</noscript>|<!--.*?-->", re.S | re.I)
LD_BLOCK = re.compile(r'(<script[^>]*type=["\']application/ld\+json["\'][^>]*>)(.*?)(</script>)', re.S | re.I)
H2_TAG = re.compile(r"<h2\b([^>]*)>(.*?)</h2>", re.S | re.I)
H1_TAG = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.S | re.I)
ARTICLE_TAG = re.compile(r"<article\b[^>]*>(.*?)</article>", re.S | re.I)
FAQQ_PAIR = re.compile(r'<p\s+class="faq-q">(.*?)</p>\s*<p(?![^>]*class="faq-q")(?:\s[^>]*)?>(.*?)</p>', re.S | re.I)
H3_PAIR = re.compile(r"<h3\b(?:\s[^>]*)?>(.*?)</h3>\s*<p(?:\s[^>]*)?>(.*?)</p>", re.S | re.I)
DETAILS_PAIR = re.compile(r"<details\b(?:\s[^>]*)?>\s*<summary\b(?:\s[^>]*)?>(.*?)</summary>(.*?)</details>", re.S | re.I)
DETAILS_P = re.compile(r"<p(?:\s[^>]*)?>(.*?)</p>", re.S | re.I)

DEFAULT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Typography fold for the keep-rule containment test ONLY (never drift
# comparison): a visible/schema twin differing by nothing but a curly quote or
# dash must not read as "not found" and get deleted -- one-directional, can
# only turn a REMOVE into a KEEP, never the reverse.
_TYPOGRAPHY_FOLD = str.maketrans({"‘": "'", "’": "'", "“": '"',
                                   "”": '"', "–": "-", "—": "-", " ": " "})


def _collapse(s):
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()

def norm_visible(s):
    # Decode entities, drop tags, collapse whitespace -- deliberately minimal,
    # no case/quote folding: a curly vs straight apostrophe is drift, not noise.
    return _collapse(H.unescape(TAG.sub("", s)))

def norm_schema(s):
    return _collapse(str(s))  # json.loads already resolved \uXXXX and JSON escapes


def visible_text_corpus(src):
    # Whole-page text for the removal-safety containment test: deliberately
    # unscoped, since h1s / pre-FAQ h3 blocks / question-form h2s live outside
    # the FAQ section. Callers compare case-insensitively -- "any evidence it
    # survives", not byte parity.
    s = SCRIPT_STYLE.sub(" ", src)
    s = NOSCRIPT_COMMENT.sub(" ", s)
    s = H.unescape(TAG.sub(" ", s)).replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def excerpt(a, b, width=40):
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    def cut(s):
        start = max(0, i - width)
        end = min(len(s), i + width)
        return ("..." if start > 0 else "") + s[start:end] + ("..." if end < len(s) else "")
    return {"visible": cut(a), "schema": cut(b), "diverges_at": i}


def is_faq_heading(attrs, text):
    if re.search(r'id=["\']frequently-asked-questions["\']', attrs, re.I):
        return True
    if re.search(r'id=["\']more-questions["\']', attrs, re.I):
        return True
    plain = TAG.sub("", text)
    return bool(re.fullmatch(r"\s*Frequently [Aa]sked [Qq]uestions?\s*", plain))

def faq_spans(src):
    """Byte ranges (in `src`) following each FAQ-relevant h2, up to the next h2."""
    h2s = list(H2_TAG.finditer(src))
    spans = []
    for i, m in enumerate(h2s):
        if not is_faq_heading(m.group(1), m.group(2)):
            continue
        start = m.end()
        end = h2s[i + 1].start() if i + 1 < len(h2s) else len(src)
        spans.append((start, end))
    return spans


def page_is_faq_only(src):
    """A few pages ARE the FAQ (h1 ends "... FAQ"), questions under thematic
    h2 sub-sections instead of one "Frequently Asked Questions" heading."""
    m = H1_TAG.search(src)
    if not m:
        return False
    text = H.unescape(TAG.sub("", m.group(1))).strip()
    return bool(re.search(r"\bFAQs?\s*$", text, re.I))


def extract_visible_pairs(src):
    """[(pos, question_raw_html, answer_raw_html), ...] in document order.

    Each answer is the single <p> immediately after its question marker
    (verified: every apparent multi-paragraph answer was really the next
    paragraph bleeding in from a CTA/disclaimer/"Related:" block; none matched
    schema when joined). Details/summary may join multiple <p>s since
    </details> is a hard boundary. A candidate is dropped unless its question
    ends with "?", so a non-question heading followed by a bare <p> can never
    be mistaken for one.
    """
    vis_src = SCRIPT_STYLE.sub(" ", src)
    spans = faq_spans(vis_src)
    if not spans and page_is_faq_only(vis_src):
        am = ARTICLE_TAG.search(vis_src)
        spans = [(am.start(1), am.end(1))] if am else [(0, len(vis_src))]
    pairs = []
    for a, b in spans:
        section = vis_src[a:b]
        for m in FAQQ_PAIR.finditer(section):
            pairs.append((a + m.start(), m.group(1), m.group(2)))
        for m in H3_PAIR.finditer(section):
            pairs.append((a + m.start(), m.group(1), m.group(2)))
        for m in DETAILS_PAIR.finditer(section):
            ans_raw = " ".join(DETAILS_P.findall(m.group(2)))
            pairs.append((a + m.start(), m.group(1), ans_raw))
    pairs = [p for p in pairs if norm_visible(p[1]).endswith("?")]
    pairs.sort(key=lambda t: t[0])
    return pairs


def find_faqpage_node(data):
    if not isinstance(data, dict):
        return None
    if data.get("@type") == "FAQPage":
        return data
    for n in data.get("@graph") or []:
        if isinstance(n, dict) and n.get("@type") == "FAQPage":
            return n
    return None

def extract_schema(src):
    """(pairs, raw_entries, block_info, bad_json_errors, faqpage_block_count).
    raw_entries holds the exact original Question dict objects (same order as
    pairs) so a "kept" entry carries into a rebuild byte-identical, never
    reconstructed from just its name/text. block_info: that script element."""
    pairs, raw_entries, block_info, bad = [], [], None, []
    faqpage_blocks = 0
    for m in LD_BLOCK.finditer(src):
        open_tag, content, close_tag = m.group(1), m.group(2), m.group(3)
        if "FAQPage" not in content:
            continue
        try:
            data = json.loads(content)
        except Exception as e:
            bad.append(str(e))
            continue
        node = find_faqpage_node(data)
        if node is None:
            continue
        faqpage_blocks += 1
        pairs, raw_entries = [], []
        for qa in node.get("mainEntity") or []:
            if not isinstance(qa, dict):
                continue
            q = qa.get("name", "")
            a = (qa.get("acceptedAnswer") or {}).get("text", "")
            pairs.append((q, a))
            raw_entries.append(qa)
        block_info = {"start": m.start(), "end": m.end(), "open_tag": open_tag,
                       "content": content, "close_tag": close_tag, "data": data}
    return pairs, raw_entries, block_info, bad, faqpage_blocks


def compare(visible_pairs, schema_pairs, corpus_lower, min_ratio):
    """Returns (defects, kept). defects: actionable, rewritten by --fix. kept:
    schema-only entries whose question survives the containment test --
    {"question", "tag": "KEPT"|"KEPT_UNVERIFIED", "orig_index"} -- never
    rewritten, carried into the rebuild byte-identical."""
    vis = [(norm_visible(q), norm_visible(a)) for _, q, a in visible_pairs]
    sch = [(norm_schema(q), norm_schema(a)) for q, a in schema_pairs]
    vis_used = [False] * len(vis)
    sch_used = [False] * len(sch)
    defects = []

    for i, (vq, va) in enumerate(vis):
        for j, (sq, sa) in enumerate(sch):
            if sch_used[j] or vq != sq:
                continue
            vis_used[i] = sch_used[j] = True
            if va != sa:
                defects.append({"type": "ANSWER_DRIFT", "question": vq, "excerpt": excerpt(va, sa)})
            break

    left_v = [i for i in range(len(vis)) if not vis_used[i]]
    left_s = [j for j in range(len(sch)) if not sch_used[j]]
    cands = []
    for i in left_v:
        for j in left_s:
            r = difflib.SequenceMatcher(None, vis[i][0], sch[j][0]).ratio()
            if r >= min_ratio:
                cands.append((r, i, j))
    cands.sort(key=lambda t: -t[0])
    for r, i, j in cands:
        if vis_used[i] or sch_used[j]:
            continue
        vis_used[i] = sch_used[j] = True
        vq, va = vis[i]
        sq, sa = sch[j]
        d = {"type": "QUESTION_DRIFT", "visible_question": vq, "schema_question": sq, "ratio": round(r, 3)}
        if va != sa:
            d["answer_also_differs"] = True
            d["excerpt"] = excerpt(va, sa)
        defects.append(d)

    for i in range(len(vis)):
        if not vis_used[i]:
            defects.append({"type": "MISSING_FROM_SCHEMA", "question": vis[i][0]})

    kept = []
    for j in range(len(sch)):
        if sch_used[j]:
            continue
        sq, sa = sch[j]
        if sq.lower().translate(_TYPOGRAPHY_FOLD) not in corpus_lower:
            defects.append({"type": "SCHEMA_ONLY", "question": sq})
        else:
            tag = "KEPT" if sa.lower().translate(_TYPOGRAPHY_FOLD) in corpus_lower else "KEPT_UNVERIFIED"
            kept.append({"question": sq, "tag": tag, "orig_index": j})
    return defects, kept


def scan_file(path):
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        src = f.read()
    visible_pairs = extract_visible_pairs(src)
    schema_pairs, raw_entries, block_info, bad, faqpage_blocks = extract_schema(src)
    corpus_lower = visible_text_corpus(src).lower().translate(_TYPOGRAPHY_FOLD)
    return src, visible_pairs, schema_pairs, raw_entries, block_info, bad, faqpage_blocks, corpus_lower


def classify(visible_pairs, schema_pairs, block_info, bad, faqpage_blocks, corpus_lower, min_ratio):
    if bad:
        return "BAD_JSON", [{"type": "BAD_JSON", "error": e} for e in bad], []
    if faqpage_blocks > 1:
        return "SUSPECT_MULTI", [], []
    if not visible_pairs and schema_pairs:
        return "SUSPECT", [], []
    if visible_pairs and block_info is None:
        return "NO_SCHEMA", [], []
    if not visible_pairs and not schema_pairs:
        return "CLEAN", [], []
    defects, kept = compare(visible_pairs, schema_pairs, corpus_lower, min_ratio)
    state = "DEFECTS" if defects else ("KEPT_ONLY" if kept else "CLEAN")
    return state, defects, kept


def rebuild_block(block_info, entries):
    """Render the FAQPage script's new inner content (entries: fresh dicts for
    visible-derived pairs, the exact original object for a kept entry),
    matching the original's indentation/ascii-escaping style. None if no-op."""
    content = block_info["content"]
    ensure_ascii = bool(re.search(r"\\u[0-9a-fA-F]{4}", content))

    full_expand = bool(re.search(r'"acceptedAnswer"\s*:\s*\{\s*\n', content))
    if full_expand:
        node = find_faqpage_node(block_info["data"])
        if node is None:
            return None
        node["mainEntity"] = entries
        new_json = json.dumps(block_info["data"], indent=2, ensure_ascii=ensure_ascii)
        lead = content[:len(content) - len(content.lstrip())]
        trail = content[len(content.rstrip()):]
        new_content = lead + new_json + trail
    else:
        km = re.search(r'"mainEntity"\s*:\s*', content)
        if not km:
            return None
        value_start = km.end()
        try:
            _, value_end = json.JSONDecoder().raw_decode(content, value_start)
        except Exception:
            return None
        old_array = content[value_start:value_end]
        pretty = "\n" in old_array
        cm = re.search(r'"@type"\s*:(\s*)"Question"', old_array)
        spaced = bool(cm and cm.group(1) == " ")
        sep = (", ", ": ") if spaced else (",", ":")
        item_texts = [json.dumps(e, ensure_ascii=ensure_ascii, separators=sep) for e in entries]
        if pretty:
            im = re.match(r"\[\s*\n([ \t]*)", old_array)
            indent_unit = im.group(1) if im else "    "
            cm2 = re.search(r"\n([ \t]*)\]\s*\Z", old_array)
            closing_indent = cm2.group(1) if cm2 else ""
            inner = "\n" + indent_unit + (",\n" + indent_unit).join(item_texts) + "\n" + closing_indent
        else:
            inner = (", " if spaced else ",").join(item_texts)
        new_array = "[" + inner + "]"
        new_content = content[:value_start] + new_array + content[value_end:]

    return None if new_content == content else new_content


def build_entries(visible_pairs, kept, raw_entries):
    entries = [{"@type": "Question", "name": norm_visible(q),
                "acceptedAnswer": {"@type": "Answer", "text": norm_visible(a)}}
               for _, q, a in visible_pairs]
    for k in sorted(kept, key=lambda k: k["orig_index"]):
        entries.append(raw_entries[k["orig_index"]])  # byte-identical, never rewritten
    return entries


def fix_file(path, src, block_info, visible_pairs, kept, raw_entries):
    entries = build_entries(visible_pairs, kept, raw_entries)
    new_content = rebuild_block(block_info, entries)
    if new_content is None:
        return None
    json.loads(new_content)  # must still be valid JSON before it touches disk
    start, end = block_info["start"], block_info["end"]
    new_block = block_info["open_tag"] + new_content + block_info["close_tag"]
    new_src = src[:start] + new_block + src[end:]
    assert src[:start] + src[end:] == new_src[:start] + new_src[start + len(new_block):], \
        "bytes outside the replaced <script> element changed"
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(new_src)
    return new_src


def fix_counts(defects):
    c = collections.Counter(d["type"] for d in defects)
    return c["MISSING_FROM_SCHEMA"], c["ANSWER_DRIFT"] + c["QUESTION_DRIFT"], c["SCHEMA_ONLY"]

def format_defect(d):
    t = d["type"]
    if t == "ANSWER_DRIFT":
        e = d["excerpt"]
        return f"  [ANSWER_DRIFT] {d['question']}\n     visible: {e['visible']!r}\n     schema:  {e['schema']!r}"
    if t == "QUESTION_DRIFT":
        s = (f"  [QUESTION_DRIFT] (ratio {d['ratio']})\n"
             f"     visible: {d['visible_question']!r}\n     schema:  {d['schema_question']!r}")
        if d.get("answer_also_differs"):
            e = d["excerpt"]
            s += f"\n     answer also differs -- visible: {e['visible']!r}\n" \
                 f"                             schema:  {e['schema']!r}"
        return s
    if t == "MISSING_FROM_SCHEMA":
        return f"  [MISSING_FROM_SCHEMA] {d['question']}"
    if t == "SCHEMA_ONLY":
        return f"  [SCHEMA_ONLY] {d['question']}"
    if t == "BAD_JSON":
        return f"  [BAD_JSON] {d['error']}"
    return f"  [{t}] {d}"


def print_report(results, out=sys.stdout):
    defect_counts = collections.Counter()
    files_with_defects = 0
    no_schema, suspect, suspect_multi = [], [], []
    kept_total = kept_unverified = 0
    kept_unverified_list, removals = [], []

    for rel in sorted(results):
        state, defects, kept = results[rel]
        if state == "NO_SCHEMA":
            no_schema.append(rel)
            continue
        if state == "SUSPECT":
            suspect.append(rel)
            continue
        if state == "SUSPECT_MULTI":
            suspect_multi.append(rel)
            continue
        if not defects and not kept:
            continue
        if defects:
            files_with_defects += 1
        print(rel, file=out)
        for d in defects:
            defect_counts[d["type"]] += 1
            print(format_defect(d), file=out)
            if d["type"] == "SCHEMA_ONLY":
                removals.append({"file": rel, "question": d["question"]})
        for k in kept:
            kept_total += 1
            print(f"  [{k['tag']}] {k['question']}", file=out)
            if k["tag"] == "KEPT_UNVERIFIED":
                kept_unverified += 1
                kept_unverified_list.append({"file": rel, "question": k["question"]})
        print(file=out)

    for label, bucket in (("NO-SCHEMA] visible FAQ pairs but no FAQPage block at all", no_schema),
                           ("SUSPECT] FAQPage block but zero visible pairs detected -- inspect by hand", suspect),
                           ("SUSPECT-MULTI] more than one FAQPage node in this file -- never auto-fixed", suspect_multi)):
        if bucket:
            print(f"[{label} ({len(bucket)})", file=out)
            for rel in bucket:
                print(" ", rel, file=out)
            print(file=out)

    print("==== summary ====", file=out)
    print(f"scanned {len(results)} files; {files_with_defects} with defects", file=out)
    for k, v in defect_counts.most_common():
        print(f"  {v:5d}  {k}", file=out)
    print(f"  {kept_total:5d}  KEPT entries (of which {kept_unverified} KEPT_UNVERIFIED)", file=out)
    print(f"  {len(no_schema):5d}  NO-SCHEMA (report-only)", file=out)
    print(f"  {len(suspect):5d}  SUSPECT (report-only)", file=out)
    print(f"  {len(suspect_multi):5d}  SUSPECT-MULTI (report-only)", file=out)
    return {"files_with_defects": files_with_defects, "defect_counts": defect_counts,
            "no_schema": no_schema, "suspect": suspect, "suspect_multi": suspect_multi,
            "kept_total": kept_total, "kept_unverified": kept_unverified,
            "kept_unverified_list": kept_unverified_list, "removals": removals}


def to_jsonable(results, root):
    out = {}
    for rel, (state, defects, kept) in results.items():
        if state == "CLEAN":
            continue
        out[rel] = {"state": state, "defects": defects, "kept": kept}
    return {"root": root, "files_scanned": len(results), "files": out}

def run_scan(files, root, min_ratio):
    results, raw = {}, {}
    for path in files:
        rel = os.path.relpath(path, root)
        src, vis, sch, raw_entries, blk, bad, nblocks, corpus_lower = scan_file(path)
        state, defects, kept = classify(vis, sch, blk, bad, nblocks, corpus_lower, min_ratio)
        results[rel] = (state, defects, kept)
        raw[rel] = (path, src, vis, blk, raw_entries)
    return results, raw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--fix", action="store_true")
    ap.add_argument("--json", dest="json_path", default=None)
    ap.add_argument("--min-ratio", type=float, default=0.6)
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()

    files = [str(f) for f in sorted(Path(a.root, "public/insights").glob("*.html")) +
             sorted(Path(a.root, "public/answers").glob("*.html"))]

    def exit_code(s):
        report_only = s["no_schema"] or s["suspect"] or s["suspect_multi"] or s["kept_unverified"]
        return 1 if s["files_with_defects"] or (a.strict and report_only) else 0

    results, raw = run_scan(files, a.root, a.min_ratio)
    summary = print_report(results)
    if a.json_path:
        Path(a.json_path).write_text(json.dumps(to_jsonable(results, a.root), indent=1), encoding="utf-8")

    if not a.fix:
        sys.exit(exit_code(summary))

    # KEPT_ONLY is deliberately never rebuilt here: a kept entry sitting out of
    # canonical order (e.g. before the visible-derived entries instead of
    # after) is not drift by itself -- only real adds/updates/removals are.
    print("\n==== applying fixes ====")
    n_fixed = added_t = updated_t = removed_t = 0
    for rel, (state, defects, kept) in sorted(results.items()):
        if state != "DEFECTS":
            continue
        path, src, vis, blk, raw_entries = raw[rel]
        new_src = fix_file(path, src, blk, vis, kept, raw_entries)
        if new_src is None:
            continue
        added, updated, removed = fix_counts(defects)
        n_fixed += 1
        added_t += added
        updated_t += updated
        removed_t += removed
        print(f"  fixed {rel}  (+{added} ~{updated} -{removed})")
    print(f"\nfiles_fixed={n_fixed} added={added_t} updated={updated_t} removed={removed_t}")

    print("\n==== post-fix re-scan ====")
    results2, _ = run_scan(files, a.root, a.min_ratio)
    summary2 = print_report(results2)
    sys.exit(exit_code(summary2))


if __name__ == "__main__":
    main()
