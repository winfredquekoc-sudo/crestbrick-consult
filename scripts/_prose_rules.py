"""Single source of truth for Winfred's visible prose rules.

Imported by both scripts/dehyphen.py (which enforces them) and
scripts/verify-site-changes.py (which checks them). They must never diverge:
when they did, the checker flagged the exact hyphens the fixer was told to keep.
"""
import re

# Proper nouns. Removing these hyphens is a factual error, not a style choice:
# school names, official MRT line names, planning areas, statutory scheme names.
KEEP = {
    "e-mail", "wi-fi", "t-junction", "x-ray", "u-turn", "k-12",
    "co-op", "re-sign", "re-cover", "re-creation", "re-form", "re-lease",
    "vis-a-vis", "cul-de-sac", "so-called",
    "anglo-chinese", "thomson-east", "one-north", "kallang-bugis",
    "north-south", "east-west", "north-east", "bishan-ang",
    "build-to-order", "india-singapore", "marine-parade",
    "mon-sat", "mon-fri", "sat-sun", "tues-thurs",
    "newton-novena", "farrer-holland", "queenstown-redhill",
    "3-5-7",
}

# Prefixes where a space breaks the grammar: "self-employed" must never
# become "self employed".
KEEP_PREFIX = {
    "non", "self", "co", "ex", "anti", "semi", "quasi", "pseudo",
    "inter", "intra", "ultra", "counter", "sub", "vice", "all",
}

# Compounds that read better closed up than spaced.
JOIN = {
    "e-application": "eApplication", "e-service": "eService",
    "e-services": "eServices", "e-map": "eMap", "e-appointment": "eAppointment",
    "co-ordinate": "coordinate", "co-ordinated": "coordinated",
    "co-operate": "cooperate", "co-operation": "cooperation",
    "pre-empt": "preempt", "re-enter": "reenter",
    "by-laws": "bylaws", "by-law": "bylaw", "add-ons": "addons",
    "add-on": "addon", "on-going": "ongoing", "under-writing": "underwriting",
}

# A dash between two numbers is a RANGE and must read "to", never a comma.
_UNIT = (r"%|m|k|bn|psf|sqft|sqm|yr|yrs|year|years|mth|mths|month|months|"
         r"day|days|week|weeks|bed|beds|br|rm|pa|sqm|km")
RANGE_NUM = re.compile(
    rf"(?<![\w/#-])((?:S?\$)?\d[\d,]*(?:\.\d+)?\s*(?:{_UNIT})?)"
    rf"\s*(?:-|–|&ndash;|&#8211;)\s*"
    rf"((?:S?\$)?\d[\d,]*(?:\.\d+)?\s*(?:{_UNIT})?)(?![\w/-])",
    re.I)

_RANGE_TOKEN = re.compile(r"^[A-Za-z]?\d+[A-Za-z]?-[A-Za-z]?\d+[A-Za-z]?$")
_COMPOUND = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+")

# Files that are page fragments, not pages: they legitimately have no title.
PARTIAL_DIRS = ("public/_snippets/", "public/_partials/", "public/_templates/")


def bad_hyphens(s):
    """Hyphenated tokens in `s` that violate the no hyphens rule."""
    out = []
    for tok in _COMPOUND.findall(s):
        low = tok.lower()
        if low in KEEP or low in JOIN or _RANGE_TOKEN.match(tok):
            continue
        if low.split("-")[0] in KEEP_PREFIX:
            continue
        out.append(tok)
    return out
