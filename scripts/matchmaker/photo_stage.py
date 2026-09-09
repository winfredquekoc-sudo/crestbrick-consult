#!/usr/bin/env python3
"""Stage landlord room photos (declutter, brighten, wide angle, bedroom staging)
for landlords who have returned the intake form and sent photos.

The Higgsfield Cloud REST API (the one HIGGSFIELD_API_KEY/HIGGSFIELD_SECRET in
~/.claude/mcp-configs/higgsfield.json authenticate against) does NOT expose
nano_banana_pro as of 7 Sep 2026 -- POST /estimate/higgsfield-ai/nano-banana-pro/*
and every naming variant tried returns {"detail":"model_not_found"}; only
higgsfield-ai/soul/* (text-to-image, no reference image) and
higgsfield-ai/popcorn/* (image_urls, 720p/1600p, not 1K) exist on that surface.
So this script does NOT call the REST API to generate anything. Instead it:

  1. classifies each candidate photo's room type locally (moondream, same call
     the harvest already makes to describe() the image, so no extra cost),
  2. builds the staging prompt from photo_staging_prompts.json,
  3. appends a job to deploy/photos/staging-queue.json.

A Claude Code session with the `higgsfield` MCP loaded (mcp__higgsfield__*)
then walks the queue, generates each image via generate_image_soul (or
whichever tool covers nano_banana_pro once/if Higgsfield exposes it), saves
the result, and calls this script's --complete to write the ledger entry.

Usage:
  DRY_RUN=1 python3 photo_stage.py --landlord LL215   # classify + print prompts, no writes
  python3 photo_stage.py --landlord LL215             # enqueue jobs for LL215
  python3 photo_stage.py --all                        # enqueue for every active landlord
  python3 photo_stage.py --complete photos/LL215/2.jpg --output /tmp/out.jpg \\
      --job-id abc123 --credits 2                     # record a finished job
"""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import harvest_landlord_photos as harvest  # noqa: E402 (reuse describe/decide/LANDLORD_DB)

LANDLORD_DB = harvest.LANDLORD_DB
PHOTOS_DIR = harvest.PHOTOS_DIR
HARVEST_INDEX = harvest.INDEX_OUT
PROMPTS_PATH = os.path.join(HERE, "photo_staging_prompts.json")
QUEUE_PATH = os.path.join(PHOTOS_DIR, "staging-queue.json")
LEDGER_PATH = os.path.join(PHOTOS_DIR, "staging-ledger.json")
PROMPT_VERSION = "v1"  # bump when photo_staging_prompts.json's wording changes
CREDITS_PER_IMAGE = 2  # 1K nano_banana_pro-equivalent estimate, per prompts json notes
ROOM_TYPES = ("bedroom", "living_room", "kitchen", "bathroom", "storage_or_corridor", "exterior")
DRY_RUN = os.environ.get("DRY_RUN") == "1"


def _load_json(path, default):
    return harvest._load_json(path, default)


def _save_json(path, data):
    harvest._save_json(path, data)


def load_prompts():
    return json.load(open(PROMPTS_PATH))


def load_landlords():
    d = json.load(open(LANDLORD_DB))
    arr = d if isinstance(d, list) else (d.get("landlords") or d.get("records") or [])
    return arr


def is_active(l):
    st = (l.get("status") or "").lower()
    return not (st.startswith("closed") or st.startswith("archived") or st.startswith("cold"))


def form_and_photos_returned(l):
    """Gate used by default: onboarding form complete AND photos actually received,
    same fields src/wa-pipeline/intake_engine.py mirrors onto the record
    (_sync_landlord_db_fields: onboarding_stage, info_complete, photos_received)."""
    if l.get("onboarding_stage") == "SUPPLY_READY":
        return True
    return bool(l.get("info_complete")) and bool(l.get("photos_received"))


def classify_room(desc):
    """Room type from the moondream description already computed for the harvest
    KEEP/DROP decision -- no second model call. Order matters: check the more
    specific rooms before the catch-alls. Unmatched/unclear falls back to
    storage_or_corridor, same as the docstring in photo_staging_prompts.json."""
    d = (desc or "").lower()
    if any(k in d for k in ("bathroom", "toilet", "shower", "bathtub", "sanitary")):
        return "bathroom"
    if any(k in d for k in ("kitchen", "stove", "hob", "cooker", "kitchenette")):
        return "kitchen"
    if any(k in d for k in ("bedroom", "bed ", " bed,", "bed.", "mattress", "wardrobe")):
        return "bedroom"
    if any(k in d for k in ("living room", "sofa", "couch", "dining", "lounge")):
        return "living_room"
    if any(k in d for k in ("exterior", "building", "facade", "balcony view",
                             "outside", "window view", "hdb block", "condo facade")):
        return "exterior"
    return "storage_or_corridor"


