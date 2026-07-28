#!/opt/homebrew/bin/python3
"""
wa_send_guard.py — Shared send guard for ALL WhatsApp automation scripts.

Every script that sends a WhatsApp message MUST call:
    from wa_send_guard import can_send, mark_sent

Before sending:
    if not can_send(chat_jid):
        return  # already replied recently — skip

After sending:
    mark_sent(chat_jid, source="whatsapp-autoreply")

This is the single source of truth across:
- whatsapp-autoreply.sh (via CLI wrapper below)
- wa_prospect_agent.py (imported directly)
- pg-rental-auto-reply, pg-qualify-reply, wa-tenant-profile-reply (import directly)

State file: ~/.claude/state/wa-unified-sent.json
Lock file:  ~/.claude/state/wa-unified-sent.lock

Format:
{
  "6512345678@lid": {
    "last_sent": "2026-06-04T13:00:00+08:00",
    "source": "whatsapp-autoreply",
    "count": 3
  }
}

COOLDOWN: 20 minutes — a DIFFERENT script cannot send to the same JID within 20 minutes
of another script sending to it (cross-sender dedup). The SAME source is EXEMPT in
reserve(): its own per-conversation latches govern its sequencing, so the intake engine can
send the form and then a follow-up (ask / nudge / viewing offer) once the prospect replies.
"""

import os
import json
import fcntl
import sys
import sqlite3
from datetime import datetime, timedelta, timezone

SENT_LOG  = os.path.expanduser("~/.claude/state/wa-unified-sent.json")
LOCK_FILE = os.path.expanduser("~/.claude/state/wa-unified-sent.lock")
WA_DB     = os.path.expanduser("~/whatsapp-mcp/whatsapp-bridge/store/whatsapp.db")
COOLDOWN_MINUTES = 20

# In-process LID↔phone cache — avoids hitting SQLite on every guard check
_lid_to_phone: dict = {}
_phone_to_lid: dict = {}


def _load_lid_map():
    """Populate LID↔phone caches from whatsapp.db (once per process)."""
    global _lid_to_phone, _phone_to_lid
    if _lid_to_phone:
        return
    if not os.path.exists(WA_DB):
        return
    try:
        conn = sqlite3.connect(WA_DB)
        rows = conn.execute("SELECT lid, pn FROM whatsmeow_lid_map").fetchall()
        conn.close()
        for lid, pn in rows:
            _lid_to_phone[lid] = pn
            _phone_to_lid[pn] = lid
    except Exception:
        pass


def _normalize(chat_jid: str) -> str:
    """
    Normalize any JID form to a canonical phone number string so that
    LID JIDs and phone JIDs for the same contact share the same guard key.

    Resolution order:
      1. Strip @domain to get the bare identifier.
      2. If it looks like a LID (no leading 65/8/9, all digits, >11 chars),
         resolve to phone via whatsmeow_lid_map.
      3. Strip leading + and whitespace from whatever remains.

    Examples:
      "100000000000000@lid"      → "6500000000"  (LID resolved to phone)
      "6500000000@s.whatsapp.net" → "6500000000"
      "85446246"                → "85446246"    (8-digit, left as-is)
    """
    _load_lid_map()
    bare = chat_jid.split("@")[0].replace("+", "").replace(" ", "").replace("-", "")
    # If bare is a LID (long numeric, not a SG phone number), resolve to phone
    if bare.isdigit() and len(bare) > 11 and bare in _lid_to_phone:
        bare = _lid_to_phone[bare]
    return bare


def _load(fh) -> dict:
    try:
        fh.seek(0)
        return json.load(fh)
    except (json.JSONDecodeError, ValueError):
        return {}


def _save(fh, data: dict):
    fh.seek(0)
    fh.truncate()
    json.dump(data, fh, indent=2)
    fh.flush()


