#!/usr/bin/env python3
"""scripts/matchmaker/crm_pull.py — CRM pull bridge.

Pulls two things down from the Matchmaker CRM API onto this Mac, so neither
one needs a manual export from the app anymore:

  1. Dispatch rows the app has marked Queued (crm_dispatch, status queued).
     Each one is run through queue_drafts.py's own checks (dead lead refusal,
     dedupe by tenant id and jid, cold over the dead lead threshold, an
     unverifiable tenant) by importing that module's own functions, never by
     copying them. A row is marked pulled on the server FIRST, and only once
     that write is confirmed does the draft get appended to the real morning
     dispatch queue (~/.claude/state/morning-dispatch-queue.json), tagged
     with its own dispatch_id.

     There is no separate ledger. What was actually appended is read
     straight off the queue file and every one of its .done-* archives —
     both carry a dispatch_id on each item this script ever writes, and
     that is the only record of "already delivered" this script keeps. The
     whole sequence — fetching the CRM snapshot, marking rows sent or
     expired, classifying and appending every remaining row — runs under
     ONE exclusive lock on the queue file (<queue file>.lock), held from
     before the snapshot is even fetched until the last append. An
     overlapping run of this same script simply waits for that lock, then
     fetches its own snapshot and finds everything the first run did
     already sitting in the queue file, so it appends nothing twice.

     A pulled row whose dispatch id turns up in ANY of the queue file's
     .done-* archives is marked sent on the server — a terminal state
     nothing here ever changes again. A pulled row older than 7 days (by
     the CRM API's own clock, in the snapshot's server_time field — never
     the Mac's local clock, which this script cannot trust to agree with
     the server) with no archive match is marked cancelled with reason
     "expired". Every other pulled row — however old, as long as it is
     under 7 days and not already sitting in the current queue file — is
     simply given a fresh classify_items check and appended if it still
     passes, exactly like a brand new row. A row that fails a check, or
     whose append itself fails after being marked pulled, is marked
     cancelled with the reason.

     A cancelled row (refused, expired, unqueued, or an append failure) is
     never resurrected by this script: the app always writes a FRESH
     dispatch id for a new attempt (see app.js's writeDispatchRow), so a
     later requeue of the same tenant/listing pair arrives here as an
     entirely different row with its own id, never as a status flip on the
     old one.

  2. Deals at stage completed that are not yet in clients.db. Each one is
     imported through scripts/ops/log_deal.py's own import-json function
     (imported, never shelled out to), and the import is recorded as a
     crm_activity row through the API.

A separate cleanup step removes any queue item whose dispatch row has since
been cancelled (only items that carry a dispatch_id), so cancelling a row
before the real 08:00 send still stops it. This step takes its own
exclusive lock on the queue file, re reads it under that lock, skips
entirely if the file changed since this run first looked at it, and never
runs at all between 07:45 and 08:45 Singapore time.

This script never sends a WhatsApp message. Sending stays the job of the
existing morning dispatch job (or Winfred by hand) reading the queue file
this script only appends to and prunes.

Reads MM_USER and MM_PASS from the environment only, and never sets or
prints them. When either is absent this is a no op: one line, exit 0 — the
normal state on any machine that has not been handed deploy slot
credentials.

Dry run by default: fetches the CRM snapshot and reports what it would do,
with no writes anywhere (dry run still takes the lock briefly to read the
queue file consistently, but never writes through it). Pass --apply to
actually write.

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
CHUNK_SIZE = 50                # matches the app's own flush() batching (deploy/api/crm.js MAX_OPS is 200)
TIME_BUDGET_SECONDS = 60
LOCK_TIMEOUT_SECONDS = 120     # give up waiting for <queue file>.lock rather than block forever
LOCK_POLL_SECONDS = 0.5        # LOCK_NB poll interval while waiting for the lock
EXPIRY_DAYS = 7                # a pulled row older than this, unarchived, is given up on and cancelled
BLACKOUT_START = (7, 45)       # cleanup and the dispatch queue append both never run in this window
BLACKOUT_END = (8, 45)         # — the real 08:00 send, run by morning-dispatch.sh with no lock of its own
_SGT = datetime.timezone(datetime.timedelta(hours=8))
# morning-dispatch.sh loads the queue file into memory, then (much later,
# once every send in the batch is done) renames it to a .done-<stamp>
# archive — a rename that always picks up whatever is on disk at that
# moment, including a row this script appended AFTER the load but BEFORE
# the rename. Such a row ends up sitting in the archive despite never
# actually being sent (the in memory send loop never saw it). The only
# defence available here, with no lock on morning-dispatch.sh's side, is
# distance: an archive stamped comfortably after the row's own pulled_at is
# almost certainly a LATER run's archive, safely past the risky window: an
# archive stamped at or soon after pulled_at might be the very run that
# raced this row's own append, so it is left ambiguous rather than trusted.
MIN_SENT_ARCHIVE_AGE = datetime.timedelta(minutes=5)
_ARCHIVE_STAMP_RE = re.compile(r"\.done-(\d{12})$")
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


def _acquire_lock(lock_path, timeout=None, poll=None):
    """Opens lock_path and takes the exclusive fcntl lock, polling with
    LOCK_NB rather than blocking forever — a wedged holder (or just an
    unusually long overlapping run) must never stall this one indefinitely.
    Gives up after timeout seconds and returns None; the caller is
    responsible for logging "queue lock busy, skipping this run" (the exact
    line every caller here uses) and treating that exactly like "nothing to
    do this run", never as an error. Returns the open, locked file object on
    success — the caller owns unlocking and closing it.

    timeout/poll default to the LOCK_TIMEOUT_SECONDS/LOCK_POLL_SECONDS
    MODULE GLOBALS, read at call time (never bound as ordinary default
    argument values, which Python evaluates once at function definition
    time) — a test overriding those globals to run this on a short fuse
    must actually take effect on every call, not just on whichever call
    happens to be compiled first."""
    if timeout is None:
        timeout = LOCK_TIMEOUT_SECONDS
    if poll is None:
        poll = LOCK_POLL_SECONDS
    lock_dir = os.path.dirname(lock_path)
    if lock_dir:
        os.makedirs(lock_dir, exist_ok=True)
    lockf = open(lock_path, "a+")
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return lockf
        except OSError:
            if time.monotonic() >= deadline:
                lockf.close()
                return None
            time.sleep(poll)


def _parse_iso(s):
    """Parses a timestamp this script or the API produced. Tolerant of a bare
    'Z' suffix (Python's own fromisoformat only accepts that from 3.11
    onward, and log_deal.py's own header notes 3.9 compatibility elsewhere in
    this project), and of anything malformed — returns None rather than
    raising, since a timestamp this script cannot read is exactly the same as
    one that is not there for every caller here (an unknown age is never
    treated as fresh, and a missing server clock never falls back to the
    Mac's own)."""
    if not s:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt
    except ValueError:
        return None


def _now_sgt():
    # A plain function, not an inline call, so a test can monkeypatch this one
    # name and get a deterministic clock through every call site below —
    # including the ones inside main() — without the real wall clock ever
    # being able to land a test run inside the cleanup blackout window, or
    # skew a dead lead check, by accident.
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))