def build_prompt(prompts, room):
    room_line = prompts["rooms"].get(room, prompts["rooms"]["storage_or_corridor"])
    return "%s %s Avoid: %s" % (prompts["base"], room_line, prompts["negative"])


def already_handled(source, ledger, queue):
    if any(e.get("source") == source for e in ledger):
        return True
    if any(e.get("source") == source and e.get("status") == "queued" for e in queue):
        return True
    return False


def staged_sibling_exists(abs_src):
    base, ext = os.path.splitext(abs_src)
    return os.path.exists(base + "_staged" + ext)


def landlord_number(lid):
    """Numeric part of a landlord id, LL229 -> 229, for newest first ordering."""
    digits = "".join(ch for ch in (lid or "") if ch.isdigit())
    return int(digits) if digits else -1


def file_md5(path, block_size=65536):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(block_size), b""):
            h.update(chunk)
    return h.hexdigest()


def staged_md5_index(folder):
    """md5 of every photo in folder whose staged sibling already exists, so a
    duplicate photo saved under a different filename in the same folder is
    recognised and skipped rather than staged a second time."""
    out = set()
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    for name in names:
        if "_staged" in name:
            continue
        full = os.path.join(folder, name)
        if os.path.isfile(full) and staged_sibling_exists(full):
            out.add(file_md5(full))
    return out


def gather_candidates(landlords_by_id, harvested, only_lid, include_all, min_id=None):
    """(landlord_id, source_rel_path, abs_path) for every harvested photo eligible
    for staging this run, respecting the onboarding gate unless include_all.

    Ordered newest landlord first (by the numeric part of the landlord id) and,
    within a landlord, newest photo file first (by modification time), so that
    --per and --max-credits are applied against the most recent supply first."""
    out = []
    ordered_lids = sorted(harvested.keys(), key=landlord_number, reverse=True)
    for lid in ordered_lids:
        if only_lid and lid != only_lid:
            continue
        if min_id is not None and landlord_number(lid) < min_id:
            continue
        l = landlords_by_id.get(lid)
        if l is None or not is_active(l):
            continue
        if not include_all and not form_and_photos_returned(l):
            continue

        candidates = []
        for rel in harvested[lid]:
            abs_path = os.path.join(HERE, "deploy", rel)
            if not os.path.exists(abs_path) or staged_sibling_exists(abs_path):
                continue
            candidates.append((rel, abs_path))
        candidates.sort(key=lambda t: os.path.getmtime(t[1]), reverse=True)

        dup_md5 = {}
        for rel, abs_path in candidates:
            folder = os.path.dirname(abs_path)
            if folder not in dup_md5:
                dup_md5[folder] = staged_md5_index(folder)
            if dup_md5[folder] and file_md5(abs_path) in dup_md5[folder]:
                continue
            out.append((lid, rel, abs_path))
    return out


