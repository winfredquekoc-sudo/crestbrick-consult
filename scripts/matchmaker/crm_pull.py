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
     queue.json), tagged with its own dispatch_id.

     Every successful append is recorded in a local ledger
     (~/.claude/state/matchmaker-dispatch-ledger.json, written atomically),
     with the append time and the queue file it went into. A pulled row is
     only ever reconsidered for a fresh append (running it through
     classify_items exactly like a brand new one, so a tenant who went cold
     or dead in the meantime is still refused) when its id is absent from the
     ledger AND it was pulled within the last two hours — the crash window
     of the same slot. Any other pulled row is left alone by this path.
     Once a pulled row's id turns up in ANY of the queue file's .done-*
     archives, or has sat in the ledger for 24 hours or more, it is marked
     sent on the server, a terminal state nothing here ever changes again.

     If the append itself fails after a row was marked pulled, that row is
     moved to cancelled with reason "append failed" so nothing is left
     pulled but unqueued. A row that instead fails one of queue_drafts.py's
     checks is marked cancelled with that reason. A cancelled row accepts a
     fresh queued write later (the app's own Mark Queued action does this)
     and is treated exactly like a new row from there.

     A separate cleanup step removes any queue item whose dispatch row has
     since been cancelled (only items that carry a dispatch_id), so
     cancelling a row before the real 08:00 send still stops it. This step
     takes an exclusive lock on the queue file, re reads it under that lock,
     skips entirely if the file changed since this run first looked at it,
     and never runs at all between 07:45 and 08:45 Singapore time.

  2. Deals at stage completed that are not yet in clients.db. Each one is
     imported through scripts/ops/log_deal.py's own import-json function
     (imported, never shelled out to), and the import is recorded as a
     crm_activity row through the API.

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
import argparse, base64, datetime, fcntl, glob, io, json, os, re, socket, sys, tempfile, time
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
LEDGER_PATH = os.path.expanduser("~/.claude/state/matchmaker-dispatch-ledger.json")
CHUNK_SIZE = 50                # matches the app's own flush() batching (deploy/api/crm.js MAX_OPS is 200)
TIME_BUDGET_SECONDS = 60
RECOVERY_WINDOW_HOURS = 2      # the crash window of the same slot — see module docstring
SENT_LEDGER_AGE_HOURS = 24     # a ledger entry this old is treated as sent even with no archive match yet
BLACKOUT_START = (7, 45)       # cleanup never runs in this Singapore time window — the real 08:00 send
BLACKOUT_END = (8, 45)
# The exact tag log_deal.py's import-json mode stamps into notes for dedupe
# (see its cmd_import_json). Read here only to tell which deals are already
# in clients.db before deciding what is new — the actual insert/dedupe logic
# stays entirely inside log_deal.py.
IMPORT_TAG_RE = re.compile(r"\[matchmaker-import-id=(\S+)\]")
_MTIME_UNSET = object()


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


def _parse_iso(s):
    """Parses a timestamp this script or the API produced. Tolerant of a bare
    'Z' suffix (Python's own fromisoformat only accepts that from 3.11
    onward, and log_deal.py's own header notes 3.9 compatibility elsewhere in
    this project), and of anything malformed — returns None rather than
    raising, since a timestamp this script cannot read is exactly the same as
    one that is not there for every caller here (an unknown age is never
    treated as fresh)."""
    if not s:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt
    except ValueError:
        return None


# ------------------------------------------------------------- the ledger --
def load_ledger():
    try:
        data = json.load(open(LEDGER_PATH))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_ledger(ledger):
    d = os.path.dirname(LEDGER_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = LEDGER_PATH + ".tmp"
    json.dump(ledger, open(tmp, "w"), indent=1, ensure_ascii=False)
    os.replace(tmp, LEDGER_PATH)


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


def all_archived_dispatch_ids(queue_path):
    """dispatch_id values found in EVERY .done-* archive next to the queue
    file (the morning dispatch job's own naming: <queue_path>.done-
    <timestamp>), not only the newest one — a dispatch id can sit in an
    archive that has since been superseded by one or more later rotations,
    and checking only the newest file was exactly the bug this function
    exists to not repeat. Read only, never written here."""
    ids = set()
    for f in glob.glob(queue_path + ".done-*"):
        ids |= _dispatch_ids_in_file(f)
    return ids


# ------------------------------------------------------------- dispatch ----
def gather_candidates(rows, ledger, now_utc):
    """Every status queued row, plus every status pulled row worth giving a
    second try: its id is absent from the ledger (nothing here has ever
    actually appended it) AND it was pulled within RECOVERY_WINDOW_HOURS —
    the crash window of the same slot. A pulled row already in the ledger, or
    one that has aged past the window without ever reaching the ledger, is
    left alone entirely by this function; mark_sent_rows is what eventually
    resolves those once they show up archived or old enough."""
    queued_rows = [r for r in rows if r.get("status") == "queued"]
    recoverable_rows = []
    for r in rows:
        if r.get("status") != "pulled":
            continue
        if r.get("id") in ledger:
            continue
        pulled_at = _parse_iso(r.get("pulled_at"))
        if pulled_at is None:
            continue
        if now_utc - pulled_at <= datetime.timedelta(hours=RECOVERY_WINDOW_HOURS):
            recoverable_rows.append(r)
    return queued_rows, recoverable_rows


def classify_and_mark(rows, ledger, user, pw, apply_, deadline=None):
    """Runs every candidate row (queued, plus any recoverable pulled row —
    see gather_candidates) through queue_drafts.py's own checks, exactly the
    same call for both: a recovered row is never trusted just because it was
    approved once before, since a tenant can go cold or dead in between.
    When apply_, POSTs the cancelled ops and the pulled ops (only for rows
    that were actually status queued — a recovered row is pulled already) in
    chunks of CHUNK_SIZE, confirming each pulled write against that same
    response. Returns (confirmed, summary), confirmed a list of (row, item)
    tuples ready for append_entries — never appends anything itself, so a run
    that dies here leaves exactly the crash window append_entries' own ledger
    check is built to recover from."""
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    queued_rows, recoverable_rows = gather_candidates(rows, ledger, now_utc)
    all_rows = queued_rows + recoverable_rows
    summary = {"queued": len(queued_rows), "recoverable": len(recoverable_rows),
               "approved": 0, "cancelled": 0, "time_budget_hit": False}
    if not all_rows:
        return [], summary

    tenants_by_id = queue_drafts.load_tenants_by_id(queue_drafts.TENANT_DB_PATH)
    wa_conn = enrich.open_wa_bridge(queue_drafts.WA_DB_PATH)
    if wa_conn is None:
        print("crm_pull: WhatsApp bridge unavailable — cold check relies on tenant-db last_contact only")
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).date()
    queued_ids, queued_jids = queue_drafts.pending_recipients(queue_drafts.QUEUE_PATH)

    pulled_ops, cancel_ops, approved_entries = [], [], []
    try:
        for row in all_rows:
            if _time_up(deadline):
                remaining = len(all_rows) - (len(approved_entries) + len(cancel_ops))
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
                needs_pulled_post = row.get("status") == "queued"
                if needs_pulled_post:
                    pulled_ops.append({
                        "op": "dispatch", "id": row.get("id"), "tenant_id": row.get("tenant_id"),
                        "listing_id": row.get("listing_id"), "jid": a.get("jid"), "phone": row.get("phone"),
                        "text": row.get("text"), "viewing_slot": row.get("viewing_slot"),
                        "status": "pulled", "device": device_name(),
                    })
                approved_entries.append((row, a, needs_pulled_post))
            else:
                reason = (refused or skipped or duplicates)[0][2]
                cancel_ops.append({"op": "dispatch_cancel", "id": row.get("id"), "reason": reason})
    finally:
        if wa_conn:
            wa_conn.close()

    summary["approved"] = len(approved_entries)
    summary["cancelled"] = len(cancel_ops)
    if not apply_:
        return [], summary

    if cancel_ops:
        for chunk in _chunks(cancel_ops, CHUNK_SIZE):
            post_ops(user, pw, chunk)

    confirmed = []
    id_to_entry = {row.get("id"): (row, a) for row, a, needs in approved_entries}
    for chunk in _chunks(pulled_ops, CHUNK_SIZE):
        resp = post_ops(user, pw, chunk)
        by_id = {d.get("id"): d for d in (resp.get("dispatch") or [])}
        for op in chunk:
            d = by_id.get(op["id"])
            if d and d.get("status") == "pulled":
                confirmed.append(id_to_entry[op["id"]])
    # A recovered row is pulled already — nothing to POST or confirm for it,
    # it goes straight to the append stage.
    for row, a, needs in approved_entries:
        if not needs:
            confirmed.append((row, a))
    return confirmed, summary


def append_entries(entries, user, pw, ledger):
    """entries: a list of (row, item) tuples, item carrying tenant_id/jid/
    message. Skips any row whose dispatch id is already in the ledger (this
    script has appended it before, in this run or an earlier one) or already
    sitting in the current queue file's own items (a defensive second check,
    e.g. a hand edited ledger). Stamps every appended item with its
    dispatch_id and records it in the ledger, written atomically. On a local
    failure to write the queue file, marks every row in this batch cancelled
    with reason "append failed" so nothing is left pulled but unqueued."""
    summary = {"appended": 0, "already_appended": 0, "append_failed": 0}
    if not entries:
        return summary
    current_ids = _dispatch_ids_in_file(queue_drafts.QUEUE_PATH) if os.path.exists(queue_drafts.QUEUE_PATH) else set()
    to_append = [(row, item) for row, item in entries
                 if row.get("id") not in ledger and row.get("id") not in current_ids]
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
    now = datetime.datetime.now(datetime.timezone.utc)
    for row, item in to_append:
        ledger[row.get("id")] = {"appended_at": now.isoformat(), "queue_file": queue_drafts.QUEUE_PATH}
    save_ledger(ledger)
    summary["appended"] = len(to_append)
    return summary


def mark_sent_rows(rows, ledger, user, pw, apply_):
    """For each currently pulled row whose dispatch id shows up in ANY of the
    queue file's .done-* archives, or has sat in the ledger for
    SENT_LEDGER_AGE_HOURS or more, marks it sent on the server — the terminal
    state nextDispatchStatus (crm-validate.js) only allows a pulled row to
    move into, and never changes again. This is what stops a long lived
    pulled row from ever being reconsidered by gather_candidates once it is
    genuinely done, regardless of how many further archive rotations happen
    afterward."""
    pulled_rows = [r for r in rows if r.get("status") == "pulled"]
    summary = {"marked_sent": 0}
    if not pulled_rows:
        return summary
    archived_ids = all_archived_dispatch_ids(queue_drafts.QUEUE_PATH)
    now = datetime.datetime.now(datetime.timezone.utc)
    to_mark = []
    for row in pulled_rows:
        rid = row.get("id")
        if rid in archived_ids:
            to_mark.append(row)
            continue
        entry = ledger.get(rid)
        if entry:
            appended_at = _parse_iso(entry.get("appended_at"))
            if appended_at and (now - appended_at) >= datetime.timedelta(hours=SENT_LEDGER_AGE_HOURS):
                to_mark.append(row)
    summary["marked_sent"] = len(to_mark)
    if not apply_ or not to_mark:
        return summary
    ops = [{"op": "dispatch", "id": row.get("id"), "tenant_id": row.get("tenant_id"),
            "listing_id": row.get("listing_id"), "jid": row.get("jid"), "phone": row.get("phone"),
            "text": row.get("text"), "viewing_slot": row.get("viewing_slot"),
            "status": "sent", "device": row.get("device")} for row in to_mark]
    for chunk in _chunks(ops, CHUNK_SIZE):
        post_ops(user, pw, chunk)
    return summary


def process_dispatch(rows, user, pw, apply_, deadline=None):
    ledger = load_ledger()
    confirmed, summary = classify_and_mark(rows, ledger, user, pw, apply_, deadline)
    if apply_:
        summary.update(append_entries(confirmed, user, pw, ledger))
    summary.update(mark_sent_rows(rows, ledger, user, pw, apply_))
    return summary


def _in_blackout(now_sgt):
    hm = (now_sgt.hour, now_sgt.minute)
    return BLACKOUT_START <= hm <= BLACKOUT_END


def _now_sgt():
    # A plain function, not an inline call, so a test can monkeypatch this one
    # name and get a deterministic clock through every call site below —
    # including the ones inside main() — without the real wall clock ever
    # being able to land a test run inside the blackout window by accident.
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))


