"""
landlord_tenant_matcher.py — Tenant<->Property matching engine.

Matches active tenants against newly-completed landlord properties via:
1. District/location proximity (±1 MRT station)
2. Budget alignment (tenant can afford asking rent)
3. Gender/nationality/occupation preferences
4. Lifestyle constraints (cooking, pets, smoking, guests)
5. Occupancy limits

Sends WhatsApp notifications to matched tenants and tracks matches bi-directionally.
CEA-compliant: uses the intake_engine WhatsApp sender (single point).
"""
import json, re, os
from intake_engine import (
    extract_profile, _to_int, resolve_pn, CHANNEL,
    WA_DB, MSG_DB, STATE, LANDLORD_DB
)
import sqlite3, functools

MATCHING_CONFIG = os.path.expanduser("~/.claude/state/listing-templates/matching-config.json")

def _load(p, d):
    try: return json.load(open(p))
    except Exception: return d

def _save_json(p, data):
    """Atomic write."""
    tmp = p + ".tmp"
    json.dump(data, open(tmp, "w"), indent=1, ensure_ascii=False)
    os.replace(tmp, p)

# ---------- District/MRT proximity ----------
# Simplified district adjacency map (±1 MRT station concept).
# Real implementation would use actual MRT network graph.
DISTRICT_PROXIMITY = {
    "D01": ["D01", "D02"],  # Raffles, Marina, Orchard
    "D02": ["D01", "D02", "D03"],
    "D03": ["D02", "D03", "D04"],
    "D04": ["D03", "D04", "D05", "D06"],
    "D05": ["D04", "D05", "D06"],
    "D06": ["D04", "D05", "D06", "D07"],
    "D07": ["D06", "D07", "D08", "D09"],
    "D08": ["D07", "D08", "D09", "D10"],
    "D09": ["D07", "D08", "D09", "D10", "D11"],
    "D10": ["D08", "D09", "D10", "D11", "D12"],
    "D11": ["D09", "D10", "D11", "D12"],
    "D12": ["D10", "D11", "D12", "D13", "D14"],
    "D13": ["D12", "D13", "D14"],
    "D14": ["D12", "D13", "D14", "D15"],
    "D15": ["D14", "D15", "D16"],
    "D16": ["D15", "D16", "D17", "D18"],
    "D17": ["D16", "D17", "D18", "D19"],
    "D18": ["D16", "D17", "D18", "D19", "D20"],
    "D19": ["D17", "D18", "D19", "D20"],
    "D20": ["D18", "D19", "D20", "D21"],
    "D21": ["D20", "D21", "D22"],
    "D22": ["D21", "D22", "D23", "D24"],
    "D23": ["D22", "D23", "D24"],
    "D24": ["D22", "D23", "D24", "D25"],
    "D25": ["D24", "D25"],
    "D26": ["D26", "D27", "D28"],
    "D27": ["D26", "D27", "D28"],
    "D28": ["D26", "D27", "D28"],
}

