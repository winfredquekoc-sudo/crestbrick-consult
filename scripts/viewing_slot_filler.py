#!/usr/bin/env python3
"""
viewing_slot_filler.py — calendar aware slot filler for the WA rental intake engine.

For every OPEN listing that has NO fixed_viewing and NO open future viewing slot, this
proposes ONE new slot into viewing-availability.json so intake_engine.next_future_slot()
can offer it to the next tenant who enquires. It never touches fixed_viewing listings
(those are strictly funnelled to their one weekly time) or closed/hold listings.

Date picked: the first day >= today+2 (SGT) on which Winfred is free in the evening
after 19:30 per his Google Calendar. Time: 19:30, or the first free 15 minute block
after 19:30 that evening. If the listing already has a slot that same day (a viewer
already booked), the new slot stacks 15 minutes after the last slot's start on the
SAME day, back to back, as long as that stays a reasonable evening hour; otherwise it
rolls over to the next free evening instead of stacking indefinitely.

SAFE BY DEFAULT: this script only ever PRINTS the proposed slots as JSON. Nothing is
written to disk, and no tenant is ever messaged (that is intake_engine's job, not this
script's) unless you pass --write.

    python3 viewing_slot_filler.py              # dry run (default) — prints only
    python3 viewing_slot_filler.py --write       # writes new slots into
                                                  # ~/.claude/state/listing-templates/viewing-availability.json

Calendar read: reuses the exact OAuth refresh token + direct REST pattern already live
in ~/.claude/bin/viewing-arranged-ping.py (a launchd job that polls the same calendar
every 30 min) rather than inventing a new integration. See winfred_free_evening() below
— that is the ONE function isolating the calendar dependency; every other function in
this file is pure and independently testable with it stubbed out.
"""
import argparse, datetime, json, os, sys, urllib.request, urllib.parse
from pathlib import Path

# Prefer whatever intake_engine is already importable (e.g. a test setting PYTHONPATH to
# a worktree copy); only fall back to the live working directory used in production —
# see reference_wa_pipeline_deploy_model: the engine runs from the working dir, not main.
try:
    from intake_engine import listing_reqs, _listing_unavailable, AVAIL
except ImportError:
    sys.path.append(os.path.expanduser("~/crestbrick-consult/src/wa-pipeline"))
    from intake_engine import listing_reqs, _listing_unavailable, AVAIL

SGT = datetime.timezone(datetime.timedelta(hours=8))
CAL_ID = "winfredquekoc@gmail.com"          # "Winfred Real Estate Cal", his primary calendar
TOKEN_PATH = Path(os.path.expanduser("~/.google-mcp/tokens/winfred.json"))
CRED_PATH = Path(os.path.expanduser("~/.google-mcp/credentials.json"))

EVENING_FLOOR = datetime.time(19, 30)       # never propose a start before 7.30pm
EVENING_CEILING = datetime.time(22, 0)      # last reasonable NEW (non stacked) start
STACK_CEILING = datetime.time(22, 30)       # last reasonable STACKED start before rolling over
VIEWING_MINUTES = 30                        # each proposed slot's duration
STACK_STEP_MINUTES = 15                     # gap between stacked back to back slots
CALENDAR_HORIZON_DAYS = 21                  # give up looking for a free evening after 3 weeks
QUIET_HOUR_START = 23
QUIET_HOUR_END = 8

_WD_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_MON_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _now_sgt():
    return datetime.datetime.now(SGT)


def _today_sgt():
    return _now_sgt().date()


