import sys, os, datetime
# resolve relative to THIS file so the suite tests the checkout/worktree it lives in, not
# whichever copy happens to be at the shared live path (matches test_intake_engine.py).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src", "wa-pipeline"))
import wa_intake_runner as R

P = 0; F = 0
def ok(name, cond):
    global P, F
    if cond: P += 1; print("  PASS", name)
    else: F += 1; print("  FAIL", name)

print("== runner send safeguards (29 Jul 2026, post cold-lead blast) ==")

ok("5 day rule constant present", getattr(R, "SEND_MAX_INBOUND_AGE_HOURS", None) == 120)
ok("48h stale row guard still present", getattr(R, "STALE_ROW_HOURS", None) == 48)

now_sgt = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
fresh = now_sgt.strftime("%Y-%m-%d %H:%M:%S+08:00")
ok("fresh row -> age ~0h", R._real_age_hours(fresh) < 1)

old6d = (now_sgt - datetime.timedelta(days=6)).strftime("%Y-%m-%d %H:%M:%S+08:00")
ok("6 day old row -> older than the 5 day send limit",
   R._real_age_hours(old6d) > R.SEND_MAX_INBOUND_AGE_HOURS)

old3d = (now_sgt - datetime.timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S+08:00")
ok("3 day old row -> inside the 5 day send limit (but outside 48h backfill guard)",
   R.STALE_ROW_HOURS < R._real_age_hours(old3d) < R.SEND_MAX_INBOUND_AGE_HOURS)

# the bridge stamps mixed offsets (-04:00 seen in prod) — age must honour the offset
mixed = (datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-4)))
         - datetime.timedelta(days=6)).strftime("%Y-%m-%d %H:%M:%S-04:00")
ok("mixed offset (-04:00) 6 day old row -> still stale", R._real_age_hours(mixed) > 120)

ok("unparseable ts -> treated as fresh (never drop a live lead)", R._real_age_hours("garbage") == 0)

src = open(os.path.join(_REPO_ROOT, "src", "wa-pipeline", "wa_intake_runner.py")).read()
send_block = src[src.index("HARD SEND SAFEGUARDS"):src.index("if E.DRY_RUN:")]
# the daily cap decision itself (bounded-once fields, the ordinary ceiling, the notify
# text) was split out to wa_intake_send.daily_cap_should_skip, 9 Sep 2026 merge review --
# the runner's own HARD SEND SAFEGUARDS block just calls it now.
send_module_src = open(os.path.join(_REPO_ROOT, "src", "wa-pipeline", "wa_intake_send.py")).read()
ok("freshness guard sits before every real send path", "STALE_SKIP" in send_block and "SEND_MAX_INBOUND_AGE_HOURS" in send_block)
ok("manual takeover guard sits before every real send path", "TAKEOVER_SKIP" in send_block and "manual_takeover" in send_block)
ok("co-pilot actions are exempt from the takeover guard only", 'a.get("copilot")' in send_block)

print("== daily cap: max 2 automated touches per client per day (29 Jul 2026) ==")
ok("cap constant is 2", getattr(R, "DAILY_SEND_CAP", None) == 2)
ok("cap guard sits before every real send path", "DAILY_CAP_SKIP" in send_block and "DAILY_SEND_CAP" in send_block)
ok("cap keyed to the SGT day",
   "8 * 3600" in send_block and "sends_today_date" in send_module_src)
ok("viewing confirmations exempt (a YES must never dead-end overnight)",
   '"CONFIRM_VIEWING"' in send_module_src
   and ('a.get("type") != "CONFIRM_VIEWING"' in send_module_src
        or 'a.get("type") in ("CONFIRM_VIEWING", "OFFER_VIEWING", "ASK_ONE")' in send_module_src
        or ('a.get("type") not in ("CONFIRM_VIEWING", "OFFER_VIEWING", "ASK_ONE",'
            in send_module_src)))
# REDIRECT (unit gone / policy excluded / cross sell) and LEASE_NOTE are each a direct,
# one-shot reply to something the tenant just said -- holding them for the ordinary daily
# cap dead-ends a prospect who was mid conversation (9 Sep 2026 cycle 3 attack replay fix).
# Bounded to ONE touch a day via their own date stamp (merge review 9 Sep 2026 -- same
# mechanism as the VIEWING_TIME_PROPOSED/ASK_TENANT_TIME time reply, never fully exempt;
# see tests/wa-pipeline/test_time_reply_cap.py for the behavioural coverage).
ok("daily cap decision delegated to wa_intake_send.daily_cap_should_skip",
   "daily_cap_should_skip" in send_block)