def _all_keys(chat_jid: str) -> list:
    """
    Return every key format this JID might be stored under — canonical first,
    then all legacy forms (bare LID, full @lid JID, and the reverse mapping).
    Used by can_send to catch entries written by older code paths regardless of
    whether they were keyed by phone, LID, or raw JID.
    """
    _load_lid_map()
    canonical = _normalize(chat_jid)
    seen = [canonical]

    bare = chat_jid.split("@")[0].replace("+", "").replace(" ", "").replace("-", "")

    # If we resolved a LID to a phone (canonical != bare), also check the bare LID
    # and the full @lid JID — old code wrote those directly.
    if bare != canonical and bare.isdigit() and len(bare) > 11:
        if bare not in seen:
            seen.append(bare)
        lid_jid = f"{bare}@lid"
        if lid_jid not in seen:
            seen.append(lid_jid)

    # If canonical is a phone number, also check the corresponding LID forms
    if canonical in _phone_to_lid:
        lid = _phone_to_lid[canonical]
        if lid not in seen:
            seen.append(lid)
        lid_jid = f"{lid}@lid"
        if lid_jid not in seen:
            seen.append(lid_jid)

    # Always include the raw input as a final fallback
    if chat_jid not in seen:
        seen.append(chat_jid)

    return seen


def _check_entry(entry) -> bool:
    """Return True (blocked) if this log entry is within the cooldown window."""
    if not isinstance(entry, dict):
        # Legacy plain-timestamp format: {"jid": "2026-06-03T..."}
        try:
            last_sent = datetime.fromisoformat(str(entry))
        except Exception:
            return False
    else:
        last_sent_str = entry.get("last_sent", "")
        if not last_sent_str:
            return False
        try:
            last_sent = datetime.fromisoformat(last_sent_str)
        except Exception:
            return False
    if last_sent.tzinfo is None:
        last_sent = last_sent.replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=COOLDOWN_MINUTES)
    return last_sent > cutoff


def can_send(chat_jid: str) -> bool:
    """
    Returns True if it is safe to send to this JID right now.
    Returns False if another script sent to this JID within the last COOLDOWN_MINUTES.
    Checks all legacy key formats so stale entries written by old code are still honoured.
    """
    os.makedirs(os.path.dirname(SENT_LOG), exist_ok=True)
    lock_fh = open(LOCK_FILE, "a+")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        if not os.path.exists(SENT_LOG):
            return True
        with open(SENT_LOG) as sf:
            try:
                data = json.load(sf)
            except (json.JSONDecodeError, ValueError):
                return True
        for key in _all_keys(chat_jid):
            entry = data.get(key)
            if entry and _check_entry(entry):
                return False  # blocked — found a recent send under any key format
        return True
    finally:
        fcntl.flock(lock_fh, fcntl.LOCK_UN)
        lock_fh.close()


