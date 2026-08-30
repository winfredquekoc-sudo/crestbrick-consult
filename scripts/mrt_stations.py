#!/usr/bin/env python3
"""Nearest-MRT lookup shared by the public site and the private Matchmaker.

Station coordinates are geocoded once via OneMap (free, no key) from a fixed name
list and cached in scripts/mrt-coords.json. nearest_mrt(lat, lng) returns the
closest station within WALK_MAX metres, with a rough walk time, or None.
"""
import json
import math
import os
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "mrt-coords.json")
WALK_MAX = 1300          # metres — beyond this we don't claim "near" an MRT
WALK_MPM = 80            # ~80 m per minute walking

# Operational MRT/LRT stations across the network (names OneMap resolves cleanly).
STATIONS = [
    # North-South Line
    "Jurong East", "Bukit Batok", "Bukit Gombak", "Choa Chu Kang", "Yew Tee", "Kranji",
    "Marsiling", "Woodlands", "Admiralty", "Sembawang", "Canberra", "Yishun", "Khatib",
    "Yio Chu Kang", "Ang Mo Kio", "Bishan", "Braddell", "Toa Payoh", "Novena", "Newton",
    "Orchard", "Somerset", "Dhoby Ghaut", "City Hall", "Raffles Place", "Marina Bay", "Marina South Pier",
    # East-West Line
    "Pasir Ris", "Tampines", "Simei", "Tanah Merah", "Bedok", "Kembangan", "Eunos", "Paya Lebar",
    "Aljunied", "Kallang", "Lavender", "Bugis", "Tanjong Pagar", "Outram Park", "Tiong Bahru",
    "Redhill", "Queenstown", "Commonwealth", "Buona Vista", "Dover", "Clementi", "Chinese Garden",
    "Lakeside", "Boon Lay", "Pioneer", "Joo Koon", "Gul Circle", "Tuas Crescent", "Tuas West Road", "Tuas Link", "Expo", "Changi Airport",
    # North East Line
    "HarbourFront", "Chinatown", "Clarke Quay", "Little India", "Farrer Park", "Boon Keng",
    "Potong Pasir", "Woodleigh", "Serangoon", "Kovan", "Hougang", "Buangkok", "Sengkang", "Punggol",
    # Circle Line
    "Bras Basah", "Esplanade", "Promenade", "Nicoll Highway", "Stadium", "Mountbatten", "Dakota",
    "Paya Lebar", "MacPherson", "Tai Seng", "Bartley", "Lorong Chuan", "Marymount", "Caldecott",
    "Botanic Gardens", "Farrer Road", "Holland Village", "one-north", "Kent Ridge", "Haw Par Villa",
    "Pasir Panjang", "Labrador Park", "Telok Blangah",
    # Downtown Line
    "Bukit Panjang", "Cashew", "Hillview", "Beauty World", "King Albert Park", "Sixth Avenue",
    "Tan Kah Kee", "Stevens", "Rochor", "Downtown", "Telok Ayer", "Fort Canning", "Bencoolen",
    "Jalan Besar", "Bendemeer", "Geylang Bahru", "Mattar", "Ubi", "Kaki Bukit", "Bedok North",
    "Bedok Reservoir", "Tampines West", "Tampines East", "Upper Changi",
    # Thomson-East Coast Line
    "Woodlands North", "Springleaf", "Lentor", "Mayflower", "Bright Hill", "Upper Thomson",
    "Napier", "Orchard Boulevard", "Great World", "Havelock", "Maxwell", "Shenton Way",
    "Gardens by the Bay", "Tanjong Rhu", "Katong Park", "Tanjong Katong", "Marine Parade",
    "Marine Terrace", "Siglap", "Bayshore",
]


def _load():
    try:
        return json.load(open(CACHE))
    except Exception:
        return {}


def _geocode(name, cache):
    if name in cache:
        return cache[name]
    q = urllib.parse.urlencode({"searchVal": name + " MRT Station", "returnGeom": "Y",
                                "getAddrDetails": "N", "pageNum": 1})
    for _ in range(3):
        try:
            r = json.load(urllib.request.urlopen("https://www.onemap.gov.sg/api/common/elastic/search?" + q, timeout=20))
            res = r.get("results") or []
            cache[name] = [float(res[0]["LATITUDE"]), float(res[0]["LONGITUDE"])] if res else None
            return cache[name]
        except Exception:
            time.sleep(1.5)
    cache[name] = None
    return None


def ensure_cache():
    """Geocode any not-yet-cached stations (one-time; polite throttle)."""
    cache = _load()
    changed = False
    for s in STATIONS:
        if s not in cache:
            _geocode(s, cache)
            changed = True
            time.sleep(0.3)
    if changed:
        json.dump(cache, open(CACHE, "w"), indent=0)
    return cache


def _haversine(a, b, c, d):
    R = 6371000
    p1, p2 = math.radians(a), math.radians(c)
    dp, dl = math.radians(c - a), math.radians(d - b)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(x))


def nearest_mrt(lat, lng, cache=None):
    """(station, distance_m, walk_min) for the closest station within WALK_MAX, else None."""
    if lat is None or lng is None:
        return None
    cache = cache if cache is not None else _load()
    best = None
    for name, co in cache.items():
        if not co:
            continue
        d = _haversine(lat, lng, co[0], co[1])
        if best is None or d < best[1]:
            best = (name, d)
    if not best or best[1] > WALK_MAX:
        return None
    return {"station": best[0], "distance_m": round(best[1]), "walk_min": max(1, round(best[1] / WALK_MPM))}


if __name__ == "__main__":
    c = ensure_cache()
    print("geocoded stations:", sum(1 for v in c.values() if v), "/", len(STATIONS))
    print("test Toa Payoh block (1.3244,103.8638):", nearest_mrt(1.3244, 103.8638, c))
    print("test Tampines (1.3536,103.9451):", nearest_mrt(1.3536, 103.9451, c))
