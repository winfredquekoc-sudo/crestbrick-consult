#!/usr/bin/env python3
"""Rebuild the offline Matchmaker app from the current rental databases. Fails
CLOSED at every step: if tests fail, the export looks wrong, or a source file
the UI lane owns (styles.css/scoring.js/app.js) is missing, nothing is written
and the previous _local/matchmaker.html (if any) is left exactly as it was.

Run any time after the nightly DB refresh:  python3 scripts/matchmaker/build.py
Stats-only Telegram ping (off by default):  python3 scripts/matchmaker/build.py --telegram
"""
import argparse, json, os, re, shutil, subprocess, sys, tempfile, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
WORKTREE_ROOT = os.path.dirname(os.path.dirname(HERE))          # tracked files (tests/, template.html, ...)
ROOT = os.path.expanduser("~/crestbrick-consult")                # main checkout: _templates in, _local out
OUT = os.path.join(HERE, "matchmaker-data.json")
PREV = os.path.join(HERE, "matchmaker-data.prev.json")
TEMPLATE = os.path.join(HERE, "template.html")
CSS = os.path.join(HERE, "styles.css")
SCORING = os.path.join(HERE, "scoring.js")
APPJS = os.path.join(HERE, "app.js")
# item 5/47 -- the ONE shared list of test files build.py and deploy.sh both
# gate on (see read_suites()). Was two hardcoded tuples that drifted: deploy.sh
# ran a 4 suite subset of build.py's 6 for months, so a by hand deploy of an
# artifact built before a failing change on the 2 uncovered suites could still
# ship. A plain text file, not another Python structure, so deploy.sh's bash
# can read it with nothing fancier than a loop + case statement.
SUITES_FILE = os.path.join(WORKTREE_ROOT, "tests", "matchmaker", "SUITES")
STATS_PATH = os.path.expanduser("~/.claude/state/matchmaker-stats.json")
BUILD_HISTORY_PATH = os.path.expanduser("~/.claude/state/matchmaker-build-history.jsonl")
TELEGRAM_SEND = os.path.expanduser("~/.claude/bin/telegram_send.sh")
TELEGRAM_ENV_PRIMARY = os.path.expanduser("~/.telegram-bot.env")
TELEGRAM_ENV_FALLBACK = os.path.expanduser("~/.claude/.env")

# item 6/37 -- soft ceiling only, matches item 37's own stated number. Never
# fails the build (same "warn, never block" spirit as ANOMALY_THRESHOLD below):
# this artifact is offline first by design (a single inlined PWA, see item
# 45's own note), so blocking a build over size would trade a working app for
# no app at all. The artifact was already ~1.9MB and growing with every new
# landlord/tenant, with no visibility into which section was doing the
# growing — check_payload_size() below turns "it's bigger again" into "it's
# THIS section that's bigger", which is what a size regression needs to be
# fixed without guessing.
PAYLOAD_SIZE_WARN_MB = 2.5
RING_SIZE = 3                                    # [61] artifacts kept in _local/
# Accepts both widths on purpose: names are YYYYMMDD-HHMMSS now (two builds in
# the same MINUTE used to write the same filename, so the second silently
# replaced the ring copy of the first instead of adding one), but copies written
# by earlier builds are YYYYMMDD-HHMM and must stay prunable rather than
# lingering forever as unrecognised files. Plain string sort still orders them
# correctly: the prefix is fixed width and "." sorts before any digit, so the
# older 4 digit name of a given minute sorts before that minute's 6 digit ones.
RING_RE = re.compile(r"^matchmaker-\d{8}-\d{4}(?:\d{2})?\.html$")
DIGEST_NAME = "matchmaker-digest.html"           # [64]
ANOMALY_THRESHOLD = 0.30                         # [63]
BUILD_HISTORY_TAIL = 7                           # [62]


def fail(msg):
    print("BUILD ABORTED: " + msg, file=sys.stderr)
    sys.exit(1)


def atomic_write_text(path, text):
    """Write via a temp file in the SAME directory, then os.replace().

    open(path, "w") truncates first: a crash, a kill, or a full disk part way
    through leaves a half written file where the last good one used to be —
    and a truncated matchmaker.html still opens, it just renders a broken app.
    This file's whole contract is "if anything goes wrong the previous artifact
    is left exactly as it was", which a truncating write cannot honour."""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".part", dir=d)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def atomic_copy(src, dst):
    """shutil.copy() truncates the destination too — same hazard as above, and
    here the destination is either the previous good payload (PREV) or the
    payload being restored onto (OUT)."""
    with open(src) as f:
        atomic_write_text(dst, f.read())


# --------------------------------------------------------- [1] run tests --
# item 5 -- EVERY tunable threshold scoring.js declares at its own top now has
# a matching config.json entry (was 3 of ~12); INT_PAIRS/FLOAT_PAIRS/ARRAY_PAIRS
# below are checked exhaustively so a threshold added later only needs an
# entry here to be covered, instead of silently drifting unnoticed like the
# other ~9 did before this. WHOLE_UNIT_FLAG/SGT_TZ/DIMENSION_MAX/ADJ/MRT_LINES
# are deliberately not here: they are not tunable numeric thresholds (a
# string, a map, or a fixed geography table), so config.json is not the
# right home for them.
INT_PAIRS = (("DEAD_DAYS_THRESHOLD", "dead_days"), ("COLD_DAYS_THRESHOLD", "cold_days"),
             ("DAYS_LISTED_ELASTICITY_THRESHOLD", "days_listed_elasticity"),
             ("LOOKALIKE_PENALTY", "lookalike_penalty"), ("NUDGE_AFTER_DAYS", "nudge_after_days"),
             ("DATA_AGE_AMBER_DAYS", "data_age_amber_days"), ("DATA_AGE_RED_DAYS", "data_age_red_days"))
