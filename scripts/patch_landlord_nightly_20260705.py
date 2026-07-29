#!/usr/bin/env python3
"""Nightly landlord DB patch for 2026-07-05. One-shot script, safe to re-run (idempotent)."""
import json, os, tempfile, shutil

DB_PATH = os.path.expanduser("~/crestbrick-consult/_templates/landlord-db.json")
TODAY = "2026-07-05"
NOW_SGT = "2026-07-05T00:00:13+0800"

with open(DB_PATH) as f:
    db = json.load(f)

landlords = db["landlords"]
idx = {ll["id"]: ll for ll in landlords}

def patch(lid, **kwargs):
    if lid not in idx:
        return False
    for k, v in kwargs.items():
        if v is not None:
            idx[lid][k] = v
    return True

# LL017 Asmah: add ethnicity restriction
if "ethnicity" not in idx.get("LL017", {}).get("requirements", {}):
    idx["LL017"]["requirements"]["ethnicity"] = "No Indian (landlord preference)"
idx["LL017"]["last_contact"] = "2026-07-03"
idx["LL017"]["last_refreshed"] = TODAY

# LL028 Fei: confirm rent $1300 firm
idx["LL028"]["rent_min"] = 1300
idx["LL028"]["rent_max"] = 1300
idx["LL028"]["last_refreshed"] = TODAY

# LL033 LK Eastpoint Green: viewing done 4 Jul, Ryan Chuah
ll = idx["LL033"]
ll["follow_up"] = (
    ll.get("follow_up","") +
    "; 4 Jul: viewing at 6pm done (LK alone). Ryan Chuah (M'sian Chinese M 28 WP flight steward 1pax $950). "
    "Ryan said will reply by Mon 6 Jul after more viewings in area."
)
ll["last_contact"] = "2026-07-04"
ll["viewing_availability"] = "Digital lock code shared; unaccompanied viewing ok; Sat fixed 6 to 7pm"
ll["viewing_availability_updated"] = TODAY
ll["last_refreshed"] = TODAY

# LL044 Ram: CLOSED tenanted
ll = idx["LL044"]
ll["status"] = "closed (tenanted)"
ll["follow_up"] = (
    ll.get("follow_up","") +
    "; 4 Jul 2026: tenant found per Ram ('I just found a tenant for the room'). No further action. Closed."
)
ll["last_contact"] = "2026-07-04"
ll["last_refreshed"] = TODAY

# LL052 Sheena: viewing done 4 Jul 9pm
ll = idx["LL052"]
ll["follow_up"] = (
    ll.get("follow_up","") +
    "; 4 Jul: viewing at 9pm done. 2 viewers: Angelynn (M'sian Chinese F 25 WP 1pax move-in 7 Jul 1yr) "
    "and Alvin Wong Ping Rui (M'sian Chinese M, details partial). Unit 168A Simei Lane #07-38 confirmed."
)
ll["last_contact"] = "2026-07-04"
ll["last_refreshed"] = TODAY

# LL063 Leyi: open house 4 Jul done, no deposit
ll = idx["LL063"]
ll["follow_up"] = (
    ll.get("follow_up","") +
    "; 4 Jul: open house done 7.15 to 8.45pm. CN group profile (6 pax WP/SP/EP hairdresser/engineer/service, "
    "Han, 2yr lease immediate) viewed. Another viewer arrived 8.30pm. No deposit received; viewers still deciding. "
    "Washer 9kg dryer 6kg confirmed. Leyi removing desk and piano from living room on 9 Jul."
)
ll["last_contact"] = "2026-07-04"
ll["viewing_availability"] = "Sat/Sun afternoon and Tue/Wed evenings by appointment; open house model"
ll["viewing_availability_updated"] = TODAY
ll["last_refreshed"] = TODAY

# LL064 Simon: OK for social media marketing, no unit number online
ll = idx["LL064"]
ll["follow_up"] = (
    ll.get("follow_up","") +
    "; 4 Jul: Winfred asked to market online. Simon approved social media but NO unit number online "
    "until tenant is keen. Will remove from social media once rented."
)
ll["last_contact"] = "2026-07-04"
ll["last_refreshed"] = TODAY

# LL068 Mr Chong: Winfred in hospital, colleague to help
ll = idx["LL068"]
ll["follow_up"] = (
    ll.get("follow_up","") +
    "; 4 Jul: Winfred missed Mr Chong call (was in hospital). Asked to share details with colleague "
    "so she can help get things moving. Mr Chong agreed ('Take care, dont worry; Sure Tq')."
)
ll["last_contact"] = "2026-07-04"
ll["last_refreshed"] = TODAY

