"""
wa_intake_paths.py -- single source of truth for every filesystem path the wa-intake engine
family opens, and the sandbox seal built on top of it (STEP 0, 9 Sep 2026 merge redo).

WHY THIS EXISTS
    Two sandbox leak incidents. (1) a harness's mock.patch only covered its own module's
    imported name binding, not the module a function is actually DEFINED in, so a real
    Telegram/bridge send fired with synthetic scenario numbers -- fixed by choke-point kill
    switches in wa_intake_notify._tg_send / wa_intake_send._send. (2) the SAME shape of bug,
    unfixed: wa_intake_attack_harness.py does mock.patch.object(R, "LASTF", tmp) (R = the
    wa_intake_runner module), but wa_intake_send._write_last() -- the function that actually
    performs the write -- is DEFINED in wa_intake_send.py and reads the bare name LASTF via
    ITS OWN module globals, which the harness never touched. Every harness run was silently
    writing rowid 1 into the LIVE runner-last.json (328,000-message rescan) and, by the same
    mechanism elsewhere, synthetic records into the LIVE intake-state.json.

    A per-call-site mock.patch is easy to aim at the wrong module and easy to forget on a
    new call site. This module removes the aiming problem: three environment variables
    redirect EVERY path the engine family touches, read fresh on every call, so a script
    that merely sets them before doing anything else is sandboxed regardless of which
    module a given function happens to be defined in.

ENV VARS (all optional; unset = identical to the historical hardcoded paths -- production
is unchanged)
    WA_INTAKE_STATE_ROOT   replaces ~/.claude/state/listing-templates
    WA_INTAKE_DATA_ROOT    replaces ~/crestbrick-consult/_templates
    WA_INTAKE_MSG_DB       replaces ~/whatsapp-mcp/whatsapp-bridge/store
    WA_INTAKE_REPO_ROOT    replaces ~/crestbrick-consult (scripts/binaries only, e.g. the
                           cross-sender guard script and the matchmaker media dirs)
    WA_INTAKE_SANDBOX=1    see sandbox_init() below

USAGE PATTERN (see wa_intake_send.py / wa_intake_notify.py for the reference
implementation): a module keeps its historical module-level constant (e.g. LASTF) for
backward compatibility with existing `mock.patch.object(module, "LASTF", ...)` tests, but
every function that actually touches disk resolves the path via resolved(globals(), ...)
at CALL TIME instead of reading the bare constant directly -- so an explicit patch on the
DEFINING module still wins (back compat), and otherwise the live env vars always win (the
actual fix), even for a call reached through a re-exported name in a different module.
"""
import os

_REAL_STATE_ROOT = os.path.expanduser("~/.claude/state/listing-templates")
_REAL_DATA_ROOT = os.path.expanduser("~/crestbrick-consult/_templates")
_REAL_MSG_ROOT = os.path.expanduser("~/whatsapp-mcp/whatsapp-bridge/store")
_REAL_REPO_ROOT = os.path.expanduser("~/crestbrick-consult")

REAL_ROOTS = (_REAL_STATE_ROOT, _REAL_MSG_ROOT)  # dirs a sandboxed run must never resolve into
# NOTE: _REAL_DATA_ROOT / _REAL_REPO_ROOT are NOT in REAL_ROOTS -- _templates/ and the repo
# root hold the checked-in property/landlord-onboarding templates and the guard script, which
# a sandbox is allowed to READ (they are not live per-run state); only STATE_ROOT and MSG_DB
# hold data a sandbox run could corrupt or leak from (watermark, tenant conversations, real
# WhatsApp messages). See sandbox_init() for the refusal check that uses this tuple.


def _env_root(var, default):
    v = os.environ.get(var)
    return os.path.expanduser(v) if v else default