FLOAT_PAIRS = (("NEAR_MISS_CLEARANCE", "near_miss_clearance"), ("BUDGET_HARD_BLOCK_RATIO", "budget_hard_block_ratio"))
ARRAY_PAIRS = (("PRICE_ELASTICITY_DELTAS", "price_elasticity_deltas"),)


def check_config_consistency():
    """config.json is the single home for thresholds; scoring.js keeps mirror
    literals because it runs in the browser. A drifted mirror ships wrong
    behavior silently, so drift fails the build (Winfred, 21 Aug 2026)."""
    cfg = json.load(open(os.path.join(HERE, "config.json")))
    src = open(SCORING, encoding="utf-8").read()
    for js_name, cfg_key in INT_PAIRS:
        m = re.search(r"var %s = (\d+)" % js_name, src)
        if not m:
            fail(f"scoring.js no longer declares {js_name} — cannot verify against config.json")
        if int(m.group(1)) != int(cfg[cfg_key]):
            fail(f"threshold drift: scoring.js {js_name}={m.group(1)} but config.json {cfg_key}={cfg[cfg_key]} — fix one, they must match")
    for js_name, cfg_key in FLOAT_PAIRS:
        m = re.search(r"var %s = ([0-9.]+)" % js_name, src)
        if not m:
            fail(f"scoring.js no longer declares {js_name} — cannot verify against config.json")
        if float(m.group(1)) != float(cfg[cfg_key]):
            fail(f"threshold drift: scoring.js {js_name}={m.group(1)} but config.json {cfg_key}={cfg[cfg_key]} — fix one, they must match")
    for js_name, cfg_key in ARRAY_PAIRS:
        m = re.search(r"var %s = \[([0-9,\s]+)\]" % js_name, src)
        if not m:
            fail(f"scoring.js no longer declares {js_name} — cannot verify against config.json")
        js_vals = [int(x.strip()) for x in m.group(1).split(",") if x.strip()]
        if js_vals != [int(x) for x in cfg[cfg_key]]:
            fail(f"threshold drift: scoring.js {js_name}={js_vals} but config.json {cfg_key}={cfg[cfg_key]} — fix one, they must match")
    if int(cfg["stale_days"]) != int(cfg["dead_days"]):
        fail("config.json stale_days != dead_days — same rule seen from two sides, must stay equal")


def read_suites(path=SUITES_FILE):
    """[47] Parse the shared SUITES file: one path per line, relative to the
    repo root, `#` starts a comment, blank lines ignored. Extension picks the
    runner (.mjs -> node --test, .py -> python3) in run_tests() below."""
    if not os.path.exists(path):
        fail(f"{path} missing — the shared test suite list build.py and deploy.sh both read")
    out = []
    for line in open(path):
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(os.path.join(WORKTREE_ROOT, line))
    if not out:
        # Mirrors deploy.sh's own gate on the exact same file: a SUITES file
        # that exists but parses to zero paths (every line blank or
        # commented out) is a broken gate, not an empty one -- fail closed
        # instead of silently skipping every test and shipping unverified.
        fail(f"{path} parsed to zero test files")
    return out


def run_tests():
    suites = read_suites()
    missing = [p for p in suites if not os.path.exists(p)]
    if missing:
        fail("required test file(s) missing, cannot verify before build:\n  " + "\n  ".join(missing))
    if not shutil.which("node"):
        fail("node not found on PATH — needed for the .mjs test suites")
    for path in suites:
        if path.endswith(".mjs"):
            if subprocess.run(["node", "--test", path]).returncode != 0:
                fail(f"tests/matchmaker/{os.path.basename(path)} failed — aborting, nothing written")
        elif path.endswith(".py"):
            if subprocess.run(["/usr/bin/python3", path]).returncode != 0:
                fail(f"tests/matchmaker/{os.path.basename(path)} failed — aborting, nothing written")


# ----------------------------------------------------- [2]/[3] export -----
def snapshot_prev():
    if os.path.exists(OUT):
        atomic_copy(OUT, PREV)

def run_export():
    r = subprocess.run(["/usr/bin/python3", os.path.join(HERE, "export_data.py")])
    if r.returncode != 0:
        restore_prev()
        fail("export_data.py failed — aborting; previous artifact (if any) restored")


def restore_prev():
    if os.path.exists(PREV):
        atomic_copy(PREV, OUT)
    elif os.path.exists(OUT):
        os.remove(OUT)  # no prior good state to fall back to — don't leave a known-bad file


# --------------------------------------------------------- [4] validate ---
def validate_payload(now):
    if not os.path.exists(OUT):
        fail("export_data.py did not produce matchmaker-data.json")
    try:
        data = json.load(open(OUT))
    except (OSError, ValueError) as e:
        restore_prev()
        fail(f"matchmaker-data.json does not parse ({e}) — restored previous artifact")
    problems = []
    if data.get("schema_version") != 2: problems.append("schema_version != 2")
    if len(data.get("listings") or []) < 1: problems.append("zero listings")
    if len(data.get("tenants") or []) < 1: problems.append("zero tenants")
    ts = data.get("generated_ts")
    fresh = False
    if ts:
        try:
            parsed = datetime.datetime.fromisoformat(ts)
            fresh = abs((now - parsed).total_seconds()) < 300
        except ValueError:
            pass
    if not fresh: problems.append("generated_ts missing or not fresh (>5min old)")
    if problems:
        restore_prev()
        fail("payload validation failed (" + "; ".join(problems) + ") — restored previous artifact")
    return data