def reserve(chat_jid: str, source: str = "unknown") -> bool:
    """
    Atomically check-and-reserve: if safe to send, mark it immediately under the SAME lock.
    Returns True if reserved (caller should send), False if blocked by cooldown.

    Use this instead of separate can_send() + mark_sent() calls to eliminate the
    race condition where two concurrent processes both pass can_send() before either
    calls mark_sent().
    """
    canonical = _normalize(chat_jid)
    os.makedirs(os.path.dirname(SENT_LOG), exist_ok=True)
    lock_fh = open(LOCK_FILE, "a+")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        # Load current state
        data = {}
        if os.path.exists(SENT_LOG):
            try:
                with open(SENT_LOG) as sf:
                    data = json.load(sf)
            except (json.JSONDecodeError, ValueError):
                data = {}
        # Check all key formats. A recent send by a DIFFERENT source blocks (cross-sender
        # dedup). A recent send by the SAME source does NOT block — that sender's own
        # per-conversation latches govern its sequencing (intake engine: send the form, then a
        # nudge / ask / viewing offer once the prospect replies). Without this exemption the
        # engine blocks its OWN follow-ups for the whole cooldown and silently drops them.
        for key in _all_keys(chat_jid):
            entry = data.get(key)
            if entry and _check_entry(entry):
                if isinstance(entry, dict) and entry.get("source") == source:
                    continue  # same sender — allow (its own latches prevent spam)
                return False  # different sender within cooldown — block
        # Safe — mark immediately while still holding the lock
        for legacy_key in _all_keys(chat_jid):
            if legacy_key != canonical and legacy_key in data:
                del data[legacy_key]
        entry = data.get(canonical, {"count": 0})
        entry["last_sent"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        entry["source"]    = source
        entry["count"]     = entry.get("count", 0) + 1
        data[canonical]    = entry
        mode = "r+" if os.path.exists(SENT_LOG) else "w+"
        with open(SENT_LOG, mode) as fh:
            fh.seek(0); fh.truncate()
            json.dump(data, fh, indent=2)
        return True
    finally:
        fcntl.flock(lock_fh, fcntl.LOCK_UN)
        lock_fh.close()


def mark_sent(chat_jid: str, source: str = "unknown"):
    """
    Record that a message was sent to this JID right now.
    Writes under the canonical key and removes any legacy-format duplicates
    for the same contact so future lookups stay clean.
    """
    canonical = _normalize(chat_jid)
    os.makedirs(os.path.dirname(SENT_LOG), exist_ok=True)

    mode = "r+" if os.path.exists(SENT_LOG) else "w+"
    with open(SENT_LOG, mode) as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            data = _load(fh)
            # Remove stale legacy-format entries for this same contact
            for legacy_key in _all_keys(chat_jid):
                if legacy_key != canonical and legacy_key in data:
                    del data[legacy_key]
            entry = data.get(canonical, {"count": 0})
            entry["last_sent"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
            entry["source"]    = source
            entry["count"]     = entry.get("count", 0) + 1
            data[canonical]    = entry
            _save(fh, data)
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def was_sent_by(chat_jid: str) -> str:
    """Returns the source that last sent to this JID, or empty string."""
    if not os.path.exists(SENT_LOG):
        return ""
    key = _normalize(chat_jid)
    with open(SENT_LOG) as fh:
        data = json.load(fh)
    return data.get(key, {}).get("source", "")


def clear_jid(chat_jid: str):
    """Remove a JID from the sent log (e.g. to allow re-sending after a long gap)."""
    if not os.path.exists(SENT_LOG):
        return
    key = _normalize(chat_jid)
    with open(SENT_LOG, "r+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            data = _load(fh)
            data.pop(key, None)
            _save(fh, data)
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


# ── CLI interface (for shell scripts) ────────────────────────────────────────
# Usage from bash:
#   python3 wa_send_guard.py can_send  <jid>          → exits 0 if ok, 1 if blocked
#   python3 wa_send_guard.py mark_sent <jid> <source> → records the send
#   python3 wa_send_guard.py status    <jid>          → prints last sender

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: wa_send_guard.py <can_send|mark_sent|status> <jid> [source]")
        sys.exit(2)

    cmd = sys.argv[1]
    jid = sys.argv[2]

    if cmd == "can_send":
        if can_send(jid):
            print(f"OK: {jid} — safe to send")
            sys.exit(0)
        else:
            src = was_sent_by(jid)
            print(f"BLOCKED: {jid} — already sent by {src} within {COOLDOWN_MINUTES} min")
            sys.exit(1)

    elif cmd == "mark_sent":
        source = sys.argv[3] if len(sys.argv) > 3 else "unknown"
        mark_sent(jid, source)
        print(f"Logged: {jid} sent by {source}")
        sys.exit(0)

    elif cmd == "reserve":
        # atomic check-and-mark: exit 0 = reserved (caller should send), 1 = blocked
        source = sys.argv[3] if len(sys.argv) > 3 else "unknown"
        if reserve(jid, source):
            print(f"RESERVED: {jid} for {source}")
            sys.exit(0)
        else:
            print(f"BLOCKED: {jid} — already reserved within {COOLDOWN_MINUTES} min")
            sys.exit(1)

    elif cmd == "status":
        src = was_sent_by(jid)
        print(f"Last sender for {jid}: {src or 'none'}")
        sys.exit(0)

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(2)