def paths(root=None):
    """Fresh dict of every path the engine family opens, computed from the CURRENT
    environment (never cached). `root`, if given, overrides WA_INTAKE_STATE_ROOT for this
    call only -- lets a caller ask "what would resolve under this tempdir" without mutating
    os.environ."""
    state_root = os.path.expanduser(root) if root else _env_root("WA_INTAKE_STATE_ROOT", _REAL_STATE_ROOT)
    data_root = _env_root("WA_INTAKE_DATA_ROOT", _REAL_DATA_ROOT)
    msg_root = _env_root("WA_INTAKE_MSG_DB", _REAL_MSG_ROOT)
    repo_root = _env_root("WA_INTAKE_REPO_ROOT", _REAL_REPO_ROOT)
    j = os.path.join
    return {
        "state_root": state_root,
        "data_root": data_root,
        "msg_root": msg_root,
        "repo_root": repo_root,
        # ---- state_root (~/.claude/state/listing-templates) ----
        "intake_state": j(state_root, "intake-state.json"),
        "runner_last": j(state_root, "runner-last.json"),
        "dry_run_preview": j(state_root, "dry-run-preview.log"),
        "drafts": j(state_root, "drafts.jsonl"),
        "owner_questions": j(state_root, "owner-questions.jsonl"),
        "notify_queue": j(state_root, "notify-queue.json"),
        "notify_coalesce": j(state_root, "notify-coalesce.json"),
        "notify_allow": j(state_root, "notify-allow.json"),
        "notify_muted": j(state_root, "notify-muted.log"),
        "engine_pin": j(state_root, "engine-pin.json"),
        "listing_index": j(state_root, "listing-index.json"),
        "property_templates": j(state_root, "property-templates.json"),
        "viewing_availability": j(state_root, "viewing-availability.json"),
        "matching_config": j(state_root, "matching-config.json"),
        "send_circuit": j(state_root, "send-circuit.json"),
        "lock_file": j(state_root, ".wa-intake.lock"),
        # background Haiku draft/extract worker (9 Sep 2026 merge redo, item 3): pending
        # request records + one result file per request, so the blocking claude-guard call
        # never sits inside a runner tick.
        "draft_worker_pending": j(state_root, "draft-worker-pending.json"),
        "draft_worker_results_dir": j(state_root, "draft-worker-results"),
        "cobroke_db": j(state_root, "cobroke-agents.json") if state_root != _REAL_STATE_ROOT
                      else os.path.expanduser("~/.claude/state/cobroke-agents.json"),
        "refresh_lock_dir": j(state_root, "refresh-rental-dbs.lock.d") if state_root != _REAL_STATE_ROOT
                      else os.path.expanduser("~/.claude/state/refresh-rental-dbs.lock.d"),
        # ---- data_root (~/crestbrick-consult/_templates) ----
        "landlord_db": j(data_root, "landlord-db.json"),
        "tenant_db": j(data_root, "tenant-db.json"),
        "landlord_onboarding_md": j(data_root, "landlord-onboarding.md"),
        "seller_intake_md": j(data_root, "seller-intake.md"),
        # ---- msg_root (~/whatsapp-mcp/whatsapp-bridge/store) ----
        "messages_db": j(msg_root, "messages.db"),
        "whatsapp_db": j(msg_root, "whatsapp.db"),
        # ---- repo_root (~/crestbrick-consult) ----
        "guard_script": j(repo_root, "scripts", "wa_send_guard.py"),
        "matchmaker_photos_dir": j(repo_root, "scripts", "matchmaker", "deploy", "photos"),
        "matchmaker_video_stills_dir": j(repo_root, "scripts", "matchmaker", "deploy", "video-stills"),
    }


def resolved(module_globals, name, key, root=None):
    """Call-time path resolution for one module-level constant. `module_globals` is that
    module's own globals() dict, `name` the constant's name (e.g. "LASTF"), `key` its entry
    in paths(). Priority: (1) an explicit mock.patch.object on the DEFINING module (constant
    no longer equals the default captured at import time) wins, for backward compatibility
    with existing tests; (2) otherwise resolve fresh from the current environment every call
    -- this is what actually fixes the cross-module desync (a patch aimed at a re-exporting
    module, e.g. wa_intake_runner.LASTF, never reaches this function's caller lookup, but the
    env var always does)."""
    default = module_globals.get("_default_" + name)
    current = module_globals.get(name)
    if default is not None and current != default:
        return current
    return paths(root=root)[key]


class SandboxViolation(RuntimeError):
    pass


def sandbox_init():
    """Call once, first thing, in every harness/replay/importer/test entrypoint. When
    WA_INTAKE_SANDBOX=1:
      (i)   forces WA_INTAKE_NO_TELEGRAM=1 and WA_INTAKE_NO_SEND=1 -- notify and bridge send
            become recorders no matter what the caller forgot to set.
      (ii)  refuses to proceed (raises SandboxViolation) if WA_INTAKE_STATE_ROOT or
            WA_INTAKE_MSG_DB resolves inside (or equal to) a real live root -- BEFORE
            anything is touched on disk.
      (iii) is a no-op with WA_INTAKE_SANDBOX unset -- production (the launchd runner) never
            calls this, and if it did, it would pass through unchanged.
    Callers still take their OWN lock under paths()["lock_file"]; sandbox_init() only
    guarantees that path can never BE the live lock (see (ii))."""
    if os.environ.get("WA_INTAKE_SANDBOX") != "1":
        return
    os.environ["WA_INTAKE_NO_TELEGRAM"] = "1"
    os.environ["WA_INTAKE_NO_SEND"] = "1"
    p = paths()
    for real in REAL_ROOTS:
        real_abs = os.path.realpath(real)
        for key in ("state_root", "msg_root"):
            cand = os.path.realpath(p[key])
            if cand == real_abs or cand.startswith(real_abs + os.sep):
                raise SandboxViolation(
                    f"WA_INTAKE_SANDBOX=1 but {key}={p[key]!r} resolves inside the real "
                    f"live root {real!r}. Refusing to start -- set WA_INTAKE_STATE_ROOT / "
                    f"WA_INTAKE_MSG_DB to a temp directory before importing any wa-pipeline "
                    f"module.")