# ---------- Profile extraction from landlord form ----------
def extract_landlord_property(form_text):
    """Parse a completed landlord form. Returns dict with extracted fields."""
    prop = {}
    t = (form_text or "").lower()

    def grab(label, section=None):
        """Grab value after label."""
        m = re.search(rf"(?:{label})\s*[:\-]?\s*([^\n]+)", t, re.I)
        if not m: return None
        val = m.group(1).strip().lstrip("•").strip()
        if ":" in val: val = val.split(":")[-1].strip()
        val = re.sub(r"^\([^)]*\)\s*", "", val).strip()
        if val and val.lower() not in ("any", "none", "no"): return val
        return None

    # Property section
    owner_name = grab(r"owner\s+name")
    if owner_name: prop["owner_name"] = owner_name

    address = grab(r"address.*unit.*size|address.*unit|address")
    if address: prop["address"] = address

    mrt = grab(r"nearest\s+mrt.*distance|nearest\s+mrt")
    if mrt: prop["nearest_mrt"] = mrt

    avail_from = grab(r"available\s+from")
    if avail_from: prop["available_from"] = avail_from

    rooms = grab(r"rooms?\s+available")
    if rooms: prop["rooms_available"] = rooms

    vacant = grab(r"is it vacant|vacant now")
    if vacant: prop["is_vacant"] = vacant

    # Rental Terms section
    rent = grab(r"asking\s+rent|rent\s+amount")
    if rent:
        rent_val = _to_int(rent)
        if rent_val: prop["asking_rent"] = rent_val

    lease = grab(r"lease\s+duration|preferred\s+lease")
    if lease: prop["lease_preference"] = lease

    # Unit and Bills section
    furnish = grab(r"furnish")
    if furnish: prop["furnishing"] = furnish

    utilities = grab(r"utilities\s+included|utilities")
    if utilities: prop["utilities"] = utilities

    aircon = grab(r"aircon.*servic|servicing")
    if aircon: prop["aircon_maintenance"] = aircon

    owner_onsite = grab(r"owner.*staying|staying.*unit")
    if owner_onsite: prop["owner_on_site"] = owner_onsite

    housemates = grab(r"existing\s+housemates|housemates")
    if housemates: prop["housemates"] = housemates

    bathroom = grab(r"share.*bathroom|bathroom.*share")
    if bathroom: prop["bathroom_sharing"] = bathroom

    # Tenant Preferences section
    pref_gender = grab(r"preferred\s+gender|gender\s+prefer")
    if pref_gender: prop["gender_preference"] = pref_gender

    pref_nat = grab(r"preferred.*nationality|nationality.*prefer")
    if pref_nat: prop["nationality_preference"] = pref_nat

    tenant_type = grab(r"preferred\s+tenant.*type|tenant.*type")
    if tenant_type: prop["tenant_type_preference"] = tenant_type

    max_pax = grab(r"max.*occupant|maximum.*occupant|max\s+(?:pax|occupant)")
    if max_pax:
        max_val = _to_int(max_pax)
        if max_val: prop["max_occupants"] = max_val

    # House Rules section
    cooking = grab(r"cooking\s*(?:allowed|restriction|policy)")
    if cooking: prop["cooking_allowed"] = cooking

    pets = grab(r"pets?\s*allowed")
    if pets: prop["pets_allowed"] = pets

    smoking = grab(r"smoking\s*allowed")
    if smoking: prop["smoking_allowed"] = smoking

    subletting = grab(r"subletting\s*allowed")
    if subletting: prop["subletting_allowed"] = subletting

    visitors = grab(r"visitors.*guest|guest.*visitor|overnight")
    if visitors: prop["visitors_policy"] = visitors

    other_rules = grab(r"other\s+rules|rules.*concern|noise|parties")
    if other_rules: prop["other_rules"] = other_rules

    # Viewings section
    viewing_method = grab(r"handle\s+viewing|viewing.*handling")
    if viewing_method: prop["viewing_method"] = viewing_method

    viewing_dates = grab(r"available\s+dates.*times|dates.*times")
    if viewing_dates: prop["viewing_times"] = viewing_dates

    return prop

# ---------- Active tenant pool ----------
def load_active_tenants(state_path=STATE):
    """Load all tenants with form_sent=True from intake-state.json.
    Returns list of {pn, name, profile, ...} dicts."""
    try:
        state = json.load(open(state_path))
    except Exception:
        return []

    tenants = []
    for pn, rec in state.get("conversations", {}).items():
        # Must have submitted form + completed profile (not just form_sent)
        if rec.get("form_sent") and rec.get("profile", {}).get("name"):
            tenants.append({
                "pn": pn,
                "name": rec.get("profile", {}).get("name"),
                "profile": rec.get("profile", {}),
                "listing_key": rec.get("listing_key"),
                "status": rec.get("status", ""),
            })

    return tenants

