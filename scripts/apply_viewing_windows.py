#!/usr/bin/env python3
"""
apply_viewing_windows.py -- writes the slots Winfred confirmed (from
extract_viewing_windows.py's viewing-windows-proposed.json) into the listing index as
fixed_viewing, the engine's existing data driven fixed slot mechanism
(intake_engine._fixed_viewing_slot / next_future_slot). One weekly slot per listing: for a
listing whose free text yielded several candidates, whichever candidate(s) still carry
"confirm": true after Winfred's one pass review is (are) written -- the first one found wins
if more than one is left true for the same listing (reported, never silently merged).

    python3 apply_viewing_windows.py --dry-run PROPOSED.json --index INDEX.json
        prints the diff (what WOULD be written) -- never touches disk.

    python3 apply_viewing_windows.py --confirm PROPOSED.json --index INDEX.json
        writes fixed_viewing into INDEX.json, atomically, under the engine's own flock
        (intake_engine.apply_fixed_viewing), with a INDEX.json.bak backup made first.

--index is REQUIRED for both modes -- this script never defaults to the live
~/.claude/state/listing-templates/listing-index.json, and --confirm additionally refuses to
run at all if --index resolves to that real live path (belt and suspenders: --confirm must
only ever be pointed at a sandbox/fixture copy, never the live index, from this tool).
"""
import argparse, json, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "wa-pipeline"))
import intake_engine as E  # noqa: E402

_REAL_LIVE_INDEX = os.path.expanduser("~/.claude/state/listing-templates/listing-index.json")

_ENGINE_FIELDS = ("weekday", "start", "end", "time_label")


def _confirmed_updates(proposed):
    """proposed: the dict loaded from viewing-windows-proposed.json. Returns
    (updates, warnings) where updates is {listing_key: fixed_viewing_dict} (engine schema
    only -- drops the proposal's own provenance fields) and warnings names any listing where
    more than one candidate was still confirm:true (the first is used)."""
    updates, warnings = {}, []
    for lk, entry in proposed.items():
        confirmed = [c for c in (entry.get("parsed") or []) if c.get("confirm")]
        if not confirmed:
            continue
        if len(confirmed) > 1:
            warnings.append(f"{lk}: {len(confirmed)} candidates still confirm:true, "
                             f"using the first ({confirmed[0].get('weekday')} "
                             f"{confirmed[0].get('time_label')})")
        chosen = confirmed[0]
        updates[lk] = {k: chosen.get(k) for k in _ENGINE_FIELDS}
    return updates, warnings


def _diff_lines(updates, index_data):
    by_key = {l.get("listing_key"): l for l in index_data.get("listings", [])}
    lines = []
    for lk, fv in sorted(updates.items()):
        entry = by_key.get(lk)
        before = (entry or {}).get("fixed_viewing")
        lines.append(f"{lk}: {before!r} -> {fv!r}" + ("" if entry else "  [listing NOT FOUND in index]"))
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", metavar="JSON", help="proposed/confirmed windows JSON; print the diff only")
    mode.add_argument("--confirm", metavar="JSON", help="proposed/confirmed windows JSON; WRITE fixed_viewing")
    ap.add_argument("--index", required=True,
                     help="listing index JSON to read/write -- REQUIRED; never defaults to "
                          "the live path. --confirm additionally refuses the real live path.")
    args = ap.parse_args(argv)

    json_path = args.dry_run or args.confirm
    proposed = json.load(open(json_path))
    updates, warnings = _confirmed_updates(proposed)

    if args.confirm:
        if os.path.realpath(args.index) == os.path.realpath(_REAL_LIVE_INDEX):
            print("Refusing: --index resolves to the LIVE listing index. Point --confirm at "
                  "a sandbox/fixture copy instead.", file=sys.stderr)
            return 1
        if not updates:
            print("Nothing confirmed (no candidate carries \"confirm\": true) -- nothing written.")
            return 0
        for w in warnings:
            print("WARNING: " + w, file=sys.stderr)
        result = E.apply_fixed_viewing(updates, path=args.index)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if result.get("missing"):
            print(f"{len(result['missing'])} listing_key(s) not found in the index: "
                  f"{', '.join(result['missing'])}", file=sys.stderr)
        return 0 if not result.get("error") else 1

    # --dry-run: read only, never writes
    index_data = json.load(open(args.index))
    for w in warnings:
        print("WARNING: " + w)
    if not updates:
        print("Nothing confirmed (no candidate carries \"confirm\": true).")
        return 0
    print(f"Would write {len(updates)} fixed_viewing entrie(s):")
    for line in _diff_lines(updates, index_data):
        print("  " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
