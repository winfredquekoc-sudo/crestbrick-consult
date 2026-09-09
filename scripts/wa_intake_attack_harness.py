#!/usr/bin/env python3
"""
wa_intake_attack_harness.py -- replay an adversarial WhatsApp conversation through the REAL
intake_engine / wa_intake_runner code, entirely inside a throwaway sandbox. Nothing here ever
touches the live messages.db, the live listing-templates state, or a real WhatsApp/Telegram
send -- every path the runner touches on disk is redirected under a tempdir, and every path
it touches over the network (the bridge send, the cross-sender guard, Telegram, the Haiku
draft call) is a recording stub. Modeled directly on tests/wa-pipeline/test_takeover_resume.py
(_isolated_runner / TestResumeSendSite) and test_runner_guards.py -- read those first if this
file's plumbing is unclear.

Scenario JSON shape:
{
  "name": "short-slug",                       # required; also the default tenant number seed
  "listing_key": "informational only, optional",
  "tenant_number": "8161XXXX",                 # optional: override the tenant's bare number
  "fixtures": {                                # optional: merged into the COPIED reference
      "listing": {...},                        # files only -- the live ones are never written
      "template": {...},
      "availability": {...}
  },
  "messages": [
    {"t": 600, "from": "tenant"|"winfred"|"maddie", "text": "...",
     "media_type": null, "jid": "optional override", "self_chat": false}
  ]
}
"t" is seconds BEFORE "now" the message is stamped at (600 = 10 minutes ago). Messages must
be given oldest first -- they are inserted, and the runner is ticked, in that order, so a
later message can build on an earlier one's latch exactly as production would. A "tenant" row
MUST live on an "@lid" chat_jid (the runner's own SQL only ever selects '%@lid' or Winfred's
own self chat) -- this harness picks that jid for you unless a message sets "jid" itself.
"winfred" / "maddie" rows are stamped is_from_me=1 in the SAME chat (a hand reply or assistant
reply typed into the tenant's own thread); set "self_chat": true to route a "winfred" row
through the OWN_JID self-chat command console instead (the /send, /drop draft commands).

Usage:
  wa_intake_attack_harness.py scenario.json          run one scenario, print full JSON result
  wa_intake_attack_harness.py --batch DIR            run every *.json in DIR, write <name>.result.json
                                                      next to each, print a summary table
  wa_intake_attack_harness.py --self-test            run the embedded self test (also the default
                                                      with no arguments)
"""
import sys, os, io, re, json, time, sqlite3, hashlib, tempfile, argparse, traceback, contextlib, datetime
from unittest import mock

# Telegram/bridge kill switch (incident, 9 Sep 2026 merge redo -- see wa_intake_notify.py's
# _tg_send / wa_intake_send.py's _send docstrings for the root cause). Set BEFORE importing
# any wa-pipeline module: belt and suspenders alongside the per-scenario mock.patch calls and
# the physical choke-point checks those two functions now do on their own.
os.environ["WA_INTAKE_NO_TELEGRAM"] = "1"
os.environ["WA_INTAKE_NO_SEND"] = "1"

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_REPO_ROOT, "src", "wa-pipeline")
sys.path.insert(0, _SRC)

import intake_engine as E                    # noqa: E402
import wa_intake_resume as RES                # noqa: E402
import wa_intake_runner as R                  # noqa: E402
import wa_intake_notify as N                  # noqa: E402  -- patched directly below: R's
                                               # notify_winfred is a SEPARATE import binding
                                               # from N's own (see _tg_send's docstring)

# ---------- read-only live sources (copied into the sandbox, NEVER written) ----------
_LIVE_IDX        = E.IDX
_LIVE_TEMPLATES  = E.TEMPLATES
_LIVE_AVAIL      = E.AVAIL
_LIVE_LANDLORD   = E.LANDLORD_DB
_LIVE_COBROKE    = E.COBROKE_DB

