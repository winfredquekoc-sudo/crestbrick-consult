#!/usr/bin/env python3
"""Pure assert suite for scripts/matchmaker/crm_pull.py — the CRM pull bridge.
No pytest, stdlib only:
    /usr/bin/python3 tests/matchmaker/test_crm_pull.py

Runs crm_pull.main() against a fake CRM API (an in memory stand in for
fetch_snapshot/post_ops, since a real network call has no place in a test)
and a throwaway sqlite clients.db. Covers: a dead lead dispatch row is
refused and cancelled with a reason, an accepted draft is appended once and
marked pulled, a second run appends nothing more, a completed deal is
imported once, and a deal not at stage completed is left alone.

All fixture people are invented (SG plausible, obviously fake).
"""
import json
import os
import sqlite3
import sys
import tempfile

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
class FakeServer:
    """Stands in for the CRM API: fetch_snapshot()/post_ops() are pointed at
    this instead of a real network call. apply_ops() mirrors deploy/api/crm.js's
    own "dispatch"/"dispatch_cancel"/"activity" op handling closely enough for
    this script's own logic to be exercised end to end, including the second
    run of a snapshot that now reflects what the first run already did."""

    def __init__(self):
        self.dispatch = {}
        self.deals = {}
        self.activity = []
        self.posted_ops = []

    def snapshot(self):
        return {
            "dispatch": [dict(r) for r in self.dispatch.values() if r["status"] in ("queued", "pulled")],
            "deals": [dict(d) for d in self.deals.values()],
        }

    def apply_ops(self, ops):
        self.posted_ops.extend(ops)
        for o in ops:
            if o["op"] == "dispatch":
                row = self.dispatch.get(o["id"], {})
                row.update({k: v for k, v in o.items() if k != "op"})
                self.dispatch[o["id"]] = row
            elif o["op"] == "dispatch_cancel":
                row = self.dispatch.get(o["id"])
                if row:
                    row["status"] = "cancelled"
            elif o["op"] == "activity":
                self.activity.append(o)
        return {"ok": True, "applied": len(ops)}


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


def today_sgt():
    import datetime
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).date().isoformat()


def days_ago_sgt(n):
    import datetime
    d = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).date()
    return (d - datetime.timedelta(days=n)).isoformat()


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
        for k in ("MM_USER", "MM_PASS"):
            if self._orig[k] is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = self._orig[k]

    def queue_items(self):
        if not os.path.exists(self.queue_path):
            return []
        return json.load(open(self.queue_path)).get("items", [])

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
        assert fx.server.dispatch["dispatch_L1_T1"]["status"] == "queued"
        assert fx.server.dispatch["dispatch_L2_T2"]["status"] == "queued"
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
        assert fx.server.dispatch["dispatch_L1_T1"]["status"] == "pulled"
        pulled_ops = [o for o in fx.server.posted_ops if o["op"] == "dispatch" and o["id"] == "dispatch_L1_T1"]
        assert len(pulled_ops) == 1
        assert pulled_ops[0]["status"] == "pulled"
    finally:
        fx.restore()
check("crm_pull --apply: an accepted draft is appended to the queue once and marked pulled", t_accepted_draft_appended_once_and_marked_pulled)


def t_second_run_appends_nothing_more():
    fx = Fixture()
    try:
        crm_pull.main(["--apply"])
        first_items = fx.queue_items()
        posted_after_first = len(fx.server.posted_ops)
        crm_pull.main(["--apply"])
        second_items = fx.queue_items()
        assert second_items == first_items, "a second run must never double append the dispatch queue"
        # Every dispatch row is already pulled or cancelled after the first run, so
        # the second run has nothing status queued left to act on and posts no ops.
        assert len(fx.server.posted_ops) == posted_after_first, "a second run must not repost ops for rows already settled"
    finally:
        fx.restore()
check("crm_pull --apply: run twice, the second run appends nothing more to the queue", t_second_run_appends_nothing_more)


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
        # Idempotent: running again must not insert a second row for the same deal.
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


print("=" * 60)
if FAILED:
    print(f"{len(FAILED)} check(s) FAILED: {FAILED}")
    sys.exit(1)
print("all checks passed")
