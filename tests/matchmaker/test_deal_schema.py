#!/usr/bin/env python3
"""tests/matchmaker/test_deal_schema.py -- item 5/46.

Three vocabularies describe "how far along is this deal": scripts/ops/
log_deal.py's own --type/--role CLI flags, clients.db's deal_type_t/
deal_stage_t CHECK constraint value sets (the Postgres ENUM in db/schema.sql
-- the canonical schema log_deal.py's live SQLite copy mirrors), and the
Matchmaker CRM's own stage/kind vocabulary (CRM_STAGES/CRM_KINDS, exported
from scripts/matchmaker/deploy/lib/db.js). log_deal.py's MM_STAGE_MAP and
MM_DEAL_TYPE_MAP are the two hand maintained dictionaries reconciling the
CRM's vocabulary into clients.db's -- this asserts every key and value in
both is still a real member of the vocabulary it claims to belong to, so a
rename on either side fails a test instead of failing silently (or importing
the wrong stage) at run time.

Pure regex/text parsing only -- no sqlite3, no Postgres, no server needed --
so it runs anywhere python3 does, per build.py's own fast gate convention.
Not part of tests/matchmaker/SUITES: it checks scripts/ops/log_deal.py, which
is not part of the shipped matchmaker artifact build.py/deploy.sh gate.

    /usr/bin/python3 tests/matchmaker/test_deal_schema.py
"""
import glob, io, json, os, re, shutil, sys, tempfile
from contextlib import redirect_stdout
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
SCRIPTS_OPS = os.path.join(ROOT, "scripts", "ops")
DB_JS = os.path.join(ROOT, "scripts", "matchmaker", "deploy", "lib", "db.js")
SCHEMA_SQL = os.path.join(ROOT, "db", "schema.sql")
sys.path.insert(0, SCRIPTS_OPS)
import log_deal  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}  {detail}")
        FAILURES.append(name)


def section(title):
    print(f"\n== {title} ==")


def _parse_js_string_array(src, const_name):
    """`export const NAME = [ "a", "b", ... ];` -> {"a", "b", ...}."""
    m = re.search(re.escape(const_name) + r"\s*=\s*\[(.*?)\]", src, re.S)
    if not m:
        raise AssertionError(f"{const_name} not found in {DB_JS}")
    return {x.strip().strip('"').strip("'") for x in m.group(1).split(",") if x.strip()}


def _parse_pg_enum(src, type_name):
    """`CREATE TYPE NAME AS ENUM ('a','b',...);` -> {"a", "b", ...}."""
    m = re.search(r"CREATE TYPE\s+" + re.escape(type_name) + r"\s+AS ENUM\s*\((.*?)\)", src, re.S)
    if not m:
        raise AssertionError(f"{type_name} not found in {SCHEMA_SQL}")
    return {x.strip().strip("'") for x in m.group(1).split(",") if x.strip()}


