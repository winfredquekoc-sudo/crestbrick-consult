#!/usr/bin/env python3
"""
Post-processor: reads Claude's JSON decisions, sends WA messages, writes all state.
This is the ONLY place state files are written. Claude never writes state directly.
"""
import json, os, sys, re, requests
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wa_send_guard import mark_sent as guard_mark_sent, reserve as guard_reserve

STATE_DIR  = os.path.expanduser("~/.claude/state/listing-templates")
CONV_STATE = os.path.join(STATE_DIR, "conversation-state.json")
REPLIED_LOG= os.path.join(STATE_DIR, "replied-jids.json")
CAROUSELL  = os.path.expanduser("~/.claude/state/wa-agent/carousell-landlord-jids.json")
WA_API     = "http://127.0.0.1:8080/api/send"
BOT_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID    = os.environ.get("TELEGRAM_WINFRED_CHAT_ID", "")
SGT        = timezone(timedelta(hours=8))

def load_json(path, default):
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding="utf-8"))
        except:
            pass
    return default

def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def send_wa(jid, message):
    try:
        r = requests.post(WA_API, json={"recipient": jid, "message": message}, timeout=15)
        return r.status_code in (200, 201)
    except Exception as e:
        print(f"[WA ERROR] {jid}: {e}", file=sys.stderr)
        return False

def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text},
            timeout=10
        )
    except:
        pass

def extract_json(raw):
    """Extract JSON from Claude output even if it has surrounding text."""
    # Try direct parse first
    try:
        return json.loads(raw)
    except:
        pass
    # Try to find JSON block
    match = re.search(r'\{[\s\S]*"actions"[\s\S]*\}', raw)
    if match:
        try:
            return json.loads(match.group(0))
        except:
            pass
    return None