# real messages.db schema, PRAGMA-inspected once (2026-09-09) and hardcoded here so the
# harness never has to open the live bridge store at all, read-only or otherwise.
_MESSAGES_SCHEMA = """
CREATE TABLE messages (
    id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TIMESTAMP,
    is_from_me BOOLEAN, media_type TEXT, filename TEXT, url TEXT,
    media_key BLOB, file_sha256 BLOB, file_enc_sha256 BLOB, file_length INTEGER
)"""
_CHATS_SCHEMA = "CREATE TABLE chats (jid TEXT, name TEXT, last_message_time TIMESTAMP)"
_WA_SCHEMA = """
CREATE TABLE whatsmeow_contacts (their_jid TEXT, full_name TEXT, push_name TEXT, business_name TEXT);
CREATE TABLE whatsmeow_lid_map (lid TEXT, pn TEXT)
"""


def _load_json(path, default):
    try:
        return json.load(open(path))
    except Exception:
        return default


def _tenant_jid(scenario):
    """Deterministic per-scenario fake number, prefixed '70' (no real SG mobile/landline
    starts with 70) so it can never collide with a real number sitting in the copied
    landlord DB. An explicit "tenant_number" always wins."""
    if scenario.get("tenant_number"):
        return str(scenario["tenant_number"]) + "@lid"
    h = hashlib.sha256(scenario.get("name", "scenario").encode()).hexdigest()
    return "70" + "".join(c for c in h if c.isdigit())[:8] + "@lid"


def _sgt_ts(seconds_ago):
    dt = (datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
          - datetime.timedelta(seconds=seconds_ago))
    return dt.isoformat()


def _merge_listing_fixture(idx_doc, fixture):
    idx_doc["listings"] = [l for l in idx_doc.get("listings", [])
                           if l.get("listing_key") != fixture.get("listing_key")]
    idx_doc["listings"].append(fixture)


def _merge_template_fixture(tpl_doc, fixture):
    tpl_doc["listings"] = [l for l in tpl_doc.get("listings", [])
                           if l.get("id") != fixture.get("id")]
    tpl_doc["listings"].append(fixture)


def _canned_haiku_draft(prompt):
    """Stub for the Haiku draft call -- a fixed line that passes RES.validate_draft() (no
    hyphen/dash, no CEA mention, no money figure, no advice wording, <=3 sentences) so a
    scenario that reaches the resume-draft path exercises the REAL validator against
    something a live draft would actually look like, instead of always hitting the failure
    branch."""
    return ("Thanks for checking in! I am still around and happy to help sort this out. "
            "Would a viewing this week suit you?"), None


def build_sandbox(tmp_dir, scenario):
    """Creates every throwaway file the run needs and returns a dict of their paths, plus the
    scenario's own tenant jid/pn. Called once per scenario."""
    open(os.path.join(tmp_dir, "SANDBOX"), "w").close()   # third, independent kill switch
                                                            # signal -- see _tg_send/_send
    msg_db = os.path.join(tmp_dir, "messages.db")
    con = sqlite3.connect(msg_db)
    con.execute(_MESSAGES_SCHEMA)
    con.execute(_CHATS_SCHEMA)
    con.commit()

    wa_db = os.path.join(tmp_dir, "whatsapp.db")
    wcon = sqlite3.connect(wa_db)
    wcon.executescript(_WA_SCHEMA)
    wcon.commit(); wcon.close()

    idx_doc = _load_json(_LIVE_IDX, {"listings": []})
    tpl_doc = _load_json(_LIVE_TEMPLATES, {"listings": []})
    avail_doc = _load_json(_LIVE_AVAIL, {})
    landlord_doc = _load_json(_LIVE_LANDLORD, {"landlords": []})
    cobroke_doc = _load_json(_LIVE_COBROKE, {})

    fx = scenario.get("fixtures") or {}
    if fx.get("listing"):
        _merge_listing_fixture(idx_doc, fx["listing"])
    if fx.get("template"):
        _merge_template_fixture(tpl_doc, fx["template"])
    if fx.get("availability"):
        avail_doc[fx["availability"]["listing_key"]] = fx["availability"]

    idx_path = os.path.join(tmp_dir, "listing-index.json")
    tpl_path = os.path.join(tmp_dir, "property-templates.json")
    avail_path = os.path.join(tmp_dir, "viewing-availability.json")
    landlord_path = os.path.join(tmp_dir, "landlord-db.json")
    cobroke_path = os.path.join(tmp_dir, "cobroke-agents.json")
    for path, doc in ((idx_path, idx_doc), (tpl_path, tpl_doc), (avail_path, avail_doc),
                      (landlord_path, landlord_doc), (cobroke_path, cobroke_doc)):
        json.dump(doc, open(path, "w"), ensure_ascii=False)

    state_path = os.path.join(tmp_dir, "intake-state.json")
    json.dump({"version": 1, "conversations": {}}, open(state_path, "w"))

    drafts_path = os.path.join(tmp_dir, "drafts.jsonl")
    open(drafts_path, "w").close()

    return {
        "con": con, "msg_db": msg_db, "wa_db": wa_db,
        "idx_path": idx_path, "tpl_path": tpl_path, "avail_path": avail_path,
        "landlord_path": landlord_path, "cobroke_path": cobroke_path,
        "state_path": state_path, "drafts_path": drafts_path,
        "lastf": os.path.join(tmp_dir, "runner-last.json"),
        "lockf": os.path.join(tmp_dir, ".wa-intake.lock"),
        "tenant_jid": _tenant_jid(scenario),
    }


