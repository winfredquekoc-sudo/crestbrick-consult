#!/usr/bin/env python3
"""Rerunnable lead revival scanner. Read only against WhatsApp data. Writes exactly
one file: the Revival board markdown. Never sends messages, never mutates any DB.
Adapted from client_miner.py / lead_tiering.py (scratchpad) -- same lid/pn merge,
name resolution, exclusion sets, and intent regexes; new tier/EV rules per spec."""
import argparse, json, os, re, sqlite3, sys, collections
from datetime import datetime, timedelta, timezone

SGT = timezone(timedelta(hours=8))
MSG = os.path.expanduser("~/whatsapp-mcp/whatsapp-bridge/store/messages.db")
WA = os.path.expanduser("~/whatsapp-mcp/whatsapp-bridge/store/whatsapp.db")
LANDLORD_DB = os.path.expanduser("~/crestbrick-consult/_templates/landlord-db.json")
TENANT_DB = os.path.expanduser("~/crestbrick-consult/_templates/tenant-db.json")
COBROKE_DB = os.path.expanduser("~/.claude/state/cobroke-agents.json")
BOARD = os.path.expanduser(
    "~/Desktop/Real Estate Related/Winfred Brain/Deals/Revival board.md")

OWN_LINES = {"6581618149", "228397905117356", "6581211221", "2989431513111"}  # own lines + Ziing
FAMILY = ("quek", "rachel annabel", "madeleine", "don chuang", "wanni", "shaw", "darren",
          "amanda", "mummy", "papa", "daddy", "jason landlord")

BUY = re.compile(r"\b(buy|buying|purchase|new launch|showflat|otp\b|downpayment|absd|bsd\b|mortgage)\b", re.I)
SELL = re.compile(r"\b(sell|selling|sell my|valuation|list my|divest|en.?bloc)\b", re.I)
UPGRADE_STRONG = re.compile(r"\b(mop\b|bto\b|upgrad\w*|\bec\b|resale flat)\b", re.I)
UPGRADE_WEAK = re.compile(r"\b(hfe\b|ipa\b)\b", re.I)  # see kind_and_evidence WHY
INVEST = re.compile(r"\b(invest|investment|yield|portfolio|decoupl|99.to.1|second propert|rental income|passive)\b", re.I)
RENT_STRONG = re.compile(r"\b(rent|rental|lease|tenant|move in|landlord)\b", re.I)
RENT_WEAK = re.compile(r"\b(room|viewing)\b", re.I)  # see kind_and_evidence WHY
CLOSED = re.compile(r"\b(not interested|no thanks|already bought|already found|already sold|"
                     r"confirmed|see you (at|on)|book(ed)? the viewing|deal.?s done|signed the otp)\b", re.I)
VENDOR = re.compile(r"\b(law corporation|conveyancing|kindly contact me should you require)\b", re.I)
# Winfred's own buyer-qualifying form asks "Any property to sell first?" and "HFE valid? (if
# HDB) or IPA valid? (if private)?" on every sale enquiry; a lead answering "no"/"HFE valid"
# still echoes the literal words "sell"/"hfe"/"ipa" in the label, falsely outranking BUY
# (confirmed: Esther, Richard CHOO, Carolyn Chia). Strip those two label lines before intent
# matching -- every other field (name/budget/Timeline to buy/etc) still counts.
FORM_NOISE_LINES = re.compile(
    r"^.*(any propert(?:y|ies) to sell first|hfe valid\?.*ipa valid\?.*private\).*).*$", re.I | re.M)
# cobroke-agents.json is sometimes stale; some competitor agents only reveal themselves by
# naming their agency ("Cheers jade / PropNex PNG"). Brand names are distinctive enough to
# match anywhere with negligible false-positive risk; "era" alone is a common word, so it
# stays restricted to a clear sign-off line.
AGENT_SIGNOFF = re.compile(
    r"\b(propnex|huttons|orangetee|knight frank|savills|edmund tie|dennis wee)\b|\bera\s*$", re.I | re.M)
VENDORS = {"6589239197"}  # Accel Scaling / Salespresso -- false-positive on sell/buy keywords, not a lead

TIER_W = {"HOT": 3, "WARM": 2, "COLD": 1}
KIND_W = {"seller": 5, "upgrader": 4, "buyer": 4, "investor": 4, "rental": 1}


def load_lookups():
    wc = sqlite3.connect("file:" + WA + "?mode=ro", uri=True, timeout=20)
    lid2pn = dict(wc.execute("SELECT lid, pn FROM whatsmeow_lid_map"))
    name_for_pn = {}
    # A contact can have an incomplete name on one jid form (e.g. push_name only on the
    # @lid row) and the real full name on the other (@s.whatsapp.net row). Resolve BOTH
    # forms to the same canonical pn and keep the longest name so family/agent filters
    # can't be dodged by hitting the thinner row first.
    for jid, fn, full, push in wc.execute(
            "SELECT their_jid, first_name, full_name, push_name FROM whatsmeow_contacts"):
        base = jid.split("@")[0].split(":")[0]
        nm = full or fn or push
        if not nm:
            continue
        pn = lid2pn.get(base, base) if jid.endswith("@lid") else base
        cur = name_for_pn.get(pn)
        if not cur or len(nm) > len(cur):
            name_for_pn[pn] = nm
    wc.close()
    return lid2pn, name_for_pn


