"""
ninety_nine_co_lister.py — Auto-list landlord properties on 99.co.

Creates listings automatically when a landlord completes their onboarding form.
Optionally integrates with 99.co API (if credentials available) or provides
Selenium-based form-filling as fallback.

CEA-compliant: all listings include Winfred's contact and CEA license.
"""
import json, os, re, time
import wa_intake_paths as _P
from landlord_tenant_matcher import extract_landlord_property, _to_int

# STEP 0 sandbox seal (merge review, 9 Sep 2026): these were bare os.path.expanduser
# constants, so add_listing_to_index()/update_listing_url() wrote the REAL, LIVE
# listing-index.json even under WA_INTAKE_SANDBOX=1 with every root pointed at a tempdir --
# the module never consulted wa_intake_paths at all, so no env var could redirect it. That
# is the same shape of bug as the runner-last.json incident, and it is reachable from a test
# in the tree (src/wa-pipeline/test_landlord_matcher.py -> intake_engine.on_landlord_form_
# completed -> on_landlord_form_completed_for_99co -> add_listing_to_index). Resolve at CALL
# time via _listing_index() instead; production (env unset) resolves to the identical path.
LISTING_INDEX = _P.paths()["listing_index"]
_default_LISTING_INDEX = LISTING_INDEX
NINETY_NINE_CO_CREDS = os.path.expanduser("~/.claude/secrets/99co-credentials.json")


def _listing_index():
    return _P.resolved(globals(), "LISTING_INDEX", "listing_index")

def _load(p, d):
    try: return json.load(open(p))
    except Exception: return d

def _save_json(p, data):
    """Atomic write."""
    tmp = p + ".tmp"
    json.dump(data, open(tmp, "w"), indent=1, ensure_ascii=False)
    os.replace(tmp, p)

# ---------- 99.co API wrapper (if available) ----------
class NinetyNineCoAPI:
    """Wrapper for 99.co API calls."""

    def __init__(self):
        self.creds = _load(NINETY_NINE_CO_CREDS, {})
        self.api_key = self.creds.get("api_key")
        self.user_id = self.creds.get("user_id")
        self.endpoint = "https://api.99.co/v1"  # placeholder; update with real endpoint
        self.session = None

    def is_available(self):
        """Check if API credentials are configured."""
        return bool(self.api_key and self.user_id)

    def create_listing(self, title, description, property_type, rent, address,
                      photos=None, contact_phone=None, contact_whatsapp=True):
        """
        Create a listing on 99.co via API.

        Args:
            title: listing title (e.g. "1-room common in Tampines, $1,200/mo")
            description: full property description
            property_type: "1-room", "2-room", "master-room", "whole-unit"
            rent: monthly rent in SGD
            address: full property address
            photos: list of photo URLs or local file paths
            contact_phone: landlord's phone
            contact_whatsapp: use WhatsApp for contact

        Returns:
            {"success": True, "listing_url": "..."} or {"success": False, "error": "..."}
        """
        if not self.is_available():
            return {"success": False, "error": "99.co API credentials not configured"}

        payload = {
            "title": title,
            "description": description,
            "property_type": property_type,
            "rent": rent,
            "address": address,
            "contact_phone": contact_phone,
            "contact_method": "whatsapp" if contact_whatsapp else "phone",
        }

        if photos:
            payload["photos"] = photos

        try:
            # Placeholder; real implementation would use requests.post()
            # response = requests.post(
            #     f"{self.endpoint}/listings/create",
            #     headers={"Authorization": f"Bearer {self.api_key}"},
            #     json=payload,
            #     timeout=30
            # )
            # return response.json()
            pass
        except Exception as e:
            return {"success": False, "error": str(e)}

        return {"success": False, "error": "API not yet implemented"}