def _today_sgt():
    return _now_sgt().date()


# ------------------------------------------------------- queue file reads ---
def _item_was_skipped(item):
    """True when an archived item carries anything morning-dispatch.sh (or a
    future patched version of it — see the sender patch in the PR body) could
    use to say "this one was never actually sent", as opposed to genuinely
    going out: a "skipped" field, a free text "reason", or a "status" of
    "skipped". The live sender today writes none of these — a chat that moved
    since the queue was built ends up in the very same archive, indistinguishable
    on the wire from a row that really sent, with only its tag string ("...
    (chat moved since queue built)") giving it away, and that string is never
    parsed here since a manual queue_drafts.py tag could read anything. This
    check is forward looking, for the day the sender is patched to say so
    itself; until then a deliberately skipped id can still read back as sent
    from the archive alone (see the PR body's sender patch section)."""
    if item.get("skipped"):
        return True
    if item.get("reason"):
        return True
    if item.get("status") == "skipped":
        return True
    return False


def _dispatch_ids_in_file(path):
    ids = set()
    try:
        data = json.load(open(path))
    except (OSError, ValueError):
        return ids
    for item in data.get("items") or []:
        did = item.get("dispatch_id")
        if did and not _item_was_skipped(item):
            ids.add(did)
    return ids


def _archive_stamp(path):
    """Parses the SGT timestamp baked into a .done-<stamp> archive's own
    filename — morning-dispatch.sh's own `now = dt.datetime.now(...)`,
    captured ONCE at the very top of the script (the moment it loads the
    queue file into memory), well before the send loop runs at all, and
    reused unchanged for this archive filename when the queue is renamed to
    it at the very end. So this is the sender's queue LOAD time, not its
    rename time — a delta close to zero between this stamp and a row's own
    pulled_at means the row was appended around when the sender loaded the
    queue, not around when it finished sending, which is exactly the append
    versus load race MIN_SENT_ARCHIVE_AGE below exists to catch. Returns
    None for a name that does not parse; a caller must treat that exactly
    like "no stamp available", never as a safe (or unsafe) age on its own."""
    m = _ARCHIVE_STAMP_RE.search(path)
    if not m:
        return None
    try:
        return datetime.datetime.strptime(m.group(1), "%Y%m%d%H%M").replace(tzinfo=_SGT)
    except ValueError:
        return None


