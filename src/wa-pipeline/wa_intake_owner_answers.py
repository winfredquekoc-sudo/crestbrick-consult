"""
wa_intake_owner_answers.py -- closes the loop: reads a landlord's reply to an owner
question (wa_intake_owner.py), extracts a fact via a Haiku subprocess call (kept OUT of
intake_engine.py -- "keep the engine pure", Winfred's own framing), records it under the
same lock protocols the rest of the pipeline already uses, and hands the waiting tenant a
ready draft through the existing drafts.jsonl + /send rig (wa_intake_resume/selfchat).

Split from wa_intake_owner.py purely to keep both files under the repo's 500 line
guideline; imported by wa_intake_runner.py the same way wa_intake_resume is.
"""
import os, re, json, time, datetime, subprocess

import wa_intake_owner as OWN
import wa_intake_resume as RES
import wa_intake_paths as _P
import wa_intake_draft_worker as WORKER

# STEP 0 sandbox seal (9 Sep 2026 merge redo): IDX kept for backward compat with existing
# mock.patch.object(wa_intake_owner_answers, "IDX", ...) tests; _idx() resolves it at call
# time (see wa_intake_paths.resolved's docstring).
IDX = _P.paths()["listing_index"]
_default_IDX = IDX


def _idx():
    return _P.resolved(globals(), "IDX", "listing_index")


HAIKU_BIN = os.path.expanduser("~/.claude/bin/claude-guard")
HAIKU_MODEL = "claude-haiku-4-5-20251001"
HAIKU_MCP_CONFIG = os.path.expanduser("~/.claude/mcp-configs/none.json")
HAIKU_TIMEOUT_SEC = 25

# facts-sheet vs requirements vs top-level: mirrors intake_engine's own field homes so a
# recorded answer is immediately usable by the existing tenant fact-answer path (never
# re-implemented here -- intake_engine.py is untouched by this branch).
_CODE_TO_FACTS_KEY = {"WIFI": "wifi", "AIRCON": "aircon", "UTILITIES": "utilities",
                      "MRT": "mrt", "AVAILABILITY": "available", "MOVE_IN": "available",
                      "PAX": "pax", "VISITORS": "visitors", "HOUSE_RULES": "house_rules"}
_CODE_TO_REQ_KEY = {"COOKING": "cooking", "SMOKING": "smoking",
                    "PETS": "pets_tenant_may_bring"}


# ---------- viewing window: unambiguous only if exactly ONE weekday and both a start AND
# an end time each carrying an explicit am/pm. Anything else (no day, two+ days, a generic
# "weekday"/"weekend", a missing am/pm) is free text for Winfred to confirm by hand -- the
# listing registry's fixed_viewing field only ever holds ONE weekday + one time window
# (intake_engine._fixed_viewing_slot), so a genuinely mixed answer like "weekday evenings,
# Sat 10 to 12" cannot be represented there without guessing which half Winfred meant. ----------
_WD_RE = re.compile(r"\b(mon|tue|wed|thu|fri|sat|sun)[a-z]*\b", re.I)
_TIME_RANGE_RE = re.compile(
    r"(\d{1,2}(?:[:.]\d{2})?)\s*(am|pm)\s*(?:to|-|–|until|till)\s*"
    r"(\d{1,2}(?:[:.]\d{2})?)\s*(am|pm)", re.I)
_WD_FULL = {"mon": "mon", "tue": "tue", "wed": "wed", "thu": "thu", "fri": "fri",
            "sat": "sat", "sun": "sun"}


