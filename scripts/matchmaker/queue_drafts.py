#!/usr/bin/env python3
"""Take the matchmaker app's exported queue JSON ({"items":[{tenant_id,phone,name,
message}]}) and append approved items to the existing morning dispatch queue
(~/.claude/state/morning-dispatch-queue.json). This script NEVER sends a message
itself — it only appends to the queue file that the existing 08:00 dispatch job
(com.crestbrick single sender) later sends, after Winfred reviews it.

Usage:
  python3 scripts/matchmaker/queue_drafts.py exported-queue.json
  cat exported-queue.json | python3 scripts/matchmaker/queue_drafts.py [--yes]
"""
import argparse, datetime, fcntl, json, os, sys, time
import enrich

ROOT = os.path.expanduser("~/crestbrick-consult")
TENANT_DB_PATH = os.path.join(ROOT, "_templates/tenant-db.json")
WA_DB_PATH = os.path.expanduser("~/whatsapp-mcp/whatsapp-bridge/store/messages.db")
QUEUE_PATH = os.path.expanduser("~/.claude/state/morning-dispatch-queue.json")
LOCK_TIMEOUT_SECONDS = 120     # give up waiting for <queue file>.lock rather than block forever
LOCK_POLL_SECONDS = 0.5        # LOCK_NB poll interval while waiting for the lock


def _acquire_lock(lock_path, timeout=None, poll=None):
    """Opens lock_path and takes the exclusive fcntl lock, polling with
    LOCK_NB rather than blocking forever — the same shape as crm_pull.py's
    own _acquire_lock (duplicated here rather than imported, since crm_pull
    imports this module, not the other way around). Gives up after timeout
    seconds and returns None; the caller logs "queue lock busy, skipping
    this run" and treats that as nothing to do, never as an error. Returns
    the open, locked file object on success — the caller owns unlocking and
    closing it.

    timeout/poll default to the LOCK_TIMEOUT_SECONDS/LOCK_POLL_SECONDS
    MODULE GLOBALS, read at call time rather than bound as ordinary default
    argument values (which Python evaluates once, at function definition
    time) — a test overriding those globals to run this on a short fuse
    must actually take effect on every call."""
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
# Named DEAD, not COLD, on purpose — mirroring scoring.js's split of the same two
# ideas. Collapsing them onto one constant is a bug this codebase has already had
# once: the app hard blocked outreach at the COLD value and gagged 155 of 218 tenants
# when only 72 were actually dead (see scoring.js:63). That split landed 13 Aug 2026
# but never reached this file, which kept hard refusing at 5 days while Winfred's
# real dead-lead rule had been widened to 30 on 12 Aug — so from 12 to 17 Aug it
# silently declined to draft to anyone quiet 6+ days, leads he still considers live.
# It erred safe (refusing more than policy requires, never less), which is why it
# went unnoticed. Cold is a ranking signal; only DEAD may block an action.
# Landlords/co-broke are exempt from the rule but never reach this path — the
# matchmaker queue carries tenants only.
# Read from config.json (single home for the thresholds, 21 Aug 2026) — this
# file holding its own literal is exactly how it sat at 5 for five days after
# the rule moved to 30, and at 30 for a day after the rule moved to 45.
DEAD_DAYS = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "config.json")))["dead_days"]


def freshest_days(tenant, last_wa, today):
    """Most recent of tenant-db last_contact and the WA bridge's last_wa.ts.
    None means we truly have no signal (never seen this tenant active)."""
    candidates = []
    lc = enrich.norm_date(tenant.get("last_contact"))
    if lc: candidates.append(lc)
    if last_wa and last_wa.get("ts"):
        d = enrich.norm_date(str(last_wa["ts"])[:10])
        if d: candidates.append(d)
    if not candidates:
        return None
    best = max(candidates)  # YYYY-MM-DD strings sort lexicographically -> max = most recent
    try:
        return (today - datetime.date.fromisoformat(best)).days
    except ValueError:
        return None