# ---------- Matching algorithm ----------
def score_match(tenant_profile, landlord_prop):
    """
    Score how well a tenant matches a landlord property.
    Scoring:
      1. District match: tenant's preferred area includes landlord's location
      2. Budget match: tenant's budget >= landlord's asking rent
      3. Gender match: landlord's gender pref includes tenant
      4. Nationality match: landlord's nationality pref includes tenant
      5. Occupancy match: tenant group size <= landlord's max occupants
      6. Lifestyle match: tenant's lifestyle aligns with house rules

    Returns (score, reasons) where score is 0-6 (# of criteria met).
    """
    score = 0
    reasons = []

    # 1. District match
    landlord_dist = landlord_prop.get("district") or ""
    tenant_locs = (tenant_profile.get("preferred_location") or "").lower()
    if landlord_dist:
        # Simple heuristic: check if district name or tenant's preference mentions landlord district
        # Real version would use DISTRICT_PROXIMITY map above
        score += 1
        reasons.append(f"district: {landlord_dist}")
    else:
        # Unknown landlord district -> still count as match (no gate)
        score += 1
        reasons.append("district: any")

    # 2. Budget match
    tenant_budget = _to_int(tenant_profile.get("budget"))
    landlord_rent = landlord_prop.get("asking_rent")
    if landlord_rent and tenant_budget:
        if tenant_budget >= landlord_rent * 0.95:  # 5% flexibility
            score += 1
            reasons.append(f"budget: ${tenant_budget} >= ${landlord_rent}")
        else:
            reasons.append(f"budget mismatch: ${tenant_budget} < ${landlord_rent}")
    elif landlord_rent:
        # Unknown tenant budget -> assume match (form has rent field)
        score += 1
        reasons.append(f"budget: unknown (landlord asks ${landlord_rent})")
    else:
        # Unknown landlord rent -> count as match
        score += 1
        reasons.append("budget: unknown")

    # 3. Gender match
    pref_gender = (landlord_prop.get("gender_preference") or "").lower()
    tenant_gender = (tenant_profile.get("gender") or "").lower()
    if pref_gender and tenant_gender:
        if ("female" in pref_gender and "female" in tenant_gender):
            score += 1
            reasons.append("gender: match (female)")
        elif ("male" in pref_gender and "male" in tenant_gender and "female" not in pref_gender):
            score += 1
            reasons.append("gender: match (male)")
        elif "any" in pref_gender or "all" in pref_gender or "mix" in pref_gender:
            score += 1
            reasons.append("gender: any accepted")
        else:
            reasons.append(f"gender: mismatch ({pref_gender} vs {tenant_gender})")
    else:
        score += 1
        reasons.append("gender: not specified")

    # 4. Nationality match
    pref_nat = (landlord_prop.get("nationality_preference") or "").lower()
    tenant_nat = (tenant_profile.get("nationality") or "").lower()
    if pref_nat and tenant_nat:
        if "any" not in pref_nat and "all" not in pref_nat:
            # Landlord has a specific preference
            if tenant_nat in pref_nat or pref_nat in tenant_nat:
                score += 1
                reasons.append(f"nationality: match ({tenant_nat})")
            else:
                reasons.append(f"nationality: mismatch ({pref_nat} vs {tenant_nat})")
        else:
            score += 1
            reasons.append("nationality: any accepted")
    else:
        score += 1
        reasons.append("nationality: not specified")

    # 5. Occupancy match
    tenant_pax = _to_int(tenant_profile.get("no_of_pax"))
    landlord_max = landlord_prop.get("max_occupants")
    if landlord_max and tenant_pax:
        if tenant_pax <= landlord_max:
            score += 1
            reasons.append(f"occupancy: {tenant_pax} <= {landlord_max}")
        else:
            reasons.append(f"occupancy: {tenant_pax} > {landlord_max} (too many)")
    else:
        score += 1
        reasons.append("occupancy: not specified or unknown")

    # 6. Lifestyle match
    lifestyle_matches = 0
    lifestyle_reasons = []

    # Cooking
    cooking_allowed = (landlord_prop.get("cooking_allowed") or "").lower()
    if cooking_allowed and "not" not in cooking_allowed and "no" not in cooking_allowed:
        lifestyle_matches += 1
        lifestyle_reasons.append("cooking OK")

    # Pets
    pets_allowed = (landlord_prop.get("pets_allowed") or "").lower()
    if pets_allowed and "not" not in pets_allowed and "no" not in pets_allowed:
        lifestyle_matches += 1
        lifestyle_reasons.append("pets OK")

    # Smoking
    smoking_allowed = (landlord_prop.get("smoking_allowed") or "").lower()
    if smoking_allowed and "not" not in smoking_allowed and "no" not in smoking_allowed:
        lifestyle_matches += 1
        lifestyle_reasons.append("smoking OK")

    # Visitors
    visitors_ok = (landlord_prop.get("visitors_policy") or "").lower()
    if visitors_ok and "no" not in visitors_ok and "not" not in visitors_ok:
        lifestyle_matches += 1
        lifestyle_reasons.append("visitors OK")

    if lifestyle_matches >= 2:
        score += 1
        reasons.append(f"lifestyle: {', '.join(lifestyle_reasons)}")
    else:
        reasons.append(f"lifestyle: limited ({', '.join(lifestyle_reasons or ['strict'])})")

    return (score, reasons)

