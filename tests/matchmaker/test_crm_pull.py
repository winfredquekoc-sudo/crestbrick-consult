#!/usr/bin/env python3
"""Pure assert suite for scripts/matchmaker/crm_pull.py — the CRM pull bridge.
No pytest, stdlib only:
    /usr/bin/python3 tests/matchmaker/test_crm_pull.py

Runs crm_pull.main() against a fake CRM API (an in memory stand in for
fetch_snapshot/post_ops, since a real network call has no place in a test)
and a throwaway sqlite clients.db. There is no ledger any more (PR #132
third rework): the record of what was actually appended is the queue file
plus its .done-* archives, and a pulled row's age is judged only against the
CRM API's own server_time, never the Mac's local clock. Covers:
  (a) a crash after marking a row pulled but before appending it is
      recovered by the next run, appended exactly once
  (b) a crash after the append itself never double appends and never
      cancels on the next run
  (c) a row that has been through the real 08:00 archive and two further
      rotations is never appended again and is marked sent, even once it
      is older than the 7 day expiry window
  (d) cancelling a row after it was pulled prunes its queue item once
  (e) 212 dispatch rows needing a sent/expired mark in one run still POST
      in chunks of 50
  (f) a cancelled row re queued as a brand new dispatch id (the app never
      reuses a cancelled row's own id) reaches the real queue exactly once
  (g) a pulled row absent from both the queue file and every archive is
      recovered and appended once, regardless of its age, as long as it is
      under the 7 day expiry window
  (h) a recovered row that has since gone dead is cancelled with the dead
      lead reason, never appended
  (i) the queue file disappearing mid run cancels nothing — the row is
      still appended (recreating the file), never blamed on the missing file
  (j) two overlapping runs (the second genuinely blocked on the queue file's
      lock, not just interleaved by chance) append the same row exactly once
  (k) skewing the Mac's own clock 3 hours either way changes nothing about
      which pulled rows are judged expired — only server_time decides that
plus the original dead lead / accepted draft / non completed deal / missing
deals table / blackout / time budget cases from earlier review rounds.

All fixture people are invented (SG plausible, obviously fake).
"""
import contextlib
import datetime
import fcntl
import io
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "..", "scripts", "matchmaker")))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "..", "scripts", "ops")))
import crm_pull  # noqa: E402
import queue_drafts  # noqa: E402
import log_deal  # noqa: E402

FAILED = []


def check(name, fn):
    try:
        fn()
        print(f"  ok   {name}")
    except AssertionError as e:
        FAILED.append(name)
        print(f"  FAIL {name}: {e}")


# ------------------------------------------------------------- fixtures ----
def today_sgt():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).date().isoformat()


def days_ago_sgt(n):
    d = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).date()
    return (d - datetime.timedelta(days=n)).isoformat()