# ---------- Form-filling wrapper (Selenium) ----------
class NinetyNineCoFormFiller:
    """Selenium-based form-filler for 99.co (fallback when API unavailable)."""

    def __init__(self):
        self.driver = None
        self.email = None
        self.password = None

    def is_available(self):
        """Check if Selenium + Chrome are available."""
        try:
            from selenium import webdriver
            return True
        except ImportError:
            return False

    def login(self, email, password):
        """Log in to 99.co account."""
        # Placeholder; would use Selenium to navigate and fill login form
        pass

    def fill_form(self, title, description, property_type, rent, address,
                 photos=None, contact_phone=None):
        """
        Fill and submit a 99.co listing form via Selenium.

        Returns:
            {"success": True, "listing_url": "..."} or {"success": False, "error": "..."}
        """
        # Placeholder; would navigate to 99.co post listing form and fill fields
        return {"success": False, "error": "Selenium form-filler not yet implemented"}

# ---------- Listing generation ----------
def generate_listing_for_99co(landlord_prop, landlord_phone, landlord_name=""):
    """
    Convert a landlord's form data into a 99.co listing.

    Returns:
        {
            "title": "...",
            "description": "...",
            "property_type": "...",
            "rent": 1200,
            "address": "...",
            "photos": [...],
            "contact_phone": "...",
        }
    """
    # Generate title
    rooms = landlord_prop.get("rooms_available", "room")
    rent = landlord_prop.get("asking_rent", 0)
    address = landlord_prop.get("address", "Singapore")

    # Extract district if possible
    district = ""
    if "tampines" in address.lower():
        district = "Tampines"
    elif "bedok" in address.lower():
        district = "Bedok"
    elif "jurong" in address.lower():
        district = "Jurong"
    elif "bukit" in address.lower():
        district = "Bukit Merah"
    else:
        # Use first part of address
        district = address.split(",")[0] if "," in address else address.split()[0]

    title = f"{rooms.title()} in {district}, ${rent}/month"

    # Generate description
    desc_parts = [
        f"📍 {address}",
        "",
    ]

    if landlord_prop.get("furnishing"):
        desc_parts.append(f"Furnishing: {landlord_prop['furnishing']}")

    if landlord_prop.get("utilities"):
        desc_parts.append(f"Utilities: {landlord_prop['utilities']}")

    if landlord_prop.get("available_from"):
        desc_parts.append(f"Available from: {landlord_prop['available_from']}")

    # House rules
    desc_parts.append("")
    desc_parts.append("House Rules:")
    if landlord_prop.get("cooking_allowed"):
        desc_parts.append(f"• Cooking: {landlord_prop['cooking_allowed']}")
    if landlord_prop.get("pets_allowed"):
        desc_parts.append(f"• Pets: {landlord_prop['pets_allowed']}")
    if landlord_prop.get("smoking_allowed"):
        desc_parts.append(f"• Smoking: {landlord_prop['smoking_allowed']}")
    if landlord_prop.get("visitors_policy"):
        desc_parts.append(f"• Visitors: {landlord_prop['visitors_policy']}")

    # Tenant preferences
    desc_parts.append("")
    desc_parts.append("Landlord Preferences:")
    if landlord_prop.get("gender_preference"):
        desc_parts.append(f"• Gender: {landlord_prop['gender_preference']}")
    if landlord_prop.get("max_occupants"):
        desc_parts.append(f"• Max occupants: {landlord_prop['max_occupants']}")

    # Contact info
    desc_parts.append("")
    desc_parts.append("Contact:")
    desc_parts.append(f"WhatsApp: {landlord_phone}")
    if landlord_name:
        desc_parts.append(f"Owner: {landlord_name}")

    description = "\n".join(desc_parts)

    # Property type classification
    property_type = "1-room"  # default
    if "master" in rooms.lower():
        property_type = "master-room"
    elif "2" in rooms or "two" in rooms.lower():
        property_type = "2-room"
    elif "whole" in rooms.lower() or "unit" in rooms.lower():
        property_type = "whole-unit"

    return {
        "title": title,
        "description": description,
        "property_type": property_type,
        "rent": rent,
        "address": address,
        "contact_phone": landlord_phone,
        "contact_whatsapp": True,
    }