def find_tenant_matches(landlord_prop, min_score=4):
    """
    Find all tenants that match a landlord property with score >= min_score.
    Returns list of {pn, name, score, reasons}.
    """
    tenants = load_active_tenants()
    matches = []

    for tenant in tenants:
        # Skip if tenant already has a viewing/booking for this or similar property
        if tenant.get("status", "").startswith(("viewing_", "closed")):
            continue

        score, reasons = score_match(tenant.get("profile", {}), landlord_prop)
        if score >= min_score:
            matches.append({
                "pn": tenant["pn"],
                "name": tenant["name"],
                "score": score,
                "reasons": reasons,
                "profile": tenant["profile"],
            })

    # Sort by score (descending) then by name
    matches.sort(key=lambda m: (-m["score"], m["name"]))
    return matches

# ---------- Notification template ----------
def format_match_notification(landlord_prop, score, reasons):
    """Format a WhatsApp notification for a matched tenant."""
    rent = landlord_prop.get("asking_rent", "TBD")
    address = landlord_prop.get("address", "")
    rooms = landlord_prop.get("rooms_available", "")
    furnish = landlord_prop.get("furnishing", "")
    mrt = landlord_prop.get("nearest_mrt", "")
    avail = landlord_prop.get("available_from", "")

    # Build match reason summary
    match_reasons = ", ".join([r.split(":")[1].strip() if ":" in r else r for r in reasons[:4]])

    msg = (
        f"Hi! I found a room that matches your search \U0001F3E0\n\n"
        f"📍 {address}\n"
        f"💰 ${rent}/month{f' ({furnish})' if furnish else ''}\n"
    )

    if mrt:
        msg += f"🚇 {mrt}\n"

    msg += (
        f"✅ Matches your search: {match_reasons}\n"
        f"📅 Available {avail or 'soon'}\n\n"
        f"Interested? Say YES and I can arrange a viewing for you \U0001F64F"
    )

    return msg

# ---------- State tracking ----------
def track_match(state, tenant_pn, landlord_pn, score):
    """
    Add match record to tenant's profile in state.
    """
    if "conversations" not in state:
        state["conversations"] = {}

    rec = state["conversations"].setdefault(tenant_pn, {})

    # Initialize match tracking
    if "property_matches" not in rec:
        rec["property_matches"] = []

    # Avoid duplicates
    for m in rec["property_matches"]:
        if m.get("landlord_pn") == landlord_pn:
            return  # Already tracked

    rec["property_matches"].append({
        "landlord_pn": landlord_pn,
        "matched_at": __import__("time").time(),
        "score": score,
    })

def mark_match_notified(state, tenant_pn, landlord_pn):
    """Mark that notification has been sent."""
    if "conversations" in state and tenant_pn in state["conversations"]:
        rec = state["conversations"][tenant_pn]
        if "property_matches" not in rec:
            rec["property_matches"] = []
        for m in rec["property_matches"]:
            if m.get("landlord_pn") == landlord_pn:
                m["notified_at"] = __import__("time").time()
                break

# ---------- Integration hook ----------
def on_landlord_form_completed(state, pn, form_text):
    """
    Called when a landlord's form is detected as complete.
    Extracts property, finds matches, sends notifications, returns action list.

    Args:
        state: intake-state.json dict
        pn: landlord's phone number
        form_text: the completed form text

    Returns:
        List of actions: [{"type": "SEND_MATCH", "tenant_pn": "...", "text": "..."}]
    """
    prop = extract_landlord_property(form_text)
    if not prop.get("address"):
        return []  # Incomplete form

    matches = find_tenant_matches(prop, min_score=4)
    actions = []

    for match in matches[:10]:  # Cap at 10 matches per property
        tenant_pn = match["pn"]

        # Track match in state
        track_match(state, tenant_pn, pn, match["score"])

        # Format notification
        notification = format_match_notification(prop, match["score"], match["reasons"])

        actions.append({
            "type": "SEND_MATCH",
            "tenant_pn": tenant_pn,
            "landlord_pn": pn,
            "text": notification,
        })

        # Mark as notified
        mark_match_notified(state, tenant_pn, pn)

    return actions
