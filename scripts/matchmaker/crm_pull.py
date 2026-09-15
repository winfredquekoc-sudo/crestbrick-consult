#!/usr/bin/env python3
"""scripts/matchmaker/crm_pull.py — CRM pull bridge.

Pulls two things down from the Matchmaker CRM API onto this Mac, so neither
one needs a manual export from the app anymore:

  1. Dispatch rows the app has marked Queued (crm_dispatch, status queued).
     Each one is run through queue_drafts.py's own checks (dead lead refusal,
     dedupe by tenant id and jid, cold over the dead lead threshold, an
     unverifiable tenant) by importing that module's own functions, never by
     copying them. Accepted drafts are appended to the real morning dispatch
     queue (~/.claude/state/morning-dispatch-queue.json) exactly the way
     queue_drafts.py appends them, and the row is marked pulled through the
     CRM API. A refused row is marked cancelled with the reason instead.

  2. Deals at stage completed that are not yet in clients.db. Each one is
     imported through scripts/ops/log_deal.py's own import-json function
     (imported, never shelled out to), and the import is recorded as a
     crm_activity row through the API.

This script never sends a WhatsApp message. Sending stays the job of the
existing morning dispatch job (or Winfred by hand) reading the queue file
this script only appends to.

Reads MM_USER and MM_PASS from the environment only, and never sets or
prints them. When either is absent this is a no op: one line, exit 0 — the
normal state on any machine that has not been handed deploy slot
credentials.

Dry run by default: fetches the CRM snapshot and reports what it would do,
with no writes anywhere. Pass --apply to actually write.

Idempotent: a dispatch row already marked pulled or cancelled is not status
queued anymore, so a second run skips it rather than reprocessing it. A deal
already imported is found by its own [matchmaker-import-id=...] tag in
clients.db and skipped the same way.

Usage:
  python3 scripts/matchmaker/crm_pull.py            # dry run, no writes
  python3 scripts/matchmaker/crm_pull.py --apply    # write for real
"""
import argparse, base64, datetime, io, json, os, re, socket, sys, tempfile
import urllib.error, urllib.request
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
OPS_DIR = os.path.normpath(os.path.join(HERE, "..", "ops"))
for _p in (HERE, OPS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import enrich          # scripts/matchmaker/enrich.py — WhatsApp bridge helpers
import queue_drafts    # scripts/matchmaker/queue_drafts.py — imported, never copied
import log_deal        # scripts/ops/log_deal.py — imported, never shelled out to

DEFAULT_API_URL = "https://crestbrick-matchmaker-private.vercel.app/api/crm"
# The exact tag log_deal.py's import-json mode stamps into notes for dedupe
# (see its cmd_import_json). Read here only to tell which deals are already
# in clients.db before deciding what is new — the actual insert/dedupe logic
# stays entirely inside log_deal.py.
IMPORT_TAG_RE = re.compile(r"\[matchmaker-import-id=(\S+)\]")


def api_url():
    # Read live, not at import time, so a test (or a future override) can
    # point this at a local server after this module is already imported.
    return os.environ.get("MM_CRM_URL", DEFAULT_API_URL)


def device_name():
    try:
        return socket.gethostname().split(".")[0] or "mac"
    except Exception:
        return "mac"


def _auth_header(user, pw):
    token = base64.b64encode(("%s:%s" % (user, pw)).encode("utf8")).decode("ascii")
    return "Basic " + token


def fetch_snapshot(user, pw):
    req = urllib.request.Request(api_url(), headers={
        "Authorization": _auth_header(user, pw), "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf8"))


def post_ops(user, pw, ops):
    """POSTs a batch of ops to the CRM API. Never called with anything that
    sends a WhatsApp message — dispatch/dispatch_cancel/activity only."""
    if not ops:
        return {}
    body = json.dumps({"ops": ops}).encode("utf8")
    req = urllib.request.Request(api_url(), data=body, method="POST", headers={
        "Authorization": _auth_header(user, pw), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf8"))


# ------------------------------------------------------------- dispatch ----
def process_dispatch(rows, user, pw, apply_):
    """Runs every status queued dispatch row through queue_drafts.py's own
    checks. In dry run mode this still runs the checks (read only — nothing
    is written anywhere) so the summary reflects what a real run would do;
    only the queue file append and the CRM API POST are gated on apply_."""
    queued_rows = [r for r in rows if r.get("status") == "queued"]
    summary = {"queued": len(queued_rows), "approved": 0, "cancelled": 0}
    if not queued_rows:
        return summary

    tenants_by_id = queue_drafts.load_tenants_by_id(queue_drafts.TENANT_DB_PATH)
    wa_conn = enrich.open_wa_bridge(queue_drafts.WA_DB_PATH)
    if wa_conn is None:
        print("crm_pull: WhatsApp bridge unavailable — cold check relies on tenant-db last_contact only")
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).date()
    queued_ids, queued_jids = queue_drafts.pending_recipients(queue_drafts.QUEUE_PATH)

    approved_items, pulled_ops, cancel_ops = [], [], []
    for row in queued_rows:
        item = {"tenant_id": row.get("tenant_id"), "phone": row.get("phone") or "",
                "name": "", "message": row.get("text") or ""}
        approved, refused, skipped, duplicates = queue_drafts.classify_items(
            [item], tenants_by_id, wa_conn, today, queued_ids, queued_jids)
        if approved:
            a = approved[0]
            # classify_items mutates a copy of queued_ids/queued_jids internally
            # (see its own "queued_ids = set(queued_ids or ())"), so the running
            # sets here have to be updated by hand for the NEXT row in this same
            # batch to see this one as already spoken for — mirrors the within
            # batch "one message per recipient" rule queue_drafts.py documents
            # on its own classify_items.
            queued_ids.add(a["tenant_id"]); queued_jids.add(a["jid"])
            approved_items.append(a)
            pulled_ops.append({
                "op": "dispatch", "id": row.get("id"), "tenant_id": row.get("tenant_id"),
                "listing_id": row.get("listing_id"), "jid": a.get("jid"), "phone": row.get("phone"),
                "text": row.get("text"), "viewing_slot": row.get("viewing_slot"),
                "status": "pulled", "device": device_name(),
            })
        else:
            reason = (refused or skipped or duplicates)[0][2]
            cancel_ops.append({"op": "dispatch_cancel", "id": row.get("id"), "reason": reason})

    if wa_conn:
        wa_conn.close()

    summary["approved"] = len(approved_items)
    summary["cancelled"] = len(cancel_ops)

    if apply_:
        if approved_items:
            queue_drafts.merge_into_queue(approved_items, queue_drafts.QUEUE_PATH)
        ops = pulled_ops + cancel_ops
        if ops:
            post_ops(user, pw, ops)
    return summary


# ----------------------------------------------------------------- deals ---
def _imported_ids(con):
    rows = con.execute("SELECT notes FROM deals WHERE notes LIKE '%[matchmaker-import-id=%'").fetchall()
    ids = set()
    for (notes,) in rows:
        m = IMPORT_TAG_RE.search(notes or "")
        if m:
            ids.add(m.group(1))
    return ids


def _to_export_row(d):
    """Shapes one crm_deal snapshot row (validateDealFields' field names) into
    the same field names dealExportRow() in app.js produces, which is the
    shape log_deal.py's import-json mode expects — property_address, not the
    API's own property column."""
    return {
        "id": d.get("id"), "deal_type": d.get("deal_type"), "property_address": d.get("property"),
        "price": d.get("price"), "commission_gross": d.get("commission_gross"),
        "commission_net": d.get("commission_net"), "cobroke_agent": d.get("cobroke_agent"),
        "cobroke_split_pct": d.get("cobroke_split_pct"), "stage": d.get("stage"),
        "otp_date": d.get("otp_date"), "completion_date": d.get("completion_date"),
        "deal_date": d.get("deal_date"), "notes": d.get("notes"), "created_at": d.get("created_at"),
    }


def process_deals(deals, user, pw, apply_):
    completed = [d for d in deals if d.get("stage") == "completed"]
    summary = {"completed": len(completed), "pending_import": 0, "imported": 0}
    if not completed:
        return summary

    con = log_deal.connect()
    try:
        before = _imported_ids(con)
    finally:
        con.close()
    new_rows = [_to_export_row(d) for d in completed if str(d.get("id")) not in before]
    summary["pending_import"] = len(new_rows)
    if not new_rows or not apply_:
        return summary

    fd, tmp_path = tempfile.mkstemp(prefix="mm-deals-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(new_rows, f)
        # log_deal.py's own function, imported above — never a subprocess call.
        # Its prints are informative on a terminal but this script promises one
        # summary line, so they are swallowed here rather than left to print
        # underneath ours.
        buf = io.StringIO()
        with redirect_stdout(buf):
            log_deal.cmd_import_json(argparse.Namespace(file=tmp_path))
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    con = log_deal.connect()
    try:
        after = _imported_ids(con)
    finally:
        con.close()
    newly = after - before
    summary["imported"] = len(newly)

    if newly:
        activity_ops = []
        for d in completed:
            if str(d.get("id")) in newly:
                activity_ops.append({
                    "op": "activity", "key": d.get("key"), "verb": "deal:imported",
                    "detail": ("Deal %s imported into clients.db" % d.get("id"))[:500],
                })
        post_ops(user, pw, activity_ops)
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                     help="write for real: append the dispatch queue, mark rows pulled or cancelled, import completed deals")
    args = ap.parse_args(argv)

    user = os.environ.get("MM_USER")
    pw = os.environ.get("MM_PASS")
    if not user or not pw:
        print("credentials not set, skipping")
        return 0

    try:
        snap = fetch_snapshot(user, pw)
    except (urllib.error.URLError, OSError, ValueError) as e:
        print("crm_pull: could not reach the CRM API (%s) — nothing done" % e, file=sys.stderr)
        return 1

    d_summary = process_dispatch(snap.get("dispatch") or [], user, pw, args.apply)
    l_summary = process_deals(snap.get("deals") or [], user, pw, args.apply)

    mode = "apply" if args.apply else "dry run"
    print("crm_pull (%s): dispatch %d queued, %d approved, %d cancelled — "
          "deals %d completed, %d pending import, %d imported" % (
              mode, d_summary["queued"], d_summary["approved"], d_summary["cancelled"],
              l_summary["completed"], l_summary["pending_import"], l_summary["imported"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
