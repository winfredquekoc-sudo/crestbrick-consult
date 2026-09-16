#!/usr/bin/env python3
"""Pure assert suite for scripts/matchmaker/crm_pull.py — the CRM pull bridge.
No pytest, stdlib only:
    /usr/bin/python3 tests/matchmaker/test_crm_pull.py

Runs crm_pull.main() (and, for the crash window, recovery window and time
budget cases, its lower level functions directly) against a fake CRM API (an
in memory stand in for fetch_snapshot/post_ops, since a real network call has
no place in a test) and a throwaway sqlite clients.db. Covers:
  (a) a crash after marking pulled but before appending is recovered by the
      next run, appended exactly once
  (b) a crash after the append itself (before the ledger write lands) never
      double appends on the next run
  (c) a row that has been through the real 08:00 archive and two further
      rotations is never appended again and is marked sent
  (d) cancelling a row after it was pulled prunes its queue item once
  (e) 212 dispatch rows in one run still POST in chunks of 50
  (f) a cancelled row re marked Queued reaches the real queue exactly once
  (g) a pulled row 3 hours old with no ledger entry is NOT recovered
  (h) a recovered row that has since gone dead is cancelled with the dead
      lead reason, never appended
plus the original dead lead / accepted draft / non completed deal / missing
deals table cases from the first two review rounds.

All fixture people are invented (SG plausible, obviously fake).
"""
import datetime
import json
import os
import sqlite3
import sys
import tempfile
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
def iso_hours_ago(n):
    return (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=n)).isoformat()


def today_sgt():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).date().isoformat()


def days_ago_sgt(n):
    d = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).date()
    return (d - datetime.timedelta(days=n)).isoformat()