def load_excludes():
    hard = set(OWN_LINES)
    ldb = json.load(open(LANDLORD_DB))["landlords"]
    for l in ldb:
        hard.add(re.sub(r"\D", "", str(l.get("phone") or "")))
        hard.add(str(l.get("chat_jid") or "").split("@")[0])
    cb = json.load(open(COBROKE_DB))["agents"]
    for a in cb:
        hard.add(str(a.get("jid") or "").split("@")[0])
    tenants = set()
    tdb = json.load(open(TENANT_DB))["tenants"]
    for t in tdb:
        tenants.add(re.sub(r"\D", "", str(t.get("phone") or "")))
        tenants.add(str(t.get("jid") or "").split("@")[0])
    return hard, tenants


def mk_chat():
    return {"in": 0, "out": 0, "buy": [], "sell": [], "upgrade": [], "upgrade_weak": [],
            "invest": [], "rent": [], "rent_weak": [], "last_ts": None, "last_sender": None,
            "last_line": "", "recent": collections.deque(maxlen=4), "is_tenant": False,
            "is_agent": False}


def kind_and_evidence(c):
    # Bare "hfe"/"ipa" also shows up as a rental applicant's pass-type value ("Pass Type:
    # IPA"), unrelated to upgrading (confirmed: Xianji, ishita, Qi En, GagaGood). Bare "room"
    # also shows up in a plain HDB flat-type descriptor on a SALE listing ("3 Room 3A HDB for
    # sale"), unrelated to renting (confirmed: Ruth). Only let either weak signal win last.
    if c["sell"]:
        return "seller", c["sell"]
    if c["upgrade"]:
        return "upgrader", c["upgrade"]
    if c["buy"]:
        return "buyer", c["buy"]
    if c["invest"]:
        return "investor", c["invest"]
    if c["rent"]:
        return "rental", c["rent"]
    if c["upgrade_weak"]:
        return "upgrader", c["upgrade_weak"]
    if c["rent_weak"]:
        return "rental", c["rent_weak"]
    return None, []


def scan(days):
    lid2pn, name_for_pn = load_lookups()
    hard_known, tenant_set = load_excludes()
    mc = sqlite3.connect("file:" + MSG + "?mode=ro", uri=True, timeout=20)
    rows = mc.execute(
        "SELECT chat_jid, is_from_me, content, timestamp FROM messages "
        "WHERE datetime(timestamp) > datetime('now', ?) AND content != '' "
        "AND chat_jid NOT LIKE '%@g.us' AND chat_jid NOT LIKE '%@newsletter' "
        "AND chat_jid NOT LIKE '120363%' AND chat_jid NOT LIKE '%@broadcast' ORDER BY rowid",
        (f"-{days} days",)).fetchall()
    mc.close()

    chats = collections.defaultdict(mk_chat)
    for jid, ifm, ct, ts in rows:
        base = jid.split("@")[0]
        pn = lid2pn.get(base, base) if jid.endswith("@lid") else base
        if pn in hard_known or base in hard_known or pn in VENDORS or base in VENDORS:
            continue
        nm = (name_for_pn.get(pn) or "").lower()
        if any(f in nm for f in FAMILY):
            continue
        # group by canonical pn, not raw jid base -- the same person can carry both an older
        # @s.whatsapp.net thread and a newer @lid thread resolving to the same pn; keying on
        # the raw base fragments one lead into two chats and can hide the recent thread.
        c = chats[pn]
        if pn in tenant_set or base in tenant_set:
            c["is_tenant"] = True
        c["in" if not ifm else "out"] += 1
        try:
            tdt = datetime.fromisoformat(str(ts))
            if tdt.tzinfo is None:
                tdt = tdt.replace(tzinfo=SGT)
        except ValueError:
            continue
        c["last_ts"] = tdt
        c["last_sender"] = "them" if not ifm else "winfred"
        c["recent"].append(ct)
        if not ifm:
            if AGENT_SIGNOFF.search(ct):
                c["is_agent"] = True
            line = " ".join(ct.split())[:120]
            c["last_line"] = line
            intent_ct = FORM_NOISE_LINES.sub("", ct)
            if SELL.search(intent_ct) and len(c["sell"]) < 2:
                c["sell"].append(line)
            if UPGRADE_STRONG.search(intent_ct) and len(c["upgrade"]) < 2:
                c["upgrade"].append(line)
            if UPGRADE_WEAK.search(intent_ct) and len(c["upgrade_weak"]) < 2:
                c["upgrade_weak"].append(line)
            if BUY.search(intent_ct) and len(c["buy"]) < 2:
                c["buy"].append(line)
            if INVEST.search(intent_ct) and len(c["invest"]) < 2:
                c["invest"].append(line)
            if RENT_STRONG.search(intent_ct) and len(c["rent"]) < 2:
                c["rent"].append(line)
            if RENT_WEAK.search(intent_ct) and len(c["rent_weak"]) < 2:
                c["rent_weak"].append(line)

    now = datetime.now(SGT)
    leads = []
    for pn, c in chats.items():
        if c["last_ts"] is None or c["in"] < 1:
            continue
        kind, evidence = kind_and_evidence(c)
        if kind is None:
            continue
        if c["is_tenant"] and kind not in ("seller", "upgrader", "buyer"):
            continue  # rental/investor intent alone does not surface an existing tenant
        nm = name_for_pn.get(pn) or "no name saved"
        if re.search(r"\b(landlord|cobroke|co-broke|agent|broker)\b", nm, re.I):
            continue  # contact self-tagged in saved name; registries are sometimes stale
        if c["is_agent"]:
            continue  # signed off with a competitor agency name -- not Winfred's lead
        if c["in"] == 1 and VENDOR.search(c["last_line"]):
            continue  # single-message professional-service solicitation, not a lead
        if closed_thread(c):
            continue  # deal already closed or declined -- nothing to revive
        days_quiet = (now - c["last_ts"]).days
        tier = classify_tier(days_quiet, c["last_sender"])
        mult = 2 if c["last_sender"] == "them" else 1
        ev = TIER_W[tier] * KIND_W[kind] * mult
        leads.append({
            "name": nm, "phone": pn, "tier": tier, "kind": kind, "ev": ev,
            "days_quiet": days_quiet, "last_speaker": "Them" if c["last_sender"] == "them" else "Winfred",
            "evidence": snippet(evidence[0] if evidence else ""),
        })

    leads.sort(key=lambda x: (-x["ev"], x["days_quiet"], x["name"]))
    return leads, len(chats)


