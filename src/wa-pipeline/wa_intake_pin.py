#!/usr/bin/env python3
"""Engine integrity pin for the WhatsApp intake runner.

WHY THIS EXISTS
    launchd runs src/wa-pipeline/wa_intake_runner.py directly out of the working
    directory every 60s, and `import intake_engine` resolves to whatever sits
    beside it on disk. The worktree IS production. A silent revert, a stray
    checkout, or a half-applied edit therefore changes live tenant-facing
    behaviour with no commit, no review and no signal -- the same shape as the
    July cold-lead incident.

    This module pins the engine to a state that is known-committed, and lets the
    runner fail CLOSED when the bytes on disk stop matching it.

USAGE
    python3 src/wa-pipeline/wa_intake_pin.py --check    # verdict + exit 0/1
    python3 src/wa-pipeline/wa_intake_pin.py --pin      # bless current state (must be clean vs HEAD)
    python3 src/wa-pipeline/wa_intake_pin.py --show     # print the current pin
"""
import os, sys, json, hashlib, subprocess, datetime
import wa_intake_paths as _P

REPO = os.path.expanduser("~/crestbrick-consult")
SRC = os.path.join(REPO, "src", "wa-pipeline")
# STEP 0 sandbox seal (9 Sep 2026 merge redo): PIN resolves through wa_intake_paths like
# every other engine-family state file, so a sandboxed test never blesses/reads the LIVE
# engine-pin.json. Unset (production, launchd) it is byte-for-byte the historical hardcoded
# path -- see wa_intake_paths.resolved's docstring for the back compat / call-time rule.
PIN = _P.paths()["engine_pin"]
_default_PIN = PIN


def _pin_path():
    return _P.resolved(globals(), "PIN", "engine_pin")

# Every module the runner loads at import time. A change to any of these changes
# what the live sender does, so all of them are pinned, not just the engine.
# Extended 9 Sep 2026 (intake-merged integration): the merge redo split the runner into
# many more sibling modules (owner loop, category-2 replies, listing match, the low level
# send/guard primitives, the sandbox seal path layer, and the shared draft generator) -- an
# unpinned edit to any of THESE is exactly as live-facing as an edit to intake_engine.py
# itself, so the pin now covers every module wa_intake_runner.py loads at import time,
# transitively (wa_intake_resume.py itself imports wa_intake_draft.py at module load).
ENGINE_FILES = (
    "intake_engine.py",
    "wa_intake_runner.py",
    "wa_intake_paths.py",
    "wa_intake_resume.py",
    "wa_intake_selfchat.py",
    "wa_intake_notify.py",
    "wa_intake_send.py",
    "wa_intake_replies.py",
    "wa_intake_owner.py",
    "wa_intake_owner_answers.py",
    "wa_intake_listing_match.py",
    "wa_intake_draft.py",
    "wa_intake_echo.py",
    "wa_intake_pin.py",          # the guard guards itself, or it can be silently defanged
)


def _git(*args):
    return subprocess.run(("git", "-C", REPO) + args, capture_output=True,
                          text=True, timeout=30)


def _sha(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def digest():
    return {n: _sha(os.path.join(SRC, n)) for n in ENGINE_FILES}


def _rel(n):
    return os.path.join("src", "wa-pipeline", n)


def uncommitted():
    """Engine files that differ from HEAD (staged, unstaged, or untracked)."""
    r = _git("status", "--porcelain", "--", *[_rel(n) for n in ENGINE_FILES])
    if r.returncode != 0:
        return None                      # git unavailable: caller decides
    return sorted({ln[3:].strip() for ln in r.stdout.splitlines() if ln.strip()})


def head_sha():
    r = _git("rev-parse", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else None


def load_pin():
    try:
        with open(_pin_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def verify():
    """(ok, reason, detail) -- reason is a short stable code for alert dedup."""
    cur = digest()
    missing = [n for n, h in cur.items() if h is None]
    if missing:
        return False, "engine_file_missing", {"missing": missing}

    p = load_pin()
    if not p:
        return False, "no_pin", {"hint": "run src/wa-pipeline/wa_intake_pin.py --pin"}

    drifted = sorted(n for n in ENGINE_FILES if cur.get(n) != (p.get("files") or {}).get(n))
    if drifted:
        return False, "engine_drift", {
            "drifted": drifted,
            "pinned_sha": p.get("sha"),
            "pinned_at": p.get("pinned_at"),
            "head": head_sha(),
        }

    # The pin matched, so the bytes are the blessed ones. Belt-and-braces: the
    # blessed state must still be a committed state, or the pin was taken over
    # a dirty tree by hand.
    dirty = uncommitted()
    if dirty:
        return False, "pinned_state_uncommitted", {"dirty": dirty}

    return True, "ok", {"sha": p.get("sha")}


def do_pin():
    dirty = uncommitted()
    if dirty is None:
        print("REFUSED: git not available; cannot prove the tree is committed.")
        return 1
    if dirty:
        print("REFUSED: engine files differ from HEAD. Commit them first, then re-pin.")
        for d in dirty:
            print("   dirty:", d)
        return 1
    cur = digest()
    if any(h is None for h in cur.values()):
        print("REFUSED: engine file missing.")
        return 1
    rec = {
        "sha": head_sha(),
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip(),
        "pinned_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "files": cur,
    }
    pin_path = _pin_path()
    os.makedirs(os.path.dirname(pin_path), exist_ok=True)
    tmp = pin_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rec, f, indent=1)
    os.replace(tmp, pin_path)
    print("pinned", rec["sha"][:12], "on", rec["branch"], "->", pin_path)
    return 0


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else "--check"
    if arg == "--pin":
        return do_pin()
    if arg == "--show":
        print(json.dumps(load_pin(), indent=1))
        return 0
    ok, reason, detail = verify()
    print(("OK  " if ok else "FAIL") + "  " + reason)
    if not ok:
        print(json.dumps(detail, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