def parse_viewing_window(text):
    """Returns (fixed_viewing_dict, None) when unambiguous, or (None, text) when it must be
    stored as free text and flagged. Never raises on garbage input."""
    t = (text or "").strip()
    if not t:
        return None, t
    days = {_WD_FULL[m.group(1).lower()[:3]] for m in _WD_RE.finditer(t)
            if m.group(1).lower()[:3] in _WD_FULL}
    if len(days) != 1:
        return None, t                 # zero or multiple distinct weekdays -> ambiguous
    m = _TIME_RANGE_RE.search(t)
    if not m:
        return None, t                 # no clean "Xam to Ypm" range -> ambiguous
    sh, sap, eh, eap = m.groups()

    def _to_24h(hm, ap):
        hm = hm.replace(".", ":")
        if ":" in hm:
            h, mi = hm.split(":")
        else:
            h, mi = hm, "00"
        h = int(h) % 12
        if ap.lower() == "pm":
            h += 12
        return f"{h:02d}:{mi}"

    start = _to_24h(sh, sap)
    end = _to_24h(eh, eap)
    day = next(iter(days))
    label = f"{sh}{sap} to {eh}{eap}"
    return {"weekday": day, "start": start, "end": end, "time_label": label}, None


# ---------- listing index write (under the engine's own flock, same style as
# intake_engine.book_slot -- read, mutate in place, seek(0)+truncate, unlock) ----------
def _write_listing_fact(listing_key, code, value):
    import fcntl
    if not listing_key:
        return False
    try:
        f = open(_idx(), "r+")
    except OSError:
        return False
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            data = json.load(f)
        except (ValueError, OSError):
            return False
        entries = data.get("listings") or []
        hit = None
        for e in entries:
            if e.get("listing_key") == listing_key:
                hit = e
                break
        if hit is None:
            return False
        if code == "VIEWING_WINDOW":
            fv, free_text = parse_viewing_window(value)
            if fv:
                hit["fixed_viewing"] = fv
            else:
                (hit.setdefault("facts", {}))["viewing_window_note"] = free_text
        elif code in _CODE_TO_REQ_KEY:
            (hit.setdefault("requirements", {}))[_CODE_TO_REQ_KEY[code]] = value
        elif code in _CODE_TO_FACTS_KEY:
            (hit.setdefault("facts", {}))[_CODE_TO_FACTS_KEY[code]] = value
        else:
            return False
        f.seek(0)
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.truncate()
        return True
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


# ---------- landlord-db write (mkdir based lock -- macOS has no flock CLI convention here,
# refresh-rental-dbs.sh already uses this exact protocol so the nightly refresh and this
# module can never race each other) ----------
def _acquire_refresh_lock(timeout_sec=10):
    lock_dir = OWN._refresh_lock_dir()
    deadline = time.time() + timeout_sec
    while True:
        try:
            if (os.path.isdir(lock_dir)
                    and time.time() - os.path.getmtime(lock_dir) > OWN.REFRESH_LOCK_STALE_SEC):
                os.rmdir(lock_dir)          # stale lock from a crashed holder -- clear it
        except OSError:
            pass
        try:
            os.mkdir(lock_dir)
            return True
        except FileExistsError:
            if time.time() > deadline:
                return False
            time.sleep(0.2)
        except OSError:
            return False


def _release_refresh_lock():
    try:
        os.rmdir(OWN._refresh_lock_dir())
    except OSError:
        pass


def _write_landlord_evidence(landlord_id, code, quote):
    if not _acquire_refresh_lock():
        return False
    try:
        try:
            d = json.load(open(OWN.LANDLORD_DB))
        except Exception:
            return False
        hit = None
        for l in d.get("landlords", []):
            if str(l.get("id")) == str(landlord_id):
                hit = l
                break
        if hit is None:
            return False
        hit.setdefault("wa_evidence", []).append(
            {"code": code, "quote": quote, "date": OWN._today_sgt_str()})
        tmp = OWN.LANDLORD_DB + ".tmp"
        json.dump(d, open(tmp, "w"), indent=1, ensure_ascii=False)
        os.replace(tmp, OWN.LANDLORD_DB)
        return True
    finally:
        _release_refresh_lock()