def closed_thread(c):
    return any(CLOSED.search(x) for x in c["recent"])


def classify_tier(days_quiet, last_sender):
    if days_quiet <= 5 and last_sender == "them":
        return "HOT"
    if days_quiet <= 14:
        return "WARM"
    return "COLD"


def snippet(text):
    text = " ".join(text.split()).replace("|", "/")
    return text if len(text) <= 80 else text[:77] + "..."


def fmt_phone(pn):
    if pn.startswith("65") and len(pn) == 10:
        return f"+65 {pn[2:6]} {pn[6:10]}"
    return "+" + pn


def load_notes(path):
    if not os.path.exists(path):
        return "## Notes\n"
    text = open(path, encoding="utf-8").read()
    m = re.search(r"^## Notes\b.*", text, re.S | re.M)
    return m.group(0).rstrip("\n") if m else "## Notes\n"


def render(leads, scanned, days):
    counts = collections.Counter(l["tier"] for l in leads)
    now = datetime.now(SGT)
    lines = []
    lines.append("---")
    lines.append(f"generated: {now.strftime('%d %b %Y %H:%M')} SGT")
    lines.append(f"window_days: {days}")
    lines.append("---")
    lines.append("")
    lines.append("# Lead Revival Board")
    lines.append("")
    lines.append("HOT and WARM leads are actionable now. COLD entries are past the five "
                  "day dead lead rule and are manual judgment calls only. Landlords are "
                  "fully excluded from this scan so they never sit in COLD by that rule.")
    lines.append("")
    lines.append(f"HOT {counts.get('HOT', 0)} | WARM {counts.get('WARM', 0)} | "
                 f"COLD {counts.get('COLD', 0)} | Total {len(leads)} | Chats scanned {scanned}")
    lines.append("")
    lines.append("## Top 20 by revival value")
    lines.append("")
    lines.append("| Rank | EV | Name | Phone | Tier | Kind | Days Quiet | Last Speaker | Evidence |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for i, l in enumerate(leads[:20], 1):
        lines.append(f"| {i} | {l['ev']} | {l['name']} | {fmt_phone(l['phone'])} | {l['tier']} | "
                      f"{l['kind']} | {l['days_quiet']} | {l['last_speaker']} | {l['evidence']} |")
    lines.append("")
    for tier in ("HOT", "WARM", "COLD"):
        group = [l for l in leads if l["tier"] == tier]
        lines.append(f"## {tier} ({len(group)})")
        lines.append("")
        if not group:
            lines.append("None this run.")
        for l in group:
            lines.append(f"* {l['name']}  {fmt_phone(l['phone'])}  {l['kind']}  {l['days_quiet']}d quiet")
        lines.append("")
    lines.append(load_notes(BOARD))
    lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()
    leads, scanned = scan(args.days)
    out = render(leads, scanned, args.days)
    os.makedirs(os.path.dirname(BOARD), exist_ok=True)
    tmp = BOARD + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(out)
    os.replace(tmp, BOARD)
    counts = collections.Counter(l["tier"] for l in leads)
    print(f"wrote {BOARD}", file=sys.stderr)
    print(f"HOT={counts.get('HOT',0)} WARM={counts.get('WARM',0)} COLD={counts.get('COLD',0)} "
          f"total={len(leads)} chats_scanned={scanned}", file=sys.stderr)


if __name__ == "__main__":
    main()