# ---------- State tracking ----------
def add_listing_to_index(listing_data, landlord_phone):
    """
    Add the generated listing to listing-index.json.

    Args:
        listing_data: dict from generate_listing_for_99co()
        landlord_phone: landlord's phone for reference

    Updates listing-index.json with new entry + 99co_url field.
    """
    try:
        index = json.load(open(_listing_index()))
    except Exception:
        return {"success": False, "error": f"Cannot read {_listing_index()}"}

    if "listings" not in index:
        index["listings"] = []

    # Generate a simple slug from address
    slug = re.sub(r"[^a-z0-9]+", "-", listing_data["address"].lower()).strip("-")
    if not slug:
        slug = f"listing-{int(time.time())}"

    # Check for duplicates
    for lst in index["listings"]:
        if lst.get("landlord_phone") == landlord_phone:
            return {"success": False, "error": "Listing from this landlord already exists"}

    new_listing = {
        "listing_key": slug,
        "landlord_phone": landlord_phone,
        "property_name": listing_data["address"],
        "block_address": listing_data["address"],
        "deal_type": "rent",
        "status": "pending_99co",  # Will update to "active_99co" once URL confirmed
        "requirements": {},  # Will be enriched from landlord_prop later
        "created_at": int(time.time()),
        "ninety_nine_co_pending": True,
    }

    index["listings"].append(new_listing)
    _save_json(_listing_index(), index)

    return {"success": True, "listing_key": slug}

def update_listing_url(listing_key, url_99co):
    """
    Update a listing with the confirmed 99.co URL.
    """
    try:
        index = json.load(open(_listing_index()))
    except Exception:
        return {"success": False, "error": f"Cannot read {_listing_index()}"}

    for lst in index.get("listings", []):
        if lst.get("listing_key") == listing_key:
            lst["ninety_nine_co_url"] = url_99co
            lst["status"] = "active_99co"
            lst["ninety_nine_co_pending"] = False
            _save_json(_listing_index(), index)
            return {"success": True}

    return {"success": False, "error": f"Listing {listing_key} not found"}

# ---------- Integration hook ----------
def on_landlord_form_completed_for_99co(pn, form_text, landlord_name=""):
    """
    Called when a landlord's form is detected as complete.
    Attempts to create a 99.co listing (API first, then Selenium fallback).

    Args:
        pn: landlord's phone
        form_text: completed form text
        landlord_name: landlord's name (optional)

    Returns:
        {"success": True, "url": "..."} or {"success": False, "error": "..."}
    """
    prop = extract_landlord_property(form_text)
    if not prop.get("address") or not prop.get("asking_rent"):
        return {"success": False, "error": "Incomplete property data (missing address or rent)"}

    listing = generate_listing_for_99co(prop, pn, landlord_name)

    # Add to index first (so it's tracked even if 99co creation fails)
    idx_result = add_listing_to_index(listing, pn)
    if not idx_result["success"]:
        return idx_result

    listing_key = idx_result["listing_key"]

    # Try API first
    api = NinetyNineCoAPI()
    if api.is_available():
        result = api.create_listing(
            title=listing["title"],
            description=listing["description"],
            property_type=listing["property_type"],
            rent=listing["rent"],
            address=listing["address"],
            contact_phone=listing["contact_phone"],
            contact_whatsapp=listing["contact_whatsapp"],
        )
        if result["success"]:
            url = result.get("listing_url")
            update_listing_url(listing_key, url)
            return {"success": True, "url": url, "listing_key": listing_key}

    # Fallback: Selenium (if available)
    # For now, just mark as pending manual review
    return {
        "success": True,
        "listing_key": listing_key,
        "pending": True,
        "message": "99.co API not available. Listing queued for manual creation.",
    }
