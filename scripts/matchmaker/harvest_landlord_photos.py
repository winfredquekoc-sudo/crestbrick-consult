#!/usr/bin/env python3
"""Harvest landlord ROOM photos from WhatsApp into the gated Matchmaker.

Privacy is on-device: every image is described by a local vision model
(moondream via Ollama) and anything that reads as a document, ID/NRIC,
screenshot, QR code, or a person is DROPPED — never copied, never shown. Only
clear room / house / building photos are kept. No image ever leaves the machine
for classification (Winfred 28 Aug 2026: "auto pull but do not show NRIC, just
pictures of what may seem to be their house").

Kept photos are downscaled into deploy/photos/<LLID>/ (served behind the same
login as the app, never public) and listed in photos-harvested.json, which
build.py merges into each listing's `photos`.

Usage:
  harvest_landlord_photos.py                     # dry run: classify on-disk images, report only
  harvest_landlord_photos.py --download [--per N] # first pull the most recent N images per landlord via the bridge
  harvest_landlord_photos.py --apply             # copy kept room photos + write the index
"""
import argparse
import base64
import glob
import json
import os
import sqlite3
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DB = os.path.expanduser("~/whatsapp-mcp/whatsapp-bridge/store/messages.db")
STORE = os.path.expanduser("~/whatsapp-mcp/whatsapp-bridge/store")
LANDLORD_DB = os.path.join(REPO, "_templates", "landlord-db.json")
PHOTOS_DIR = os.path.join(HERE, "deploy", "photos")
# Drop owner-sent photos you picked yourself into manual-photos/<LLID>/ — they are
# TRUSTED (you chose them) so they skip the classifier and always show, first.
MANUAL_DIR = os.path.join(HERE, "manual-photos")
INDEX_OUT = os.path.join(HERE, "photos-harvested.json")
OLLAMA = "http://localhost:11434/api/generate"
MODEL = "moondream"
MAX_PER_LANDLORD = 6
MAX_EDGE = 1000  # downscale longest edge, px

# Drop-first: ANY hint of a document / person / screen wins over a room hint, so
# an ID card described as "an id card on a table" is dropped, not kept.
DROP = ["document", "paper", "text", "id ", "nric", "passport", " card", "receipt",
        "form", "screenshot", "phone screen", "iphone", "conversation", "message",
        "chat", "signed", "signature", "agreement", "contract", "invoice", "person",
        "man", "woman", "people", "face", "selfie", "portrait", "child", "baby",
        "food", "meal", "license", "logo", "poster", "qr code", "handwrit"]
KEEP = ["room", "bedroom", "kitchen", "living", "apartment", "house", "building",
        "window", "bed", "sofa", "couch", "floor", "wall", "hdb", "condo", "balcony",
        "toilet", "bathroom", "furniture", "ceiling", "door", "interior", "hallway",
        "closet", "wardrobe", "dining", "unit", "flat", "tiles", "shelf", "desk",
        "chair", "curtain", "corridor", "cabinet", "kitchenette", "staircase"]


def describe(path):
    b = base64.b64encode(open(path, "rb").read()).decode()
    body = json.dumps({"model": MODEL, "prompt": "Describe this image in one short sentence.",
                       "images": [b], "stream": False}).encode()
    req = urllib.request.Request(OLLAMA, body, {"Content-Type": "application/json"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=180)).get("response", "").strip()
    except Exception as e:
        return "__error__ " + str(e)[:60]


def decide(desc):
    d = (desc or "").lower()
    if not d or d.startswith("__error__"):
        return "DROP", "unreadable"
    if any(k in d for k in DROP):
        return "DROP", "not-a-room"
    if any(k in d for k in KEEP):
        return "KEEP", "room"
    return "DROP", "unclear"


def load_landlords():
    d = json.load(open(LANDLORD_DB))
    arr = d if isinstance(d, list) else (d.get("landlords") or d.get("records") or [])
    return [l for l in arr if l.get("chat_jid")]


def on_disk_images(jid):
    return sorted(glob.glob(os.path.join(STORE, jid, "*.jpg")) +
                  glob.glob(os.path.join(STORE, jid, "*.jpeg")) +
                  glob.glob(os.path.join(STORE, jid, "*.png")))