# --------------------------------------------------------- [63] anomaly ---
def pct_swing(old, new):
    """Pure. old falsy (None or 0) -> None if new is also falsy (nothing to
    compare, not an anomaly) else +inf (can't express a % of zero, but a
    0 -> N jump is exactly the kind of swing this guard exists to catch)."""
    if not old:
        return None if not new else float("inf")
    return abs(new - old) / old


PENDING_REVIEW_WARN_THRESHOLD = 5  # item 4/43 -- unreviewed content-sweep stubs; absolute, not a swing

# item 1/43 (revised 18 Sep 2026) -- the original absolute threshold on
# export_data.py's health["tenants_sparse"] counter (fewer than
# SPARSE_PROFILE_MIN of 14 profile fields filled) fired on the very first
# live build: 299 of 455 tenants in this book have fewer than 4 fields
# filled, so a sparse count sits well past any single digit absolute number
# as a matter of course. That is the normal shape of this roster, not a
# fault -- an absolute threshold cannot tell "this book is naturally sparse"
# from "something upstream just degraded intake quality", so it is useless
# here. Same fix as the listings/tenants counts below: track the SPARSE
# SHARE (tenants_sparse / still_looking_tenants) and warn only when it rises
# more than this many percentage points versus the previous build's share,
# which is what an intake regression (a WA bridge outage truncating fields,
# a parsing bug) actually looks like -- a sudden jump, not a steady state.
SPARSE_SHARE_SWING_THRESHOLD = 0.10

# item 1/43 -- the pending_review absolute check above catches a SINGLE build
# with too many unreviewed stubs, but the 22-stub incident it was written for
# actually accumulated a few at a time across MANY builds, each individually
# under the threshold. This catches that shape directly: pending_review
# sitting above zero for this many consecutive builds (current build
# included) means stubs are piling up faster than they are being reviewed,
# regardless of how small each build's own count is. 5 consecutive daytime
# refresh builds is roughly one calendar day at the 5-slot cadence (09/12/15/
# 18/21 SGT) — enough time for Winfred to have reviewed a fresh stub the same day
# if he were going to, so 5 in a row nonzero is a genuine "this is not being
# cleared" signal, not noise from one busy afternoon.
CONTENT_SWEEP_PERSIST_BUILDS = 5


def check_anomalies(prev_counts, cur_counts, cur_health=None, threshold=ANOMALY_THRESHOLD,
                     pending_review_threshold=PENDING_REVIEW_WARN_THRESHOLD,
                     sparse_swing_threshold=SPARSE_SHARE_SWING_THRESHOLD, prev_sparse_share=None,
                     recent_pending_review=None, persist_builds=CONTENT_SWEEP_PERSIST_BUILDS):
    """Pure — no file I/O, so tests can feed fixture dicts directly. Returns a
    list of human readable warning strings (empty = nothing crossed the
    threshold). Never raises, never blocks the build — this is a smell
    detector (a source file truncated, exclusions got too aggressive, a
    status mapping broke), not a hard gate; a real market swing is also
    allowed through, just noted.

    The pending_review check (item 4/43) is an ABSOLUTE threshold, checked
    even on a first run with no prev_counts at all: it exists because
    refresh-rental-dbs.sh's own content sweep (running Haiku several times a
    day) writes unconfirmed landlord stubs straight into the live database,
    and 22 of them went unreviewed long enough to reach tenants as live
    listings before 15 Sep 2026 — a swing only check would never have caught
    a slow accumulation like that, since each individual build's INCREASE
    can be too small to cross a percentage threshold.

    tenants_sparse (item 1/43, revised 18 Sep 2026) is instead a SHARE swing
    check, same style as the listings/tenants counts below: cur_health's
    tenants_sparse divided by cur_counts's still_looking_tenants is compared
    against prev_sparse_share (the previous build's share, computed by
    build.py's anomaly_guard() from the build history file's last shipped
    entry) and flagged only if it rose by more than sparse_swing_threshold
    percentage points. prev_sparse_share=None (no previous entry, or one
    that predates this counter) skips the check entirely — never guessed.

    recent_pending_review (item 1/43), when given, is the pending_review
    value from each of the last `persist_builds` builds INCLUDING this one,
    oldest first — see build.py's anomaly_guard() for how it is assembled
    from the build history file. A value of None for a build that predates
    this counter is treated as "not persistently nonzero" (never falsely
    triggers on old history alone)."""
    out = []
    pending_review = (cur_health or {}).get("pending_review")
    if pending_review is not None and pending_review > pending_review_threshold:
        out.append(f"pending_review stubs at {pending_review} (unreviewed content-sweep records), "
                    f"above {pending_review_threshold}")
    sparse = (cur_health or {}).get("tenants_sparse")
    still_looking = (cur_counts or {}).get("still_looking_tenants")
    if sparse is not None and still_looking and prev_sparse_share is not None:
        cur_sparse_share = sparse / still_looking
        share_delta = cur_sparse_share - prev_sparse_share
        if share_delta > sparse_swing_threshold:
            out.append(f"tenants_sparse share at {cur_sparse_share * 100:.0f}% of still looking tenants, "
                        f"up {share_delta * 100:.0f}pp from the previous build's {prev_sparse_share * 100:.0f}% "
                        f"(more than {sparse_swing_threshold * 100:.0f}pp)")
    if recent_pending_review and len(recent_pending_review) >= persist_builds and \
            all((v or 0) > 0 for v in recent_pending_review[-persist_builds:]):
        out.append(f"pending_review stubs have stayed above zero for the last {persist_builds} "
                    f"builds in a row (currently {pending_review}) — accumulating rather than being cleared")
    if not prev_counts:
        return out
    for key, label in (("available_listings", "listings"), ("still_looking_tenants", "tenants")):
        old, new = prev_counts.get(key), cur_counts.get(key)
        if old is None or new is None:
            continue
        swing = pct_swing(old, new)
        if swing is not None and swing > threshold:
            pct_txt = "inf" if swing == float("inf") else f"{swing * 100:.0f}%"
            out.append(f"{label} count swung {pct_txt} vs previous build ({old} -> {new})")
    return out