def main():
    raw = sys.stdin.read().strip()
    data = extract_json(raw)

    if not data:
        print(f"[POST-PROCESSOR] Could not parse Claude output", file=sys.stderr)
        print(f"Raw (first 500):\n{raw[:500]}", file=sys.stderr)
        print("NO ACTIVITY")
        return

    actions = data.get("actions", [])
    if not actions:
        print("NO ACTIVITY")
        return

    now_iso = datetime.now(SGT).isoformat()

    conv_state    = load_json(CONV_STATE, {"conversations": {}})
    conversations = conv_state.get("conversations", {})
    replied_data  = load_json(REPLIED_LOG, {"replied": []})
    replied_list  = replied_data.get("replied", [])
    replied_set   = set(replied_list)
    carousell_list = load_json(CAROUSELL, [])
    carousell_set  = set(carousell_list)

    sent_this_run = set()
    logs = []

    PROFILE_CORE = {"name", "nationality", "gender", "age", "pass_type", "pax"}

    def profile_complete(profile: dict) -> bool:
        """True if the stored profile has at least 3 core fields filled."""
        if not profile:
            return False
        filled = sum(1 for f in PROFILE_CORE if profile.get(f))
        return filled >= 3

    def auto_fix_stage(jid: str):
        """
        If a JID has a complete profile but stage is still template_sent,
        promote it to profile_received so Claude stops re-sending the form.
        """
        entry = conversations.get(jid, {})
        if entry.get("stage") == "template_sent" and profile_complete(entry.get("profile") or {}):
            conversations[jid]["stage"] = "profile_received"

    for action in actions:
        jid = action.get("jid","").strip()
        if not jid or jid in sent_this_run:
            continue

        # Auto-fix any stuck template_sent stage before evaluating
        auto_fix_stage(jid)

        message = (action.get("message") or "").strip()
        atype   = action.get("type","")
        log_line = action.get("log", f"{atype}: {jid}")

        if not message:
            continue

        # Block template resend if prospect already has a complete profile
        if atype in ("send_tenant_template", "send_budget_check", "send_fallback"):
            existing = conversations.get(jid, {})
            if profile_complete(existing.get("profile") or {}):
                print(f"[BLOCKED] {jid} — profile already complete, refusing to resend template (stage={existing.get('stage')})", file=sys.stderr)
                continue

        # Atomically reserve send slot — blocks concurrent script instances from double-sending
        if not guard_reserve(jid, source="whatsapp-autoreply"):
            print(f"[RACE BLOCKED] {jid} — another process already reserved this JID", file=sys.stderr)
            continue

        # Send via WA bridge
        ok = send_wa(jid, message)
        if not ok:
            print(f"[FAILED TO SEND] {jid}", file=sys.stderr)
            continue

        sent_this_run.add(jid)
        logs.append(log_line)
        print(log_line)

        new_stage = action.get("stage")
        listing   = action.get("listing")
        profile   = action.get("profile") or {}
        hot_lead  = action.get("hot_lead", False)

        # ── State updates ────────────────────────────────────────────────
        if atype in ("send_tenant_template", "send_budget_check", "send_fallback"):
            if jid not in replied_set:
                replied_list.append(jid)
                replied_set.add(jid)
            conversations[jid] = {
                "stage":         new_stage or "template_sent",
                "listing":       listing,
                "last_bot_reply": now_iso,
                "profile":       profile if profile else None,
                "hot_lead":      hot_lead,
                "offered_slots": None,
                "requested_slot": None,
                "reminders_sent": {"24h": False, "2h": False},
                "enthusiasm":    1 if hot_lead else 0,
            }

        elif atype == "send_reply":
            existing = conversations.get(jid, {})
            # Merge profile fields — never overwrite with null
            merged_profile = existing.get("profile") or {}
            if profile:
                merged_profile.update({k: v for k, v in profile.items() if v is not None})
            existing.update({
                "last_bot_reply": now_iso,
                "profile":        merged_profile or None,
            })
            if new_stage:
                existing["stage"] = new_stage
            if hot_lead:
                existing["hot_lead"] = True
            if action.get("offered_slots"):
                existing["offered_slots"] = action["offered_slots"]
            if action.get("requested_slot"):
                existing["requested_slot"] = action["requested_slot"]
            if action.get("enthusiasm") is not None:
                existing["enthusiasm"] = action["enthusiasm"]
            conversations[jid] = existing

        elif atype == "send_reminder":
            rtype = action.get("reminder_type")
            if jid in conversations and rtype:
                conversations[jid]["last_bot_reply"] = now_iso
                rs = conversations[jid].get("reminders_sent", {"24h": False, "2h": False})
                rs[rtype] = True
                conversations[jid]["reminders_sent"] = rs

        elif atype == "send_seller_intake":
            if jid not in replied_set:
                replied_list.append(jid)
                replied_set.add(jid)
            conversations[jid] = {
                "stage":          "seller_intake_sent",
                "listing":        None,
                "last_bot_reply": now_iso,
                "profile":        None,
                "hot_lead":       False,
                "offered_slots":  None,
                "requested_slot": None,
                "reminders_sent": {"24h": False, "2h": False},
                "enthusiasm":     0,
            }
            # Alert disabled — only viewing confirmations matter
            pass

        elif atype == "send_buyer_form":
            if jid not in replied_set:
                replied_list.append(jid)
                replied_set.add(jid)
            conversations[jid] = {
                "stage":          "buyer_form_sent",
                "listing":        action.get("listing"),
                "last_bot_reply": now_iso,
                "profile":        None,
                "hot_lead":       False,
                "offered_slots":  None,
                "requested_slot": None,
                "reminders_sent": {"24h": False, "2h": False},
                "enthusiasm":     0,
            }
            # Alert disabled — only viewing confirmations matter
            pass

        elif atype == "landlord_reply":
            if action.get("add_to_blocklist") and jid not in carousell_set:
                carousell_list.append(jid)
                carousell_set.add(jid)
                save_json(CAROUSELL, carousell_list)
            # Write to conversation-state so Phase A/A0 skips this JID as "already engaged"
            if jid not in conversations:
                conversations[jid] = {
                    "stage":         "landlord_engaged",
                    "listing":       action.get("listing"),
                    "last_bot_reply": now_iso,
                    "profile":       None,
                    "hot_lead":      False,
                    "offered_slots": None,
                    "requested_slot": None,
                    "reminders_sent": {"24h": False, "2h": False},
                    "enthusiasm":    0,
                }
            else:
                conversations[jid]["last_bot_reply"] = now_iso
                conversations[jid]["stage"] = "landlord_engaged"

        # ── Telegram alerts ──────────────────────────────────────────────
        if "VIEWING REQUESTED" in log_line:
            # Build cluster summary
            cluster = {}
            for cjid, c in conversations.items():
                if c.get("stage") in ("viewing_requested","viewing_confirmed"):
                    s = c.get("requested_slot")
                    if s:
                        raw_slot = s.get("raw", str(s)) if isinstance(s, dict) else str(s)
                        lst = c.get("listing","?")
                        cluster.setdefault(lst, []).append(raw_slot)
            cluster_text = ""
            for lst, slots in cluster.items():
                cluster_text += f"\n{lst}: {len(slots)} booked: {', '.join(slots)}"
            msg = f"📅 Viewing request\n{log_line}"
            if cluster_text:
                msg += f"\n\n👥 Cluster{cluster_text}"
            msg += "\n\nConfirm with prospect and update stage to viewing_confirmed."
            send_telegram(msg)

        elif "LANDLORD LEAD" in log_line:
            # Alert disabled — only viewing confirmations matter
            pass
        elif log_line.startswith("HOT:"):
            send_telegram(f"🔥 Wants to proceed\n{log_line}\n\nFollow up now to close.")
        elif "REMINDER CANCELLED" in log_line:
            send_telegram(f"❌ Viewing cancelled\n{log_line}\n\nBot reset stage. Reschedule the slot.")
        elif log_line.startswith("NO-SHOW:"):
            send_telegram(f"👻 No-show\n{log_line}\n\nSlot is free. Offer to next prospect.")
        elif "STUCK:" in log_line:
            send_telegram(f"⚠️ Stuck conversation\n{log_line}")

    # ── Save all state ───────────────────────────────────────────────────
    conv_state["conversations"] = conversations
    save_json(CONV_STATE, conv_state)
    replied_data["replied"] = replied_list
    save_json(REPLIED_LOG, replied_data)

    if not logs:
        print("NO ACTIVITY")

if __name__ == "__main__":
    main()