class FakeServer:
    """Stands in for the CRM API: fetch_snapshot()/post_ops() are pointed at
    this instead of a real network call. apply_ops() mirrors deploy/api/
    crm.js's own "dispatch"/"dispatch_cancel"/"activity" op handling —
    including nextDispatchStatus's transition table (queued moves anywhere,
    pulled only ever moves on to sent, cancelled only ever moves back to
    queued, sent is terminal) and stamping pulled_at fresh only when a row
    newly BECOMES pulled — closely enough for this script's own logic to be
    exercised end to end against realistic server responses.

    server_time is its own clock, independent of the real wall clock and of
    crm_pull.py's own _now_sgt() (the Mac's clock) — exactly the separation
    the real API/Mac pair has, and what test (k) below exists to prove
    actually matters."""

    def __init__(self):
        self.dispatch = {}
        self.deals = {}
        self.activity = []
        self.posted_ops = []
        self.server_time = datetime.datetime.now(datetime.timezone.utc)

    def snapshot(self):
        return {
            "dispatch": [dict(r) for r in self.dispatch.values() if r["status"] in ("queued", "pulled", "cancelled")],
            "deals": [dict(d) for d in self.deals.values()],
            "server_time": self.server_time.isoformat().replace("+00:00", "Z"),
        }

    def _next_status(self, current, incoming):
        if current is None:
            return incoming
        if current == "sent":
            return "sent"
        if current == "queued":
            return incoming
        if current == "pulled":
            return "sent" if incoming == "sent" else current
        if current == "cancelled":
            return "queued" if incoming == "queued" else current
        return current

    def apply_ops(self, ops):
        self.posted_ops.extend(ops)
        for o in ops:
            if o["op"] == "dispatch":
                row = self.dispatch.get(o["id"])
                current = row["status"] if row else None
                next_status = self._next_status(current, o["status"])
                if row is None:
                    row = {"id": o["id"]}
                    self.dispatch[o["id"]] = row
                row.update({k: v for k, v in o.items() if k not in ("op", "status")})
                row["status"] = next_status
                if next_status == "pulled" and current != "pulled":
                    row["pulled_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            elif o["op"] == "dispatch_cancel":
                row = self.dispatch.get(o["id"])
                if row and row["status"] != "sent":
                    row["status"] = "cancelled"
            elif o["op"] == "activity":
                self.activity.append(o)
        return {"ok": True, "applied": len(ops), "dispatch": [dict(r) for r in self.dispatch.values()]}


def make_deals_db(path):
    con = sqlite3.connect(path)
    con.execute("""
        CREATE TABLE deals (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          client_slug TEXT,
          deal_type TEXT,
          property_address TEXT,
          price REAL,
          commission_gross REAL,
          commission_net REAL,
          cobroke_agent TEXT,
          cobroke_split_pct REAL,
          cobroke_amount REAL,
          stage TEXT,
          otp_date TEXT,
          completion_date TEXT,
          deal_date TEXT,
          closed_date TEXT,
          notes TEXT,
          created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    con.commit()
    con.close()


class Fixture:
    """One full environment: a temp tenant-db.json, a temp morning dispatch
    queue path, a temp clients.db, and a fresh FakeServer — plus the module
    level monkeypatches crm_pull/queue_drafts/log_deal need to use them
    instead of Winfred's real files. restore() undoes every patch."""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="mm-crm-pull-test-")
        self.tenant_db_path = os.path.join(self.dir, "tenant-db.json")
        self.queue_path = os.path.join(self.dir, "morning-dispatch-queue.json")
        self.clients_db_path = os.path.join(self.dir, "clients.db")
        make_deals_db(self.clients_db_path)

        tenants = [
            {"id": "T1", "name": "Tenant One", "phone": "91111111",
             "jid": "6591111111@s.whatsapp.net", "last_contact": today_sgt()},
            {"id": "T2", "name": "Tenant Two", "phone": "92222222",
             "jid": "6592222222@s.whatsapp.net", "last_contact": days_ago_sgt(100)},
        ]
        with open(self.tenant_db_path, "w") as f:
            json.dump({"tenants": tenants}, f)

        self.server = FakeServer()
        self.server.dispatch = {
            "dispatch_L1_T1": {
                "id": "dispatch_L1_T1", "tenant_id": "T1", "listing_id": "L1", "jid": None,
                "phone": "91111111", "text": "Hi Tenant One, a unit just opened up",
                "viewing_slot": None, "status": "queued", "device": None,
            },
            "dispatch_L2_T2": {
                "id": "dispatch_L2_T2", "tenant_id": "T2", "listing_id": "L2", "jid": None,
                "phone": "92222222", "text": "Hi Tenant Two, a unit just opened up",
                "viewing_slot": None, "status": "queued", "device": None,
            },
        }
        self.server.deals = {
            "deal_abc1": {
                "id": "deal_abc1", "key": None, "deal_type": "rental", "property": "123 Test Rd #01-01",
                "price": 3000, "commission_gross": 1500, "commission_net": 1500,
                "cobroke_agent": None, "cobroke_split_pct": None, "stage": "completed",
                "otp_date": None, "completion_date": "2026-09-01", "deal_date": "2026-09-01",
                "notes": "test fixture deal", "created_at": "2026-09-01T00:00:00Z",
            },
            "deal_def2": {
                "id": "deal_def2", "key": None, "deal_type": "sale", "property": "456 Test Ave",
                "price": 900000, "commission_gross": 9000, "commission_net": 9000,
                "cobroke_agent": None, "cobroke_split_pct": None, "stage": "agreed",
                "otp_date": None, "completion_date": None, "deal_date": None,
                "notes": "still in progress, must not import", "created_at": "2026-09-05T00:00:00Z",
            },
        }

        self._orig = {
            "TENANT_DB_PATH": queue_drafts.TENANT_DB_PATH,
            "WA_DB_PATH": queue_drafts.WA_DB_PATH,
            "QUEUE_PATH": queue_drafts.QUEUE_PATH,
            "DB_PATH": log_deal.DB_PATH,
            "BACKUP_DIR": log_deal.BACKUP_DIR,
            "fetch_snapshot": crm_pull.fetch_snapshot,
            "post_ops": crm_pull.post_ops,
            "_now_sgt": crm_pull._now_sgt,
            "MM_USER": os.environ.get("MM_USER"),
            "MM_PASS": os.environ.get("MM_PASS"),
        }
        queue_drafts.TENANT_DB_PATH = self.tenant_db_path
        queue_drafts.WA_DB_PATH = os.path.join(self.dir, "no-such-wa-bridge.db")
        queue_drafts.QUEUE_PATH = self.queue_path
        log_deal.DB_PATH = self.clients_db_path
        log_deal.BACKUP_DIR = os.path.join(self.dir, "backups")
        crm_pull.fetch_snapshot = lambda user, pw: self.server.snapshot()
        crm_pull.post_ops = lambda user, pw, ops: self.server.apply_ops(ops)
        # A fixed, safe (well outside 07:45-08:45) Singapore time for every
        # cleanup_cancelled call this fixture drives through main() — without
        # this, a test run happening to land inside the real send window
        # would make cleanup skip everywhere for a reason that has nothing to
        # do with the code under test.
        safe_time = datetime.datetime(2026, 9, 16, 12, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=8)))
        crm_pull._now_sgt = lambda: safe_time
        os.environ["MM_USER"] = "test-user-placeholder"
        os.environ["MM_PASS"] = "test-pass-placeholder"

    def restore(self):
        queue_drafts.TENANT_DB_PATH = self._orig["TENANT_DB_PATH"]
        queue_drafts.WA_DB_PATH = self._orig["WA_DB_PATH"]
        queue_drafts.QUEUE_PATH = self._orig["QUEUE_PATH"]
        log_deal.DB_PATH = self._orig["DB_PATH"]
        log_deal.BACKUP_DIR = self._orig["BACKUP_DIR"]
        crm_pull.fetch_snapshot = self._orig["fetch_snapshot"]
        crm_pull.post_ops = self._orig["post_ops"]
        crm_pull._now_sgt = self._orig["_now_sgt"]
        for k in ("MM_USER", "MM_PASS"):
            if self._orig[k] is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = self._orig[k]

    def add_tenant(self, tid, phone, jid, last_contact=None):
        tdb = json.load(open(self.tenant_db_path))
        tdb["tenants"].append({"id": tid, "name": "Bulk Tenant " + tid, "phone": phone,
                                "jid": jid, "last_contact": last_contact or today_sgt()})
        json.dump(tdb, open(self.tenant_db_path, "w"))

    def queue_items(self):
        if not os.path.exists(self.queue_path):
            return []
        return json.load(open(self.queue_path)).get("items", [])

    def write_archive(self, suffix, dispatch_ids):
        """Writes a .done-<suffix> archive next to the queue file, containing
        one item per dispatch id — mirrors what the real morning dispatch job
        leaves behind once it actually sends a batch."""
        path = self.queue_path + ".done-" + suffix
        items = [{"jid": "x@s.whatsapp.net", "tag": "matchmaker", "message": "sent",
                  "tenant_id": "Tx", "dispatch_id": d} for d in dispatch_ids]
        json.dump({"created": "2026-09-01T00:00:00+08:00", "items": items}, open(path, "w"))
        return path

    def deal_rows(self):
        con = sqlite3.connect(self.clients_db_path)
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute("SELECT * FROM deals").fetchall()]
        con.close()
        return rows