# ---------- Haiku extraction ----------
def _build_extract_prompt(pending, transcript):
    lines = "\n".join(f"- {q['question_code']}: {q['question_text']}" for q in pending)
    convo = "\n".join(f"{'ME' if m['ifm'] else 'LANDLORD'}: {m['text']}" for m in transcript)
    codes = ", ".join(q["question_code"] for q in pending)
    return (
        "A Singapore property agent (Winfred) asked his landlord a consolidated WhatsApp "
        "message covering these questions:\n" + lines + "\n\n"
        "Here is the landlord's reply thread since then (oldest first):\n" + convo + "\n\n"
        "For EACH question code above, decide if the landlord's reply answers it clearly. "
        "Reply with ONLY a JSON object, one key per code from this exact set: " + codes + ". "
        "Each value is either null (no clear answer yet) or an object "
        '{"value": <short normalized answer>, "quote": <the exact landlord words>}. '
        "Normalize COOKING to one of none/light/all. Normalize SMOKING to one of no/any. "
        "Normalize PETS to true or false. For every other code, value is a short factual "
        "phrase drawn only from the landlord's own words, never invented. No prose outside "
        "the JSON object.")


def call_haiku_extract(pending, transcript):
    """Returns (dict-or-None, error-or-None). dict maps question_code -> {"value","quote"}
    or None, for exactly the codes in `pending`. Never raises.

    WA_INTAKE_SANDBOX=1 short circuits before the real subprocess call (STEP 0 sandbox seal,
    9 Sep 2026 merge redo): a harness/replay run with a pending owner answer to extract would
    otherwise spawn a real claude-guard/Haiku process -- costs real API tokens and is exactly
    the kind of live engine I/O a sandboxed run must never perform on its own. A harness that
    wants real extraction behaviour patches this function directly (see
    wa_intake_attack_harness.py's RES.call_haiku patch for the matching draft-generation
    case), same as it already does for Telegram/bridge sends."""
    if os.environ.get("WA_INTAKE_SANDBOX") == "1":
        return None, "sandboxed: real Haiku subprocess call suppressed"
    prompt = _build_extract_prompt(pending, transcript)
    try:
        r = subprocess.run(
            [HAIKU_BIN, "-p", prompt, "--model", HAIKU_MODEL, "--strict-mcp-config",
             "--mcp-config", HAIKU_MCP_CONFIG, "--output-format", "json"],
            capture_output=True, text=True, timeout=HAIKU_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"
    if r.returncode != 0:
        return None, f"exit {r.returncode}: {(r.stderr or '')[:200]}"
    try:
        outer = json.loads(r.stdout)
    except Exception:
        return None, "unparseable outer json"
    if outer.get("is_error"):
        return None, "is_error: " + str(outer.get("result"))[:200]
    raw = (outer.get("result") or "").strip()
    return _extract_json_object(raw)


def _extract_json_object(raw):
    """The claude-guard result TEXT (already pulled out of the outer claude-guard JSON
    envelope) still has one JSON object embedded in it, not always the whole string --
    shared by the synchronous call_haiku_extract above and finish_owner_extract's
    background-worker path below, so the two never drift on how they read a result."""
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None, "no json object in result"
    try:
        return json.loads(m.group(0)), None
    except Exception:
        return None, "unparseable inner json"


def _fetch_landlord_transcript(con, jid, since_iso, limit=200):
    """Every message (both directions) in this chat since `since_iso`, oldest first. Filters
    in PYTHON on a timezone aware parse (RES._parse_ts), never a raw SQL string comparison
    on the timestamp column -- the bridge mixes +08:00/-04:00 offsets (Winfred travels), so
    'timestamp > ?' as a plain string compare can put a genuinely LATER row before an
    earlier watermark and silently miss the landlord's reply."""
    out = []
    if con is None or not jid:
        return out
    try:
        rows = con.execute(
            "SELECT is_from_me, content, timestamp FROM messages WHERE chat_jid=? "
            "ORDER BY rowid ASC", (jid,)          # rowid, not timestamp -- see docstring
        ).fetchall()
    except Exception:
        return out
    since_dt = RES._parse_ts(since_iso)
    for ifm, content, ts in rows:
        if not (content and content.strip()):
            continue
        dt = RES._parse_ts(ts)
        if since_dt is not None and dt is not None and dt <= since_dt:
            continue
        out.append({"ifm": bool(ifm), "text": content.strip(), "ts": ts})
        if len(out) >= limit:
            break
    return out


def _fact_sentence(code, value):
    """A short, plain, no-hyphen sentence for the tenant, drawn only from a recorded fact."""
    if code == "COOKING":
        return {"none": "no cooking is allowed in the unit", "light": "light cooking only is allowed",
                "all": "cooking is allowed in the unit"}.get(str(value).lower(), None)
    if code == "SMOKING":
        return {"no": "no smoking is allowed at the unit", "any": "smoking is fine at the unit"
                }.get(str(value).lower(), None)
    if code == "PETS":
        truthy = value is True or str(value).lower() in ("true", "yes")
        return "you can bring your pet" if truthy else "sorry, no pets allowed for this unit"
    if code in ("AVAILABILITY", "MOVE_IN"):
        return f"the unit is available from {value}"
    if code == "WIFI":
        return f"on wifi: {value}"
    if code == "AIRCON":
        return f"on aircon: {value}"
    if code == "UTILITIES":
        return f"on utilities: {value}"
    if code == "MRT":
        return f"on the nearest MRT: {value}"
    if code == "VISITORS":
        return f"on visitors: {value}"
    if code == "HOUSE_RULES":
        return f"on house rules: {value}"
    if code == "PAX":
        return f"on pax: {value}"
    return None


def spawn_owner_answer_extracts(con, log_fn):
    """Called once per runner tick. For every landlord with a 'sent'/'chased' question who
    has replied since the ask, spawns a BACKGROUND claude-guard extract request and returns
    immediately -- never blocks the tick (9 Sep 2026 merge redo, item 3: the old cut of this
    function called call_haiku_extract synchronously, holding up every OTHER landlord and
    every tenant draft in the same tick for up to 25 seconds). The actual fact recording /
    tenant follow up drafting happens later, in finish_owner_extract(), called from
    wa_intake_draft_worker.sweep() once the background job finishes (or times out). Never
    raises."""
    items = OWN._load_queue()
    pending_by_landlord = {}
    for q in items:
        if q.get("status") in ("sent", "chased") and q.get("asked_at"):
            pending_by_landlord.setdefault(q["landlord_id"], []).append(q)
    if not pending_by_landlord:
        return
    db = OWN._load_landlord_db()
    if db is None:
        return
    for lid, qs in pending_by_landlord.items():
        l = OWN.landlord_by_id(lid, db)
        jid = OWN._landlord_jid(l) if l else None
        if not jid:
            continue
        since = min(q["asked_at"] for q in qs)
        transcript = _fetch_landlord_transcript(con, jid, since)
        if not any(m["ifm"] is False for m in transcript):
            continue                      # no reply yet, nothing to extract
        prompt = _build_extract_prompt(qs, transcript)
        context = {"lid": lid, "qs": qs, "landlord_name": l.get("landlord_name"),
                  "landlord_phone": l.get("phone")}
        status = WORKER.spawn_request("owner_extract", lid, jid, prompt, context)
        if status == "spawned":
            log_fn("OWNER_EXTRACT_SPAWNED", lid, f"background extract requested ({len(qs)} question(s))")
        elif status == "in_flight":
            log_fn("OWNER_EXTRACT_IN_FLIGHT", lid, "already has an unresolved extract request")
        elif status == "cap_reached":
            log_fn("OWNER_EXTRACT_CAP", lid, "tick spawn cap reached, will retry next tick")
        elif status == "spawn_error":
            log_fn("OWNER_EXTRACT_SPAWN_ERROR", lid, "failed to start the background extract worker")
        # "sandboxed" -- silent and expected under WA_INTAKE_SANDBOX=1 (see spawn_request).


def finish_owner_extract(record, text, err, notify_fn, log_fn, timed_out=False):
    """The on_result/on_timeout handler wa_intake_runner.run() wires into
    wa_intake_draft_worker.sweep() for kind='owner_extract'. Same per-code processing
    run_owner_answer_capture used to run inline right after call_haiku_extract returned.
    Never raises."""
    ctx = record.get("context") or {}
    lid = ctx.get("lid")
    qs = ctx.get("qs") or []
    landlord_name = ctx.get("landlord_name")
    jid = record.get("jid")
    if timed_out or err:
        # Owner extracts are never tenant time critical the way a resume draft is -- the
        # SAME pending question is simply picked up again the next time this landlord's
        # chat is checked for a reply (spawn_owner_answer_extracts re-spawns for any
        # 'sent'/'chased' question, unaffected by one timed out attempt), and Winfred
        # already has the landlord's raw reply in his own WhatsApp regardless. No
        # FLAG_HUMAN carve out here (contrast wa_intake_resume.finish_resume_draft, which
        # DOES escalate on timeout when the ORIGINAL tenant message demanded an answer).
        log_fn("DRAFT_TIMEOUT", lid, "wall budget exceeded" if timed_out else str(err))
        return
    parsed, perr = _extract_json_object(text)
    if perr:
        log_fn("OWNER_ANSWER_EXTRACT_FAIL", lid, perr)
        return
    for q in qs:
        code = q["question_code"]
        got = parsed.get(code) if isinstance(parsed, dict) else None
        if not isinstance(got, dict) or got.get("value") in (None, ""):
            OWN.mark_question(q["id"], "drafted")
            did = RES.new_draft(
                ctx.get("landlord_phone"), jid, q.get("listing_key"),
                f"Sorry to double check, {q['question_text'].rstrip('?')}?")
            notify_fn(f"{landlord_name or lid}'s reply did not clearly answer "
                      f"'{q['question_text']}'. Draft clarifying follow up ready: /send {did}")
            continue
        value, quote = got.get("value"), got.get("quote") or ""
        _write_landlord_evidence(lid, code, quote)
        _write_listing_fact(q.get("listing_key"), code, value)
        OWN.mark_question(q["id"], "answered", answer=value, evidence=quote)
        notify_fn(f"{landlord_name or lid} answered {code}: {value} "
                  f"(\"{quote[:100]}\")")
        sentence = _fact_sentence(code, value)
        source_pn = q.get("source")
        if sentence and source_pn and source_pn != "clarity-report":
            tenant_text = f"Just heard back from the owner, {sentence} \U0001F642"
            bad = RES.validate_draft(tenant_text)
            if not bad:
                tenant_jid = q.get("source_jid") or f"{source_pn}@s.whatsapp.net"
                did = RES.new_draft(source_pn, tenant_jid, q.get("listing_key"), tenant_text)
                notify_fn(f"Tenant follow up ready for {source_pn}: /send {did}")
            else:
                notify_fn(f"Owner answered for {source_pn} but the drafted follow up "
                          f"failed its own check ({bad}); reply by hand: {sentence}")


def sweep_all_drafts(notify_fn, log_fn):
    """One call, once per tick, collects BOTH kinds of background worker request -- resume
    drafts (wa_intake_resume.py) and owner extracts (this module). Kept here rather than in
    wa_intake_runner.py purely to keep that file under the repo's 500 line guideline; this
    module already imports both RES and WORKER, so it is the natural place for the one glue
    call that wires their finish functions into wa_intake_draft_worker.sweep()."""
    WORKER.sweep({
        "resume_draft": {
            "on_result": lambda rec, text, err: RES.finish_resume_draft(
                rec, text, err, notify_fn, log_fn),
            "on_timeout": lambda rec: RES.finish_resume_draft(
                rec, None, "timeout", notify_fn, log_fn, timed_out=True),
        },
        "owner_extract": {
            "on_result": lambda rec, text, err: finish_owner_extract(
                rec, text, err, notify_fn, log_fn),
            "on_timeout": lambda rec: finish_owner_extract(
                rec, None, "timeout", notify_fn, log_fn, timed_out=True),
        },
    })