ok("REDIRECT and LEASE_NOTE also bounded (one touch a day) from the daily cap",
   '"REDIRECT"' in send_module_src and '"LEASE_NOTE"' in send_module_src
   and "redirect_sent_date" in send_module_src
   and "lease_note_reply_sent_date" in send_module_src)
# whatever type the ORDINARY cap DOES still hold back must still reach Winfred -- a held
# reply must never vanish with zero signal. (Not the earlier bounded-once DAILY_CAP_SKIP
# shared by the time reply / REDIRECT / LEASE_NOTE carve out -- that one intentionally does
# not notify, same as before this fix.)
_ordinary_cap_marker = "Daily touch cap reached for "
ok("a daily cap skip still force notifies Winfred",
   _ordinary_cap_marker in send_module_src
   and "notify_winfred_coalesced(" in send_block)
after_send = src[src.index("count this touch against the per-client daily cap"):]
ok("counter increments only on a fully delivered send", "sends_today" in after_send[:500]
   and src.index("count this touch") > src.index("_r0.pop(\"partial_sent\", None)"))

print("== A1: PRE-PASS decision never latches on a pasted blank intake form ==")
_ZWJ = "⁠"
_bl = lambda label: "•" + _ZWJ + "  " + _ZWJ + label
_blank_form = ("Hi can help fill in so I can send tenant\n\n"
               + "\n".join(_bl(x) for x in ("Email address:", "Name:", "Nationality:",
                                            "Ethnicity:", "Gender:", "Age:")))
ok("blank form paste (no header, ZWJ, real row 326110 shape) -> FORM_PASTED, not LATCH",
   R._prelatch_decision(_blank_form) == "FORM_PASTED")

_filled_form = (_bl("Name: chris") + "\n" + _bl("Nationality: malaysia") + "\n"
                + _bl("Ethnicity: chinese") + "\n" + _bl("Gender:female") + "\n"
                + _bl("Age:50"))
ok("FILLED profile forward (real row 327078 shape) -> LATCH (still a human message)",
   R._prelatch_decision(_filled_form) == "LATCH")

ok("a genuine hand reply ('ok can, see you saturday') -> LATCH",
   R._prelatch_decision("ok can, see you saturday") == "LATCH")

# the engine's own literal blank template is structurally indistinguishable from a hand
# paste of it (both are the blank form, verbatim) -- it still resolves to FORM_PASTED here,
# but the caller's "not _rec.get('form_sent')" guard makes the stamp a no-op once the
# engine's own send flow has already set form_sent, so nothing double fires in practice.
ok("the engine's own exact template send also reads as a (harmless, idempotent) FORM_PASTED",
   R._prelatch_decision(R.E.INTAKE_FORM) == "FORM_PASTED")

ok("pre-pass wires the pure decision helper, not an inline is_engine_outbound branch",
   "_prelatch_decision(_content)" in src and "FORM_PASTED" in src)

print("== A4: match_listing() prefers OPEN entries over CLOSED ones ==")
# a stale keyword can survive on a CLOSED row that also matches a live OPEN one (the
# reviewer found "ang mo kio ave 3" on both). Fixture dict order deliberately puts the
# CLOSED entry FIRST so a naive first-match-wins scan would return the wrong (closed) one.
_reqs_amk = {
    "amk-closed": {"listing_key": "amk-closed", "status": "closed (tenanted)",
                   "pg_url_keywords": ["ang mo kio ave 3"]},
    "amk-open": {"listing_key": "amk-open", "status": "open",
                 "pg_url_keywords": ["ang mo kio ave 3"]},
}
ok("closed-first dict order still resolves to the OPEN listing",
   R.match_listing("still available at ang mo kio ave 3?", _reqs_amk) == "amk-open")

_reqs_amk_open_first = {
    "amk-open": _reqs_amk["amk-open"], "amk-closed": _reqs_amk["amk-closed"],
}
ok("open-first dict order also resolves to the OPEN listing (order never matters)",
   R.match_listing("still available at ang mo kio ave 3?", _reqs_amk_open_first) == "amk-open")

_reqs_only_closed = {"amk-closed": _reqs_amk["amk-closed"]}
ok("a CLOSED listing is still returned when nothing OPEN matches (never silently drop it)",
   R.match_listing("still available at ang mo kio ave 3?", _reqs_only_closed) == "amk-closed")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
