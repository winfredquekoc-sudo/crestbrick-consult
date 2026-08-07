#!/usr/bin/env python3
"""Repair FAQPage entries whose question field holds a whole section body.

Some pages were generated with the section heading AND its following prose both
crammed into the schema "name" field, leaving a placeholder answer such as
"See the full article for details." That is an invalid FAQPage: the question is
not a question, and the answer carries no information.

The fix splits at the first newline: the heading becomes the question, the rest
becomes the answer. Both halves already exist on the page, so nothing is
invented. Entries that do not match this shape are left alone.

  --root DIR   repo root (default ~/crestbrick-consult)
  --apply      write changes (default is a dry run)
"""
import argparse, collections, json, os, re

LD = re.compile(r'(<script[^>]+ld\+json[^>]*>)(.*?)(</script>)', re.S | re.I)
PLACEHOLDER = re.compile(
    r"^\s*(see the full article|refer to the article|see above|n/?a)\b", re.I)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.expanduser("~/crestbrick-consult"))
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    stats = collections.Counter()
    fixed = []

    for dp, _, names in os.walk(os.path.join(a.root, "public")):
        for n in sorted(names):
            if not n.endswith(".html"):
                continue
            path = os.path.join(dp, n)
            rel = os.path.relpath(path, a.root)
            src = open(path, encoding="utf-8", errors="replace").read()
            if "FAQPage" not in src:
                continue
            changed = False

            def fix(m):
                nonlocal changed
                open_t, blk, close_t = m.groups()
                try:
                    data = json.loads(blk)
                except Exception:
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
                            name = str(qa.get("name", ""))
                            if "\n" not in name:
                                continue
                            head, _, rest = name.partition("\n")
                            head = head.strip()
                            rest = re.sub(r"\s+", " ", rest).strip()
                            if not head or not rest or len(head) > 160:
                                continue
                            ans = (qa.get("acceptedAnswer") or {}).get("text", "")
                            # Only overwrite an answer that says nothing.
                            if ans and not PLACEHOLDER.match(str(ans)):
                                rest = f"{rest} {ans}".strip()
                            qa["name"] = head
                            qa["acceptedAnswer"] = {"@type": "Answer", "text": rest}
                            touched = True
                            stats["entries_repaired"] += 1
                            fixed.append((rel, head))
                    for v in node.values():
                        walk(v)

                walk(data)
                if not touched:
                    return m.group(0)
                changed = True
                return open_t + json.dumps(data, ensure_ascii=False, indent=2) + close_t

            out = LD.sub(fix, src)
            if changed:
                stats["files_changed"] += 1
                if a.apply:
                    open(path, "w", encoding="utf-8").write(out)

    print(f"{'APPLIED' if a.apply else 'DRY RUN'}")
    for k, v in stats.most_common():
        print(f"  {k:<20} {v}")
    for rel, head in fixed[:20]:
        print(f"  {rel}\n     question -> {head[:90]!r}")


if __name__ == "__main__":
    main()