def archived_dispatch_id_stamps(queue_path):
    """Every dispatch_id found in any .done-* archive next to the queue file,
    mapped to the list of stamps (parsed from each archive's own filename)
    it turned up in — normally one archive per id, but every match is kept
    since a dispatch id could in principle land in more than one rotation.
    Read only, never written here."""
    stamps = {}
    for f in glob.glob(queue_path + ".done-*"):
        stamp = _archive_stamp(f)
        for did in _dispatch_ids_in_file(f):
            stamps.setdefault(did, []).append(stamp)
    return stamps


def all_archived_dispatch_ids(queue_path):
    """dispatch_id values found in EVERY .done-* archive next to the queue
    file (the morning dispatch job's own naming: <queue_path>.done-
    <timestamp>), not only the newest one — a dispatch id can sit in an
    archive that has since been superseded by one or more later rotations.
    Read only, never written here."""
    return set(archived_dispatch_id_stamps(queue_path).keys())


def _read_queue_items(queue_path):
    if not os.path.exists(queue_path):
        return []
    try:
        return json.load(open(queue_path)).get("items") or []
    except (OSError, ValueError):
        return []


def _is_expired(row, server_now):
    if server_now is None:
        return False
    pulled_at = _parse_iso(row.get("pulled_at"))
    if pulled_at is None:
        return False
    return (server_now - pulled_at) >= datetime.timedelta(days=EXPIRY_DAYS)


