#!/usr/bin/env python3
"""Rerunnable matchmaker lead-revival board. Read only against the matchmaker
tenant/landlord DBs (distinct from revival_scan.py, which mines raw WhatsApp
history). Surfaces "still looking" tenants past the lead-cutoff window alongside
the best-fit currently available listing, for Winfred's manual review before any
outreach. Writes exactly one file, never sends anything, never drafts messages."""
import datetime
import json
import os
import sys

ROOT = os.path.expanduser("~/crestbrick-consult")
sys.path.insert(0, os.path.join(ROOT, "scripts", "matchmaker"))
from export_data import availability, infer_district, looking, num  # noqa: E402

LEAD_CUTOFF_DAYS = 30  # feedback_lead_cutoff.md — leads quieter than this are not revival candidates
BOARD = os.path.expanduser(
    "~/Desktop/Real Estate Related/Winfred Brain/Deals/Matchmaker Revival board.md")


def load():
    ten = json.load(open(os.path.join(ROOT, "_templates/tenant-db.json")))
    land = json.load(open(os.path.join(ROOT, "_templates/landlord-db.json")))
    return ten["tenants"], land["landlords"]


def days_since(datestr, today):
    if not datestr:
        return None
    try:
        y, m, d = [int(x) for x in datestr[:10].split("-")]
        return (today - datetime.date(y, m, d)).days
    except (ValueError, TypeError):
        return None


def best_match(t, avail):
    # Only 9 listings are live citywide -- "some listing passes the budget floor" is not
    # the same as "actually a fit", so this returns a quality TIER, not just a pick.
    b = num(t.get("budget")) or num(t.get("budget_max"))
    pd = t.get("preferred_districts") or []
    td = t.get("district") or ""
    best, best_score = None, -1
    for l in avail:
        if b is not None and l["rent_min"] and b < l["rent_min"] * 0.9:
            continue  # tenant's budget can't reach this landlord's minimum ask, not a fit
        district_ok = (pd and l["district"] in pd) or (td and td == l["district"])
        budget_ok = b is None or not (l["rent_max"] and b > l["rent_max"] * 1.8)
        score = (3 if district_ok else 0) + (2 if budget_ok else 0)
        if score > best_score:
            best_score, best = score, l
    if best is None:
        return None, "none"
    if best_score >= 5:
        return best, "good"
    if best_score >= 2:
        return best, "weak"
    return None, "none"


def fmt_phone(pn):
    pn = (pn or "").strip()
    if pn.startswith("65") and len(pn) == 10:
        return "+65 " + pn[2:6] + " " + pn[6:10]
    return pn or "no phone"


def fmt_money(v):
    return "$" + format(v, ",") if v else "?"


def build_rows(tenants, avail, today):
    rows = []
    for t in tenants:
        if looking(t) != "Still looking":
            continue
        dq = days_since(t.get("last_contact"), today)
        if dq is not None and dq <= LEAD_CUTOFF_DAYS:
            continue  # legitimately active per the lead cutoff, not a revival candidate
        b = num(t.get("budget")) or num(t.get("budget_max"))
        pd_raw = t.get("preferred_districts") or []
        district = (t.get("district") or (pd_raw[0] if pd_raw else "")
                    or infer_district("", pd_raw, t.get("preferred_location")))
        match, tier = best_match(t, avail)
        rows.append({
            "name": t.get("name") or "no name", "phone": t.get("phone") or "",
            "dq": dq, "budget": b, "district": district or "?",
            "pax": t.get("no_of_pax") or "?", "match": match, "tier": tier,
        })
    # good matches first, then weak, then none; within each group by days quiet ascending;
    # no-date entries sort last since we cannot judge how stale they really are
    tier_rank = {"good": 0, "weak": 1, "none": 2}
    rows.sort(key=lambda r: (tier_rank[r["tier"]], r["dq"] is None, r["dq"] if r["dq"] is not None else 0))
    return rows


def render(rows, avail_count, active_count, today):
    lines = [
        "---", f"generated: {today.strftime('%d %b %Y')} SGT",
        f"lead_cutoff_days: {LEAD_CUTOFF_DAYS}", "---", "",
        "# Matchmaker Lead Revival Board", "",
        f"Still-looking tenants past the {LEAD_CUTOFF_DAYS} day lead cutoff, cross-checked against "
        "currently available listings. This is a REVIEW LIST ONLY -- no messages have "
        "been drafted or sent. 'Good match' means the district and budget both line up; "
        "'weak match' means only budget clears (wrong or unknown area) -- worth a second "
        "look, not a send list; 'no match' means nothing currently live fits their budget.", "",
        f"Active leads within {LEAD_CUTOFF_DAYS} days: {active_count} (excluded, not shown here) | "
        f"Past cutoff: {len(rows)} | Good match: {sum(1 for r in rows if r['tier']=='good')} | "
        f"Weak match: {sum(1 for r in rows if r['tier']=='weak')} | "
        f"No match: {sum(1 for r in rows if r['tier']=='none')} | "
        f"Available listings scanned: {avail_count}", "",
        "| Name | Phone | Days quiet | Budget | District wanted | Pax | Match | Best current option |",
        "|---|---|---|---|---|---|---|---|",
    ]
    tier_label = {"good": "✅ good", "weak": "❓ weak", "none": "— none"}
    for r in rows:
        dq = f"{r['dq']}d" if r["dq"] is not None else "no date"
        if r["match"]:
            m = r["match"]
            mtxt = f"{m['name'] or m['id']} · {m['district']} · {fmt_money(m['rent_min'] or m['rent_max'])}"
        else:
            mtxt = "none right now"
        lines.append(f"| {r['name']} | {fmt_phone(r['phone'])} | {dq} | {fmt_money(r['budget'])} "
                      f"| {r['district']} | {r['pax']} | {tier_label[r['tier']]} | {mtxt} |")
    lines.append("")
    return "\n".join(lines)


def main():
    tenants, landlords = load()
    today = datetime.date.today()
    avail = [{
        "id": l.get("id"), "name": l.get("landlord_name"), "district": l.get("district") or "",
        "rent_min": num(l.get("rent_min")), "rent_max": num(l.get("rent_max")),
    } for l in landlords if availability(l) == "Available"]
    active_count = sum(1 for t in tenants
                        if looking(t) == "Still looking"
                        and (days_since(t.get("last_contact"), today) or 0) <= LEAD_CUTOFF_DAYS)
    rows = build_rows(tenants, avail, today)
    out = render(rows, len(avail), active_count, today)
    os.makedirs(os.path.dirname(BOARD), exist_ok=True)
    tmp = BOARD + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(out)
    os.replace(tmp, BOARD)
    print(f"wrote {BOARD}", file=sys.stderr)
    print(f"active(<={LEAD_CUTOFF_DAYS}d)={active_count} past_cutoff={len(rows)} "
          f"good={sum(1 for r in rows if r['tier']=='good')} "
          f"weak={sum(1 for r in rows if r['tier']=='weak')} "
          f"none={sum(1 for r in rows if r['tier']=='none')} avail_listings={len(avail)}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