def load_tenants_by_id(path):
    tdb = json.load(open(path))
    records = tdb.get("tenants") or tdb.get("records") or []
    return {t.get("id"): t for t in records}


def pending_recipients(queue_path):
    """(tenant_ids, jids) already sitting in the morning dispatch queue, waiting
    to be sent. Deliberately NOT filtered by tag: a tenant already queued by any
    producer must not also receive a matchmaker message in the same 08:00 batch.

    This exists because nothing upstream clears a "Queued" mark once its draft
    has been exported — the app's dispatch drawer keeps showing every Queued
    pair, so exporting again after queueing three more tenants re-exports the
    ones already handed over, and without this check they would be appended (and
    therefore SENT) a second time. A corrupt/unreadable queue returns empty
    rather than raising: failing to parse it must not block queueing, and
    merge_into_queue re-reads it anyway."""
    if not os.path.exists(queue_path):
        return set(), set()
    try:
        q = json.load(open(queue_path))
    except (ValueError, OSError):
        print(f"warning: could not read {queue_path} for a duplicate check — "
              "proceeding without it, review the queue file by hand before 08:00")
        return set(), set()
    items = q.get("items") or []
    return ({i.get("tenant_id") for i in items if i.get("tenant_id")},
            {i.get("jid") for i in items if i.get("jid")})


def classify_items(items, tenants_by_id, wa_conn, today, queued_ids=None, queued_jids=None):
    """Returns (approved, refused, skipped, duplicates). approved carries the
    resolved jid; refused = dead-lead rule (or unknown recency, refused to be safe);
    skipped = we could not identify a real tenant/jid at all (never guessed);
    duplicates = this recipient is already awaiting send, so queueing again
    would double message them."""
    queued_ids = set(queued_ids or ())
    queued_jids = set(queued_jids or ())
    approved, refused, skipped, duplicates = [], [], [], []
    for item in items:
        tid = item.get("tenant_id")
        name = item.get("name") or "(no name)"
        phone = item.get("phone") or "?"
        message = item.get("message") or ""
        t = tenants_by_id.get(tid)
        if not t:
            skipped.append((tid, name, "tenant id not found in tenant-db")); continue
        jid = t.get("jid")
        if not jid:
            skipped.append((tid, name, "no jid on file for this tenant — never guessing")); continue
        # Checked before the recency lookup: an already queued recipient is settled
        # regardless of how recently they were active, and this keeps one
        # exported-twice batch from re-running a bridge query per duplicate.
        if tid in queued_ids or jid in queued_jids:
            duplicates.append((tid, name, "already awaiting send in the dispatch queue — not queueing a second message")); continue
        last_wa, _lang = enrich.fetch_wa_info(wa_conn, jid)
        days = freshest_days(t, last_wa, today)
        if days is None:
            refused.append((tid, name, "no contact date on file — cannot verify recency, refusing")); continue
        if days > DEAD_DAYS:
            refused.append((tid, name, f"last activity {days}d ago (>{DEAD_DAYS}d dead-lead rule)")); continue
        # One message per recipient per batch: the same tenant queued against
        # two different listings exports as two items with different text, and
        # sending both at 08:00 would still read as a double message.
        queued_ids.add(tid); queued_jids.add(jid)
        approved.append({"tenant_id": tid, "name": name, "phone": phone, "message": message, "jid": jid})
    return approved, refused, skipped, duplicates