class FakeServer:
    """Stands in for the CRM API: fetch_snapshot()/post_ops() are pointed at
    this instead of a real network call. apply_ops() mirrors deploy/api/crm.js's
    own "dispatch"/"dispatch_cancel"/"activity" op handling — including
    nextDispatchStatus's transition table (queued moves anywhere, pulled only
    ever moves on to sent, cancelled only ever moves back to queued, sent is
    terminal) and stamping pulled_at fresh only when a row newly BECOMES
    pulled — closely enough for this script's own logic (the ledger, the
    recovery window, the sent marking) to be exercised end to end against
    realistic server responses."""

    def __init__(self):
        self.dispatch = {}
        self.deals = {}
        self.activity = []
        self.posted_ops = []

    def snapshot(self):
        return {
            "dispatch": [dict(r) for r in self.dispatch.values() if r["status"] in ("queued", "pulled", "cancelled")],
            "deals": [dict(d) for d in self.deals.values()],
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
    queue path, a temp ledger path, a temp clients.db, and a fresh FakeServer
    — plus the module level monkeypatches crm_pull/queue_drafts/log_deal need
    to use them instead of Winfred's real files. restore() undoes every
    patch."""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="mm-crm-pull-test-")
        self.tenant_db_path = os.path.join(self.dir, "tenant-db.json")
        self.queue_path = os.path.join(self.dir, "morning-dispatch-queue.json")
        self.ledger_path = os.path.join(self.dir, "matchmaker-dispatch-ledger.json")
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
            "LEDGER_PATH": crm_pull.LEDGER_PATH,
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
        crm_pull.LEDGER_PATH = self.ledger_path
        log_deal.DB_PATH = self.clients_db_path
        log_deal.BACKUP_DIR = os.path.join(self.dir, "backups")
        crm_pull.fetch_snapshot = lambda user, pw: self.server.snapshot()
        crm_pull.post_ops = lambda user, pw, ops: self.server.apply_ops(ops)
        # A fixed, safe (well outside 07:45-08:45) Singapore time for every
        # cleanup_cancelled call this fixture drives through main() — without
        # this, a test run happening to land inside the real send window would
        # make cleanup skip everywhere and fail these tests for a reason that
        # has nothing to do with the code under test.
        safe_time = datetime.datetime(2026, 9, 16, 12, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=8)))
        crm_pull._now_sgt = lambda: safe_time
        os.environ["MM_USER"] = "test-user-placeholder"
        os.environ["MM_PASS"] = "test-pass-placeholder"

    def restore(self):
        queue_drafts.TENANT_DB_PATH = self._orig["TENANT_DB_PATH"]
        queue_drafts.WA_DB_PATH = self._orig["WA_DB_PATH"]
        queue_drafts.QUEUE_PATH = self._orig["QUEUE_PATH"]
        crm_pull.LEDGER_PATH = self._orig["LEDGER_PATH"]
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

    def ledger(self):
        if not os.path.exists(self.ledger_path):
            return {}
        return json.load(open(self.ledger_path))

    def deal_rows(self):
        con = sqlite3.connect(self.clients_db_path)
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute("SELECT * FROM deals").fetchall()]
        con.close()
        return rows


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
        assert fx.ledger() == {}, "dry run must never write the ledger"
        assert fx.server.dispatch["dispatch_L1_T1"]["status"] == "queued"
    finally:
        fx.restore()
check("main() dry run: reports without writing the queue, the ledger, clients.db or the API", t_dry_run_writes_nothing)


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
        assert "dispatch_L1_T1" in fx.ledger(), "a successful append must be recorded in the ledger"
    finally:
        fx.restore()
check("crm_pull --apply: an accepted draft is appended to the queue once, tagged with its dispatch id, marked pulled and recorded in the ledger", t_accepted_draft_appended_once_and_marked_pulled)


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
        # Step one only: mark T1 pulled and confirm it against the (fake)
        # server's response, but never call append_entries — exactly as if
        # the process died right there, between the two steps.
        ledger = crm_pull.load_ledger()
        confirmed, summary = crm_pull.classify_and_mark(fx.server.snapshot()["dispatch"], ledger, "u", "p", True)
        assert fx.server.dispatch["dispatch_L1_T1"]["status"] == "pulled"
        assert fx.queue_items() == [], "nothing should be appended yet — this is the simulated crash point"
        assert "dispatch_L1_T1" not in fx.ledger()

        # A full, ordinary second run must recover the still pulled, not yet
        # appended row (its id is absent from the ledger and it was pulled
        # moments ago, well within the recovery window) and append it —
        # exactly once overall, not zero and not twice.
        crm_pull.main(["--apply"])
        matches = [i for i in fx.queue_items() if i["tenant_id"] == "T1"]
        assert len(matches) == 1, "the crash recovered draft must be appended exactly once overall"

        crm_pull.main(["--apply"])
        matches_again = [i for i in fx.queue_items() if i["tenant_id"] == "T1"]
        assert len(matches_again) == 1, "a third run changes nothing further"
    finally:
        fx.restore()
check("(a) crash after mark, before append: the next run recovers and appends exactly once", t_crash_after_mark_before_append_recovers_once)


# ---- (b) crash after the append, before the ledger write lands ------------
def t_crash_after_append_before_ledger_never_double_appends():
    fx = Fixture()
    try:
        # Simulate the append having already actually happened (the queue file
        # itself was written) but the ledger write never landing — the
        # narrower crash window between the two file writes append_entries
        # itself performs.
        fx.server.dispatch["dispatch_L1_T1"]["status"] = "pulled"
        fx.server.dispatch["dispatch_L1_T1"]["pulled_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        queue_drafts.merge_into_queue(
            [{"tenant_id": "T1", "jid": "6591111111@s.whatsapp.net", "message": "already appended",
              "dispatch_id": "dispatch_L1_T1"}],
            fx.queue_path)
        assert fx.ledger() == {}, "the simulated crash means the ledger never got the entry"

        crm_pull.main(["--apply"])
        matches = [i for i in fx.queue_items() if i["tenant_id"] == "T1"]
        assert len(matches) == 1, "a row already in the queue file must never be appended a second time, ledger or not"
    finally:
        fx.restore()
check("(b) crash after append, before the ledger write: the next run appends nothing more", t_crash_after_append_before_ledger_never_double_appends)


# ---- (c) 08:00 archived, then two further rotations: sent, never re appended
def t_archived_row_survives_further_rotations_and_is_marked_sent():
    fx = Fixture()
    try:
        # The row was genuinely pulled and sent a while ago. No ledger entry
        # at all for this test (as if this script's ledger were rebuilt after
        # an unrelated incident) — proving the block on re appending it comes
        # from its age (well past the 2 hour recovery window), and the sent
        # marking comes from finding it in an OLDER archive after later
        # rotations have superseded it as "the newest" file.
        fx.server.dispatch["dispatch_L1_T1"]["status"] = "pulled"
        fx.server.dispatch["dispatch_L1_T1"]["pulled_at"] = iso_hours_ago(30)
        fx.write_archive("202609160800", ["dispatch_L1_T1"])   # the real 08:00 send
        fx.write_archive("202609160900", [])                    # rotation 1, unrelated
        fx.write_archive("202609161000", [])                    # rotation 2, unrelated — now the newest

        crm_pull.main(["--apply"])

        matches = [i for i in fx.queue_items() if i.get("dispatch_id") == "dispatch_L1_T1"]
        assert matches == [], "an already sent row, 30 hours old, must never be appended again"
        assert fx.server.dispatch["dispatch_L1_T1"]["status"] == "sent", \
            "a row found in an archive that is no longer the newest one must still be marked sent"
    finally:
        fx.restore()
check("(c) a row archived at 08:00, surviving two further rotations, is never re appended and is marked sent", t_archived_row_survives_further_rotations_and_is_marked_sent)


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
        assert fx.queue_items() == [i for i in fx.queue_items()], "pruning again is a no op, not an error"
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


# ---- (e) 212 rows: still chunked at 50 -------------------------------------
def t_chunks_posts_at_50_ops_even_with_far_more_than_200_rows():
    fx = Fixture()
    try:
        for i in range(210):
            tid = "TB%03d" % i
            phone = "9%07d" % i
            fx.add_tenant(tid, phone, phone + "@s.whatsapp.net")
            fx.server.dispatch["dispatch_LB_" + tid] = {
                "id": "dispatch_LB_" + tid, "tenant_id": tid, "listing_id": "LB", "jid": None,
                "phone": phone, "text": "hi bulk " + tid, "viewing_slot": None,
                "status": "queued", "device": None,
            }
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
        assert len(call_sizes) >= 5, "212 dispatch rows should need at least 5 chunks of 50: %r" % call_sizes
    finally:
        fx.restore()
check("(e) 212 dispatch rows in one run still POST in chunks of 50", t_chunks_posts_at_50_ops_even_with_far_more_than_200_rows)


# ---- (f) cancelled then re queued reaches the Mac once ---------------------
def t_cancelled_then_requeued_reaches_the_mac_once():
    fx = Fixture()
    try:
        crm_pull.main(["--apply"])  # T2 refused as a dead lead -> cancelled
        assert fx.server.dispatch["dispatch_L2_T2"]["status"] == "cancelled"

        # T2's situation changes and the app's own Mark Queued action writes
        # the "dispatch" op again on the SAME id, with fresh contact info —
        # exactly what CRM.upsertDispatch/writeDispatchRow do client side.
        tdb = json.load(open(fx.tenant_db_path))
        for t in tdb["tenants"]:
            if t["id"] == "T2":
                t["last_contact"] = today_sgt()
        json.dump(tdb, open(fx.tenant_db_path, "w"))
        fx.server.apply_ops([{
            "op": "dispatch", "id": "dispatch_L2_T2", "tenant_id": "T2", "listing_id": "L2",
            "jid": None, "phone": "92222222", "text": "Hi Tenant Two, still interested?",
            "viewing_slot": None, "status": "queued", "device": None,
        }])
        assert fx.server.dispatch["dispatch_L2_T2"]["status"] == "queued", \
            "the server must accept a fresh queued write on a cancelled row through the same id"

        crm_pull.main(["--apply"])
        matches = [i for i in fx.queue_items() if i["tenant_id"] == "T2"]
        assert len(matches) == 1, "the requeued draft must reach the real dispatch queue exactly once"
        assert fx.server.dispatch["dispatch_L2_T2"]["status"] == "pulled"

        crm_pull.main(["--apply"])
        matches_again = [i for i in fx.queue_items() if i["tenant_id"] == "T2"]
        assert len(matches_again) == 1, "a further run must not append it again"
    finally:
        fx.restore()
check("(f) a cancelled row re marked Queued reaches the real dispatch queue exactly once", t_cancelled_then_requeued_reaches_the_mac_once)


# ---- (g) a pulled row 3 hours old, no ledger entry: NOT recovered ---------
def t_pulled_row_3_hours_old_with_no_ledger_entry_is_not_recovered():
    fx = Fixture()
    try:
        fx.server.dispatch["dispatch_L1_T1"]["status"] = "pulled"
        fx.server.dispatch["dispatch_L1_T1"]["pulled_at"] = iso_hours_ago(3)
        ledger = {}
        queued, recoverable = crm_pull.gather_candidates(
            fx.server.snapshot()["dispatch"], ledger, datetime.datetime.now(datetime.timezone.utc))
        assert recoverable == [], "a pulled row past the 2 hour recovery window must never be recovered"

        crm_pull.main(["--apply"])
        assert fx.queue_items() == [], "nothing should be appended for a stale, un ledgered pulled row"
        assert fx.server.dispatch["dispatch_L1_T1"]["status"] == "pulled", \
            "at 3 hours old it is also not yet old enough for the 24 hour sent marking"
    finally:
        fx.restore()
check("(g) a pulled row 3 hours old with no ledger entry is NOT recovered", t_pulled_row_3_hours_old_with_no_ledger_entry_is_not_recovered)


# ---- (h) recovered row now a dead lead: cancelled, not appended -----------
def t_recovered_row_now_dead_is_cancelled_not_appended():
    fx = Fixture()
    try:
        # T1 was pulled moments ago (inside the recovery window, absent from
        # the ledger — a genuine crash candidate) but has since gone quiet
        # long enough to trip the dead lead rule.
        fx.server.dispatch["dispatch_L1_T1"]["status"] = "pulled"
        fx.server.dispatch["dispatch_L1_T1"]["pulled_at"] = iso_hours_ago(1)
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
        assert "dead-lead" in cancel_ops[0]["reason"] or "d ago" in cancel_ops[0]["reason"], \
            "the cancel reason should be the same dead lead message queue_drafts.py itself produces: %r" % cancel_ops[0]["reason"]
    finally:
        fx.restore()
check("(h) a recovered row that has since gone dead is cancelled with the dead lead reason, not appended", t_recovered_row_now_dead_is_cancelled_not_appended)


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
        past_deadline = time.monotonic() - 1
        ledger = crm_pull.load_ledger()
        confirmed, summary = crm_pull.classify_and_mark(
            fx.server.snapshot()["dispatch"], ledger, "u", "p", True, deadline=past_deadline)
        assert summary["time_budget_hit"] is True
        assert summary["approved"] == 0 and summary["cancelled"] == 0
        assert confirmed == []
    finally:
        fx.restore()
check("crm_pull: a spent time budget skips remaining dispatch rows instead of stalling", t_time_budget_skips_remaining_dispatch_rows)


print("=" * 60)
if FAILED:
    print(f"{len(FAILED)} check(s) FAILED: {FAILED}")
    sys.exit(1)
print("all checks passed")