def pulled_at_from_server(fx, days_ago=0, hours_ago=0):
    """A pulled_at timestamp expressed relative to the FakeServer's OWN
    clock, never the real wall clock — every expiry decision has to be
    judged against server_time, so fixtures built against anything else
    would not actually be testing that rule."""
    dt = fx.server.server_time - datetime.timedelta(days=days_ago, hours=hours_ago)
    return dt.isoformat()


def archive_stamp(fx, hours_from_now=0, minutes_from_now=0):
    """A .done-<stamp> filename suffix expressed relative to the FakeServer's
    OWN clock, in the SGT form crm_pull.py's own archive stamp parser
    expects (morning-dispatch.sh's own now.strftime('%Y%m%d%H%M')) — never a
    fixed calendar string, which drifts stale (and can flip a fixture from
    "safely sent" to "ambiguous" under the archive age rule) the moment this
    suite runs on a real date after the string's own."""
    dt = (fx.server.server_time + datetime.timedelta(hours=hours_from_now, minutes=minutes_from_now)
          ).astimezone(datetime.timezone(datetime.timedelta(hours=8)))
    return dt.strftime("%Y%m%d%H%M")


# ------------------------------------------------------------------ tests ---
def t_no_credentials_is_a_no_op():
    saved_user = os.environ.pop("MM_USER", None)
    saved_pass = os.environ.pop("MM_PASS", None)
    try:
        rc = crm_pull.main(["--apply"])
        assert rc == 0
    finally:
        if saved_user is not None:
            os.environ["MM_USER"] = saved_user
        if saved_pass is not None:
            os.environ["MM_PASS"] = saved_pass
check("main(): with no MM_USER/MM_PASS set, exits 0 and does nothing", t_no_credentials_is_a_no_op)


def t_dry_run_writes_nothing():
    fx = Fixture()
    try:
        rc = crm_pull.main([])
        assert rc == 0
        assert fx.queue_items() == [], "dry run must never write the dispatch queue"
        assert fx.deal_rows() == [], "dry run must never write clients.db"
        assert fx.server.posted_ops == [], "dry run must never POST anything"
        assert fx.server.dispatch["dispatch_L1_T1"]["status"] == "queued"
    finally:
        fx.restore()
check("main() dry run: reports without writing the queue, clients.db or the API", t_dry_run_writes_nothing)


def t_dead_lead_refused_and_cancelled_with_reason():
    fx = Fixture()
    try:
        crm_pull.main(["--apply"])
        row = fx.server.dispatch["dispatch_L2_T2"]
        assert row["status"] == "cancelled", "a dead (100 day old) lead must never be queued for send"
        cancel_ops = [o for o in fx.server.posted_ops if o["op"] == "dispatch_cancel" and o["id"] == "dispatch_L2_T2"]
        assert len(cancel_ops) == 1
        assert cancel_ops[0]["reason"], "a cancelled row must carry a real reason, not a blank one"
        ids_in_queue = [i["tenant_id"] for i in fx.queue_items()]
        assert "T2" not in ids_in_queue, "a refused dead lead must never reach the real dispatch queue"
    finally:
        fx.restore()
check("crm_pull --apply: a dead lead dispatch row is refused and cancelled with a reason", t_dead_lead_refused_and_cancelled_with_reason)


def t_accepted_draft_appended_once_and_marked_pulled():
    fx = Fixture()
    try:
        crm_pull.main(["--apply"])
        items = fx.queue_items()
        matches = [i for i in items if i["tenant_id"] == "T1"]
        assert len(matches) == 1, "the fresh tenant's draft should be appended exactly once"
        assert matches[0]["jid"] == "6591111111@s.whatsapp.net"
        assert matches[0]["dispatch_id"] == "dispatch_L1_T1", "every appended item must carry its dispatch id"
        assert fx.server.dispatch["dispatch_L1_T1"]["status"] == "pulled"
    finally:
        fx.restore()
check("crm_pull --apply: an accepted draft is appended to the queue once, tagged with its dispatch id and marked pulled", t_accepted_draft_appended_once_and_marked_pulled)


def t_second_run_appends_nothing_more():
    fx = Fixture()
    try:
        crm_pull.main(["--apply"])
        first_items = fx.queue_items()
        crm_pull.main(["--apply"])
        second_items = fx.queue_items()
        assert second_items == first_items, "a second run must never double append the dispatch queue"
    finally:
        fx.restore()
check("crm_pull --apply: run twice, the second run appends nothing more to the queue", t_second_run_appends_nothing_more)


# ---- (a) crash after mark, before append: recovered and appended once -----
def t_crash_after_mark_before_append_recovers_once():
    fx = Fixture()
    try:
        # Simulate the process dying right between the two steps: the mark
        # pulled write landed and was confirmed, but the append never ran.
        fx.server.dispatch["dispatch_L1_T1"]["status"] = "pulled"
        fx.server.dispatch["dispatch_L1_T1"]["pulled_at"] = pulled_at_from_server(fx)
        assert fx.queue_items() == [], "nothing should be appended yet — this is the simulated crash point"

        # A full, ordinary second run must recover the still pulled, not yet
        # appended row — its id is in neither the queue file nor any archive
        # — and append it exactly once overall, not zero and not twice.
        crm_pull.main(["--apply"])
        matches = [i for i in fx.queue_items() if i["tenant_id"] == "T1"]
        assert len(matches) == 1, "the crash recovered draft must be appended exactly once overall"

        crm_pull.main(["--apply"])
        matches_again = [i for i in fx.queue_items() if i["tenant_id"] == "T1"]
        assert len(matches_again) == 1, "a third run changes nothing further"
    finally:
        fx.restore()
