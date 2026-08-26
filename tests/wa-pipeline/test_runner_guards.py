import sys, os, datetime
sys.path.insert(0, os.path.expanduser("~/crestbrick-consult/src/wa-pipeline"))
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

src = open(os.path.expanduser("~/crestbrick-consult/src/wa-pipeline/wa_intake_runner.py")).read()
send_block = src[src.index("HARD SEND SAFEGUARDS"):src.index("if E.DRY_RUN:")]
ok("freshness guard sits before every real send path", "STALE_SKIP" in send_block and "SEND_MAX_INBOUND_AGE_HOURS" in send_block)
ok("manual takeover guard sits before every real send path", "TAKEOVER_SKIP" in send_block and "manual_takeover" in send_block)
ok("co-pilot actions are exempt from the takeover guard only", 'a.get("copilot")' in send_block)

print("== daily cap: max 2 automated touches per client per day (29 Jul 2026) ==")
ok("cap constant is 2", getattr(R, "DAILY_SEND_CAP", None) == 2)
ok("cap guard sits before every real send path", "DAILY_CAP_SKIP" in send_block and "DAILY_SEND_CAP" in send_block)
ok("cap keyed to the SGT day", "8 * 3600" in send_block and "sends_today_date" in send_block)
ok("viewing confirmations exempt (a YES must never dead-end overnight)",
   '"CONFIRM_VIEWING"' in send_block
   and ('a.get("type") != "CONFIRM_VIEWING"' in send_block
        or 'a.get("type") not in ("CONFIRM_VIEWING", "OFFER_VIEWING", "ASK_ONE")' in send_block))
after_send = src[src.index("count this touch against the per-client daily cap"):]
ok("counter increments only on a fully delivered send", "sends_today" in after_send[:500]
   and src.index("count this touch") > src.index("_r0.pop(\"partial_sent\", None)"))

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