# ------------------------------------------------------------- dispatch ----
def process_dispatch_locked(rows, server_now, user, pw, apply_, lockf, deadline=None):
    """Everything here runs while the caller already holds the exclusive
    lock on <queue file>.lock, acquired before rows was even fetched — see
    run_dispatch_locked below. A second overlapping run blocks on that same
    lock until this one releases it, then fetches its own fresh snapshot and
    finds every row this run appended already sitting in the queue file, so
    it never appends twice.

    (a) the queue file is read FIRST, before any sent/expired decision is
        made — never after. A pulled row already sitting in the queue file
        is settled by that fact alone; it is never handed to the archive
        based sent check below, no matter what an archive also says about
        it, and never expired either.
    (b) among the pulled rows NOT already sitting in the queue file, one
        whose id is in a .done-* archive is marked sent, but only when that
        archive's own stamp is at least MIN_SENT_ARCHIVE_AGE newer than the
        row's own pulled_at — see the module docstring's append vs archive
        race note. An archive too close in time to pulled_at to trust is
        ambiguous: logged and left pulled, touched no further this run. A
        row that IS already sitting in the queue file but ALSO turns up in
        an archive is likewise ambiguous (that combination should not
        normally happen) — also logged and left pulled, never marked sent
        from the archive.
    (c) every remaining pulled row (not sent, not ambiguous, not already in
        the queue file) older than EXPIRY_DAYS by server_now is marked
        cancelled with reason "expired".
    (d) the append phase below (c) — the one write that can race
        morning-dispatch.sh's own unlocked load then rename — never runs
        inside the 07:45 to 08:45 Singapore time send window, the same
        blackout cleanup_cancelled already observes. Anything otherwise
        ready to append is simply left for the next run outside the window.
    (e) every row still queued or pulled after all of the above: if its id
        is already in the current queue file, it is skipped (already
        delivered — this is the crash recovery case, needing no ledger,
        no time window, just the file itself). Otherwise it is run through
        queue_drafts.py's own classify_items, with the queue's own current
        dedupe applied but this row's own dispatch_id excluded from it (so a
        row's own earlier, still uncommitted state within this same pass
        never marks it a duplicate of itself), marked pulled on the server
        (a harmless no op if it was pulled already), and appended.

    A pulled row already stamped ambiguous_since (the "ambiguous" op, sent
    the first time (b) above found it inconclusive) is not a permanent dead
    end — it is filtered out of pulled_rows before any of the above ever
    runs, on every run, and simply logged as still awaiting an operator's
    Sent / Not sent check in the app's dispatch drawer. Nothing here ever
    re evaluates it, expires it, or marks it sent from an archive on its
    own; only that operator action (which flips status to sent or cancelled
    through the app's existing dispatch/dispatch_cancel ops) changes it, and
    a cancelled resolution unblocks a fresh Mark Queued for the same pair
    exactly like any other cancelled row."""
    summary = {"queued": 0, "pulled_seen": 0, "approved": 0, "cancelled": 0,
               "marked_sent": 0, "expired": 0, "ambiguous": 0, "ambiguous_pending": 0,
               "appended": 0, "already_appended": 0, "append_failed": 0,
               "time_budget_hit": False, "append_blackout_skipped": False}
    queued_rows = [r for r in rows if r.get("status") == "queued"]
    pulled_rows_seen = [r for r in rows if r.get("status") == "pulled"]
    # Already flagged ambiguous on a prior run: left alone entirely, forever,
    # until an operator resolves it in the app — see the docstring note
    # above. Excluded from pulled_rows up front so nothing below (sent
    # check, expiry, append) ever looks at it again.
    already_ambiguous = [r for r in pulled_rows_seen if r.get("ambiguous_since")]
    pulled_rows = [r for r in pulled_rows_seen if not r.get("ambiguous_since")]
    summary["queued"] = len(queued_rows)
    summary["pulled_seen"] = len(pulled_rows_seen)
    summary["ambiguous_pending"] = len(already_ambiguous)
    for r in already_ambiguous:
        print("crm_pull: dispatch row %s is ambiguous (flagged %s) — awaiting an operator's "
              "Sent / Not sent check in the app, not touched" % (r.get("id"), r.get("ambiguous_since")))

    queue_path = queue_drafts.QUEUE_PATH
    # Read once, before any sent/expired decision (see (a) above) — reused
    # unchanged by the remaining/append phase further down, since the lock
    # this function runs under means nothing else can touch the file
    # meanwhile.
    known_items = _read_queue_items(queue_path)
    current_ids = {i.get("dispatch_id") for i in known_items if i.get("dispatch_id")}
    archive_stamps = archived_dispatch_id_stamps(queue_path)
    archived_ids = set(archive_stamps)

    already_queued_pulled_ids = {r.get("id") for r in pulled_rows if r.get("id") in current_ids}
    ambiguous_in_queue = [r for r in pulled_rows
                          if r.get("id") in already_queued_pulled_ids and r.get("id") in archived_ids]
    for r in ambiguous_in_queue:
        print("crm_pull: dispatch row %s is in both the queue file and an archive — "
              "ambiguous, flagging it on the server for an operator to check" % r.get("id"))

    sent_candidates = [r for r in pulled_rows
                       if r.get("id") in archived_ids and r.get("id") not in already_queued_pulled_ids]
    to_sent, ambiguous_age = [], []
    for r in sent_candidates:
        pulled_at = _parse_iso(r.get("pulled_at"))
        stamps = [s for s in archive_stamps.get(r.get("id")) or [] if s is not None]
        if pulled_at is not None and stamps and any(s - pulled_at >= MIN_SENT_ARCHIVE_AGE for s in stamps):
            to_sent.append(r)
        else:
            ambiguous_age.append(r)
            print("crm_pull: dispatch row %s archived too close to its own pulled_at to trust — "
                  "ambiguous, flagging it on the server for an operator to check" % r.get("id"))
    newly_ambiguous = ambiguous_in_queue + ambiguous_age
    summary["ambiguous"] = len(newly_ambiguous)

    to_expire = [r for r in pulled_rows
                 if r.get("id") not in archived_ids and r.get("id") not in already_queued_pulled_ids
                 and _is_expired(r, server_now)]
    summary["marked_sent"] = len(to_sent)
    summary["expired"] = len(to_expire)

    if apply_ and to_sent:
        ops = [{"op": "dispatch", "id": r.get("id"), "tenant_id": r.get("tenant_id"),
                "listing_id": r.get("listing_id"), "jid": r.get("jid"), "phone": r.get("phone"),
                "text": r.get("text"), "viewing_slot": r.get("viewing_slot"),
                "status": "sent", "device": r.get("device")} for r in to_sent]
        for chunk in _chunks(ops, CHUNK_SIZE):
            post_ops(user, pw, chunk)

    if apply_ and to_expire:
        ops = [{"op": "dispatch_cancel", "id": r.get("id"), "reason": "expired"} for r in to_expire]
        for chunk in _chunks(ops, CHUNK_SIZE):
            post_ops(user, pw, chunk)

    # Flags each newly ambiguous row on the server exactly once (the "ambiguous"
    # op is itself idempotent server side — see crm.js — but there is no reason
    # to POST it again on a later run once it is set: already_ambiguous above
    # is what keeps a later run from ever reaching this point for the same row).
    if apply_ and newly_ambiguous:
        ops = [{"op": "ambiguous", "id": r.get("id")} for r in newly_ambiguous]
        for chunk in _chunks(ops, CHUNK_SIZE):
            post_ops(user, pw, chunk)

    handled_ids = ({r.get("id") for r in to_sent} | {r.get("id") for r in to_expire}
                   | {r.get("id") for r in newly_ambiguous})
    remaining = [r for r in (queued_rows + pulled_rows) if r.get("id") not in handled_ids]
    if not remaining:
        return summary

    # The one write in this function that can race morning-dispatch.sh's own
    # unlocked load then rename (see the module docstring) — never during
    # the real send window, matching cleanup_cancelled's own blackout.
    if apply_ and _in_blackout(_now_sgt()):
        print("crm_pull: skipping dispatch queue append during the 07:45 to 08:45 send window")
        summary["append_blackout_skipped"] = True
        return summary

    tenants_by_id = queue_drafts.load_tenants_by_id(queue_drafts.TENANT_DB_PATH)
    wa_conn = enrich.open_wa_bridge(queue_drafts.WA_DB_PATH)
    if wa_conn is None:
        print("crm_pull: WhatsApp bridge unavailable — cold check relies on tenant-db last_contact only")
    today = _today_sgt()

    # known_items/current_ids were already read above, before the sent/expired
    # decision (see (a) in the docstring) — reused unchanged here: this loop is
    # the only thing that mutates the queue file for the rest of this run,
    # and it keeps its own in memory copy up to date as it appends, so there
    # is no need to re read the file from disk.
    try:
        for row in remaining:
            rid = row.get("id")
            if rid in current_ids:
                summary["already_appended"] += 1
                continue
            if _time_up(deadline):
                print("crm_pull: time budget exceeded — skipping remaining dispatch row(s)")
                summary["time_budget_hit"] = True
                break
            item = {"tenant_id": row.get("tenant_id"), "phone": row.get("phone") or "",
                    "name": "", "message": row.get("text") or ""}
            # The current queue's own dedupe, minus any item that is this exact
            # row's own — a half finished pass (this row already marked pulled
            # a moment ago in a PRIOR run, its own append never landing) must
            # never see its own leftover state and call itself a duplicate.
            queued_ids = {i.get("tenant_id") for i in known_items
                          if i.get("tenant_id") and i.get("dispatch_id") != rid}
            queued_jids = {i.get("jid") for i in known_items
                           if i.get("jid") and i.get("dispatch_id") != rid}
            approved, refused, skipped, duplicates = queue_drafts.classify_items(
                [item], tenants_by_id, wa_conn, today, queued_ids, queued_jids)
            if approved:
                a = approved[0]
                summary["approved"] += 1
                if not apply_:
                    continue
                pulled_op = {
                    "op": "dispatch", "id": rid, "tenant_id": row.get("tenant_id"),
                    "listing_id": row.get("listing_id"), "jid": a.get("jid"), "phone": row.get("phone"),
                    "text": row.get("text"), "viewing_slot": row.get("viewing_slot"),
                    "status": "pulled", "device": device_name(),
                }
                post_ops(user, pw, [pulled_op])   # queued -> pulled, or pulled -> pulled as a no op
                batch_item = {"tenant_id": a["tenant_id"], "jid": a["jid"], "message": a["message"], "dispatch_id": rid}
                try:
                    queue_drafts.merge_into_queue([batch_item], queue_path, held_lock=lockf)
                except Exception as e:
                    post_ops(user, pw, [{"op": "dispatch_cancel", "id": rid, "reason": "append failed"}])
                    print("crm_pull: append failed (%s) — dispatch row %s cancelled" % (e, rid), file=sys.stderr)
                    summary["append_failed"] += 1
                    continue
                known_items.append(batch_item)
                current_ids.add(rid)
                summary["appended"] += 1
            else:
                summary["cancelled"] += 1
                if not apply_:
                    continue
                reason = (refused or skipped or duplicates)[0][2]
                post_ops(user, pw, [{"op": "dispatch_cancel", "id": rid, "reason": reason}])
    finally:
        if wa_conn:
            wa_conn.close()

    return summary