def _record_snapshot(state, pn):
    rec = (state.get("conversations") or {}).get(pn) or {}
    return {k: rec.get(k) for k in ("stage", "status", "listing_key", "listing_key_source",
                                    "manual_takeover", "form_sent", "profile", "qualify")}


def run_scenario(scenario):
    """Runs the whole scenario inside one tempdir + one mock.patch.ExitStack. Ticks
    wa_intake_runner.run() once per message, exactly as the launchd job would tick every
    120s -- one new inbound (or one hand reply) per tick."""
    with tempfile.TemporaryDirectory(prefix="wa-attack-") as tmp_dir:
        sb = build_sandbox(tmp_dir, scenario)
        with open(sb["lastf"], "w") as f:
            json.dump({"last_rowid": 0}, f)

        # module level caches that would otherwise leak a PRIOR scenario's sandbox paths
        # across a --batch run (see harness docstring / commit message for why these three).
        E._landlord_form_recipients.cache_clear()
        E._cobroke_agent_pn_set.cache_clear()
        if hasattr(E, "_TPL_HEADS"):
            delattr(E, "_TPL_HEADS")

        sent, notified, logged, actions = [], [], [], []

        def fake_send(jid, text):
            sent.append({"jid": jid, "text": text}); return True

        def fake_guard(jid):
            return True

        def fake_notify(msg):
            notified.append(msg)

        def fake_log(kind, pn, msg):
            logged.append({"kind": kind, "pn": pn, "msg": msg})

        orig_handle_event = E.handle_event

        def spy_handle_event(state, ev):
            a = orig_handle_event(state, ev)
            actions.append(dict(a) if isinstance(a, dict) else a)
            return a

        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(R, "MSG_DB", sb["msg_db"]))
        stack.enter_context(mock.patch.object(R, "LASTF", sb["lastf"]))
        stack.enter_context(mock.patch.object(R, "LOCKF", sb["lockf"]))
        stack.enter_context(mock.patch.object(RES, "DRAFTS_FILE", sb["drafts_path"]))
        stack.enter_context(mock.patch.object(E, "STATE", sb["state_path"]))
        stack.enter_context(mock.patch.object(E, "IDX", sb["idx_path"]))
        stack.enter_context(mock.patch.object(E, "TEMPLATES", sb["tpl_path"]))
        stack.enter_context(mock.patch.object(E, "AVAIL", sb["avail_path"]))
        stack.enter_context(mock.patch.object(E, "LANDLORD_DB", sb["landlord_path"]))
        stack.enter_context(mock.patch.object(E, "COBROKE_DB", sb["cobroke_path"]))
        stack.enter_context(mock.patch.object(E, "WA_DB", sb["wa_db"]))
        stack.enter_context(mock.patch.object(E, "MSG_DB", sb["msg_db"]))
        stack.enter_context(mock.patch.object(E, "DRY_RUN", False))
        stack.enter_context(mock.patch.object(E, "handle_event", spy_handle_event))
        stack.enter_context(mock.patch.object(R, "_send", fake_send))
        stack.enter_context(mock.patch.object(R, "_guard_reserve", fake_guard))
        stack.enter_context(mock.patch.object(R, "notify_winfred", fake_notify))
        stack.enter_context(mock.patch.object(R, "_log", fake_log))
        stack.enter_context(mock.patch.object(R, "_alert_hourly", lambda *a, **k: None))
        stack.enter_context(mock.patch.object(R, "_drain_notify_queue", lambda: None))
        stack.enter_context(mock.patch.object(R, "_quiet_hours", lambda: False))
        # N (wa_intake_notify) itself, not just R's separate import binding above --
        # notify_for_action/notify_winfred_coalesced/_flush_stale_coalesce_windows/
        # notify_stale_backfill are DEFINED in N and resolve notify_winfred/_tg_send as N's
        # OWN globals, never touched by patching R's binding (this is the exact bug behind
        # the incident this harness redo is guarding against). Recorded into the SAME
        # `notified` list so callers see one combined feed regardless of which path fired.
        stack.enter_context(mock.patch.object(N, "notify_winfred", fake_notify))
        stack.enter_context(mock.patch.object(N, "_tg_send", lambda msg: (_ for _ in ()).throw(
            AssertionError("_tg_send reached the real send path -- kill switch bypassed"))))
        stack.enter_context(mock.patch.object(N, "STATE_DIR", tmp_dir))
        stack.enter_context(mock.patch.object(RES, "call_haiku", _canned_haiku_draft))
        stack.enter_context(mock.patch("time.sleep", lambda *a: None))

        results = []
        pns_seen = set()
        with stack:
            for i, m in enumerate(scenario.get("messages") or []):
                role = m.get("from", "tenant")
                is_from_me = 1 if role in ("winfred", "maddie") else 0
                jid = m.get("jid") or (RES.OWN_JID if m.get("self_chat") else sb["tenant_jid"])
                pn_guess = jid.split("@")[0] if jid != RES.OWN_JID else None
                if pn_guess:
                    pns_seen.add(pn_guess)
                ts = _sgt_ts(int(m.get("t", 0)))
                entry = {"index": i, "t": m.get("t", 0), "from": role, "text": m.get("text", ""),
                         "jid": jid, "actions": [], "sent": [], "notified": [], "exception": None}
                s0, n0, a0 = len(sent), len(notified), len(actions)
                try:
                    sb["con"].execute(
                        "INSERT INTO messages (id, chat_jid, sender, content, timestamp, "
                        "is_from_me, media_type) VALUES (?,?,?,?,?,?,?)",
                        ("ATK%06d" % i, jid, jid.split("@")[0], m.get("text", ""), ts,
                         is_from_me, m.get("media_type") or ""))
                    sb["con"].commit()
                    with contextlib.redirect_stdout(io.StringIO()):   # swallow run()'s own
                        R.run()                                       # per tick print()
                except Exception:
                    entry["exception"] = traceback.format_exc()
                entry["actions"] = actions[a0:]
                entry["sent"] = sent[s0:]
                entry["notified"] = notified[n0:]
                if pn_guess:
                    state = E.load_state()
                    entry["record"] = _record_snapshot(state, pn_guess)
                results.append(entry)

            final_state = E.load_state()
            final_records = {pn: _record_snapshot(final_state, pn) for pn in sorted(pns_seen)}

        return {
            "scenario": scenario.get("name"),
            "tenant_jid": sb["tenant_jid"],
            "tenant_pn": sb["tenant_jid"].split("@")[0],
            "messages": results,
            "final_records": final_records,
            "log": logged,
        }