check("(a) crash after mark, before append: the next run recovers and appends exactly once", t_crash_after_mark_before_append_recovers_once)


# ---- (b) crash after the append itself -------------------------------------
def t_crash_after_append_never_double_appends_or_cancels():
    fx = Fixture()
    try:
        # The append has already actually happened: both the mark pulled
        # write and the queue file write landed. There is no ledger step
        # left to crash on after this — the queue file itself is the record.
        fx.server.dispatch["dispatch_L1_T1"]["status"] = "pulled"
        fx.server.dispatch["dispatch_L1_T1"]["pulled_at"] = pulled_at_from_server(fx)
        queue_drafts.merge_into_queue(
            [{"tenant_id": "T1", "jid": "6591111111@s.whatsapp.net", "message": "already appended",
              "dispatch_id": "dispatch_L1_T1"}],
            fx.queue_path)

        crm_pull.main(["--apply"])
        matches = [i for i in fx.queue_items() if i["tenant_id"] == "T1"]
        assert len(matches) == 1, "a row already in the queue file must never be appended a second time"
        assert fx.server.dispatch["dispatch_L1_T1"]["status"] == "pulled", \
            "a row that was already fully delivered must not be cancelled just for being seen again"
        cancel_ops = [o for o in fx.server.posted_ops if o["op"] == "dispatch_cancel" and o["id"] == "dispatch_L1_T1"]
        assert cancel_ops == [], "nothing about seeing an already appended row again should ever cancel it"
    finally:
        fx.restore()
check("(b) crash after the append: the next run appends nothing more and cancels nothing", t_crash_after_append_never_double_appends_or_cancels)


# ---- (c) 08:00 archived, then two further rotations: sent, never re appended
def t_archived_row_survives_further_rotations_and_is_marked_sent():
    fx = Fixture()
    try:
        # The row was genuinely pulled and sent well over EXPIRY_DAYS ago —
        # proving the sent marking comes from the archive match itself, not
        # from age, and that an archived row is never instead judged expired
        # even though it is well past the 7 day window.
        old_pulled_at = pulled_at_from_server(fx, days_ago=crm_pull.EXPIRY_DAYS + 3)
        fx.server.dispatch["dispatch_L1_T1"]["status"] = "pulled"
        fx.server.dispatch["dispatch_L1_T1"]["pulled_at"] = old_pulled_at
        fx.write_archive(archive_stamp(fx, hours_from_now=-(crm_pull.EXPIRY_DAYS + 2) * 24), ["dispatch_L1_T1"])  # the real 08:00 send
        fx.write_archive(archive_stamp(fx, hours_from_now=-(crm_pull.EXPIRY_DAYS + 1) * 24), [])                  # rotation 1, unrelated
        fx.write_archive(archive_stamp(fx, hours_from_now=-crm_pull.EXPIRY_DAYS * 24), [])                        # rotation 2, unrelated — now the newest

        crm_pull.main(["--apply"])

        matches = [i for i in fx.queue_items() if i.get("dispatch_id") == "dispatch_L1_T1"]
        assert matches == [], "an already sent row must never be appended again"
        assert fx.server.dispatch["dispatch_L1_T1"]["status"] == "sent", \
            "a row found in an archive that is no longer the newest one, and well past the expiry window, must still be marked sent, not expired"
    finally:
        fx.restore()
check("(c) a row archived at 08:00, surviving two further rotations, is never re appended and ends sent", t_archived_row_survives_further_rotations_and_is_marked_sent)


# ---- (d) cancel after pull, before 08:00: pruned once ----------------------
def t_cancel_after_pull_prunes_queue_item_once():
    fx = Fixture()
    try:
        crm_pull.main(["--apply"])  # T1 pulled and appended
        assert any(i.get("dispatch_id") == "dispatch_L1_T1" for i in fx.queue_items())
        # Something outside crm_pull.py (a future cancel path, or Winfred
        # editing the CRM directly) cancels the row after it was appended,
        # well outside the 07:45-08:45 blackout window this test runs in.
        fx.server.dispatch["dispatch_L1_T1"]["status"] = "cancelled"
        crm_pull.main(["--apply"])
        items = fx.queue_items()
        assert all(i.get("dispatch_id") != "dispatch_L1_T1" for i in items), \
            "a cancelled row's queue item must be pruned before the real 08:00 send"
        crm_pull.main(["--apply"])
        assert all(i.get("dispatch_id") != "dispatch_L1_T1" for i in fx.queue_items()), "pruning again is a no op, not an error"
    finally:
        fx.restore()
check("(d) cancelling a row after it was pulled prunes its queue item exactly once", t_cancel_after_pull_prunes_queue_item_once)


def t_cleanup_never_touches_items_without_a_dispatch_id():
    fx = Fixture()
    try:
        queue_drafts.merge_into_queue(
            [{"tenant_id": "T9", "jid": "6599999999@s.whatsapp.net", "message": "manual item"}],
            fx.queue_path)
        fx.server.dispatch["dispatch_L2_T2"]["status"] = "cancelled"
        crm_pull.main(["--apply"])
        items = fx.queue_items()
        assert any(i.get("tenant_id") == "T9" and "dispatch_id" not in i for i in items), \
            "an item with no dispatch_id must survive cleanup untouched"
    finally:
        fx.restore()
check("crm_pull --apply: cleanup only ever removes items that carry a dispatch id", t_cleanup_never_touches_items_without_a_dispatch_id)