def run_dispatch_locked(user, pw, apply_, deadline=None):
    """Acquires the exclusive lock on <queue file>.lock BEFORE even fetching
    the CRM snapshot, and holds it until process_dispatch_locked's last
    append or mark — see the module docstring. An overlapping run of this
    script blocks here until this one fully finishes, then fetches its own,
    now current, snapshot.

    The lock is polled with LOCK_NB rather than waited on forever (see
    _acquire_lock) — a run stuck behind a genuinely wedged holder, or just an
    unusually long overlapping one, gives up after LOCK_TIMEOUT_SECONDS and
    logs "queue lock busy, skipping this run" rather than stalling this slot
    indefinitely; whatever it would have done is picked up cleanly next run,
    the same "nothing done is always safe" posture as every other guard in
    this pipeline. Returns (snap, dispatch_summary), or (None, None) on that
    timeout — the caller (main()) treats that as nothing to do this run."""
    queue_path = queue_drafts.QUEUE_PATH
    lock_path = queue_path + ".lock"
    lockf = _acquire_lock(lock_path)
    if lockf is None:
        print("crm_pull: queue lock busy, skipping this run")
        return None, None
    with lockf:
        try:
            snap = fetch_snapshot(user, pw)
            dispatch_rows = snap.get("dispatch") or []
            server_now = _parse_iso(snap.get("server_time"))
            if server_now is None:
                print("crm_pull: server_time missing, expiry disabled")
            d_summary = process_dispatch_locked(dispatch_rows, server_now, user, pw, apply_, lockf, deadline)
        finally:
            fcntl.flock(lockf, fcntl.LOCK_UN)
    return snap, d_summary