def _self_test_scenario():
    """portal enquiry -> filled form -> YES, against a synthetic listing that exists only
    inside this scenario's own fixtures (never a real address). Kept independent of whatever
    the live listing-index.json happens to contain today, so this scenario's assertions never
    drift with production data."""
    tomorrow = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=8))) + datetime.timedelta(days=1)
    slot_label = tomorrow.strftime("%a %d %b") + ", 2pm to 3pm"
    return {
        "name": "self-test-portal-form-yes",
        "listing_key": "attack-selftest-room",
        "fixtures": {
            "listing": {
                "listing_key": "attack-selftest-room", "status": "open", "deal_type": "rent",
                "pg_url_keywords": ["attack selftest room", "selftestroom999999"],
                "requirements": {
                    "gender": "any", "couple_ok": True, "couple_must_be_married": False,
                    "ethnicity_rule": {"mode": "any", "list": []},
                    "nationality_pref": {"mode": "any", "list": []},
                    "pass_type_allowed": [], "occupation_rule": {"mode": "any", "list": []},
                    "max_pax": 2, "lease_min_months": 12, "lease_max_months": None,
                    "budget_floor": 1000, "min_age": None, "cooking": "light",
                    "pets_tenant_may_bring": False, "smoking": "no",
                    "notes_human": "Synthetic self test listing, harness only.",
                },
            },
            "template": {
                "id": "attack-selftest-room",
                "pg_url_keywords": ["attack selftest room"],
                "message": ("Hi! The attack selftest room is still open for a look :) "
                            "Common room near a fake test MRT stop. Light cooking allowed, "
                            "no smoking, no pets. Owner keen to meet a tenant soon."),
            },
            "availability": {
                "listing_key": "attack-selftest-room",
                "slots": [{"slot_id": "attack-selftest-room-slot-1",
                          "date": tomorrow.strftime("%Y-%m-%d"), "start": "14:00", "end": "15:00",
                          "label": slot_label, "capacity": 5, "booked": 0, "status": "open"}],
            },
        },
        "messages": [
            {"t": 600, "from": "tenant",
             "text": "Hi is the room at attack selftest room still available? Saw it on PropertyGuru."},
            {"t": 300, "from": "tenant",
             "text": ("Name: Alex Tan\nNationality: Singaporean\nEthnicity: Chinese\n"
                       "Gender: Male\nPass type: Citizen\nNo. of pax: 1\n"
                       "Move in date: 1 Oct\nLease term: 12\nBudget: 1200")},
            {"t": 0, "from": "tenant", "text": "YES"},
        ],
    }