def anomaly_guard(data):
    prev = None
    if os.path.exists(PREV):
        try:
            prev = json.load(open(PREV))
        except (OSError, ValueError):
            prev = None
    health = data.get("health") or {}
    # item 1/43 -- the last (CONTENT_SWEEP_PERSIST_BUILDS - 1) SHIPPED builds'
    # pending_review counts, plus this build's own (not yet appended to the
    # history file at this point in main()), oldest first.
    tail = read_build_history_tail(BUILD_HISTORY_PATH, CONTENT_SWEEP_PERSIST_BUILDS - 1)
    recent_pending_review = [e.get("pending_review") for e in tail] + [health.get("pending_review")]
    # item 1/43 (revised 18 Sep 2026) -- the previous SHIPPED build's sparse
    # share, derived from the build history's last entry rather than PREV
    # (matchmaker-data.prev.json only carries counts, not health). Skipped
    # (None) when there is no previous entry yet, or it predates this
    # counter and never carried tenants_sparse.
    prev_sparse_share = None
    if tail:
        prev_sparse, prev_tenants = tail[-1].get("tenants_sparse"), tail[-1].get("tenants")
        if prev_sparse is not None and prev_tenants:
            prev_sparse_share = prev_sparse / prev_tenants
    warnings = check_anomalies((prev or {}).get("counts") or {}, data.get("counts") or {}, health,
                                recent_pending_review=recent_pending_review,
                                prev_sparse_share=prev_sparse_share)
    for w in warnings:
        print("ANOMALY WARNING: " + w, file=sys.stderr)
    return warnings


# ---------------------------------------------------- [62] build history --
def build_history_entry(data, computed, now, anomaly_lines=None):
    """Pure. computed is compute_scoring_stats()'s return value (may be None
    if scoring.js was unavailable/errored) — worklist_size degrades to None
    rather than raising.

    pending_review, tenants_sparse and anomalies (item 1/2/43) are carried
    per entry so two later readers can use them without deriving them again:
    anomaly_guard() reads back a short tail of pending_review values to
    detect a persistent (not just a single build) stub pileup, plus the
    previous entry's tenants_sparse (alongside tenants, already carried) to
    compute the prior sparse share for the delta check, and monday_brief.py's
    section_matchmaker() reads the most recent non empty `anomalies` entry to
    report the last anomaly warning without ever calling the network."""
    counts = data.get("counts") or {}
    health = data.get("health") or {}
    return {
        "ts": data.get("generated_ts") or now.isoformat(timespec="seconds"),
        "listings": counts.get("available_listings"),
        "tenants": counts.get("still_looking_tenants"),
        "worklist_size": None if not computed else computed.get("worklist_size"),
        "health_total": sum(v for v in health.values() if isinstance(v, (int, float))),
        "pending_review": health.get("pending_review"),
        "tenants_sparse": health.get("tenants_sparse"),
        "anomalies": list(anomaly_lines or []),
    }


