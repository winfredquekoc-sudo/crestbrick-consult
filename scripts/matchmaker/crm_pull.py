#!/usr/bin/env python3
"""scripts/matchmaker/crm_pull.py — CRM pull bridge.

Pulls two things down from the Matchmaker CRM API onto this Mac, so neither
one needs a manual export from the app anymore:

  1. Dispatch rows the app has marked Queued (crm_dispatch, status queued).
     Each one is run through queue_drafts.py's own checks (dead lead refusal,
     dedupe by tenant id and jid, cold over the dead lead threshold, an
     unverifiable tenant) by importing that module's own functions, never by
     copying them. A row is marked pulled on the server FIRST, and only once
     that write is confirmed by the API's own response is the draft appended
     to the real morning dispatch queue (~/.claude/state/morning-dispatch-
     queue.json). Every appended item carries its own dispatch_id, so a row
     that is still pulled but was never actually appended (a crash between
     the two steps) is recovered and appended on the next run, and a row
     already appended is never appended twice. If the append itself fails
     after the row was already marked pulled, the row is moved to cancelled
     with the reason "append failed" so nothing is left pulled but unqueued.
     A row that instead fails one of queue_drafts.py's checks is marked
     cancelled with that reason.

  2. Deals at stage completed that are not yet in clients.db. Each one is
     imported through scripts/ops/log_deal.py's own import-json function
     (imported, never shelled out to), and the import is recorded as a
     crm_activity row through the API.

A separate cleanup step also removes any queue item whose dispatch row has
since been cancelled (only items that carry a dispatch_id), so cancelling a
row before the real 08:00 send still stops it.

This script never sends a WhatsApp message. Sending stays the job of the
existing morning dispatch job (or Winfred by hand) reading the queue file
this script only appends to and prunes.

Reads MM_USER and MM_PASS from the environment only, and never sets or
prints them. When either is absent this is a no op: one line, exit 0 — the
normal state on any machine that has not been handed deploy slot
credentials.

Dry run by default: fetches the CRM snapshot and reports what it would do,
with no writes anywhere. Pass --apply to actually write.

A single run stops picking up new dispatch rows and new deals once it has
been running for TIME_BUDGET_SECONDS, so a slot never stalls; whatever is
left over is picked up cleanly on the next run.

Usage:
  python3 scripts/matchmaker/crm_pull.py            # dry run, no writes
  python3 scripts/matchmaker/crm_pull.py --apply    # write for real
"""
import argparse, base64, datetime, glob, io, json, os, re, socket, sys, tempfile, time
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
CHUNK_SIZE = 50                # matches the app's own flush() batching (deploy/api/crm.js MAX_OPS is 200)
TIME_BUDGET_SECONDS = 60
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
    sends a WhatsApp message — dispatch/dispatch_cancel/activity only. Callers
    chunk at CHUNK_SIZE themselves (see _chunks) so more than 200 rows in one
    run never trips the API's own MAX_OPS limit."""
    if not ops:
        return {}
    body = json.dumps({"ops": ops}).encode("utf8")
    req = urllib.request.Request(api_url(), data=body, method="POST", headers={
        "Authorization": _auth_header(user, pw), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf8"))


def _chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def _time_up(deadline):
    return deadline is not None and time.monotonic() > deadline


# ------------------------------------------------------- queue file reads ---
def _dispatch_ids_in_file(path):
    ids = set()
    try:
        data = json.load(open(path))
    except (OSError, ValueError):
        return ids
    for item in data.get("items") or []:
        did = item.get("dispatch_id")
        if did:
            ids.add(did)
    return ids


def already_appended_dispatch_ids(queue_path):
    """dispatch_id values already present in the current queue file, or in the
    most recent .done-* archive next to it (the morning dispatch job's own
    naming: <queue_path>.done-<timestamp>). Read only, never written here —
    this is purely how a second run tells "already delivered" apart from "was
    marked pulled but the append never actually happened" (a crash between
    the two)."""
    ids = _dispatch_ids_in_file(queue_path) if os.path.exists(queue_path) else set()
    done_files = sorted(glob.glob(queue_path + ".done-*"))
    if done_files:
        ids |= _dispatch_ids_in_file(done_files[-1])
    return ids


# ------------------------------------------------------------- dispatch ----
def mark_pulled_and_confirm(rows, user, pw, apply_, deadline=None):
    """Step one only: runs every status queued row through queue_drafts.py's
    own checks and, when apply_, POSTs the pulled/cancelled ops in chunks of
    CHUNK_SIZE and confirms each pulled write against that same response —
    never against a guess. Returns (confirmed, summary) where confirmed is a
    list of (row, item) tuples the server has actually confirmed as pulled.
    Never appends anything to the real dispatch queue itself; see
    append_entries for that half. Kept separate on purpose: a run that dies
    between this function returning and append_entries running is exactly the
    crash window process_dispatch's own pulled row recovery exists to cover."""
    queued_rows = [r for r in rows if r.get("status") == "queued"]
    summary = {"queued": len(queued_rows), "approved": 0, "cancelled": 0, "time_budget_hit": False}
    if not queued_rows:
        return [], summary

    tenants_by_id = queue_drafts.load_tenants_by_id(queue_drafts.TENANT_DB_PATH)
    wa_conn = enrich.open_wa_bridge(queue_drafts.WA_DB_PATH)
    if wa_conn is None:
        print("crm_pull: WhatsApp bridge unavailable — cold check relies on tenant-db last_contact only")
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).date()
    queued_ids, queued_jids = queue_drafts.pending_recipients(queue_drafts.QUEUE_PATH)

    pulled_ops, cancel_ops = [], []
    try:
        for row in queued_rows:
            if _time_up(deadline):
                remaining = len(queued_rows) - (len(pulled_ops) + len(cancel_ops))
                print("crm_pull: time budget exceeded — skipping %d remaining dispatch row(s)" % remaining)
                summary["time_budget_hit"] = True
                break
            item = {"tenant_id": row.get("tenant_id"), "phone": row.get("phone") or "",
                    "name": "", "message": row.get("text") or ""}
            approved, refused, skipped, duplicates = queue_drafts.classify_items(
                [item], tenants_by_id, wa_conn, today, queued_ids, queued_jids)
            if approved:
                a = approved[0]
                # classify_items copies queued_ids/queued_jids internally rather
                # than mutating the caller's sets, so the running sets here have
                # to be updated by hand for the NEXT row in this same batch to
                # see this one as already spoken for.
                queued_ids.add(a["tenant_id"]); queued_jids.add(a["jid"])
                pulled_ops.append({
                    "op": "dispatch", "id": row.get("id"), "tenant_id": row.get("tenant_id"),
                    "listing_id": row.get("listing_id"), "jid": a.get("jid"), "phone": row.get("phone"),
                    "text": row.get("text"), "viewing_slot": row.get("viewing_slot"),
                    "status": "pulled", "device": device_name(),
                })
            else:
                reason = (refused or skipped or duplicates)[0][2]
                cancel_ops.append({"op": "dispatch_cancel", "id": row.get("id"), "reason": reason})
    finally:
        if wa_conn:
            wa_conn.close()

    summary["approved"] = len(pulled_ops)
    summary["cancelled"] = len(cancel_ops)
    if not apply_:
        return [], summary

    if cancel_ops:
        for chunk in _chunks(cancel_ops, CHUNK_SIZE):
            post_ops(user, pw, chunk)

    id_to_row = {row.get("id"): row for row in queued_rows}
    id_to_item = {op["id"]: {"tenant_id": op["tenant_id"], "jid": op["jid"], "message": op["text"]} for op in pulled_ops}
    confirmed = []
    for chunk in _chunks(pulled_ops, CHUNK_SIZE):
        resp = post_ops(user, pw, chunk)
        by_id = {d.get("id"): d for d in (resp.get("dispatch") or [])}
        for op in chunk:
            d = by_id.get(op["id"])
            if d and d.get("status") == "pulled":
                confirmed.append((id_to_row[op["id"]], id_to_item[op["id"]]))
    return confirmed, summary


def append_entries(entries, user, pw):
    """entries: a list of (row, item) tuples, item carrying tenant_id/jid/
    message. Skips any row whose dispatch id is already in the real queue
    file or its most recent archive (already delivered by an earlier run),
    stamps every appended item with its dispatch_id, and on a local failure
    to write the queue file marks every row in this batch cancelled with
    reason "append failed" so nothing is left pulled but unqueued."""
    summary = {"appended": 0, "already_appended": 0, "append_failed": 0}
    if not entries:
        return summary
    already = already_appended_dispatch_ids(queue_drafts.QUEUE_PATH)
    to_append = [(row, item) for row, item in entries if row.get("id") not in already]
    summary["already_appended"] = len(entries) - len(to_append)
    if not to_append:
        return summary
    batch = [{"tenant_id": item.get("tenant_id"), "jid": item.get("jid"), "message": item.get("message"),
              "dispatch_id": row.get("id")} for row, item in to_append]
    try:
        queue_drafts.merge_into_queue(batch, queue_drafts.QUEUE_PATH)
    except Exception as e:
        ids = [row.get("id") for row, item in to_append]
        cancel_ops = [{"op": "dispatch_cancel", "id": rid, "reason": "append failed"} for rid in ids]
        for chunk in _chunks(cancel_ops, CHUNK_SIZE):
            post_ops(user, pw, chunk)
        print("crm_pull: append failed (%s) — %d row(s) cancelled" % (e, len(ids)), file=sys.stderr)
        summary["append_failed"] = len(ids)
        return summary
    summary["appended"] = len(to_append)
    return summary


def process_dispatch(rows, user, pw, apply_, deadline=None):
    confirmed, summary = mark_pulled_and_confirm(rows, user, pw, apply_, deadline)
    # Every currently pulled row is a recovery candidate, not only the ones
    # this run just confirmed — append_entries' own already appended check
    # (dispatch id in the queue file or its most recent archive) is what
    # keeps a normal, uneventful row from being appended a second time here.
    # This is the other half of the crash window: a row can be pulled on the
    # server with nothing yet in the queue file if a previous run died
    # between mark_pulled_and_confirm returning and append_entries running.
    pulled_rows = [r for r in rows if r.get("status") == "pulled"]
    summary["pulled_seen"] = len(pulled_rows)
    recoverable = [(row, {"tenant_id": row.get("tenant_id"), "jid": row.get("jid"), "message": row.get("text") or ""})
                   for row in pulled_rows]
    if apply_:
        summary.update(append_entries(confirmed + recoverable, user, pw))
    return summary


def cleanup_cancelled(dispatch_rows, apply_):
    """Removes any item from the real dispatch queue whose dispatch row has
    since been cancelled — only items that carry a dispatch_id, so a manual
    or another producer's item is never touched. This is what makes
    cancelling a row before the real 08:00 send actually stop it, rather
    than just updating a status nobody downstream reads."""
    cancelled_ids = {r.get("id") for r in dispatch_rows if r.get("status") == "cancelled"}
    summary = {"cancelled_seen": len(cancelled_ids), "removed_from_queue": 0}
    if not cancelled_ids or not apply_ or not os.path.exists(queue_drafts.QUEUE_PATH):
        return summary
    try:
        q = json.load(open(queue_drafts.QUEUE_PATH))
    except (OSError, ValueError):
        return summary
    items = q.get("items") or []
    kept = [i for i in items if not (i.get("dispatch_id") and i.get("dispatch_id") in cancelled_ids)]
    removed = len(items) - len(kept)
    if removed:
        q["items"] = kept
        tmp = queue_drafts.QUEUE_PATH + ".tmp"
        json.dump(q, open(tmp, "w"), indent=1, ensure_ascii=False)
        os.replace(tmp, queue_drafts.QUEUE_PATH)
        summary["removed_from_queue"] = removed
    return summary


# ----------------------------------------------------------------- deals ---
def _deals_table_exists(con):
    row = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='deals'").fetchone()
    return row is not None


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


def process_deals(deals, user, pw, apply_, deadline=None):
    completed = [d for d in deals if d.get("stage") == "completed"]
    summary = {"completed": len(completed), "pending_import": 0, "imported": 0,
               "skipped_no_table": False, "time_budget_hit": False}
    if not completed:
        return summary

    con = log_deal.connect()
    try:
        if not _deals_table_exists(con):
            print("crm_pull: clients.db has no deals table — skipping deal import")
            summary["skipped_no_table"] = True
            return summary
        before = _imported_ids(con)
    finally:
        con.close()

    new_rows = [_to_export_row(d) for d in completed if str(d.get("id")) not in before]
    summary["pending_import"] = len(new_rows)
    if not new_rows or not apply_:
        return summary
    if _time_up(deadline):
        print("crm_pull: time budget exceeded — skipping %d pending deal import(s)" % len(new_rows))
        summary["time_budget_hit"] = True
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
        for chunk in _chunks(activity_ops, CHUNK_SIZE):
            post_ops(user, pw, chunk)
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                     help="write for real: mark rows pulled or cancelled, append the dispatch queue, prune cancelled items, import completed deals")
    args = ap.parse_args(argv)

    user = os.environ.get("MM_USER")
    pw = os.environ.get("MM_PASS")
    if not user or not pw:
        print("credentials not set, skipping")
        return 0

    deadline = time.monotonic() + TIME_BUDGET_SECONDS
    # Fetch, every POST (mark pulled/cancelled, the append's own failure
    # recovery, and the deal import activity log) and the append itself all
    # sit inside this one try, so a network problem anywhere in the run comes
    # back as one line and exit code 1, never a raw traceback.
    try:
        snap = fetch_snapshot(user, pw)
        dispatch_rows = snap.get("dispatch") or []
        d_summary = process_dispatch(dispatch_rows, user, pw, args.apply, deadline)
        c_summary = cleanup_cancelled(dispatch_rows, args.apply)
        l_summary = process_deals(snap.get("deals") or [], user, pw, args.apply, deadline)
    except (urllib.error.URLError, OSError, ValueError) as e:
        print("crm_pull: failed (%s) — nothing done" % e, file=sys.stderr)
        return 1

    mode = "apply" if args.apply else "dry run"
    print("crm_pull (%s): dispatch %d queued, %d approved, %d cancelled, %d appended "
          "(%d already queued, %d append failed) — cleanup removed %d — "
          "deals %d completed, %d pending import, %d imported" % (
              mode, d_summary["queued"], d_summary["approved"], d_summary["cancelled"],
              d_summary.get("appended", 0), d_summary.get("already_appended", 0),
              d_summary.get("append_failed", 0), c_summary["removed_from_queue"],
              l_summary["completed"], l_summary["pending_import"], l_summary["imported"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