def cleanup_cancelled(dispatch_rows, apply_, mtime_at_fetch=_MTIME_UNSET, now=None):
    """Removes any item from the real dispatch queue whose dispatch row has
    since been cancelled — only items that carry a dispatch_id, so a manual
    or another producer's item is never touched. Never runs between 07:45
    and 08:45 Singapore time (the real send window), and otherwise takes an
    exclusive lock on <queue file>.lock, re reads the queue file under that
    lock, and skips the whole pass if the file's mtime has changed since
    mtime_at_fetch (this run first looked at it, right after fetching the
    CRM snapshot) — something else touched it in between (most likely this
    same run's own append), so this pass defers to the next run rather than
    read modify write against a file it can no longer be sure it understands.
    mtime_at_fetch left at its default disables that specific check, for a
    caller (a test, most likely) that does not care about it."""
    cancelled_ids = {r.get("id") for r in dispatch_rows if r.get("status") == "cancelled"}
    summary = {"cancelled_seen": len(cancelled_ids), "removed_from_queue": 0,
               "skipped_blackout": False, "skipped_changed": False}
    if not cancelled_ids or not apply_:
        return summary
    now_sgt = now or _now_sgt()
    if _in_blackout(now_sgt):
        summary["skipped_blackout"] = True
        print("crm_pull: skipping dispatch queue cleanup during the 07:45 to 08:45 send window")
        return summary
    queue_path = queue_drafts.QUEUE_PATH
    if not os.path.exists(queue_path):
        return summary
    lock_path = queue_path + ".lock"
    lock_dir = os.path.dirname(lock_path)
    if lock_dir:
        os.makedirs(lock_dir, exist_ok=True)
    with open(lock_path, "a+") as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX)
        try:
            if not os.path.exists(queue_path):
                return summary
            current_mtime = os.path.getmtime(queue_path)
            if mtime_at_fetch is not _MTIME_UNSET and current_mtime != mtime_at_fetch:
                summary["skipped_changed"] = True
                print("crm_pull: dispatch queue changed since this run last looked at it — skipping this cleanup pass")
                return summary
            try:
                q = json.load(open(queue_path))
            except (OSError, ValueError):
                return summary
            items = q.get("items") or []
            kept = [i for i in items if not (i.get("dispatch_id") and i.get("dispatch_id") in cancelled_ids)]
            removed = len(items) - len(kept)
            if removed:
                q["items"] = kept
                tmp = queue_path + ".tmp"
                json.dump(q, open(tmp, "w"), indent=1, ensure_ascii=False)
                os.replace(tmp, queue_path)
                summary["removed_from_queue"] = removed
        finally:
            fcntl.flock(lockf, fcntl.LOCK_UN)
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
                     help="write for real: mark rows pulled, sent or cancelled, append the dispatch queue, prune cancelled items, import completed deals")
    args = ap.parse_args(argv)

    user = os.environ.get("MM_USER")
    pw = os.environ.get("MM_PASS")
    if not user or not pw:
        print("credentials not set, skipping")
        return 0

    deadline = time.monotonic() + TIME_BUDGET_SECONDS
    # Fetch, every POST (mark pulled/sent/cancelled, the append's own failure
    # recovery, and the deal import activity log) and the append itself all
    # sit inside this one try, so a network problem anywhere in the run comes
    # back as one line and exit code 1, never a raw traceback.
    try:
        snap = fetch_snapshot(user, pw)
        dispatch_rows = snap.get("dispatch") or []
        # Captured right after the fetch, before process_dispatch can touch the
        # queue file itself — see cleanup_cancelled's own docstring for why.
        mtime_at_fetch = os.path.getmtime(queue_drafts.QUEUE_PATH) if os.path.exists(queue_drafts.QUEUE_PATH) else None
        d_summary = process_dispatch(dispatch_rows, user, pw, args.apply, deadline)
        c_summary = cleanup_cancelled(dispatch_rows, args.apply, mtime_at_fetch=mtime_at_fetch)
        l_summary = process_deals(snap.get("deals") or [], user, pw, args.apply, deadline)
    except (urllib.error.URLError, OSError, ValueError) as e:
        print("crm_pull: failed (%s) — nothing done" % e, file=sys.stderr)
        return 1

    mode = "apply" if args.apply else "dry run"
    print("crm_pull (%s): dispatch %d queued, %d recoverable, %d approved, %d cancelled, "
          "%d appended (%d already, %d append failed), %d marked sent — "
          "cleanup removed %d — deals %d completed, %d pending import, %d imported" % (
              mode, d_summary["queued"], d_summary.get("recoverable", 0), d_summary["approved"],
              d_summary["cancelled"], d_summary.get("appended", 0), d_summary.get("already_appended", 0),
              d_summary.get("append_failed", 0), d_summary.get("marked_sent", 0),
              c_summary["removed_from_queue"],
              l_summary["completed"], l_summary["pending_import"], l_summary["imported"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