# ---------- calendar read (the one open integration point) ----------
def _access_token():
    """Refresh token -> access token via the same urllib + oauth2 flow as
    viewing-arranged-ping.py. Raises on any failure; callers treat that as
    'calendar unreachable', never as 'Winfred is free'."""
    tok = json.loads(TOKEN_PATH.read_text())
    cred = json.loads(CRED_PATH.read_text())
    inst = cred.get("installed", cred.get("web", {}))
    data = urllib.parse.urlencode({
        "client_id": inst["client_id"],
        "client_secret": inst["client_secret"],
        "refresh_token": tok["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())["access_token"]


def _calendar_busy_intervals(date_obj):
    """Direct Google Calendar v3 read for one SGT calendar day. Returns a list of
    (start_dt, end_dt) SGT aware datetimes covering every busy period that day, or
    None if the calendar could not be reached (auth/network failure) — callers must
    treat None as 'unknown', never as 'free'."""
    try:
        at = _access_token()
    except Exception:
        return None
    day_start = datetime.datetime.combine(date_obj, datetime.time(0, 0), tzinfo=SGT)
    day_end = day_start + datetime.timedelta(days=1)
    url = ("https://www.googleapis.com/calendar/v3/calendars/" + urllib.parse.quote(CAL_ID)
           + "/events?" + urllib.parse.urlencode({
               "timeMin": day_start.isoformat(), "timeMax": day_end.isoformat(),
               "singleEvents": "true", "orderBy": "startTime", "maxResults": 100}))
    try:
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + at})
        with urllib.request.urlopen(req, timeout=20) as r:
            ev = json.loads(r.read())
    except Exception:
        return None
    out = []
    for e in ev.get("items", []):
        if e.get("status") == "cancelled":
            continue
        s = e.get("start", {}) or {}
        en = e.get("end", {}) or {}
        if "dateTime" not in s:
            out.append((day_start, day_end))    # all day event: busy the whole day
            continue
        try:
            sdt = datetime.datetime.fromisoformat(s["dateTime"]).astimezone(SGT)
            edt = datetime.datetime.fromisoformat(en["dateTime"]).astimezone(SGT)
        except Exception:
            continue
        out.append((sdt, edt))
    return out


def _first_free_start(date_obj, floor=EVENING_FLOOR, ceiling=STACK_CEILING):
    """First 'HH:MM' free VIEWING_MINUTES block on date_obj's SGT evening, at or after
    `floor` and starting at or before `ceiling`. None if no such block exists, or if
    the calendar could not be reached."""
    busy = _calendar_busy_intervals(date_obj)
    if busy is None:
        return None
    cur = datetime.datetime.combine(date_obj, floor, tzinfo=SGT)
    last_start = datetime.datetime.combine(date_obj, ceiling, tzinfo=SGT)
    step = datetime.timedelta(minutes=15)
    dur = datetime.timedelta(minutes=VIEWING_MINUTES)
    while cur <= last_start:
        cur_end = cur + dur
        if not any(cur < b_end and cur_end > b_start for b_start, b_end in busy):
            return cur.strftime("%H:%M")
        cur += step
    return None


def winfred_free_evening(after_date, horizon_days=CALENDAR_HORIZON_DAYS):
    """First SGT date >= after_date on which Winfred has a free evening block (>= 19:30)
    per his Google Calendar ('Winfred Real Estate Cal', winfredquekoc@gmail.com). Returns
    a date, or None if nothing is free within `horizon_days` OR the calendar could not be
    reached — this fails CLOSED (never guesses a date is free when it cannot check).

    This is the single, well named seam isolating the calendar dependency: every other
    function below is pure and takes a date, so tests stub this one function out."""
    for i in range(horizon_days):
        d = after_date + datetime.timedelta(days=i)
        if _first_free_start(d, ceiling=EVENING_CEILING) is not None:
            return d
    return None


# ---------- pure slot construction (independently testable) ----------
def _hm_add(hm, minutes):
    h, m = map(int, hm.split(":"))
    total = h * 60 + m + minutes
    total %= 24 * 60
    return f"{total // 60:02d}:{total % 60:02d}"


def _label(date_obj, start_hm):
    h, m = map(int, start_hm.split(":"))
    suffix = "am" if h < 12 else "pm"
    h12 = h % 12 or 12
    time_txt = f"{h12}" + (f".{m:02d}" if m else "") + suffix
    return f"{_WD_ABBR[date_obj.weekday()]} {date_obj.day} {_MON_ABBR[date_obj.month - 1]}, after {time_txt}"


def _slot_id(listing_key, date_str, start_hm):
    return f"{listing_key}-slotfill-{date_str}-{start_hm.replace(':', '')}"


def _has_fixed_viewing(listing):
    return bool(listing.get("fixed_viewing") or (listing.get("requirements", {}) or {}).get("fixed_viewing"))


def _listing_has_open_future_slot(entry, today):
    """Mirrors intake_engine.next_future_slot's own future/open/capacity check (minus the
    fixed_viewing branch, handled separately by _has_fixed_viewing) but works off a given
    availability entry instead of re-reading disk, so it composes with injected data in
    tests. today is a date; 'future' means strictly after today, OR today (any time —
    conservative: a same day slot still counts as 'has one', the exact hour is intake_engine's
    call at offer time, not this idempotency gate's)."""
    today_str = today.isoformat()
    for s in (entry.get("slots") or []):
        if s.get("status") != "open":
            continue
        if s.get("booked", 0) >= s.get("capacity", 1):
            continue
        if s.get("date", "") >= today_str:
            return True
    return False


def propose_slot_for_listing(listing_key, existing_entry, today=None):
    """Pure aside from the calendar read: given the listing's CURRENT availability entry
    {"listing_key":..., "slots":[...]}, return a new proposed slot dict, or None if none
    should be proposed (no free evening found within the horizon / calendar unreachable)."""
    today = today or _today_sgt()
    earliest = today + datetime.timedelta(days=2)
    day = winfred_free_evening(earliest)
    if day is None:
        return None
    slots_today = [s for s in (existing_entry.get("slots") or []) if s.get("date") == day.isoformat()]
    if slots_today:
        last = max(slots_today, key=lambda s: s.get("start") or "00:00")
        candidate_start = _hm_add(last["start"], STACK_STEP_MINUTES)
        if candidate_start <= STACK_CEILING.strftime("%H:%M"):
            start = candidate_start
        else:
            # stacking would run too late in the evening: roll over to the next free evening
            # instead, rather than stacking indefinitely.
            day2 = winfred_free_evening(day + datetime.timedelta(days=1))
            if day2 is None:
                return None
            day = day2
            start = _first_free_start(day, ceiling=EVENING_CEILING) or EVENING_FLOOR.strftime("%H:%M")
    else:
        start = _first_free_start(day, ceiling=EVENING_CEILING) or EVENING_FLOOR.strftime("%H:%M")
    end = _hm_add(start, VIEWING_MINUTES)
    date_str = day.isoformat()
    return {
        "slot_id": _slot_id(listing_key, date_str, start),
        "date": date_str,
        "start": start,
        "end": end,
        "label": _label(day, start),
        "capacity": 1,
        "booked": 0,
        "status": "open",
        "proposed": True,
        "source": "slot-filler",
    }


def build_proposals(today=None, listings=None, avail_data=None):
    """Pure planning pass (aside from the calendar read inside propose_slot_for_listing):
    returns [(listing_key, proposed_slot_dict), ...] for every OPEN listing that needs
    one. `listings` / `avail_data` are injectable so tests never touch the real files."""
    today = today or _today_sgt()
    listings = listing_reqs() if listings is None else listings
    if avail_data is None:
        avail_data = _load_avail()
    proposals = []
    for lk, listing in listings.items():
        # "OPEN" means genuinely live and postable — the two statuses actually used for
        # real rentable rooms. Anything else (pending_99co, hold, closed*, ...) is
        # deliberately excluded even though _listing_unavailable() alone would let some of
        # those through (it only flags closed/hold, not "not yet set up").
        if str(listing.get("status") or "").strip().lower() not in ("open", "active"):
            continue
        if _listing_unavailable(lk, listings) is not None:
            continue                                    # master DB says closed/archived
        if _has_fixed_viewing(listing):
            continue                                    # strictly funnelled to its fixed slot
        entry = avail_data.get(lk) or {"listing_key": lk, "slots": []}
        if _listing_has_open_future_slot(entry, today):
            continue                                    # idempotent: already has one to offer
        slot = propose_slot_for_listing(lk, entry, today=today)
        if slot is None:
            continue
        proposals.append((lk, slot))
    return proposals


# ---------- disk IO (only reached with --write) ----------
def _load_avail():
    try:
        return json.load(open(AVAIL))
    except Exception:
        return {}


def _write_proposals(proposals):
    """Atomic temp+rename write, same pattern intake_engine uses for its own state file.
    Re reads the file under an exclusive lock immediately before writing to minimise the
    race window against a concurrent booking."""
    import fcntl
    f = open(AVAIL, "r+")
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            data = json.load(f)
        except Exception:
            data = {}
        for lk, slot in proposals:
            entry = data.get(lk)
            if entry is None:
                entry = {"listing_key": lk, "slots": []}
                data[lk] = entry
            entry.setdefault("slots", []).append(slot)
        tmp_path = AVAIL + ".tmp"
        with open(tmp_path, "w") as tmp:
            json.dump(data, tmp, indent=2, ensure_ascii=False)
        os.replace(tmp_path, AVAIL)
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true",
                     help="Actually write proposed slots into viewing-availability.json. "
                          "Without this flag the script only prints what it would do.")
    args = ap.parse_args(argv)

    now = _now_sgt()
    if args.write and (now.hour >= QUIET_HOUR_START or now.hour < QUIET_HOUR_END):
        print(json.dumps({"skipped": "quiet_hours", "hour_sgt": now.hour}, indent=2))
        return 0

    proposals = build_proposals(today=now.date())
    report = {
        "generated_at": now.isoformat(),
        "dry_run": not args.write,
        "proposed_count": len(proposals),
        "proposals": [{"listing_key": lk, "slot": slot} for lk, slot in proposals],
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))

    if args.write and proposals:
        _write_proposals(proposals)
        print(f"Wrote {len(proposals)} proposed slot(s) to {AVAIL}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
