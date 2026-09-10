#!/usr/bin/env python3
"""log_deal.py — record a closed deal into clients.db so commission gets tracked.

Audit 9 Sep 2026: zero rows in `deals` ever. monday_brief.py reads `deals`
directly (SUM commission_gross/net WHERE stage IN ('completion','keys',
'closed')) so every deal here lands with stage='closed' + completion_date.
Python 3 stdlib only, /usr/bin/python3 (3.9) compatible.

Usage:
  log_deal.py add --type rental --role landlord --address "123 Example Rd #05-01" \\
      --price 3200 --gross 1600 --source portal --client "Jane Tan"
  log_deal.py list [--year 2026]
  log_deal.py undo <id> [--yes]

DB path overridable with CLIENTS_DB env var, backup dir with CLIENTS_DB_BACKUP_DIR
(tests only — never the real db).
"""
import argparse, glob, os, re, shutil, sqlite3, sys
from datetime import datetime, timedelta, timezone

SGT = timezone(timedelta(hours=8))
HOME = os.path.expanduser("~")
DB_PATH = os.environ.get("CLIENTS_DB", os.path.join(HOME, ".claude", "state", "clients.db"))
BACKUP_DIR = os.environ.get("CLIENTS_DB_BACKUP_DIR", os.path.join(HOME, ".claude", "state", "backups"))
KEEP_BACKUPS = 10
TYPE_CHOICES = ["rental", "resale", "new_launch", "hdb", "commercial"]
ROLE_CHOICES = ["buyer", "seller", "landlord", "tenant", "cobroke"]
SOURCE_CHOICES = ["portal", "website", "sunfacing", "instagram", "referral",
                   "carousell", "fsbo", "walkin", "other", "unknown"]
UNLINKED_SLUG = "unlinked-deal"

def backup_db():
    if not os.path.isfile(DB_PATH):
        return None
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now(SGT).strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(BACKUP_DIR, "clients.db.bak-" + stamp)
    shutil.copy2(DB_PATH, dest)
    for old in sorted(glob.glob(os.path.join(BACKUP_DIR, "clients.db.bak-*")))[:-KEEP_BACKUPS]:
        try:
            os.remove(old)
        except OSError:
            pass
    return dest

def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def parse_money(label, raw, allow_none=False):
    if raw is None:
        if allow_none:
            return None
        raise ValueError("%s is required" % label)
    s = str(raw).strip().replace("$", "").replace(",", "")
    try:
        val = float(s)
    except ValueError:
        raise ValueError("%s must be a number, got %r" % (label, raw))
    if val < 0:
        raise ValueError("%s cannot be negative" % label)
    if val > 500_000_000:
        raise ValueError("%s looks absurd (>$500M) — refusing" % label)
    return val

def parse_cobroke_share(raw, gross):
    """Returns (cobroke_split_pct, cobroke_amount) or (None, None)."""
    if raw is None:
        return None, None
    s = str(raw).strip()
    if s.endswith("%"):
        pct = float(s[:-1])
        if pct < 0 or pct > 100:
            raise ValueError("cobroke share percent must be between 0 and 100")
        return round(pct), round(gross * pct / 100, 2)
    amount = parse_money("cobroke-share", s)
    if amount > gross:
        raise ValueError("cobroke-share ($%.2f) exceeds commission gross ($%.2f) — refusing" % (amount, gross))
    return (round(amount / gross * 100) if gross else 0), amount

def parse_closed_date(raw):
    if not raw:
        return datetime.now(SGT).date().isoformat()
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise ValueError("--closed must be YYYY-MM-DD, got %r" % raw)

def resolve_deal_type(type_, role):
    # deals.deal_type only allows buy/sell/decouple/rent_out/upgrade/new_launch,
    # not our --type vocabulary — map by role, keep --type/--role/--source in notes.
    if type_ == "rental":
        return "rent_out"
    if type_ == "new_launch":
        return "new_launch"
    if role == "seller":
        return "sell"
    if role in ("landlord", "tenant"):
        return "rent_out"
    return "buy"

def find_client(con, name):
    """Returns (slug, display_name) on a single match, raises on 0 or >1."""
    rows = con.execute("SELECT slug, display_name FROM clients WHERE lower(display_name) LIKE ?",
                        ("%" + name.lower() + "%",)).fetchall()
    if not rows:
        return None, None
    if len(rows) > 1:
        names = ", ".join(r["display_name"] for r in rows[:5])
        raise ValueError("%d clients match %r (%s%s) — be more specific" %
                          (len(rows), name, names, ", ..." if len(rows) > 5 else ""))
    return rows[0]["slug"], rows[0]["display_name"]