# LL070 Lili: rejected tenant, available 5 Jul before 3pm
ll = idx["LL070"]
ll["follow_up"] = (
    ll.get("follow_up","") +
    "; 3 Jul: rejected an unspecified prospect. Confirmed away from 15 Jul; prefers quiet single female, "
    "no visitors, hassle free. 4 Jul: tried to arrange viewing, Lili was out. "
    "Confirmed: can arrange viewing 5 Jul before 3pm."
)
ll["last_contact"] = "2026-07-04"
ll["viewing_availability"] = "Available before 3pm on 5 Jul (confirmed 4 Jul); advance notice required; away from 15 Jul"
ll["viewing_availability_updated"] = TODAY
ll["last_refreshed"] = TODAY

# LL073 David: update requirements from Jul 2 intake + note second property at The Florida 538805
ll = idx["LL073"]
ll["requirements"] = {
    "gender": "Male only",
    "ethnicity": "No Indian, no Malay (landlord preference)",
    "nationality": "No Indian, no Malaysian (landlord preference)",
    "occupation": "Office work professionals, university students, expats",
    "max_pax": "1 male tenant only",
    "lease_min": 12,
    "lease_max": "",
    "cooking": "Not specified",
    "pets": "No",
    "utilities": "Included (aircon tenant handles for own room only)",
    "other": "Fully furnished (common room). MOP met, sole owner. 2 share bathroom. Rent via bank transfer before 28th of each month.",
    "smoking": "No",
    "subletting": "",
    "overnight_visitors": "",
    "owner_on_site": "Yes (owner staying with single male landlord only)",
    "commission": "",
    "postal": ""
}
ll["rooms_and_rent"] = "Common room $1,200 (1yr lease)"
ll["rent_min"] = 1200
ll["rent_max"] = 1200
ll["follow_up"] = (
    "Jul 2: full Kim Keat Ave intake received. Common room $1,200 1yr, male only, no Indians/Malays, "
    "fully furnished, sole owner + single male staying. MOP met, room rental. "
    "4 Jul: SECOND intake received for The Florida 538805 (master+common rooms available, "
    "owner family in unit, move in July 1st, common $1,400/master $1,900 1pax +$100 2pax, "
    "utilities incl, cooking yes, all races ok). "
    "Note: The Florida 538805 same address as LL038 (JC) but different unit in condo. "
    "Confirm with David whether second property or same as LL038."
)
ll["last_contact"] = "2026-07-04"
ll["last_refreshed"] = TODAY

# LL008 Frank: refresh only (no substantive change)
idx["LL008"]["last_refreshed"] = TODAY

# LL020 Buva: refresh only
idx["LL020"]["last_refreshed"] = TODAY