def main():
    section("parsing the three real vocabulary sources")
    db_js_src = open(DB_JS, encoding="utf-8").read()
    schema_src = open(SCHEMA_SQL, encoding="utf-8").read()

    crm_stages = _parse_js_string_array(db_js_src, "CRM_STAGES")
    crm_kinds = _parse_js_string_array(db_js_src, "CRM_KINDS")
    deal_stage_t = _parse_pg_enum(schema_src, "deal_stage_t")
    deal_type_t = _parse_pg_enum(schema_src, "deal_type_t")

    check("CRM_STAGES parsed from deploy/lib/db.js is not empty", len(crm_stages) > 0)
    check("CRM_KINDS parsed from deploy/lib/db.js is not empty", len(crm_kinds) > 0)
    check("deal_stage_t parsed from db/schema.sql is not empty", len(deal_stage_t) > 0)
    check("deal_type_t parsed from db/schema.sql is not empty", len(deal_type_t) > 0)

    section("MM_STAGE_MAP: every key is a real CRM stage, every value a real deal_stage_t")
    bad_stage_keys = [k for k in log_deal.MM_STAGE_MAP if k not in crm_stages]
    bad_stage_vals = [v for v in log_deal.MM_STAGE_MAP.values() if v not in deal_stage_t]
    check("MM_STAGE_MAP is not empty", len(log_deal.MM_STAGE_MAP) > 0)
    check("every MM_STAGE_MAP key is a member of CRM_STAGES",
          bad_stage_keys == [], f"unknown keys: {bad_stage_keys} (CRM_STAGES={sorted(crm_stages)})")
    check("every MM_STAGE_MAP value is a member of deal_stage_t",
          bad_stage_vals == [], f"unknown values: {bad_stage_vals} (deal_stage_t={sorted(deal_stage_t)})")

    section("MM_DEAL_TYPE_MAP: every key is a real CRM kind, every value a real deal_type_t")
    bad_kind_keys = [k for k in log_deal.MM_DEAL_TYPE_MAP if k not in crm_kinds]
    bad_kind_vals = [v for v in log_deal.MM_DEAL_TYPE_MAP.values() if v not in deal_type_t]
    check("MM_DEAL_TYPE_MAP is not empty", len(log_deal.MM_DEAL_TYPE_MAP) > 0)
    check("every MM_DEAL_TYPE_MAP key is a member of CRM_KINDS",
          bad_kind_keys == [], f"unknown keys: {bad_kind_keys} (CRM_KINDS={sorted(crm_kinds)})")
    check("every MM_DEAL_TYPE_MAP value is a member of deal_type_t",
          bad_kind_vals == [], f"unknown values: {bad_kind_vals} (deal_type_t={sorted(deal_type_t)})")

    section("cross check: log_deal.py's OWN --type/--role -> deal_type_t vocabulary (resolve_deal_type)")
    bad_resolved = [(t, r, log_deal.resolve_deal_type(t, r))
                     for t in log_deal.TYPE_CHOICES for r in log_deal.ROLE_CHOICES
                     if log_deal.resolve_deal_type(t, r) not in deal_type_t]
    check("every (--type, --role) combination resolves to a real deal_type_t member",
          bad_resolved == [], f"out of vocabulary: {bad_resolved}")

    section("ensure_import_column: idempotent, adds a UNIQUE index, never touches an existing column twice")
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE deals (id INTEGER PRIMARY KEY, client_slug TEXT, notes TEXT)")
    log_deal.ensure_import_column(con)
    cols = {r[1] for r in con.execute("PRAGMA table_info(deals)").fetchall()}
    check("matchmaker_import_id column added", "matchmaker_import_id" in cols, str(cols))
    log_deal.ensure_import_column(con)  # second call must not raise / duplicate the column
    cols2 = {r[1] for r in con.execute("PRAGMA table_info(deals)").fetchall()}
    check("calling it again is a no op (idempotent)", cols2 == cols, f"{cols} vs {cols2}")
    con.execute("INSERT INTO deals (client_slug, matchmaker_import_id) VALUES ('a', 'crm:key1')")
    con.commit()
    dup_rejected = False
    try:
        con.execute("INSERT INTO deals (client_slug, matchmaker_import_id) VALUES ('b', 'crm:key1')")
        con.commit()
    except sqlite3.IntegrityError:
        dup_rejected = True
    check("the unique index actually rejects a duplicate matchmaker_import_id", dup_rejected)
    # SQLite's own UNIQUE semantics already treat NULL as distinct, but the
    # index is declared WHERE matchmaker_import_id IS NOT NULL on purpose
    # (explicit over implicit) -- confirm two NULL rows are still both allowed.
    con.execute("INSERT INTO deals (client_slug) VALUES ('c')")
    con.execute("INSERT INTO deals (client_slug) VALUES ('d')")
    con.commit()
    check("two NULL matchmaker_import_id rows are both allowed (nullable column)",
          con.execute("SELECT COUNT(*) FROM deals WHERE matchmaker_import_id IS NULL").fetchone()[0] == 2)
    con.close()

    section("cmd_import_mm: one backup for the whole batch, and a missing key counted as unlinked")
    tmp = tempfile.mkdtemp()
    orig_db_path, orig_backup_dir = log_deal.DB_PATH, log_deal.BACKUP_DIR
    try:
        db_path = os.path.join(tmp, "clients.db")
        log_deal.DB_PATH = db_path
        log_deal.BACKUP_DIR = os.path.join(tmp, "backups")
        con = sqlite3.connect(db_path)
        con.execute("CREATE TABLE deals (id INTEGER PRIMARY KEY, client_slug TEXT, deal_type TEXT, "
                     "property_address TEXT, stage TEXT, closed_date TEXT, notes TEXT)")
        con.commit()
        con.close()

        entities = [{"key": "crm:%d" % i, "stage": "offer", "kind": "tenant", "name": "unit %d" % i}
                    for i in range(1, 13)]  # 12 valid, importable rows
        entities.append({"stage": "offer", "kind": "tenant", "name": "no key on this one"})  # missing key
        entities.append({"key": "crm:unmappedstage", "stage": "not_a_real_stage", "kind": "tenant"})
        snapshot = os.path.join(tmp, "snapshot.json")
        json.dump({"entities": entities}, open(snapshot, "w"))

        buf = io.StringIO()
        with redirect_stdout(buf):
            log_deal.cmd_import_mm(SimpleNamespace(snapshot=snapshot))
        out = buf.getvalue()

        backups = glob.glob(os.path.join(log_deal.BACKUP_DIR, "clients.db.bak-*"))
        check("importing 12 rows creates exactly one backup, not one per row",
              len(backups) == 1, f"backups found: {backups}")

        con = sqlite3.connect(db_path)
        n = con.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
        con.close()
        check("all 12 valid entities were actually imported", n == 12, f"deals row count: {n}")

        check("the missing key entity is reported as unlinked, not unmapped stage",
              "1 unlinked (no key)" in out, out)
        check("the bad stage entity (a real key, unresolvable stage) still counts as unmapped stage",
              "1 unmapped stage" in out, out)
    finally:
        log_deal.DB_PATH, log_deal.BACKUP_DIR = orig_db_path, orig_backup_dir
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'='*60}")
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {', '.join(FAILURES)}")
        sys.exit(1)
    print("all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