def quarter_label(d):
    return "%d-Q%d" % (d.year, (d.month - 1) // 3 + 1)

def cmd_add(args):
    price = parse_money("--price", args.price)
    gross = parse_money("--gross", args.gross)
    net = parse_money("--net", args.net, allow_none=True)
    if net is None:
        net = gross
    if gross > price:
        raise ValueError("commission gross ($%.2f) exceeds price ($%.2f) — refusing" % (gross, price))
    if net > gross:
        raise ValueError("commission net ($%.2f) exceeds gross ($%.2f) — refusing" % (net, gross))
    cobroke_pct, cobroke_amount = parse_cobroke_share(args.cobroke_share, gross)
    closed_date = parse_closed_date(args.closed)
    deal_type = resolve_deal_type(args.type, args.role)
    source = args.source or "unknown"
    con = connect()
    if args.client:
        client_slug, client_name = find_client(con, args.client)
        if client_slug is None:
            link_note, client_slug = "no client record matched %r; logged unlinked" % args.client, UNLINKED_SLUG
        else:
            link_note = "linked to client: %s" % client_name
    else:
        link_note, client_slug = "no --client given; logged unlinked", UNLINKED_SLUG
    notes_tag = "[deal-logger type=%s role=%s source=%s]" % (args.type, args.role, source)
    notes = (notes_tag + " " + args.notes).strip() if args.notes else notes_tag
    backup_db()
    cur = con.execute(
        "INSERT INTO deals (client_slug, deal_type, property_address, price, "
        "commission_gross, commission_net, cobroke_agent, cobroke_split_pct, "
        "cobroke_amount, stage, completion_date, closed_date, notes) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (client_slug, deal_type, args.address, price, gross, net,
         "unspecified" if cobroke_pct is not None else None, cobroke_pct,
         cobroke_amount or 0, "closed", closed_date, closed_date, notes))
    deal_id = cur.lastrowid
    con.commit()
    print("Logged deal #%d" % deal_id)
    print("  type=%s role=%s address=%s" % (args.type, args.role, args.address))
    print("  price=$%.2f gross=$%.2f net=$%.2f" % (price, gross, net))
    if cobroke_pct is not None:
        print("  cobroke: %d%% (~$%.2f) — cobroke_agent set to 'unspecified' so commission "
              "views apply the split" % (cobroke_pct, cobroke_amount))
    print("  source=%s closed=%s" % (source, closed_date))
    print("  %s" % link_note)
    qlabel = quarter_label(datetime.strptime(closed_date, "%Y-%m-%d").date())
    row = con.execute("SELECT deals, total_commission, avg_commission FROM v_quarterly_commission "
                       "WHERE quarter = ?", (qlabel,)).fetchone()
    if row:
        print("Quarter to date (%s, v_quarterly_commission): %d deals, gross $%.2f, avg $%.2f" %
              (qlabel, row["deals"], row["total_commission"] or 0, row["avg_commission"] or 0))
    else:
        print("Quarter to date (%s): no rows yet in v_quarterly_commission "
              "(it only counts deals linked to a real client record)" % qlabel)
    con.close()

def cmd_list(args):
    con = connect()
    where, params = "1=1", []
    if args.year:
        where += " AND strftime('%Y', COALESCE(completion_date, created_at)) = ?"
        params.append(str(args.year))
    rows = con.execute("SELECT id, COALESCE(completion_date, substr(created_at,1,10)) AS d, "
                        "deal_type, notes, price, commission_gross, commission_net "
                        "FROM deals WHERE %s ORDER BY d" % where, params).fetchall()
    con.close()
    hdr_fmt = "%-4s %-11s %-9s %-10s %-8s %12s %12s %12s"
    row_fmt = "%-4s %-11s %-9s %-10s %-8s %12.2f %12.2f %12.2f"
    print(hdr_fmt % ("id", "date", "type", "role", "source", "price", "gross", "net"))
    tot_price = tot_gross = tot_net = 0
    for r in rows:
        tag = re.search(r"type=(\S+) role=(\S+) source=([^\s\]]+)", r["notes"] or "")
        type_, role, source = tag.groups() if tag else (r["deal_type"] or "?", "?", "?")
        price, gross, net = r["price"] or 0, r["commission_gross"] or 0, r["commission_net"] or 0
        tot_price += price; tot_gross += gross; tot_net += net
        print(row_fmt % (r["id"], r["d"] or "?", type_, role, source, price, gross, net))
    print("-" * 90)
    print(row_fmt % ("", "TOTAL", "", "", "", tot_price, tot_gross, tot_net))
    print("%d deal(s)" % len(rows))

def cmd_undo(args):
    con = connect()
    row = con.execute("SELECT id, property_address, commission_gross FROM deals WHERE id = ?",
                       (args.id,)).fetchone()
    if not row:
        print("No deal #%d found" % args.id)
        return con.close()
    if not args.yes:
        ans = input("Delete deal #%d (%s, gross $%.2f)? [y/N] " %
                     (row["id"], row["property_address"], row["commission_gross"] or 0))
        if ans.strip().lower() not in ("y", "yes"):
            print("Aborted")
            return con.close()
    backup_db()
    con.execute("DELETE FROM deals WHERE id = ?", (args.id,))
    con.commit()
    con.close()
    print("Deleted deal #%d" % args.id)

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_add = sub.add_parser("add", help="log a closed deal")
    p_add.add_argument("--type", choices=TYPE_CHOICES, required=True)
    p_add.add_argument("--role", choices=ROLE_CHOICES, required=True)
    p_add.add_argument("--address", required=True)
    p_add.add_argument("--price", required=True)
    p_add.add_argument("--gross", required=True)
    p_add.add_argument("--net", default=None)
    p_add.add_argument("--cobroke-share", dest="cobroke_share", default=None)
    p_add.add_argument("--source", choices=SOURCE_CHOICES, default="unknown")
    p_add.add_argument("--client", default=None)
    p_add.add_argument("--closed", default=None)
    p_add.add_argument("--notes", default="")
    p_add.set_defaults(fn=cmd_add)
    p_list = sub.add_parser("list", help="list logged deals")
    p_list.add_argument("--year", type=int, default=None)
    p_list.set_defaults(fn=cmd_list)
    p_undo = sub.add_parser("undo", help="delete a logged deal")
    p_undo.add_argument("id", type=int)
    p_undo.add_argument("--yes", action="store_true")
    p_undo.set_defaults(fn=cmd_undo)
    args = ap.parse_args()
    try:
        args.fn(args)
    except ValueError as e:
        print("Error: %s" % e, file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