# Add new landlords LL076, LL077, LL078
new_landlords = [
    {
        "id": "LL076",
        "landlord_name": "Dwight Fonseka",
        "phone": "+6585124032",
        "chat_jid": "132474055225412@lid",
        "full_address": "58 Circuit Road #04-163 S370058",
        "property_type": "HDB room",
        "deal_type": "rent",
        "rooms_and_rent": "Common room $1,500 (Carousell listing: Cozy Room with Furniture and Aircon)",
        "requirements": {
            "other": "Intake form sent 4 Jul; LL said will answer tomorrow. Address 58 Circuit Road #04-163. Near McPherson MRT and Mattar MRT, near Circuit Road hawker centre.",
            "smoking": "",
            "subletting": "",
            "overnight_visitors": "",
            "owner_on_site": "",
            "commission": "",
            "postal": "370058"
        },
        "status": "stalled",
        "follow_up": "Inbound 4 Jul via Carousell (cozy room S$1,500). Intake form sent. LL said will answer tomorrow (5 Jul). Chase full intake: rent, requirements, availability.",
        "last_contact": "2026-07-04",
        "last_refreshed": TODAY,
        "contact_label_source": "saved WhatsApp contact (name contains 'landlord')",
        "rent_min": 1500,
        "rent_max": 1500
    },
    {
        "id": "LL077",
        "landlord_name": "Hong Jia (Mr Fung and Madam Hong Jia)",
        "phone": "+6590091383",
        "chat_jid": "128617292021967@lid",
        "full_address": "34 Jalan Tanjong Singapore 468039 (standalone studio at landed house, 430sqft)",
        "property_type": "Standalone studio at landed house",
        "deal_type": "rent",
        "rooms_and_rent": "Studio $2,200/month (own bathroom, not shared; 6 existing housemates in house)",
        "requirements": {
            "gender": "Any",
            "ethnicity": "",
            "nationality": "",
            "occupation": "",
            "max_pax": "1 (URA registration; LL said can register with URA)",
            "lease_min": 12,
            "cooking": "Induction (no gas)",
            "pets": "",
            "utilities": "Electricity per meter; water $35/month; WiFi included; no gas",
            "other": "Tenant has own bathroom (not shared). 6 existing housemates mostly male. Aircon quarterly service by tenant. First 14 days LL repairs free; after 14 days tenant pays $50-$200. Rent cash 29th to 1st of month. Fully furnished (sofa dining TV). Owner NOT staying.",
            "smoking": "",
            "subletting": "",
            "overnight_visitors": "",
            "owner_on_site": "No (owner not staying)",
            "commission": "0.5 month",
            "postal": "468039"
        },
        "status": "active",
        "follow_up": "Inbound 3 Jul self ID as landlord (co-living interested; confirmed direct owner). Partial intake returned 3 Jul afternoon: 34 Jalan Tanjong S468039, standalone studio 430sqft, Tanah Merah MRT 8 min, available now vacant, $2,200 cash, 1yr+, WiFi incl, electricity per meter + water $35, induction no gas. Own bathroom. 6 housemates (mostly male). Fully furnished. Commission 0.5mth. Tenant preferences (nationality/occupation/smoking etc.) NOT yet filled; chase remainder of intake.",
        "last_contact": "2026-07-03",
        "last_refreshed": TODAY,
        "contact_label_source": "saved WhatsApp contact (name contains 'landlord')",
        "rent_min": 2200,
        "rent_max": 2200
    },
    {
        "id": "LL078",
        "landlord_name": "Willy",
        "phone": "+6593216199",
        "chat_jid": "68161181348077@lid",
        "full_address": "105A Bidadari Park Drive #09-36",
        "property_type": "HDB 5rm (common room)",
        "deal_type": "rent",
        "rooms_and_rent": "Common room $1,100 to $1,200 (available from 7/8 Aug)",
        "requirements": {
            "gender": "Female only (Chinese only)",
            "ethnicity": "Chinese only (landlord preference)",
            "nationality": "",
            "occupation": "",
            "max_pax": "1",
            "lease_min": 12,
            "cooking": "Instant noodles only (no cooking)",
            "pets": "No",
            "utilities": "Included",
            "other": "Owner staying with 14yo boy, 6yo girl, 1 helper. Bathroom shared. Aircon night only. Washing 2x/week. No visitors, no smoking, no pets. Own HDB 5rm. Rent 1st day of move-in.",
            "smoking": "No",
            "subletting": "",
            "overnight_visitors": "No visitors",
            "owner_on_site": "Yes (owner + 2 children + 1 helper)",
            "commission": "0.5 month for 1yr",
            "postal": ""
        },
        "status": "active",
        "follow_up": "Inbound 4 Jul via Carousell (near Potong Pasir MRT). Full intake returned same day: 105A Bidadari Park Drive #09-36, Potong Pasir MRT 5 min, avail 7/8 Aug, $1,100 to $1,200 1yr. Female 1pax only Chinese. No cooking except instant noodles. Utilities incl. Owner stays with family (2 kids + helper). Commission 0.5mth/1yr confirmed. Joined Winfred group chat 4 Jul.",
        "last_contact": "2026-07-04",
        "last_refreshed": TODAY,
        "contact_label_source": "saved WhatsApp contact (name contains 'landlord')",
        "rent_min": 1100,
        "rent_max": 1200,
        "viewing_availability": "By appointment with owner present",
        "viewing_availability_updated": TODAY
    }
]

# Append new landlords only if not already present
existing_ids = {ll["id"] for ll in landlords}
for nl in new_landlords:
    if nl["id"] not in existing_ids:
        landlords.append(nl)

# Recompute totals
total = len(landlords)
closed_count = sum(1 for ll in landlords if ll.get("status","").startswith("closed"))
active_count = sum(1 for ll in landlords if ll.get("status","") in ("active","active-verify","channel","sale-active"))
db["total"] = total
db["active"] = active_count
db["closed"] = closed_count
db["last_updated"] = NOW_SGT

# Atomic write
tmp = DB_PATH + ".tmp"
with open(tmp, "w") as f:
    json.dump(db, f, indent=1, ensure_ascii=False)
shutil.move(tmp, DB_PATH)
print(f"Done. total={total} active={active_count} closed={closed_count}")
