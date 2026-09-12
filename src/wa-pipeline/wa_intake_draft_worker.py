"""
wa_intake_draft_worker.py -- non blocking Haiku draft/extract generation (Winfred, 9 Sep
2026 merge redo, item 3: drafts off the critical path).

WHY THIS EXISTS
    call_haiku() (wa_intake_draft.py) and call_haiku_extract() (wa_intake_owner_answers.py)
    each ran claude-guard through a BLOCKING subprocess.run with its own 25s timeout, right
    inside the runner's single 60-120s tick -- one slow or stuck draft held up every OTHER
    prospect and every other landlord for up to 25 seconds, serially, once per tick.

    This module replaces that blocking call, for the LIVE runner only, with a detached
    background worker: spawn_request() writes a pending request record and Popens
    claude-guard with its stdout going straight to a result FILE (never a pipe the parent
    has to drain), then returns immediately. The child process is not tied to this tick's
    own lifetime -- launchd re-invokes wa_intake_runner.py as a brand new process every
    tick anyway, so a detached grandchild simply keeps running after this tick's process
    exits, exactly the way a real background job should. sweep() is called on a LATER tick
    (any number of ticks later) to collect whatever finished, or to time out and kill
    whatever is still running past WALL_BUDGET_SEC.

    The original synchronous call_haiku()/call_haiku_extract() are UNCHANGED and still used
    by the offline replay/harness tools (wa_intake_resume_replay.py,
    wa_intake_attack_harness.py), which want one real inline answer to preview, not a
    background job spanning several tool invocations.
"""
import os, time, json, signal, subprocess
import wa_intake_paths as _P

HAIKU_BIN = os.path.expanduser("~/.claude/bin/claude-guard")
HAIKU_MODEL = "claude-haiku-4-5-20251001"
HAIKU_MCP_CONFIG = os.path.expanduser("~/.claude/mcp-configs/none.json")

WALL_BUDGET_SEC = 60      # kill + timeout past this, no matter how busy claude-guard is
MAX_SPAWNS_PER_TICK = 2   # new background jobs a single runner tick may start

# STEP 0 sandbox seal pattern (see wa_intake_paths.resolved's docstring): kept as bare
# module constants for backward compatible mock.patch.object(...) in tests; the _path()
# helpers below resolve them fresh at call time so WA_INTAKE_STATE_ROOT always wins in a
# sandboxed run regardless of which module a caller happens to reach this through.
PENDING = _P.paths()["draft_worker_pending"]
_default_PENDING = PENDING


def _pending_path():
    return _P.resolved(globals(), "PENDING", "draft_worker_pending")


RESULTS_DIR = _P.paths()["draft_worker_results_dir"]
_default_RESULTS_DIR = RESULTS_DIR


def _results_dir():
    return _P.resolved(globals(), "RESULTS_DIR", "draft_worker_results_dir")


# Per-tick spawn budget. A fresh runner process starts this at 0 on import; reset_tick_budget
# is called once at the very top of wa_intake_runner.run() so a long lived test process
# (unlike the real launchd-invoked runner) does not silently carry a budget over between
# unrelated calls.
_tick_spawns = 0


def reset_tick_budget():
    """Call once at the very start of a runner tick, before any spawn_request call."""
    global _tick_spawns
    _tick_spawns = 0


def _load_pending():
    try:
        with open(_pending_path()) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _save_pending(items):
    path = _pending_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def in_flight_for_chat(key, kind=None):
    """True if `key` (a phone number or a landlord id -- any stable chat identity string)
    already has an unresolved request pending, of the given kind if one is specified."""
    return any(r.get("pn") == key and (kind is None or r.get("kind") == kind)
               for r in _load_pending())


