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
import argparse, datetime, json, os, sys
import enrich

ROOT = os.path.expanduser("~/crestbrick-consult")
TENANT_DB_PATH = os.path.join(ROOT, "_templates/tenant-db.json")
WA_DB_PATH = os.path.expanduser("~/whatsapp-mcp/whatsapp-bridge/store/messages.db")
QUEUE_PATH = os.path.expanduser("~/.claude/state/morning-dispatch-queue.json")
COLD_DAYS = 5


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
    resolved jid; refused = cold rule (or unknown recency, refused to be safe);
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
        # Checked before the cold lookup: an already queued recipient is settled
        # regardless of how recently they were active, and this keeps one
        # exported-twice batch from re-running a bridge query per duplicate.
        if tid in queued_ids or jid in queued_jids:
            duplicates.append((tid, name, "already awaiting send in the dispatch queue — not queueing a second message")); continue
        last_wa, _lang = enrich.fetch_wa_info(wa_conn, jid)
        days = freshest_days(t, last_wa, today)
        if days is None:
            refused.append((tid, name, "no contact date on file — cannot verify recency, refusing")); continue
        if days > COLD_DAYS:
            refused.append((tid, name, f"last activity {days}d ago (>{COLD_DAYS}d cold rule)")); continue
        # One message per recipient per batch: the same tenant queued against
        # two different listings exports as two items with different text, and
        # sending both at 08:00 would still read as a double message.
        queued_ids.add(tid); queued_jids.add(jid)
        approved.append({"tenant_id": tid, "name": name, "phone": phone, "message": message, "jid": jid})
    return approved, refused, skipped, duplicates


def merge_into_queue(to_queue, queue_path):
    """Preserves an existing queue's 'created' timestamp when appending — that
    keeps morning-dispatch.sh's 'did the tenant reply since queued' check
    conservative for every item in the batch, not just the newest ones."""
    if os.path.exists(queue_path):
        q = json.load(open(queue_path))
        q.setdefault("items", [])
    else:
        now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
        q = {"created": now.isoformat(timespec="seconds"), "items": []}
    for a in to_queue:
        q["items"].append({"jid": a["jid"], "tag": "matchmaker", "message": a["message"], "tenant_id": a["tenant_id"]})
    os.makedirs(os.path.dirname(queue_path), exist_ok=True)
    tmp = queue_path + ".tmp"
    json.dump(q, open(tmp, "w"), indent=1, ensure_ascii=False)
    os.replace(tmp, queue_path)


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
    # if it is ever invoked from elsewhere: the 5 day cold-refusal boundary
    # stays anchored to Singapore's calendar day rather than silently
    # inheriting whatever TZ the invoking shell/cron happens to carry.
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).date()
    queued_ids, queued_jids = pending_recipients(QUEUE_PATH)
    approved, refused, skipped, duplicates = classify_items(
        items, tenants_by_id, wa_conn, today, queued_ids, queued_jids)
    if wa_conn: wa_conn.close()

    print(f"\n{len(approved)} ready to queue, {len(refused)} refused (cold rule), "
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

    merge_into_queue(to_queue, QUEUE_PATH)
    print(f"queued {len(to_queue)} item(s) to {QUEUE_PATH}")
    print("nothing was sent — the morning dispatch job (or Winfred, by hand) sends these.")


if __name__ == "__main__":
    main()
