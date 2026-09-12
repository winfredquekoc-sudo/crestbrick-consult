#!/usr/bin/env python3
"""
wa_intake_viewing_slot_replay.py -- 7 day shadow replay for "a viewing slot on every open
listing" (11 Sep 2026): for every OFFER_VIEWING action the CURRENT engine produces against
real recent conversations, counts how many carry a NAMED slot vs the new hold line
(intake_engine.VIEWING_HOLD_TEXT) -- BEFORE vs AFTER the proposed viewing windows
(viewing-windows-proposed.json) are confirmed into a SANDBOX COPY of the listing index.

Read only against the live messages.db (mode=ro, same as wa_intake_shadow_replay.py, whose
chat discovery + row fetch this reuses directly) -- this is the SAME audited exception that
script's own docstring documents: proving real world impact needs real recent history, so
this deliberately does NOT run under WA_INTAKE_SANDBOX=1 (which would have nothing real left
to replay). It never writes anywhere live: the listing index is only ever READ (both runs),
and the "after" run applies the confirmed windows into a throwaway SCRATCH copy, never the
live index itself. DRY_RUN is forced True on the engine either way, and intake-state.json is
never touched (a fresh state dict per chat per run).

Usage:
  /usr/bin/python3 scripts/wa_intake_viewing_slot_replay.py [--days 7]
      [--live-idx PATH] [--proposed-json PATH] [--out DIR]
"""
import argparse, datetime, json, os, shutil, sys

os.environ.setdefault("WA_INTAKE_NO_TELEGRAM", "1")
os.environ.setdefault("WA_INTAKE_NO_SEND", "1")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WA_DIR = os.path.join(_REPO_ROOT, "src", "wa-pipeline")
sys.path.insert(0, _WA_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import wa_intake_paths as _P                # noqa: E402
import wa_intake_shadow_replay as SR        # noqa: E402 (chat discovery + row fetch, reused)
import intake_engine as E                   # noqa: E402
import apply_viewing_windows as APPLY       # noqa: E402 (_confirmed_updates, reused)

SCRATCH = SR.SCRATCH
LIVE_IDX_DEFAULT = SR.LIVE_IDX_DEFAULT
PROPOSED_JSON_DEFAULT = os.path.join(SCRATCH, "viewing-windows-proposed.json")


def replay_chat_for_slots(jid, rows, idx_path):
    """Replays one chat's real rows through the CURRENT intake_engine against idx_path,
    forced DRY_RUN, on a fresh state dict -- counts every OFFER_VIEWING action emitted along
    the way (not just the chat's last action) as either a named slot (action['slot'] truthy)
    or the hold line (falsy)."""
    E.DRY_RUN = True
    E.IDX = idx_path
    if hasattr(E, "_TPL_HEADS"):
        delattr(E, "_TPL_HEADS")
    reqs = E.listing_reqs()
    events = SR.build_events(rows, reqs, E, bind_outbound=True)
    state = {"version": 1, "conversations": {}}
    named = hold = 0
    for ev in events:
        ev["jid"] = jid
        a = E.handle_event(state, ev)
        if a and a.get("type") == "OFFER_VIEWING":
            if a.get("slot"):
                named += 1
            else:
                hold += 1
    return named, hold


def main(argv=None):
    _P.sandbox_init()   # no-op unless WA_INTAKE_SANDBOX=1 is set by a caller (e.g. a test)
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--live-idx", default=LIVE_IDX_DEFAULT)
    ap.add_argument("--proposed-json", default=PROPOSED_JSON_DEFAULT)
    ap.add_argument("--out", default=SCRATCH)
    args = ap.parse_args(argv)

    chats = SR.discover_filled_form_chats(args.days)
    chat_rows = {jid: SR.fetch_chat_rows(jid, args.days) for jid in chats}

    before_named = before_hold = 0
    for jid in chats:
        n, h = replay_chat_for_slots(jid, chat_rows[jid], args.live_idx)
        before_named += n; before_hold += h

    os.makedirs(args.out, exist_ok=True)
    after_idx = os.path.join(args.out, "listing-index.viewing-slot-replay-after.json")
    shutil.copy2(args.live_idx, after_idx)   # SANDBOX COPY -- never the live index itself
    proposed = json.load(open(args.proposed_json))
    updates, warnings = APPLY._confirmed_updates(proposed)
    apply_result = E.apply_fixed_viewing(updates, path=after_idx)

    after_named = after_hold = 0
    for jid in chats:
        n, h = replay_chat_for_slots(jid, chat_rows[jid], after_idx)
        after_named += n; after_hold += h

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    lines = [
        f"wa_intake_viewing_slot_replay -- {len(chats)} chats with a filled tenant form in "
        f"the last {args.days} days (DRY RUN, read only, {stamp})",
        f"live idx (read only): {args.live_idx}",
        f"after idx (scratch copy, confirmed windows applied): {after_idx}",
        f"confirmed windows applied: {apply_result['written']}"
        + (f"  (missing from index: {apply_result['missing']})" if apply_result['missing'] else ""),
        "",
        f"{'config':10} {'named slot':>12} {'hold line':>12} {'total offers':>14}",
        f"{'before':10} {before_named:>12} {before_hold:>12} {before_named + before_hold:>14}",
        f"{'after':10} {after_named:>12} {after_hold:>12} {after_named + after_hold:>14}",
    ]
    text = "\n".join(lines)
    print(text)
    out_path = os.path.join(args.out, "viewing-slot-replay-report.txt")
    open(out_path, "w").write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