def _pid_alive(pid):
    """True if `pid` is still running. In production, the process calling this is a brand
    new launchd-invoked tick, never the one that Popen'd the child (that tick already
    exited) -- os.waitpid raises ECHILD immediately for a pid that is not our own child, so
    the plain os.kill(pid, 0) existence check below is what actually decides it there.
    Inside a single long lived process (every test in this repo), though, WE are the one
    that called Popen, so an exited child sits as a zombie -- still "alive" by os.kill's
    reckoning -- until reaped; the non-blocking waitpid reaps it first so a finished job is
    never reported as still running just because nothing collected its exit status yet."""
    if not pid:
        return False
    try:
        reaped_pid, _status = os.waitpid(pid, os.WNOHANG)
        if reaped_pid == pid:
            return False
    except ChildProcessError:
        pass
    except OSError:
        pass
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def spawn_request(kind, key, jid, prompt, context=None):
    """Fire-and-forget: writes the pending record, Popens claude-guard with its stdout
    redirected straight to a result file, and returns WITHOUT waiting on it.

    Returns one of:
      "spawned"      -- background job started, will show up in a later sweep()
      "in_flight"    -- this chat already has an unresolved request of this kind
      "cap_reached"  -- MAX_SPAWNS_PER_TICK already used this tick, try again next tick
      "sandboxed"    -- WA_INTAKE_SANDBOX=1, no subprocess ever started (STEP 0 seal)
      "spawn_error"  -- Popen itself failed (bad binary, out of file descriptors, ...)

    Never raises, never blocks past starting the child process."""
    global _tick_spawns
    if os.environ.get("WA_INTAKE_SANDBOX") == "1":
        return "sandboxed"
    if in_flight_for_chat(key, kind):
        return "in_flight"
    if _tick_spawns >= MAX_SPAWNS_PER_TICK:
        return "cap_reached"
    request_id = f"{int(time.time() * 1000)}-{kind}-{key}"
    results_dir = _results_dir()
    os.makedirs(results_dir, exist_ok=True)
    result_file = os.path.join(results_dir, request_id + ".json")
    fh = None
    try:
        fh = open(result_file, "wb")
        proc = subprocess.Popen(
            [HAIKU_BIN, "-p", prompt, "--model", HAIKU_MODEL, "--strict-mcp-config",
             "--mcp-config", HAIKU_MCP_CONFIG, "--output-format", "json"],
            stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    except Exception:
        return "spawn_error"
    finally:
        if fh is not None:
            fh.close()
    items = _load_pending()
    items.append({"id": request_id, "kind": kind, "pn": key, "jid": jid,
                 "started_ts": time.time(), "pid": proc.pid,
                 "result_file": result_file, "context": context or {}})
    _save_pending(items)
    _tick_spawns += 1
    return "spawned"


def _read_result(result_file):
    """Parse claude-guard's --output-format json stdout the exact same way
    wa_intake_draft.call_haiku always has. Returns (text, err)."""
    try:
        with open(result_file) as f:
            raw = f.read()
    except OSError:
        return None, "no result file"
    if not raw.strip():
        return None, "empty result"
    try:
        data = json.loads(raw)
    except Exception:
        return None, "unparseable output"
    if data.get("is_error"):
        return None, "is_error: " + str(data.get("result"))[:200]
    text = (data.get("result") or "").strip()
    if not text:
        return None, "empty result"
    return text, None


def _cleanup(result_file):
    try:
        if result_file:
            os.remove(result_file)
    except OSError:
        pass


def sweep(handlers):
    """Called once per tick, regardless of whether this tick spawned anything itself --
    a request from an EARLIER tick is what usually finishes here. `handlers` maps
    kind -> {"on_result": fn(record, text, err), "on_timeout": fn(record)}.

    A request whose process has already exited is finished: its result file is read (text,
    err) and on_result fires (err is set if claude-guard itself errored, produced
    unparseable output, or an empty result -- same shape call_haiku always returned).
    A request still running past WALL_BUDGET_SEC is killed and on_timeout fires instead.
    Anything still running and within budget is left in the pending file for the next
    sweep. An unknown kind (should never happen) is dropped rather than left to leak
    forever. One handler raising is swallowed so it can never wedge every other pending
    request in the same sweep; the record is retired either way -- nothing is ever
    retried a second time."""
    items = _load_pending()
    if not items:
        return
    remaining = []
    for rec in items:
        h = handlers.get(rec.get("kind"))
        if h is None:
            _cleanup(rec.get("result_file"))
            continue
        if _pid_alive(rec.get("pid")):
            if time.time() - rec.get("started_ts", 0) <= WALL_BUDGET_SEC:
                remaining.append(rec)
                continue
            try:
                os.kill(rec["pid"], signal.SIGKILL)
            except Exception:
                pass
            try:
                h["on_timeout"](rec)
            except Exception:
                pass
            _cleanup(rec.get("result_file"))
            continue
        text, err = _read_result(rec.get("result_file"))
        try:
            h["on_result"](rec, text, err)
        except Exception:
            pass
        _cleanup(rec.get("result_file"))
    _save_pending(remaining)