def t_cleanup_skips_during_the_blackout_window():
    fx = Fixture()
    try:
        crm_pull.main(["--apply"])
        fx.server.dispatch["dispatch_L1_T1"]["status"] = "cancelled"
        during_send_window = datetime.datetime(2026, 9, 16, 8, 10, tzinfo=datetime.timezone(datetime.timedelta(hours=8)))
        summary = crm_pull.cleanup_cancelled(fx.server.snapshot()["dispatch"], True, now=during_send_window)
        assert summary["skipped_blackout"] is True
        assert any(i.get("dispatch_id") == "dispatch_L1_T1" for i in fx.queue_items()), \
            "cleanup must not touch the queue file at all between 07:45 and 08:45"
    finally:
        fx.restore()
check("crm_pull: cleanup never runs between 07:45 and 08:45 Singapore time", t_cleanup_skips_during_the_blackout_window)


def t_cleanup_skips_when_the_queue_file_changed_since_the_fetch():
    fx = Fixture()
    try:
        crm_pull.main(["--apply"])
        fx.server.dispatch["dispatch_L1_T1"]["status"] = "cancelled"
        stale_mtime = os.path.getmtime(fx.queue_path) - 1000  # pretend it looked different at fetch time
        summary = crm_pull.cleanup_cancelled(fx.server.snapshot()["dispatch"], True, mtime_at_fetch=stale_mtime)
        assert summary["skipped_changed"] is True
        assert any(i.get("dispatch_id") == "dispatch_L1_T1" for i in fx.queue_items()), \
            "a queue file that changed since the fetch must be left alone this pass"
    finally:
        fx.restore()
check("crm_pull: cleanup skips this pass when the queue file changed since the run last looked at it", t_cleanup_skips_when_the_queue_file_changed_since_the_fetch)


# ---- (e) 212 rows needing a sent/expired mark: still chunked at 50 --------
def t_chunks_posts_at_50_ops_even_with_far_more_than_200_rows():
    fx = Fixture()
    try:
        archived_ids = []
        for i in range(212):
            did = "dispatch_LB_TB%03d" % i
            archived_ids.append(did)
            fx.server.dispatch[did] = {
                "id": did, "tenant_id": "TB%03d" % i, "listing_id": "LB", "jid": "x@s.whatsapp.net",
                "phone": "9%07d" % i, "text": "hi bulk %d" % i, "viewing_slot": None,
                "status": "pulled", "device": "mac", "pulled_at": pulled_at_from_server(fx, hours_ago=1),
            }
        # Stamped well after pulled_at (hours_ago=1 above) by the FakeServer's
        # OWN clock — never a fixed calendar string, which would drift stale
        # (and wrongly ambiguous under the archive age rule) the moment this
        # suite runs on a real date after the string's own. archive_stamp()
        # is one line below, keeping the two anchored to the same clock together,
        # the same clock: it is entirely for this test's own use, not a
        # crm_pull.py helper.
        fx.write_archive(archive_stamp(fx, hours_from_now=2), archived_ids)  # every one of the 212 already actually sent

        call_sizes = []
        real_post_ops = crm_pull.post_ops

        def spy(user, pw, ops):
            call_sizes.append(len(ops))
            return real_post_ops(user, pw, ops)
        crm_pull.post_ops = spy
        try:
            crm_pull.main(["--apply"])
        finally:
            crm_pull.post_ops = real_post_ops

        assert call_sizes, "expected at least one POST"
        assert all(n <= 50 for n in call_sizes), "every POST must be chunked at 50 ops: %r" % call_sizes
        assert len(call_sizes) >= 5, "212 rows to mark sent should need at least 5 chunks of 50: %r" % call_sizes
        assert all(fx.server.dispatch[did]["status"] == "sent" for did in archived_ids)
    finally:
        fx.restore()
check("(e) 212 rows needing a sent mark in one run still POST in chunks of 50", t_chunks_posts_at_50_ops_even_with_far_more_than_200_rows)


# ---- (f) cancelled then re queued as a NEW id reaches the Mac once --------
def t_cancelled_then_requeued_with_a_new_id_reaches_the_mac_once():
    fx = Fixture()
    try:
        crm_pull.main(["--apply"])  # T2 refused as a dead lead -> cancelled
        old_id = "dispatch_L2_T2"
        assert fx.server.dispatch[old_id]["status"] == "cancelled"

        # T2's situation changes and the app writes a BRAND NEW dispatch id
        # for the fresh attempt — writeDispatchRow (app.js) never reuses a
        # cancelled row's own id, unlike the design this replaced.
        tdb = json.load(open(fx.tenant_db_path))
        for t in tdb["tenants"]:
            if t["id"] == "T2":
                t["last_contact"] = today_sgt()
        json.dump(tdb, open(fx.tenant_db_path, "w"))
        new_id = "dispatch_L2_T2_9999999999999"
        fx.server.apply_ops([{
            "op": "dispatch", "id": new_id, "tenant_id": "T2", "listing_id": "L2",
            "jid": None, "phone": "92222222", "text": "Hi Tenant Two, still interested?",
            "viewing_slot": None, "status": "queued", "device": None,
        }])
        assert fx.server.dispatch[new_id]["status"] == "queued"
        assert fx.server.dispatch[old_id]["status"] == "cancelled", "the old attempt stays cancelled, untouched"

        crm_pull.main(["--apply"])
        matches = [i for i in fx.queue_items() if i["tenant_id"] == "T2"]
        assert len(matches) == 1, "the requeued draft must reach the real dispatch queue exactly once"
        assert matches[0]["dispatch_id"] == new_id, "the appended item must carry the NEW attempt's id"
        assert fx.server.dispatch[new_id]["status"] == "pulled"

        crm_pull.main(["--apply"])
        matches_again = [i for i in fx.queue_items() if i["tenant_id"] == "T2"]
        assert len(matches_again) == 1, "a further run must not append it again"
    finally:
        fx.restore()