def merge_into_queue(to_queue, queue_path, held_lock=None):
    """Preserves an existing queue's 'created' timestamp when appending — that
    keeps morning-dispatch.sh's 'did the tenant reply since queued' check
    conservative for every item in the batch, not just the newest ones.

    Takes an exclusive fcntl lock on <queue_path>.lock for the read modify
    write, so this manual/CLI path is protected the same way crm_pull.py's
    own dispatch processing is. Pass held_lock (an already open, already
    locked file object) when the caller — crm_pull.py, running its own
    fetch-through-append sequence under one lock end to end — already holds
    it: this makes the call a no op on locking and reuses that lock instead
    of trying to acquire a second one on the same file from the same run,
    which would either be pointless (fcntl allows a process to re-lock a
    file it already holds) or, if flock semantics ever differ across
    platforms enough to matter, could pointlessly block."""
    def _do_merge():
        if os.path.exists(queue_path):
            q = json.load(open(queue_path))
            q.setdefault("items", [])
        else:
            now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
            q = {"created": now.isoformat(timespec="seconds"), "items": []}
        for a in to_queue:
            item = {"jid": a["jid"], "tag": "matchmaker", "message": a["message"], "tenant_id": a["tenant_id"]}
            # Set only by crm_pull.py (CRM pull bridge): the crm_dispatch row id
            # this item came from, so a later run can tell "already delivered"
            # apart from "still pulled but never actually appended" and prune a
            # cancelled row's item back out before it sends. Absent for every
            # other producer of this file (the manual queue_drafts.py flow
            # included), so their items keep exactly the 4 fields they always had.
            if a.get("dispatch_id"):
                item["dispatch_id"] = a["dispatch_id"]
            q["items"].append(item)
        os.makedirs(os.path.dirname(queue_path), exist_ok=True)
        tmp = queue_path + ".tmp"
        json.dump(q, open(tmp, "w"), indent=1, ensure_ascii=False)
        os.replace(tmp, queue_path)

    if held_lock is not None:
        _do_merge()
        return
    lock_path = queue_path + ".lock"
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    with open(lock_path, "a+") as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX)
        try:
            _do_merge()
        finally:
            fcntl.flock(lockf, fcntl.LOCK_UN)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", nargs="?", help="queue export JSON path (default: read stdin)")
    ap.add_argument("--yes", action="store_true", help="queue every approved item after the preview, no per item prompt")
    ap.add_argument("--dry-run", action="store_true",
                     help="preview only: prints the same refusal/skip/ready breakdown plus exactly what "
                          "would be appended to the queue file, never prompts, never writes")
    args = ap.parse_args()

    raw = open(args.path).read() if args.path else sys.stdin.read()
    try:
        payload = json.loads(raw)
    except ValueError as e:
        print(f"queue_drafts: input is not valid JSON ({e})", file=sys.stderr); sys.exit(1)
    items = payload.get("items") or []
    if not items:
        print("queue_drafts: no items in input — nothing to do"); return

    tenants_by_id = load_tenants_by_id(TENANT_DB_PATH)
    wa_conn = enrich.open_wa_bridge(WA_DB_PATH)
    if wa_conn is None:
        print("warning: WhatsApp bridge unavailable — cold check relies on tenant-db last_contact only")

    # Explicit +08:00, matching build.py/export_data.py's own generated_ts
    # convention — correct on the SGT machine this normally runs on (launchd,
    # per crestbrick-consult's WA pipeline deploy model), and degrades sanely
    # if it is ever invoked from elsewhere: the DEAD_DAYS refusal boundary
    # stays anchored to Singapore's calendar day rather than silently
    # inheriting whatever TZ the invoking shell/cron happens to carry.
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).date()

    # The duplicate set (pending_recipients) must be read under a lock that
    # also protects a write — otherwise a second CLI run, or crm_pull.py's
    # own append, can slot a row in between a read and a write and the same
    # tenant reaches the queue twice. This used to mean one lock held from
    # before classify_items right through the (optional) per item
    # confirmation prompt below — but that prompt waits on a human, for as
    # long as a human takes, and every other writer (crm_pull.py's own timed
    # slots included) would sit blocked on this lock the whole time a queue
    # is sitting at a prompt. So the lock is now taken twice instead: once,
    # briefly, for the classify pass below (fifth review round), released
    # before the prompt ever runs; and again, only around the final re
    # check and the append itself, once an operator has actually answered.
    # A lock busy past LOCK_TIMEOUT_SECONDS either time is never worth
    # waiting out — see _acquire_lock — this run just logs and gives up,
    # nothing done, exactly as safe as if it had never started.
    lock_path = QUEUE_PATH + ".lock"
    lockf = _acquire_lock(lock_path)
    if lockf is None:
        print("queue lock busy, skipping this run"); return
    try:
        queued_ids, queued_jids = pending_recipients(QUEUE_PATH)
        approved, refused, skipped, duplicates = classify_items(
            items, tenants_by_id, wa_conn, today, queued_ids, queued_jids)
    finally:
        fcntl.flock(lockf, fcntl.LOCK_UN)
        lockf.close()
    if wa_conn: wa_conn.close()

    print(f"\n{len(approved)} ready to queue, {len(refused)} refused (dead-lead rule), "
          f"{len(duplicates)} already queued, {len(skipped)} skipped (no id/jid)\n")
    for tid, name, reason in skipped:
        print(f"  SKIP    {name} ({tid}): {reason}")
    for tid, name, reason in refused:
        print(f"  REFUSE  {name} ({tid}): {reason}")
    for tid, name, reason in duplicates:
        print(f"  DUP     {name} ({tid}): {reason}")
    for a in approved:
        print(f"  READY   {a['name']}  {a['phone']}\n          {a['message']}\n")

    if not approved:
        print("nothing to queue."); return

    if args.dry_run:
        # Never touches QUEUE_PATH and never calls input() — the whole point of
        # --dry-run is a safe preview of exactly what a real run would append,
        # in the same per-item shape merge_into_queue() itself writes (see its
        # own q["items"].append(...) call below), without risking a stray
        # Enter keypress queuing something for real during a rehearsal.
        print(f"DRY RUN — would append {len(approved)} item(s) to {QUEUE_PATH} (nothing written, nothing sent):\n")
        for a in approved:
            would_append = {"jid": a["jid"], "tag": "matchmaker", "message": a["message"], "tenant_id": a["tenant_id"]}
            print("  WOULD APPEND " + json.dumps(would_append, ensure_ascii=False))
        return

    # No lock held here: this is exactly the interactive y/N prompt (or, with
    # --yes, an instant pass-through) that must never keep another writer
    # blocked on the queue lock while it waits on a human.
    if args.yes:
        to_queue = approved
    else:
        to_queue = []
        for a in approved:
            ans = input(f"Queue to {a['name']} ({a['phone']})? [y/N]: ").strip().lower()
            if ans in ("y", "yes"):
                to_queue.append(a)

    if not to_queue:
        print("nothing confirmed — queue unchanged."); return

    # Re acquire the lock and re check duplicates before the real append —
    # the queue file (and therefore what counts as a duplicate) can have
    # changed while this run sat at the prompt: crm_pull.py's own append, or
    # another queue_drafts.py invocation, may have queued the same tenant or
    # jid in the meantime. A row that changed is never appended anyway — it
    # is reported and dropped, not silently sent a second time.
    lockf2 = _acquire_lock(lock_path)
    if lockf2 is None:
        print("queue lock busy, skipping this run"); return
    try:
        queued_ids2, queued_jids2 = pending_recipients(QUEUE_PATH)
        changed = [a for a in to_queue if a["tenant_id"] in queued_ids2 or a["jid"] in queued_jids2]
        still_ok = [a for a in to_queue if a not in changed]
        if changed:
            print(f"\n{len(changed)} row(s) became duplicates while waiting for confirmation — not appending them:")
            for a in changed:
                print(f"  ABORT   {a['name']} ({a['phone']}): now already queued elsewhere, since this run started")
        if not still_ok:
            print("nothing left to queue after re checking duplicates."); return
        merge_into_queue(still_ok, QUEUE_PATH, held_lock=lockf2)
        print(f"queued {len(still_ok)} item(s) to {QUEUE_PATH}")
        print("nothing was sent — the morning dispatch job (or Winfred, by hand) sends these.")
    finally:
        fcntl.flock(lockf2, fcntl.LOCK_UN)
        lockf2.close()


if __name__ == "__main__":
    main()
