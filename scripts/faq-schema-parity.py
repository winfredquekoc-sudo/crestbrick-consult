#!/usr/bin/env python3
"""Scan and repair drift between visible FAQ content and FAQPage JSON-LD.

Finds visible questions missing from schema, schema entries no longer on the
page, and answers whose wording differs, then with --fix rebuilds each
FAQPage's mainEntity from the visible page (the source of truth), verbatim
and in visible order. --create-missing instead inserts a brand new FAQPage
block, verbatim from the detected visible pairs, on pages that have visible
FAQ content but no FAQPage block at all -- it never touches a page that
already has one, and --fix never creates one (that split is deliberate).

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

  --root DIR          repo root (default: parent of this script's scripts/ dir)
  --fix                write repairs (default is report only)
  --create-missing     insert a fresh FAQPage block on NO-SCHEMA pages
  --json PATH          also write a machine readable dump of the report
  --min-ratio F         similarity floor for pairing a near-miss question drift (default 0.6)
  --strict              also fail (exit 1) when report-only categories are non-empty

Extraction/serialization (parsing visible pairs and schema, rendering pairs
back into JSON/HTML) lives in scripts/_faq_lib.py -- this file keeps the
comparison policy, CLI, and reporting.
"""
import argparse, collections, difflib, json, os, sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _faq_lib import (norm_visible, norm_schema, visible_text_corpus,       # noqa: E402
                       extract_visible_pairs, extract_schema, build_entries,
                       rebuild_block, insert_missing_block)

DEFAULT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Typography fold for the keep-rule containment test ONLY (never drift
# comparison): a visible/schema twin differing by nothing but a curly quote or
# dash must not read as "not found" and get deleted -- one-directional, can
# only turn a REMOVE into a KEEP, never the reverse.
_TYPOGRAPHY_FOLD = str.maketrans({"‘": "'", "’": "'", "“": '"',
                                   "”": '"', "–": "-", "—": "-", "\xa0": " "})


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


def create_missing(results, raw, files, root, min_ratio):
    """--create-missing body: insert a fresh FAQPage block on every NO_SCHEMA
    file. Returns (results, raw, summary) after re-scanning, since inserting
    a block changes those files' classification."""
    print("\n==== creating missing FAQPage blocks ====")
    n_created, exceptions = 0, []
    for rel, (state, defects, kept) in sorted(results.items()):
        if state != "NO_SCHEMA":
            continue
        path, src, vis, blk, raw_entries = raw[rel]
        new_src, err = insert_missing_block(src, vis)
        if err:
            exceptions.append({"file": rel, "error": err})
            print(f"  SKIP {rel}: {err}")
            continue
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(new_src)
        n_created += 1
        print(f"  created {rel}  (+{len(vis)} entries)")
    print(f"\nfiles_created={n_created} exceptions={len(exceptions)}")
    if exceptions:
        print("exceptions:")
        for e in exceptions:
            print(f"  {e['file']}: {e['error']}")

    print("\n==== post-create-missing re-scan ====")
    results2, raw2 = run_scan(files, root, min_ratio)
    summary2 = print_report(results2)
    return results2, raw2, summary2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--fix", action="store_true")
    ap.add_argument("--create-missing", action="store_true")
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

    if not a.fix and not a.create_missing:
        sys.exit(exit_code(summary))

    if a.create_missing:
        results, raw, summary = create_missing(results, raw, files, a.root, a.min_ratio)

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