def append_build_history(path, entry):
    """One line per SHIPPED build (called only after the artifact is written —
    see main()). Self heals a truncated tail: if a previous run died part way
    through its line the file has no trailing newline, and appending straight
    onto it would glue two records into one unreadable line, losing both."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lead = ""
    if os.path.exists(path) and os.path.getsize(path):
        with open(path, "rb") as f:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                lead = "\n"
    with open(path, "a") as f:
        f.write(lead + json.dumps(entry, ensure_ascii=False) + "\n")


def read_build_history_tail(path, n=BUILD_HISTORY_TAIL):
    if not os.path.exists(path):
        return []
    out = []
    for line in open(path).read().splitlines()[-n:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue  # one corrupt line never sinks the whole tail
    return out


# ------------------------------------------------------- [61] artifact ring
def ring_filename(now):
    # Seconds included: two builds inside one minute produced the same name, so
    # the second overwrote the first's ring copy — the ring silently held fewer
    # than RING_SIZE distinct builds, and the copy destroyed was the older (more
    # likely still good) one.
    return f"matchmaker-{now.strftime('%Y%m%d-%H%M%S')}.html"


def select_ring_prune(existing_filenames, keep=RING_SIZE):
    """Pure. existing_filenames: any iterable of names found in the _local dir
    (the newest ring copy is expected to already be among them). Non ring
    shaped names (matchmaker.html, matchmaker-data.json, ...) are ignored
    entirely — never a candidate to keep OR prune. Returns (kept, pruned),
    both oldest-to-newest; names sort correctly as plain strings because the
    timestamp is fixed width YYYYMMDD-HHMM."""
    ring = sorted(f for f in existing_filenames if RING_RE.match(f))
    if len(ring) <= keep:
        return ring, []
    return ring[-keep:], ring[:-keep]


def update_artifact_ring(outdir, now, html_text):
    fname = ring_filename(now)
    atomic_write_text(os.path.join(outdir, fname), html_text)
    kept, pruned = select_ring_prune(os.listdir(outdir))
    for stale in pruned:
        try:
            os.remove(os.path.join(outdir, stale))
        except OSError:
            pass
    return kept, pruned


# ------------------------------------------------- [5]/[6] inline+write ---
def js_safe_json(obj):
    """json.dumps output goes inside a <script> tag, so a '</script>' anywhere
    in the data (last_wa.snippet is verbatim WhatsApp text somebody else typed,
    req_raw is verbatim landlord text) would close the tag early and let the
    rest of that value execute as markup. Escaping the '/' of every '</' fixes
    it without changing a single parsed value: inside a JSON string '\\/' is
    just '/'. U+2028/U+2029 are escaped for older JS engines that treat them as
    line terminators inside string literals."""
    return (json.dumps(obj, ensure_ascii=False)
            .replace("</", "<\\/")
            .replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029"))


def check_js_syntax():
    """scoring.js is exercised by its own test suite, but app.js has no test and
    is inlined verbatim — a syntax error there ships a blank app AND overwrites
    the last good artifact. `node --check` is the cheap gate that keeps the
    fail closed promise honest for both files."""
    for path in (SCORING, APPJS):
        r = subprocess.run(["node", "--check", path], capture_output=True, text=True)
        if r.returncode != 0:
            fail(f"{os.path.basename(path)} is not valid JavaScript, refusing to ship:\n" + (r.stderr or "").strip()[-800:])


def check_payload_size(data, size_kb):
    """[6/37] Pure — takes the already built size_kb rather than measuring
    anything itself, so a test can drive it with a fixture and a fake size
    with no real artifact on disk. Prints, never raises, never fails the
    build (see PAYLOAD_SIZE_WARN_MB's own comment). Sizes each top level
    payload key by its own JSON encoding — cheap (this is already a dict
    built entirely in memory) and gives a genuinely per section breakdown
    rather than a guess from field names."""
    size_mb = size_kb / 1024
    if size_mb <= PAYLOAD_SIZE_WARN_MB:
        return
    sizes = []
    for k, v in data.items():
        try:
            sizes.append((k, len(json.dumps(v, ensure_ascii=False))))
        except TypeError:
            continue
    sizes.sort(key=lambda kv: -kv[1])
    top3 = ", ".join(f"{k} ({n / 1024:.0f}KB)" for k, n in sizes[:3])
    print(f"PAYLOAD SIZE WARNING: artifact is {size_mb:.2f}MB, above the "
          f"{PAYLOAD_SIZE_WARN_MB}MB soft ceiling. Largest sections: {top3}", file=sys.stderr)


def inline_and_write(data, now):
    missing = [p for p in (TEMPLATE, CSS, SCORING, APPJS) if not os.path.exists(p)]
    if missing:
        fail("UI lane source file(s) missing, cannot assemble the app yet:\n  " + "\n  ".join(missing) +
             "\n(expected once the UI lane lands styles.css/scoring.js/app.js and template.html's placeholders)")
    check_js_syntax()

    tpl = open(TEMPLATE).read()
    css = open(CSS).read()
    scoring = open(SCORING).read()
    appjs = open(APPJS).read()
    # Leaflet is INLINED (not a CDN link): the app is a single offline artifact
    # behind a service worker, so an external <script> to unpkg fails to run on
    # the deployed PWA and the map draws no pins. Vendored copy under vendor/.
    leaflet_css = open(os.path.join(HERE, "vendor", "leaflet.css")).read()
    leaflet_js = open(os.path.join(HERE, "vendor", "leaflet.js")).read()
    data = dict(data)                            # local copy — never mutate the caller's dict
    # Dynamic freshness stamp on the shipped copy, derived from the SAME `now`
    # already used for generated_ts (see main()) — NOT a fresh
    # datetime.date.today() call, which reads whatever system TZ this process
    # happens to run under. Independently computing "today" here used to be
    # able to disagree with generated_ts by a day if that ever drifted from
    # SGT, which is exactly the kind of generated/generated_ts incoherence
    # app.js's data-age banner (dataAgeTier) assumes can never happen.
    data["generated"] = now.date().isoformat()
    payload = js_safe_json(data)
    # The invariant js_safe_json exists to provide, asserted on the real string
    # about to be written. A single unescaped "</" from a WhatsApp snippet or a
    # req_raw note is enough to close the script tag early.
    if "</" in payload:
        fail("data payload contains an unescaped </ after js_safe_json — refusing to ship a breakable artifact")
    # Same hazard from the other direction: an inlined source file carrying a
    # literal </script> would close the tag it is being inlined into. (A
    # <script> mention in a comment is harmless and stays allowed.)
    for src_name, src_text in (("styles.css", css), ("scoring.js", scoring), ("app.js", appjs), ("leaflet.css", leaflet_css), ("leaflet.js", leaflet_js)):
        if "</script>" in src_text:
            fail(f"{src_name} contains a literal </script>, which would close the tag it is inlined into")

    required_markers = ["/*__CSS__*/", "__SCORING__", "const DATA = __DATA__;", "__APP_JS__", "/*__LEAFLET_CSS__*/", "/*__LEAFLET_JS__*/"]
    absent = [m for m in required_markers if m not in tpl]
    if absent:
        fail("template.html is missing placeholder(s), cannot inline: " + ", ".join(absent))

    out = tpl.replace("/*__CSS__*/", css, 1)
    out = out.replace("__SCORING__", scoring, 1)
    out = out.replace("const DATA = __DATA__;", "const DATA = " + payload + ";", 1)
    out = out.replace("__APP_JS__", appjs, 1)
    out = out.replace("/*__LEAFLET_CSS__*/", leaflet_css, 1)
    out = out.replace("/*__LEAFLET_JS__*/", leaflet_js, 1)

    survivors = [m for m in required_markers if m in out]
    if survivors:
        fail("placeholder(s) survived inlining, refusing to ship: " + ", ".join(survivors))
    if 'name="robots"' not in out or "noindex" not in out or "nofollow" not in out:
        fail("robots noindex/nofollow meta tag missing from template.html output — refusing to ship a PII page that could be indexed")

    outdir = os.path.join(ROOT, "_local"); os.makedirs(outdir, exist_ok=True)
    p = os.path.join(outdir, "matchmaker.html")
    atomic_write_text(p, out)
    size_kb = round(len(out) / 1024, 1)
    print(f"built {p}  {size_kb} KB")
    check_payload_size(data, size_kb)                                        # [6/37]

    kept, pruned = update_artifact_ring(outdir, now, out)                     # [61]
    print(f"artifact ring: kept {len(kept)}" + (f" (pruned {len(pruned)})" if pruned else ""))

    return data, p, size_kb


# ------------------------------------------------------------ [64] digest -
def _esc(s):
    return (str(s) if s is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _digest_rows(items, cols):
    head = "<tr>" + "".join(f"<th>{_esc(c[0])}</th>" for c in cols) + "</tr>"
    body = "".join("<tr>" + "".join(f"<td>{_esc(c[1](it))}</td>" for c in cols) + "</tr>" for it in items)
    return head + body


def _rent_txt(l):
    """Mirrors app.js's own rentTxt() so a listing with one or both rent bounds
    missing reads as "rent TBC" here too, instead of the literal string "None"
    that f"${l.get('rent_min')}-{l.get('rent_max')}" used to print whenever a
    landlord record had neither value set (seen live: "$None-None")."""
    lo, hi = l.get("rent_min"), l.get("rent_max")
    if lo and hi and lo != hi:
        return f"${lo}-{hi}"
    return f"${lo or hi}" if (lo or hi) else "rent TBC"


def render_digest_html(data):
    """Printable, build side ONLY digest — no client JS, no scoring, just what
    export_data.py already computed. Marks/funnel state lives in the app's own
    localStorage and is deliberately not reproduced here (see footer)."""
    generated = _esc(data.get("generated"))
    counts = data.get("counts") or {}
    health = data.get("health") or {}
    delta = data.get("delta")
    area_demand = data.get("area_demand") or []
    # item 7 -- supply_overview was dropped from the shipped payload on 22 Aug
    # 2026 (the app now derives the same view from all_landlords via
    # lifecycle()), but this digest kept reading the now-absent field, so the
    # lifecycle table below has rendered "No data" silently for about three
    # weeks. all_landlords carries the same lifecycle key on every record
    # regardless of status, same as supply_overview did.
    supply = data.get("all_landlords") or []
    listings = data.get("listings") or []

    stale = sorted((l for l in listings if l.get("reconfirm_due")), key=lambda l: -(l.get("days_listed") or 0))

    lifecycle_counts = {}
    for s in supply:
        k = s.get("lifecycle") or "unknown"
        lifecycle_counts[k] = lifecycle_counts.get(k, 0) + 1

    demand_sorted = sorted((d for d in area_demand if d.get("unmatched_waiting")),
                            key=lambda d: -(d.get("unmatched_waiting") or 0))[:10]

    if not delta:
        delta_html = "<p>First build — no previous snapshot to compare.</p>"
    else:
        delta_html = (f"<p>Since {_esc(delta.get('prev_generated'))}: "
                      f"{len(delta.get('new_tenant_ids') or [])} new tenants, "
                      f"{len(delta.get('new_listing_ids') or [])} new listings, "
                      f"{len(delta.get('gone_listings') or [])} gone, "
                      f"{len(delta.get('availability_changes') or [])} availability changes.</p>")

    stale_table = _digest_rows(stale[:25], [
        ("Listing", lambda l: l.get("name") or l.get("id")),
        ("District", lambda l: l.get("district")),
        ("Days listed", lambda l: l.get("days_listed")),
        ("Rent", _rent_txt),
    ]) if stale else "<tr><td>None — nothing overdue for reconfirmation</td></tr>"

    demand_table = _digest_rows(demand_sorted, [
        ("Area", lambda d: d.get("area")),
        ("District", lambda d: d.get("district")),
        ("Unmatched waiting", lambda d: d.get("unmatched_waiting")),
        ("Sourcing priority", lambda d: d.get("sourcing_priority")),
    ]) if demand_sorted else "<tr><td>No unmatched demand recorded</td></tr>"

    lifecycle_table = _digest_rows(sorted(lifecycle_counts.items()), [
        ("Lifecycle", lambda kv: kv[0]), ("Count", lambda kv: kv[1]),
    ]) if lifecycle_counts else "<tr><td>No data</td></tr>"

    health_table = _digest_rows(sorted(health.items()), [
        ("Gap", lambda kv: kv[0].replace("_", " ")), ("Count", lambda kv: kv[1]),
    ]) if health else "<tr><td>No data</td></tr>"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Matchmaker digest — {generated}</title>
<style>
  body {{ background:#0b1220; color:#e9e6da; font-family:-apple-system,Helvetica,Arial,sans-serif; margin:0; padding:32px; }}
  h1 {{ color:#c9a961; font-size:1.4rem; margin-bottom:4px; }}
  h2 {{ color:#c9a961; font-size:1.05rem; border-bottom:1px solid #29314a; padding-bottom:6px; margin-top:32px; }}
  .sub {{ color:#9aa3b5; margin-top:0; }}
  table {{ border-collapse:collapse; width:100%; margin-top:10px; }}
  th, td {{ text-align:left; padding:6px 10px; border-bottom:1px solid #222a3d; font-size:0.9rem; }}
  th {{ color:#9aa3b5; font-weight:600; }}
  .stat-row {{ display:flex; gap:24px; flex-wrap:wrap; margin-top:10px; }}
  .stat {{ background:#141b2e; border:1px solid #29314a; border-radius:8px; padding:12px 16px; min-width:140px; }}
  .stat .n {{ font-size:1.5rem; color:#c9a961; }}
  .stat .l {{ color:#9aa3b5; font-size:0.8rem; }}
  footer {{ margin-top:40px; padding-top:16px; border-top:1px solid #29314a; color:#9aa3b5; font-size:0.8rem; }}
  @media print {{ body {{ background:#fff; color:#000; }} h1,h2 {{ color:#000; }} .stat {{ border-color:#ccc; background:#f7f7f7; }} }}
</style>
</head>
<body>
<h1>Matchmaker weekly digest</h1>
<p class="sub">Generated {generated} — build side data only, printable</p>

<h2>Snapshot</h2>
<div class="stat-row">
  <div class="stat"><div class="n">{counts.get('available_listings', '-')}</div><div class="l">available listings</div></div>
  <div class="stat"><div class="n">{counts.get('still_looking_tenants', '-')}</div><div class="l">tenants looking</div></div>
  <div class="stat"><div class="n">{len(stale)}</div><div class="l">reconfirm due (14d+)</div></div>
</div>

<h2>Since last build</h2>
{delta_html}

<h2>Supply overview (all lifecycle states)</h2>
<table>{lifecycle_table}</table>

<h2>Demand hot spots</h2>
<table>{demand_table}</table>

<h2>Stale listings (reconfirm due)</h2>
<table>{stale_table}</table>

<h2>Data health</h2>
<table>{health_table}</table>

<footer>
Funnel stats (Contacted &rarr; Viewing booked &rarr; Offer) live only in the interactive app's browser storage on
whichever device marked them — this build side digest has no access to that and does not attempt to reproduce it.
Open the app itself for live matching and marks.
</footer>
</body>
</html>"""


def write_digest(data, root):
    outdir = os.path.join(root, "_local"); os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, DIGEST_NAME)
    atomic_write_text(path, render_digest_html(data))
    print(f"wrote {path}")
    return path