check("(f) a cancelled row re queued under a brand new id reaches the real dispatch queue exactly once", t_cancelled_then_requeued_with_a_new_id_reaches_the_mac_once)


# ---- (g) a pulled row missing from queue and archives: recovered once ----
def t_pulled_row_missing_everywhere_is_recovered_once_regardless_of_age():
    fx = Fixture()
    try:
        # Well under the 7 day expiry window, but old enough that the
        # earlier (deleted) recovery window design would have refused to
        # touch it. Not in the queue file, not in any archive.
        fx.server.dispatch["dispatch_L1_T1"]["status"] = "pulled"
        fx.server.dispatch["dispatch_L1_T1"]["pulled_at"] = pulled_at_from_server(fx, days_ago=6, hours_ago=23)

        crm_pull.main(["--apply"])

        matches = [i for i in fx.queue_items() if i["tenant_id"] == "T1"]
        assert len(matches) == 1, "a pulled row absent everywhere must be recovered and appended, no matter its age under 7 days"
        assert fx.server.dispatch["dispatch_L1_T1"]["status"] == "pulled"

        crm_pull.main(["--apply"])
        matches_again = [i for i in fx.queue_items() if i["tenant_id"] == "T1"]
        assert len(matches_again) == 1, "a further run must not append it again"
    finally:
        fx.restore()
check("(g) a pulled row absent from the queue and every archive is recovered and appended exactly once, regardless of age under 7 days", t_pulled_row_missing_everywhere_is_recovered_once_regardless_of_age)


# ---- (h) recovered row now a dead lead: cancelled, not appended -----------
def t_recovered_row_now_dead_is_cancelled_not_appended():
    fx = Fixture()
    try:
        fx.server.dispatch["dispatch_L1_T1"]["status"] = "pulled"
        fx.server.dispatch["dispatch_L1_T1"]["pulled_at"] = pulled_at_from_server(fx, hours_ago=1)
        tdb = json.load(open(fx.tenant_db_path))
        for t in tdb["tenants"]:
            if t["id"] == "T1":
                t["last_contact"] = days_ago_sgt(100)
        json.dump(tdb, open(fx.tenant_db_path, "w"))

        crm_pull.main(["--apply"])

        assert fx.queue_items() == [], "a recovered row that is now dead must never be appended"
        assert fx.server.dispatch["dispatch_L1_T1"]["status"] == "cancelled"
        cancel_ops = [o for o in fx.server.posted_ops if o["op"] == "dispatch_cancel" and o["id"] == "dispatch_L1_T1"]
        assert len(cancel_ops) == 1
        assert "d ago" in cancel_ops[0]["reason"], \
            "the cancel reason should be the same dead lead message queue_drafts.py itself produces: %r" % cancel_ops[0]["reason"]
    finally:
        fx.restore()
check("(h) a recovered row that has since gone dead is cancelled with the dead lead reason, not appended", t_recovered_row_now_dead_is_cancelled_not_appended)


# ---- (i) queue file deleted mid run: cancels nothing -----------------------
def t_queue_file_deleted_mid_run_cancels_nothing():
    fx = Fixture()
    try:
        # A manual item already sitting in the queue file, standing in for
        # "something is on disk when this run starts".
        queue_drafts.merge_into_queue(
            [{"tenant_id": "T9", "jid": "6599999999@s.whatsapp.net", "message": "manual item"}],
            fx.queue_path)
        assert os.path.exists(fx.queue_path)

        real_merge = queue_drafts.merge_into_queue

        def deleting_merge(to_queue, queue_path, held_lock=None):
            # Simulates something outside this run's own lock removing the
            # queue file in the middle of processing — a hand edit, a
            # runaway cleanup script, anything. The append itself must
            # still succeed (recreating the file) and nothing here may
            # read the disappearance as a reason to cancel the row.
            if os.path.exists(queue_path):
                os.remove(queue_path)
            return real_merge(to_queue, queue_path, held_lock=held_lock)
        queue_drafts.merge_into_queue = deleting_merge
        try:
            crm_pull.main(["--apply"])
        finally:
            queue_drafts.merge_into_queue = real_merge

        assert fx.server.dispatch["dispatch_L1_T1"]["status"] == "pulled", \
            "a row must never be cancelled just because the queue file vanished mid run"
        # dispatch_L2_T2 is the fixture's own dead lead and is expected to be
        # cancelled on every run regardless of this scenario — only T1's own
        # row (the one whose append hit the disappearing file) is what this
        # test cares about never seeing a cancel op.
        cancel_ops = [o for o in fx.server.posted_ops if o["op"] == "dispatch_cancel" and o["id"] == "dispatch_L1_T1"]
        assert cancel_ops == [], "nothing about the missing file should have produced a cancel op for T1: %r" % cancel_ops
        matches = [i for i in fx.queue_items() if i["tenant_id"] == "T1"]
        assert len(matches) == 1, "the row must still land in the recreated queue file"
    finally:
        fx.restore()
check("(i) the queue file disappearing mid run cancels nothing — the row still lands, recreating the file", t_queue_file_deleted_mid_run_cancels_nothing)