def _in_blackout(now_sgt):
    hm = (now_sgt.hour, now_sgt.minute)
    return BLACKOUT_START <= hm <= BLACKOUT_END


def cleanup_cancelled(dispatch_rows, apply_, mtime_at_fetch=_MTIME_UNSET, now=None):
    """Removes any item from the real dispatch queue whose dispatch row has
    since been cancelled — only items that carry a dispatch_id, so a manual
    or another producer's item is never touched. Never runs between 07:45
    and 08:45 Singapore time (the real send window), and otherwise takes its
    own exclusive lock on <queue file>.lock (separate from, and after,
    run_dispatch_locked's own lock above), re reads the queue file under
    that lock, and skips the whole pass if the file's mtime has changed
    since mtime_at_fetch — something else touched it since this run last
    looked (most likely a concurrent process, since this run's own dispatch
    processing has already fully finished and released its lock by the time
    this is called), so this pass defers to the next run rather than read
    modify write against a file it can no longer be sure it understands.
    mtime_at_fetch left at its default disables that specific check, for a
    caller (a test, most likely) that does not care about it.

    This lock is polled with LOCK_NB the same as run_dispatch_locked's own
    (see _acquire_lock) — a busy lock after LOCK_TIMEOUT_SECONDS logs "queue
    lock busy, skipping this run" and this pass is simply skipped, exactly
    like the blackout and changed-file skips already here, never a hang."""
    cancelled_ids = {r.get("id") for r in dispatch_rows if r.get("status") == "cancelled"}
    summary = {"cancelled_seen": len(cancelled_ids), "removed_from_queue": 0,
               "skipped_blackout": False, "skipped_changed": False, "skipped_busy": False}
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
    lockf = _acquire_lock(lock_path)
    if lockf is None:
        summary["skipped_busy"] = True
        print("crm_pull: queue lock busy, skipping this run")
        return summary
    with lockf:
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
        snap, d_summary = run_dispatch_locked(user, pw, args.apply, deadline)
        if snap is None:
            # run_dispatch_locked already logged "queue lock busy, skipping
            # this run" — the lock is <queue file>.lock, the same one
            # cleanup_cancelled needs, so there is nothing safe to do for
            # either dispatch or cleanup this run. Deal import does not
            # touch that lock at all, but skipping it too keeps one run's
            # behaviour simple to reason about: busy means nothing done,
            # picked up cleanly next run, same as every other guard here.
            return 0
        # Captured only now, after run_dispatch_locked has fully finished and
        # released its own lock — see cleanup_cancelled's own docstring for
        # why this is the right moment, not before dispatch processing.
        mtime_at_fetch = os.path.getmtime(queue_drafts.QUEUE_PATH) if os.path.exists(queue_drafts.QUEUE_PATH) else None
        c_summary = cleanup_cancelled(snap.get("dispatch") or [], args.apply, mtime_at_fetch=mtime_at_fetch)
        l_summary = process_deals(snap.get("deals") or [], user, pw, args.apply, deadline)
    except (urllib.error.URLError, OSError, ValueError) as e:
        print("crm_pull: failed (%s) — nothing done" % e, file=sys.stderr)
        return 1

    mode = "apply" if args.apply else "dry run"
    print("crm_pull (%s): dispatch %d queued, %d pulled, %d approved, %d cancelled, "
          "%d marked sent, %d expired, %d newly ambiguous (%d still awaiting operator), "
          "%d appended (%d already, %d append failed"
          "%s) — cleanup removed %d — deals %d completed, %d pending import, %d imported" % (
              mode, d_summary["queued"], d_summary["pulled_seen"], d_summary["approved"],
              d_summary["cancelled"], d_summary["marked_sent"], d_summary["expired"],
              d_summary.get("ambiguous", 0), d_summary.get("ambiguous_pending", 0),
              d_summary.get("appended", 0),
              d_summary.get("already_appended", 0), d_summary.get("append_failed", 0),
              ", blackout" if d_summary.get("append_blackout_skipped") else "",
              c_summary["removed_from_queue"],
              l_summary["completed"], l_summary["pending_import"], l_summary["imported"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