def manual_images(lid):
    """Photos Winfred hand-dropped into manual-photos/<LLID>/ — trusted, shown as is."""
    d = os.path.join(MANUAL_DIR, lid)
    return sorted(glob.glob(os.path.join(d, "*.jpg")) + glob.glob(os.path.join(d, "*.jpeg")) +
                  glob.glob(os.path.join(d, "*.png")) + glob.glob(os.path.join(d, "*.JPG")))


def download_recent(jid, per):
    """Pull the most recent `per` image messages for this chat via the bridge's
    own downloader (whatsapp.py), which decrypts and saves into store/<jid>/."""
    try:
        sys.path.insert(0, os.path.expanduser("~/whatsapp-mcp/whatsapp-mcp-server"))
        import whatsapp as wa
    except Exception as e:
        print("  (download unavailable: %s)" % e)
        return
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    rows = con.execute("SELECT id FROM messages WHERE chat_jid=? AND media_type='image' "
                       "ORDER BY timestamp DESC LIMIT ?", (jid, per)).fetchall()
    con.close()
    for (mid,) in rows:
        try:
            wa.download_media(mid, jid)
        except Exception:
            pass


def downscale(src, dst):
    """Downscale + strip metadata. PIL if present, else macOS sips."""
    try:
        from PIL import Image
        im = Image.open(src).convert("RGB")
        im.thumbnail((MAX_EDGE, MAX_EDGE))
        im.save(dst, "JPEG", quality=72)  # re-encode drops EXIF (incl. GPS)
        return True
    except Exception:
        try:
            subprocess.run(["sips", "-Z", str(MAX_EDGE), src, "--out", dst],
                           check=True, capture_output=True)
            return True
        except Exception:
            return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="copy kept photos + write index")
    ap.add_argument("--download", action="store_true", help="first pull recent images per landlord")
    ap.add_argument("--per", type=int, default=12, help="images to pull per landlord with --download")
    ap.add_argument("--only", help="comma-separated LL ids to limit to (testing)")
    args = ap.parse_args()

    landlords = load_landlords()
    if args.only:
        want = set(x.strip().upper() for x in args.only.split(","))
        landlords = [l for l in landlords if (l.get("id") or "").upper() in want]

    index, totals = {}, {"kept": 0, "dropped": 0, "landlords_with_photos": 0}
    drop_reasons = {}
    for l in landlords:
        lid, jid = l.get("id"), l.get("chat_jid")
        if args.download:
            download_recent(jid, args.per)
        manual = manual_images(lid)          # trusted, always kept, shown first
        imgs = on_disk_images(jid)
        if not manual and not imgs:
            continue
        kept = list(manual)
        totals["kept"] += len(manual)
        for p in imgs:
            if len(kept) >= MAX_PER_LANDLORD:
                break
            dec, reason = decide(describe(p))
            if dec == "KEEP":
                kept.append(p)
                totals["kept"] += 1
            else:
                totals["dropped"] += 1
                drop_reasons[reason] = drop_reasons.get(reason, 0) + 1
        if not kept:
            print("  %s: %d image(s), none are rooms" % (lid, len(imgs)))
            continue
        totals["landlords_with_photos"] += 1
        rels = []
        if args.apply:
            dstdir = os.path.join(PHOTOS_DIR, lid)
            os.makedirs(dstdir, exist_ok=True)
            for i, src in enumerate(kept, 1):
                dst = os.path.join(dstdir, "%d.jpg" % i)
                if downscale(src, dst):
                    rels.append("photos/%s/%d.jpg" % (lid, i))
            index[lid] = rels
        print("  %s: kept %d room photo(s)%s" % (lid, len(kept), "" if args.apply else " (dry run)"))

    print("\n=== summary ===")
    print("landlords with room photos: %d" % totals["landlords_with_photos"])
    print("kept: %d  dropped: %d  %s" % (totals["kept"], totals["dropped"], drop_reasons))
    if args.apply:
        json.dump(index, open(INDEX_OUT, "w"), indent=1)
        print("wrote %s (%d landlords)" % (INDEX_OUT, len(index)))
    else:
        print("dry run — no photos copied, no index written. Re-run with --apply to publish.")


if __name__ == "__main__":
    main()