# ---- (j) two overlapping runs, second genuinely blocked on the lock -------
def t_two_concurrent_runs_second_blocked_on_lock_appends_once():
    fx = Fixture()
    try:
        fx.server.deals = {}  # keep sqlite out of this — the lock is what is under test here
        real_fetch = crm_pull.fetch_snapshot

        # Holds the first run inside its own lock long enough to guarantee
        # the second run's attempt to acquire the SAME lock genuinely blocks
        # at the OS level (flock releases the GIL while waiting), rather
        # than the two runs merely happening to interleave by luck.
        def slow_fetch(user, pw):
            time.sleep(0.3)
            return real_fetch(user, pw)
        crm_pull.fetch_snapshot = slow_fetch

        results = []

        def run_it():
            results.append(crm_pull.main(["--apply"]))

        t1 = threading.Thread(target=run_it)
        t2 = threading.Thread(target=run_it)
        t1.start()
        time.sleep(0.05)   # let t1 acquire the lock and enter its slow fetch first
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
        crm_pull.fetch_snapshot = real_fetch

        assert not t1.is_alive() and not t2.is_alive(), "both runs must finish, not deadlock"
        assert results == [0, 0], "both runs must exit cleanly: %r" % results
        matches = [i for i in fx.queue_items() if i["tenant_id"] == "T1"]
        assert len(matches) == 1, "two overlapping runs must still append the row exactly once, not twice: %r" % fx.queue_items()
        assert fx.server.dispatch["dispatch_L1_T1"]["status"] == "pulled"
    finally:
        fx.restore()
check("(j) two overlapping runs, the second genuinely blocked on the queue lock, append the row exactly once", t_two_concurrent_runs_second_blocked_on_lock_appends_once)


# ---- (k) Mac clock skewed 3 hours either way changes nothing --------------
def t_mac_clock_skew_never_changes_expiry_outcome():
    fx = Fixture()
    try:
        expired_id = "dispatch_L1_T1"
        fx.server.dispatch[expired_id]["status"] = "pulled"
        fx.server.dispatch[expired_id]["pulled_at"] = pulled_at_from_server(fx, days_ago=crm_pull.EXPIRY_DAYS + 1)

        not_expired_id = "dispatch_L2_T2"
        fx.server.dispatch[not_expired_id]["status"] = "pulled"
        fx.server.dispatch[not_expired_id]["pulled_at"] = pulled_at_from_server(fx, days_ago=3)
        # Already in the queue file, so the remaining row loop treats it as
        # already delivered and leaves its status alone either way — this
        # test only cares whether IT GETS EXPIRED, not whether it gets
        # reclassified against the dead lead rule too.
        queue_drafts.merge_into_queue(
            [{"tenant_id": "T2", "jid": "6592222222@s.whatsapp.net", "message": "already appended",
              "dispatch_id": not_expired_id}],
            fx.queue_path)

        real_now_sgt = crm_pull._now_sgt
        base = real_now_sgt()

        for skew_hours in (3, -3):
            fx.server.dispatch[expired_id]["status"] = "pulled"
            fx.server.dispatch[not_expired_id]["status"] = "pulled"
            crm_pull._now_sgt = lambda: base + datetime.timedelta(hours=skew_hours)
            try:
                crm_pull.main(["--apply"])
            finally:
                crm_pull._now_sgt = real_now_sgt

            assert fx.server.dispatch[expired_id]["status"] == "cancelled", \
                "server clock says this row is past the expiry window — a %+d hour Mac clock skew must not save it" % skew_hours
            assert fx.server.dispatch[not_expired_id]["status"] == "pulled", \
                "server clock says this row is well inside the expiry window — a %+d hour Mac clock skew must not expire it" % skew_hours
    finally:
        fx.restore()
check("(k) skewing the Mac's own clock 3 hours either way changes no expiry outcome — only server_time decides", t_mac_clock_skew_never_changes_expiry_outcome)


# ---- (l) archive stamped too close to pulled_at: ambiguous, stays pulled --
def t_archive_too_close_to_pulled_at_is_ambiguous_stays_pulled():
    fx = Fixture()
    try:
        fx.server.dispatch["dispatch_L1_T1"]["status"] = "pulled"
        fx.server.dispatch["dispatch_L1_T1"]["pulled_at"] = pulled_at_from_server(fx)
        # Only 2 minutes after pulled_at — well under the 5 minute safety
        # margin, exactly the race the module docstring describes: this
        # archive could be the very morning-dispatch.sh run whose in memory
        # read predates this row's own append landing on disk.
        fx.write_archive(archive_stamp(fx, minutes_from_now=2), ["dispatch_L1_T1"])

        crm_pull.main(["--apply"])

        assert fx.server.dispatch["dispatch_L1_T1"]["status"] == "pulled", \
            "an archive too close in time to pulled_at must never mark the row sent"
        matches = [i for i in fx.queue_items() if i.get("dispatch_id") == "dispatch_L1_T1"]
        assert matches == [], "an ambiguous row must not be appended to the queue either"
        cancel_ops = [o for o in fx.server.posted_ops if o["op"] == "dispatch_cancel" and o["id"] == "dispatch_L1_T1"]
        assert cancel_ops == [], "an ambiguous row must not be cancelled or expired — it stays pulled for a later run to resolve"
    finally:
        fx.restore()
check("(l) an archive stamped too close to the row's own pulled_at is ambiguous — the row stays pulled, never marked sent", t_archive_too_close_to_pulled_at_is_ambiguous_stays_pulled)


# ---- (m) a row in both the queue file and an archive: also ambiguous -----
def t_row_in_both_queue_file_and_archive_is_ambiguous():
    fx = Fixture()
    try:
        fx.server.dispatch["dispatch_L1_T1"]["status"] = "pulled"
        fx.server.dispatch["dispatch_L1_T1"]["pulled_at"] = pulled_at_from_server(fx, hours_ago=1)
        # Standing in for the exact append/rename race the module docstring
        # describes: the row is genuinely sitting in the live queue file
        # AND its id also shows up in a .done-* archive (morning-dispatch.sh
        # renamed the file with this row already on disk, without the in
        # memory send loop it was actually running ever having seen it).
        queue_drafts.merge_into_queue(
            [{"tenant_id": "T1", "jid": "6591111111@s.whatsapp.net", "message": "already appended",
              "dispatch_id": "dispatch_L1_T1"}],
            fx.queue_path)
        fx.write_archive(archive_stamp(fx, hours_from_now=3), ["dispatch_L1_T1"])

        crm_pull.main(["--apply"])

        assert fx.server.dispatch["dispatch_L1_T1"]["status"] == "pulled", \
            "a row in both the queue file and an archive must never be marked sent from the archive"
        matches = [i for i in fx.queue_items() if i.get("dispatch_id") == "dispatch_L1_T1"]
        assert len(matches) == 1, "the row's existing queue item must be left alone, not duplicated"
    finally:
        fx.restore()