def _print_json(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def run_self_test():
    result = run_scenario(_self_test_scenario())
    _print_json(result)
    types = [m["actions"][0]["type"] if m["actions"] else None for m in result["messages"]]
    ok = len(types) >= 2 and types[0] == "SEND_FORM" and types[1] == "OFFER_VIEWING"
    print("\nSELF-TEST " + ("PASS" if ok else "FAIL")
          + f" -- action types in order: {types}", file=sys.stderr)
    return 0 if ok else 1


def run_batch(directory):
    files = sorted(f for f in os.listdir(directory)
                   if f.endswith(".json") and not f.endswith(".result.json"))
    rows = []
    for fname in files:
        path = os.path.join(directory, fname)
        scenario = _load_json(path, None)
        if scenario is None:
            rows.append((fname, "UNREADABLE", 0, 0, 0)); continue
        try:
            result = run_scenario(scenario)
        except Exception:
            result = {"scenario": scenario.get("name", fname), "messages": [],
                      "final_records": {}, "log": [], "exception": traceback.format_exc()}
        out_path = os.path.join(directory, fname[:-5] + ".result.json")
        json.dump(result, open(out_path, "w"), indent=2, ensure_ascii=False, default=str)
        n_msgs = len(result.get("messages", []))
        n_sent = sum(len(m.get("sent", [])) for m in result.get("messages", []))
        n_exc = sum(1 for m in result.get("messages", []) if m.get("exception")) + \
                (1 if result.get("exception") else 0)
        rows.append((fname, result.get("scenario", fname), n_msgs, n_sent, n_exc))

    print(f"{'file':<40} {'scenario':<30} {'msgs':>5} {'sent':>5} {'exc':>4}")
    for fname, name, n_msgs, n_sent, n_exc in rows:
        print(f"{fname:<40} {str(name):<30} {n_msgs:>5} {n_sent:>5} {n_exc:>4}")
    return 1 if any(r[4] for r in rows) else 0


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("scenario", nargs="?", help="scenario JSON file")
    p.add_argument("--batch", metavar="DIR", help="run every *.json scenario in DIR")
    p.add_argument("--self-test", action="store_true", help="run the embedded self test")
    args = p.parse_args()

    if args.self_test or (not args.scenario and not args.batch):
        return run_self_test()
    if args.batch:
        return run_batch(args.batch)
    scenario = _load_json(args.scenario, None)
    if scenario is None:
        print(f"could not read scenario file: {args.scenario}", file=sys.stderr)
        return 2
    _print_json(run_scenario(scenario))
    return 0


if __name__ == "__main__":
    sys.exit(main())