def run_queue(args):
    prompts = load_prompts()
    landlords = load_landlords()
    landlords_by_id = {(l.get("id") or "").upper(): l for l in landlords if l.get("id")}
    harvested = _load_json(HARVEST_INDEX, {})
    ledger = _load_json(LEDGER_PATH, [])
    queue = _load_json(QUEUE_PATH, [])

    only_lid = args.landlord.upper() if args.landlord else None
    min_id = landlord_number(args.min_id.upper()) if args.min_id else None
    candidates = gather_candidates(landlords_by_id, harvested, only_lid, args.all, min_id)

    credits_used = 0
    per_landlord_count = {}
    enqueued = 0
    now = datetime.datetime.now().isoformat(timespec="seconds")

    for lid, rel, abs_path in candidates:
        if already_handled(rel, ledger, queue):
            continue
        if credits_used + CREDITS_PER_IMAGE > args.max_credits:
            print("stopping: --max-credits %d reached" % args.max_credits)
            break
        if per_landlord_count.get(lid, 0) >= args.per:
            continue

        desc = harvest.describe(abs_path)
        decision, reason = harvest.decide(desc)
        if decision != "KEEP":
            print("  skip %s (%s): harvest filter would reject (%s)" % (rel, lid, reason))
            continue

        room = classify_room(desc)
        prompt = build_prompt(prompts, room)

        if DRY_RUN:
            print("=== %s  %s  room=%s ===" % (lid, rel, room))
            print(prompt[:400] + ("..." if len(prompt) > 400 else ""))
            per_landlord_count[lid] = per_landlord_count.get(lid, 0) + 1
            continue

        queue.append({
            "id": "%s-%s" % (lid, os.path.splitext(os.path.basename(rel))[0]),
            "landlord": lid,
            "source": rel,
            "room": room,
            "prompt": prompt,
            "model": prompts.get("model", "nano_banana_pro"),
            "resolution": prompts.get("resolution", "1k"),
            "prompt_version": PROMPT_VERSION,
            "credits_est": CREDITS_PER_IMAGE,
            "status": "queued",
            "queued_at": now,
        })
        credits_used += CREDITS_PER_IMAGE
        per_landlord_count[lid] = per_landlord_count.get(lid, 0) + 1
        enqueued += 1

    if DRY_RUN:
        print("\ndry run: classified %d photo(s), no queue or ledger writes" %
              sum(per_landlord_count.values()))
        return

    if enqueued:
        _save_json(QUEUE_PATH, queue)
    print("enqueued %d job(s) across %d landlord(s) -> %s" %
          (enqueued, len(per_landlord_count), QUEUE_PATH))
    if enqueued:
        print("run a Claude Code session with the higgsfield MCP to process the queue, "
              "then call --complete for each finished job")


def run_complete(args):
    queue = _load_json(QUEUE_PATH, [])
    ledger = _load_json(LEDGER_PATH, [])
    entry = next((e for e in queue if e.get("source") == args.complete and e.get("status") == "queued"), None)
    if entry is None:
        print("no queued job found for source %s" % args.complete)
        sys.exit(1)
    if not os.path.exists(args.output):
        print("--output %s does not exist" % args.output)
        sys.exit(1)

    abs_src = os.path.join(HERE, "deploy", entry["source"])
    base, ext = os.path.splitext(abs_src)
    staged_abs = base + "_staged" + (os.path.splitext(args.output)[1] or ext)
    if os.path.exists(staged_abs):
        print("refusing to overwrite existing %s" % staged_abs)
        sys.exit(1)
    shutil.copy2(args.output, staged_abs)
    staged_rel = os.path.relpath(staged_abs, os.path.join(HERE, "deploy"))

    now = datetime.datetime.now().isoformat(timespec="seconds")
    ledger.append({
        "landlord": entry["landlord"],
        "source": entry["source"],
        "staged": staged_rel,
        "room": entry["room"],
        "model": entry.get("model", "nano_banana_pro"),
        "prompt_version": entry.get("prompt_version", PROMPT_VERSION),
        "job_id": args.job_id,
        "credits": args.credits if args.credits is not None else entry.get("credits_est"),
        "timestamp": now,
    })
    entry["status"] = "done"
    entry["staged"] = staged_rel
    entry["completed_at"] = now
    _save_json(LEDGER_PATH, ledger)
    _save_json(QUEUE_PATH, queue)
    print("recorded %s -> %s (job %s)" % (entry["source"], staged_rel, args.job_id))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--landlord", help="limit to one landlord id, e.g. LL215")
    ap.add_argument("--all", action="store_true",
                    help="include every active landlord, not just ones with the intake "
                         "form returned and photos received")
    ap.add_argument("--max-credits", type=int, default=100, help="credit budget for this run")
    ap.add_argument("--per", type=int, default=6, help="max photos to queue per landlord this run")
    ap.add_argument("--min-id", help="restrict to landlords at or above this id, e.g. LL160")
    ap.add_argument("--complete", metavar="SOURCE",
                     help="mark a queued job done: SOURCE is its photos/LLxxx/N.jpg source path")
    ap.add_argument("--output", help="finished staged image file (with --complete)")
    ap.add_argument("--job-id", help="Higgsfield job id (with --complete)")
    ap.add_argument("--credits", type=int, help="actual credits spent (with --complete)")
    args = ap.parse_args()

    if args.complete:
        if not args.output:
            ap.error("--complete requires --output")
        run_complete(args)
        return

    run_queue(args)


if __name__ == "__main__":
    main()