check("(m) a row sitting in both the queue file and an archive is ambiguous — never marked sent from the archive", t_row_in_both_queue_file_and_archive_is_ambiguous)


# ---- (n) the append phase itself honours the 07:45-08:45 blackout --------
def t_append_never_runs_during_the_blackout_window():
    fx = Fixture()
    try:
        real_now_sgt = crm_pull._now_sgt
        during_send_window = datetime.datetime(2026, 9, 16, 8, 10, tzinfo=datetime.timezone(datetime.timedelta(hours=8)))
        crm_pull._now_sgt = lambda: during_send_window
        try:
            crm_pull.main(["--apply"])
        finally:
            crm_pull._now_sgt = real_now_sgt

        assert fx.queue_items() == [], "nothing may be appended to the live queue file during the send window"
        assert fx.server.dispatch["dispatch_L1_T1"]["status"] == "queued", \
            "a row must be left exactly as found (not even marked pulled) when the append phase is skipped for the blackout"
        assert fx.server.dispatch["dispatch_L2_T2"]["status"] == "queued", \
            "even a dead lead row must be left alone this run — the whole append phase is skipped, not just the good rows"
    finally:
        fx.restore()
check("(n) the dispatch queue append itself never runs inside the 07:45 to 08:45 send window, even a dead lead row is left untouched", t_append_never_runs_during_the_blackout_window)


# ---- (o) missing server_time logs a line and continues, expiry disabled --
def t_missing_server_time_logs_and_continues():
    fx = Fixture()
    try:
        real_snapshot = fx.server.snapshot

        def snapshot_without_server_time():
            snap = real_snapshot()
            del snap["server_time"]
            return snap
        fx.server.snapshot = snapshot_without_server_time

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = crm_pull.main(["--apply"])
        assert rc == 0, "a missing server_time must never crash the run"
        assert "server_time missing, expiry disabled" in buf.getvalue(), \
            "a missing server_time must be logged, in plain terms, exactly once"

        # With no server clock to judge age against, a row must never be
        # expired purely because the Mac's own clock thinks it looks old.
        fx.server.dispatch["dispatch_L1_T1"]["status"] = "pulled"
        fx.server.dispatch["dispatch_L1_T1"]["pulled_at"] = pulled_at_from_server(fx, days_ago=crm_pull.EXPIRY_DAYS + 30)
        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            crm_pull.main(["--apply"])
        assert fx.server.dispatch["dispatch_L1_T1"]["status"] != "cancelled", \
            "expiry must stay disabled with no server_time, however old pulled_at looks by any other clock"
    finally:
        fx.restore()
check("(o) a missing server_time is logged plainly and disables expiry rather than crashing", t_missing_server_time_logs_and_continues)


def t_completed_deal_imported_once():
    fx = Fixture()
    try:
        crm_pull.main(["--apply"])
        rows = fx.deal_rows()
        assert len(rows) == 1, "only the completed deal should have been imported"
        assert rows[0]["property_address"] == "123 Test Rd #01-01"
        assert "[matchmaker-import-id=deal_abc1]" in rows[0]["notes"]
        activity = [a for a in fx.server.activity if "deal_abc1" in (a.get("detail") or "")]
        assert len(activity) == 1, "the import should be recorded as one crm_activity row via the API"
        crm_pull.main(["--apply"])
        rows_again = fx.deal_rows()
        assert len(rows_again) == 1, "a second run must never double import the same completed deal"
    finally:
        fx.restore()
check("crm_pull --apply: a completed deal is imported once, and never duplicated on a second run", t_completed_deal_imported_once)


def t_non_completed_deal_ignored():
    fx = Fixture()
    try:
        crm_pull.main(["--apply"])
        rows = fx.deal_rows()
        addresses = [r["property_address"] for r in rows]
        assert "456 Test Ave" not in addresses, "a deal still at stage agreed must never be imported"
    finally:
        fx.restore()
check("crm_pull --apply: a deal not at stage completed is left alone", t_non_completed_deal_ignored)


def t_missing_deals_table_skips_with_message():
    fx = Fixture()
    try:
        os.remove(fx.clients_db_path)
        sqlite3.connect(fx.clients_db_path).close()  # a real db file, but no deals table at all
        rc = crm_pull.main(["--apply"])
        assert rc == 0, "a missing deals table must be skipped gracefully, never crash the run"
        assert fx.server.dispatch["dispatch_L1_T1"]["status"] == "pulled"
    finally:
        fx.restore()
check("crm_pull --apply: a clients.db with no deals table skips import instead of crashing", t_missing_deals_table_skips_with_message)


def t_time_budget_skips_remaining_dispatch_rows():
    fx = Fixture()
    try:
        orig_budget = crm_pull.TIME_BUDGET_SECONDS
        crm_pull.TIME_BUDGET_SECONDS = -1  # already expired before main() even starts its clock
        try:
            crm_pull.main(["--apply"])
        finally:
            crm_pull.TIME_BUDGET_SECONDS = orig_budget
        # Nothing should have been appended — the very first remaining row
        # already finds the time budget spent.
        assert fx.queue_items() == [], "a spent time budget must skip remaining dispatch rows instead of stalling"
    finally:
        fx.restore()
check("crm_pull: a spent time budget skips remaining dispatch rows instead of stalling", t_time_budget_skips_remaining_dispatch_rows)


print("=" * 60)
if FAILED:
    print(f"{len(FAILED)} check(s) FAILED: {FAILED}")
    sys.exit(1)
print("all checks passed")