# ------------------------------------------------------- [7] stats file ---
def compute_scoring_stats(data):
    """Returns {top_matches, worklist_size} via a one shot `node -e` run against
    scoring.js, or None (with a warning) if scoring.js doesn't exist yet or errors."""
    if not os.path.exists(SCORING):
        print("warning: scoring.js not present — omitting top_matches/worklist_size from stats")
        return None
    fields_l = ["id","district","rent_min","rent_max","gates","units","available_from","name","availability"]
    fields_t = ["id","district","preferred_districts","budget","budget_max","budget_min","pax",
                "gender","ethnicity","lease_months","move_in","move_in_norm","last_contact","last_wa","work_anchor"]
    trimmed = {
        "listings": [{k: l.get(k) for k in fields_l} for l in data["listings"]],
        "tenants": [{k: t.get(k) for k in fields_t} for t in data["tenants"]],
    }
    # A real OS temp file, not scripts/matchmaker/.stats_input.tmp.json: that
    # path carried real tenant/landlord fields (last_wa snippets included) with
    # no .gitignore coverage of its own — a crash between this write and the
    # `finally` cleanup below would leave real PII sitting in a git trackable
    # directory. mkstemp() keeps it outside the repo entirely regardless of
    # timing, and require() below works the same on any absolute path.
    fd, tmp_path = tempfile.mkstemp(prefix="matchmaker-stats-input-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(trimmed, f)
    # worklist_size mirrors the app's own worklist: one row per tenant (their best
    # non blocked match against a listing that isn't offer pending) — computed here
    # in the same pass as top_matches so scoring.js is only invoked once.
    #
    # scoring.js exposes Scoring via globalThis, not module.exports: this repo's
    # package.json sets "type":"module", so Node loads plain .js files as ESM and
    # require()ing one runs its body (for the side effect) but returns an empty
    # namespace object since the file has no `export` statements on purpose (it's
    # inlined as a raw <script> tag too, where `export` would throw). See the
    # "exposed two ways on purpose" comment at the bottom of scoring.js itself.
    # We check both require()'s return value and globalThis defensively so this
    # keeps working if that export strategy ever changes.
    node_src = (
        "const req = require(" + json.dumps(SCORING) + ");"
        "const Scoring = (req && typeof req.score === 'function') ? req : globalThis.Scoring;"
        "if (!Scoring || typeof Scoring.score !== 'function') {"
        "  console.error('Scoring.score not found via require() return value or globalThis.Scoring'); process.exit(1); }"
        "const data = require(" + json.dumps(tmp_path) + ");"
        "const all = []; const worklisted = new Set(); let pairs = 0, errors = 0;"
        "for (const l of data.listings) {"
        "  if (l.availability === 'Offer pending') continue;"
        "  for (const t of data.tenants) {"
        "    pairs++;"
        "    let s; try { s = Scoring.score(l, t); } catch (e) { errors++; continue; }"
        "    if (!s) continue;"
        "    const total = typeof s.total === 'number' ? s.total : (typeof s === 'number' ? s : null);"
        "    if (total == null || s.verdict === 'BLOCKED') continue;"
        "    worklisted.add(t.id);"
        "    all.push({listing: l.id, tenant: t.id, score: total});"
        "  }"
        "}"
        "if (pairs > 0 && errors === pairs) {"
        "  console.error('Scoring.score(l, t) threw on every pair (' + errors + '/' + pairs + ') — check field shape'); process.exit(1); }"
        "all.sort((a,b) => b.score - a.score);"
        "console.log(JSON.stringify({top_matches: all.slice(0, 5), worklist_size: worklisted.size}));"
    )
    try:
        r = subprocess.run(["node", "-e", node_src], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            print("warning: scoring.js top_matches computation failed — omitting top_matches\n" + r.stderr[-500:])
            return None
        return json.loads(r.stdout.strip() or "null")
    except (subprocess.SubprocessError, ValueError) as e:
        print(f"warning: scoring.js top_matches computation errored ({e}) — omitting top_matches")
        return None
    finally:
        if os.path.exists(tmp_path): os.remove(tmp_path)


def write_stats(data, computed=None):
    if computed is None:
        computed = compute_scoring_stats(data)
    stats = {
        "generated_ts": data.get("generated_ts"),
        "counts": data.get("counts"),
        "source_counts": data.get("source_counts"),
        "health": data.get("health"),
        "delta_summary": None if not data.get("delta") else {
            "prev_generated": data["delta"].get("prev_generated"),
            "new_tenants": len(data["delta"].get("new_tenant_ids") or []),
            "new_listings": len(data["delta"].get("new_listing_ids") or []),
            "gone_listings": len(data["delta"].get("gone_listings") or []),
            "availability_changes": len(data["delta"].get("availability_changes") or []),
        },
        "worklist_size": None if computed is None else computed["worklist_size"],
    }
    if computed is not None:
        stats["top_matches"] = computed["top_matches"]
    atomic_write_text(STATS_PATH, json.dumps(stats, indent=1, ensure_ascii=False))
    print("wrote", STATS_PATH)
    return stats


# --------------------------------------------------------- [9] telegram ---
def send_telegram(data, stats, anomaly_lines=None):
    if not os.path.exists(TELEGRAM_SEND):
        print("warning: --telegram requested but ~/.claude/bin/telegram_send.sh missing — skipping"); return
    chat_id = None
    for env_path in (TELEGRAM_ENV_PRIMARY, TELEGRAM_ENV_FALLBACK):
        if os.path.exists(env_path):
            for line in open(env_path):
                if line.startswith("TELEGRAM_WINFRED_CHAT_ID="):
                    chat_id = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
        if chat_id: break
    if not chat_id:
        print("warning: --telegram requested but TELEGRAM_WINFRED_CHAT_ID not set — skipping"); return

    sc = data.get("source_counts") or {}
    d = data.get("delta")
    delta_line = "first build (no delta)" if not d else (
        f"{len(d['new_tenant_ids'])} new tenants, {len(d['new_listing_ids'])} new listings, "
        f"{len(d['gone_listings'])} gone, {len(d['availability_changes'])} availability changes")
    msg = (f"Matchmaker rebuilt {data.get('generated')}\n"
           f"{sc.get('listings_available','?')} listings, {sc.get('tenants_looking','?')} tenants looking "
           f"({sc.get('excluded_agents',0)} agent exclusions)\n"
           f"Since last build: {delta_line}")
    if anomaly_lines:
        msg += "\nANOMALY: " + "; ".join(anomaly_lines)
    try:
        subprocess.run([TELEGRAM_SEND, chat_id], input=msg, text=True, timeout=15)
    except subprocess.SubprocessError as e:
        print(f"warning: telegram send failed ({e}) — build result is unaffected")


# ------------------------------------------------------------------ main --
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--telegram", action="store_true", help="also ping Winfred's Telegram with a stats only summary")
    args = ap.parse_args()

    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    check_config_consistency()
    run_tests()
    snapshot_prev()
    run_export()
    # Everything from here on runs with OUT already overwritten by the new
    # export while PREV still holds the last SHIPPED payload. Any abort past
    # this point (a bad payload, an app.js syntax error, a missing placeholder)
    # used to leave OUT advanced with no artifact written — so the NEXT build
    # snapshotted that never shipped payload as its baseline and its "since last
    # build" delta silently skipped a whole build's worth of new tenants and
    # listings. Restore on every exit path instead, so the delta always compares
    # against the last build Winfred actually received.
    try:
        data = validate_payload(now)             # fails closed + restores prev on bad payload

        anomaly_lines = anomaly_guard(data)                          # [63]
        computed = compute_scoring_stats(data)
        history_entry = build_history_entry(data, computed, now, anomaly_lines)  # [62]/[1,2,43]
        # Same 7 rows the artifact used to show, assembled without touching the
        # file yet: the entry is appended only once the artifact is on disk, so
        # the history log never claims a build that was never shipped.
        data["build_history"] = read_build_history_tail(BUILD_HISTORY_PATH, BUILD_HISTORY_TAIL - 1) + [history_entry]

        data, path, size_kb = inline_and_write(data, now)            # [61] ring update inside
    except BaseException:
        # BaseException, not just SystemExit: an unhandled non-SystemExit exception
        # anywhere in this block (a bad payload shape fail() never anticipated, a
        # KeyboardInterrupt mid-build, an unexpected bug) used to skip this restore
        # entirely and leave OUT advanced past PREV with no artifact ever shipped —
        # exactly the failure this block exists to prevent. Catching only SystemExit
        # covered intentional aborts via fail() but nothing else.
        restore_prev()
        raise

    append_build_history(BUILD_HISTORY_PATH, history_entry)
    stats = write_stats(data, computed)
    write_digest(data, ROOT)                                         # [64]

    # item 1/43 (revised 18 Sep 2026) -- sparse share is a delta warning now,
    # not an absolute count, so it no longer shows up on its own unless it
    # swings; print it plainly here every build so it stays visible either way.
    sparse_count = (data.get("health") or {}).get("tenants_sparse")
    still_looking = data['counts']['still_looking_tenants']
    sparse_txt = (f"  sparse {sparse_count}/{still_looking} ({sparse_count / still_looking * 100:.0f}%)"
                  if sparse_count is not None and still_looking else "")
    print(f"listings {data['counts']['available_listings']}  tenants {still_looking}  "
          f"stamp {data['generated']}  build_id {data['build_id']}{sparse_txt}")
    if anomaly_lines:
        print("ANOMALIES: " + " | ".join(anomaly_lines))
    if args.telegram:
        send_telegram(data, stats, anomaly_lines)

if __name__ == "__main__":
    main()
