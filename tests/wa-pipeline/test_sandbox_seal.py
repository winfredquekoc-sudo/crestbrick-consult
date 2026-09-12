"""
test_sandbox_seal.py -- STEP 0 of the intake merge redo (9 Sep 2026): proves the sandbox
seal that MUST hold before any harness/replay/importer/runner code is trusted to run again.

Two prior incidents, both the same bug shape: a sandbox/test process patched a NAME in the
WRONG module (usually wa_intake_runner's own `from wa_intake_send import (LASTF, ...)` style
re-export) instead of the module a function is actually DEFINED in, so the function's own
bare-name path lookup (resolved via ITS OWN module's globals(), never the caller's) fell
through to the real, hardcoded, unpatched path.
  (1) Telegram/bridge sends reached real numbers with synthetic scenario data.
  (2) wa_intake_send._write_last() -- defined in wa_intake_send.py, but only ever patched via
      wa_intake_runner.LASTF -- wrote rowid 1 into the LIVE runner-last.json (a 328,000
      message rescan) and, by the same shape of bug, synthetic records landed in the LIVE
      intake-state.json.

wa_intake_paths.py replaces per-call-site mock.patch aiming with three environment variables
(WA_INTAKE_STATE_ROOT / WA_INTAKE_DATA_ROOT / WA_INTAKE_MSG_DB) that every module resolves
fresh at CALL TIME (see wa_intake_paths.resolved's docstring) -- a script that only sets the
env vars is sandboxed regardless of which module a given function happens to live in.

This file proves that from the OUTSIDE, adversarially: with WA_INTAKE_SANDBOX=1 and every
root pointed at a throwaway tempdir, it monkeypatches builtins.open, os.replace, os.rename,
sqlite3.connect, subprocess.run and requests.post so that ANY attempt to touch a real live
path (~/.claude/state/listing-templates, ~/whatsapp-mcp/whatsapp-bridge/store, the real
landlord-db.json/tenant-db.json) or reach api.telegram.org / localhost:8080 raises
immediately -- then runs one full harness scenario, one shadow-replay tick, one
resume-replay tick, the owner-question importer's dry run, and wa_intake_runner.run() itself,
all against the sandboxed roots. Every one of them must complete with ZERO raises. It also
proves the mirror image: with WA_INTAKE_SANDBOX unset, every path resolves to the historical
real one (production is unchanged).

ABSOLUTE RULE this file exists to satisfy: nothing downstream (the attack harness batch, the
shadow/resume replays, real verification runs) may execute until this test passes.

Run: /usr/bin/python3 tests/wa-pipeline/test_sandbox_seal.py
"""
import sys, os, re, json, sqlite3, builtins, subprocess, importlib.util, tempfile, shutil, unittest
from unittest import mock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_WA_DIR = os.path.join(_REPO_ROOT, "src", "wa-pipeline")
_SCRIPTS_DIR = os.path.join(_REPO_ROOT, "scripts")
sys.path.insert(0, _WA_DIR)

import wa_intake_paths as P  # noqa: E402


# ---------------------------------------------------------------------------------------
# The live-touch detector. Deliberately NOT a blanket "no open() calls" patch -- that would
# also break Python's own module import machinery, pytest/unittest's housekeeping, and every
# legitimate temp-file operation the code under test performs. It only raises for the
# SPECIFIC real paths/hosts a sandboxed run must never reach.
# ---------------------------------------------------------------------------------------
class LiveTouch(AssertionError):
    pass


_REAL_STATE_ROOT = os.path.realpath(os.path.expanduser("~/.claude/state/listing-templates"))
_REAL_MSG_ROOT = os.path.realpath(os.path.expanduser("~/whatsapp-mcp/whatsapp-bridge/store"))
_REAL_LANDLORD_DB = os.path.realpath(
    os.path.expanduser("~/crestbrick-consult/_templates/landlord-db.json"))
_REAL_TENANT_DB = os.path.realpath(
    os.path.expanduser("~/crestbrick-consult/_templates/tenant-db.json"))
_FORBIDDEN_ROOTS = (_REAL_STATE_ROOT, _REAL_MSG_ROOT)
_FORBIDDEN_FILES = (_REAL_LANDLORD_DB, _REAL_TENANT_DB)
_FORBIDDEN_SUBSTRINGS = ("api.telegram.org", "localhost:8080", "127.0.0.1:8080",
                         "telegram_send.sh")


def _is_forbidden_path(p):
    if not isinstance(p, (str, bytes, os.PathLike)):
        return False
    s = os.fspath(p) if not isinstance(p, str) else p
    if s.startswith("file:"):
        s = s[5:].split("?")[0]
    try:
        rp = os.path.realpath(s)
    except Exception:
        return False
    if rp in _FORBIDDEN_FILES:
        return True
    for root in _FORBIDDEN_ROOTS:
        if rp == root or rp.startswith(root + os.sep):
            return True
    return False


class _Guards:
    """Installs/removes the five monkeypatches as one unit. Not a decorator/context manager
    on purpose -- some of the code under test (the attack harness) already runs inside its
    OWN mock.patch.ExitStack, and layering context managers made the failure traces from a
    real violation harder to read than a plain install()/remove() pair."""

    def __init__(self):
        self.violations = []
        self._real_open = builtins.open
        self._real_replace = os.replace
        self._real_rename = os.rename
        self._real_sqlite_connect = sqlite3.connect
        self._real_subprocess_run = subprocess.run
        self._real_subprocess_popen = subprocess.Popen
        self._real_requests_post = None

    def _record(self, msg):
        self.violations.append(msg)
        raise LiveTouch(msg)

    def install(self):
        guards = self

        def guarded_open(file, mode="r", *a, **k):
            if _is_forbidden_path(file):
                guards._record(f"open({file!r}, {mode!r}) reached a real live path")
            return guards._real_open(file, mode, *a, **k)

        def guarded_replace(src, dst, *a, **k):
            if _is_forbidden_path(src) or _is_forbidden_path(dst):
                guards._record(f"os.replace({src!r}, {dst!r}) reached a real live path")
            return guards._real_replace(src, dst, *a, **k)

        def guarded_rename(src, dst, *a, **k):
            if _is_forbidden_path(src) or _is_forbidden_path(dst):
                guards._record(f"os.rename({src!r}, {dst!r}) reached a real live path")
            return guards._real_rename(src, dst, *a, **k)

        def guarded_sqlite_connect(database, *a, **k):
            if _is_forbidden_path(database):
                guards._record(f"sqlite3.connect({database!r}) reached a real live db")
            return guards._real_sqlite_connect(database, *a, **k)

        def guarded_subprocess_run(cmd, *a, **k):
            cmd_str = " ".join(cmd) if isinstance(cmd, (list, tuple)) else str(cmd)
            if any(sub in cmd_str for sub in _FORBIDDEN_SUBSTRINGS):
                guards._record(f"subprocess.run reached a forbidden target: {cmd_str!r}")
            return guards._real_subprocess_run(cmd, *a, **k)

        def guarded_subprocess_popen(cmd, *a, **k):
            # wa_intake_draft_worker.spawn_request (9 Sep 2026, item 3: background
            # draft/extract worker) Popens claude-guard directly instead of subprocess.run --
            # WA_INTAKE_SANDBOX=1 already short circuits before this is ever reached (same
            # pattern as call_haiku/call_haiku_extract), so this guard should never actually
            # fire; it exists purely as the same defense in depth subprocess.run already
            # gets, in case a future edit removes that early return by mistake.
            cmd_str = " ".join(cmd) if isinstance(cmd, (list, tuple)) else str(cmd)
            if any(sub in cmd_str for sub in _FORBIDDEN_SUBSTRINGS):
                guards._record(f"subprocess.Popen reached a forbidden target: {cmd_str!r}")
            return guards._real_subprocess_popen(cmd, *a, **k)

        builtins.open = guarded_open
        os.replace = guarded_replace
        os.rename = guarded_rename
        sqlite3.connect = guarded_sqlite_connect
        subprocess.run = guarded_subprocess_run
        subprocess.Popen = guarded_subprocess_popen

        # requests is imported lazily inside wa_intake_send._send; only patch it if/when the
        # module is already importable, and make ANY call a violation -- while sandboxed,
        # WA_INTAKE_NO_SEND=1 means _send() must never reach this line at all.
        try:
            import requests
            self._real_requests_post = requests.post

            def guarded_requests_post(url, *a, **k):
                guards._record(f"requests.post({url!r}) called at all in a sandboxed run")
            requests.post = guarded_requests_post
        except ImportError:
            pass

    def remove(self):
        builtins.open = self._real_open
        os.replace = self._real_replace
        os.rename = self._real_rename
        sqlite3.connect = self._real_sqlite_connect
        subprocess.run = self._real_subprocess_run
        subprocess.Popen = self._real_subprocess_popen
        if self._real_requests_post is not None:
            import requests
            requests.post = self._real_requests_post


_MESSAGES_SCHEMA = """
CREATE TABLE messages (
    id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TIMESTAMP,
    is_from_me BOOLEAN, media_type TEXT, filename TEXT, url TEXT,
    media_key BLOB, file_sha256 BLOB, file_enc_sha256 BLOB, file_length INTEGER
)"""
_CHATS_SCHEMA = "CREATE TABLE chats (jid TEXT, name TEXT, last_message_time TIMESTAMP)"


def _load_script(name, filename, in_dir=_SCRIPTS_DIR):
    spec = importlib.util.spec_from_file_location(name, os.path.join(in_dir, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestSandboxSealUnsetIsUnchanged(unittest.TestCase):
    """With WA_INTAKE_SANDBOX (and the three root vars) unset, every path must resolve to
    the historical hardcoded real path -- production, run by launchd with no env vars set
    at all, must be byte-for-byte unchanged by this refactor."""

    def test_paths_unset_equal_real(self):
        env_keys = ("WA_INTAKE_SANDBOX", "WA_INTAKE_STATE_ROOT", "WA_INTAKE_DATA_ROOT",
                    "WA_INTAKE_MSG_DB", "WA_INTAKE_REPO_ROOT")
        saved = {k: os.environ.pop(k, None) for k in env_keys}
        try:
            p = P.paths()
            self.assertEqual(p["state_root"], _REAL_STATE_ROOT)
            self.assertEqual(p["msg_root"], _REAL_MSG_ROOT)
            self.assertEqual(p["landlord_db"], _REAL_LANDLORD_DB)
            self.assertEqual(p["intake_state"],
                             os.path.join(_REAL_STATE_ROOT, "intake-state.json"))
            self.assertEqual(p["runner_last"],
                             os.path.join(_REAL_STATE_ROOT, "runner-last.json"))
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

    def test_sandbox_init_is_noop_when_unset(self):
        saved = os.environ.pop("WA_INTAKE_SANDBOX", None)
        try:
            P.sandbox_init()  # must not raise -- and must not touch WA_INTAKE_NO_TELEGRAM
        finally:
            if saved is not None:
                os.environ["WA_INTAKE_SANDBOX"] = saved


class TestSandboxInitRefusesRealRoots(unittest.TestCase):
    def test_refuses_real_state_root(self):
        saved = {k: os.environ.get(k) for k in
                 ("WA_INTAKE_SANDBOX", "WA_INTAKE_STATE_ROOT", "WA_INTAKE_MSG_DB")}
        os.environ["WA_INTAKE_SANDBOX"] = "1"
        os.environ.pop("WA_INTAKE_STATE_ROOT", None)
        os.environ.pop("WA_INTAKE_MSG_DB", None)
        try:
            with self.assertRaises(P.SandboxViolation):
                P.sandbox_init()
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_accepts_tempdir_roots(self):
        with tempfile.TemporaryDirectory(prefix="wa-seal-") as tmp:
            saved = {k: os.environ.get(k) for k in
                     ("WA_INTAKE_SANDBOX", "WA_INTAKE_STATE_ROOT", "WA_INTAKE_MSG_DB")}
            os.environ["WA_INTAKE_SANDBOX"] = "1"
            os.environ["WA_INTAKE_STATE_ROOT"] = tmp
            os.environ["WA_INTAKE_MSG_DB"] = tmp
            try:
                P.sandbox_init()  # must not raise
                self.assertEqual(os.environ.get("WA_INTAKE_NO_TELEGRAM"), "1")
                self.assertEqual(os.environ.get("WA_INTAKE_NO_SEND"), "1")
            finally:
                for k, v in saved.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v


class TestLiveTouchDetectorSanity(unittest.TestCase):
    """The detector itself must actually fire on the real paths it exists to catch, and
    must NOT fire on ordinary temp-file / stdlib activity -- otherwise the tests below prove
    nothing."""

    def test_detects_real_state_dir(self):
        self.assertTrue(_is_forbidden_path(os.path.join(_REAL_STATE_ROOT, "runner-last.json")))
        self.assertTrue(_is_forbidden_path(_REAL_LANDLORD_DB))

    def test_ignores_tempdir(self):
        with tempfile.TemporaryDirectory(prefix="wa-seal-") as tmp:
            self.assertFalse(_is_forbidden_path(os.path.join(tmp, "runner-last.json")))

    def test_guards_do_not_break_ordinary_io(self):
        g = _Guards()
        g.install()
        try:
            with tempfile.TemporaryDirectory(prefix="wa-seal-") as tmp:
                p = os.path.join(tmp, "x.json")
                with open(p, "w") as f:
                    f.write("{}")
                with open(p) as f:
                    self.assertEqual(f.read(), "{}")
                con = sqlite3.connect(os.path.join(tmp, "x.db"))
                con.execute("CREATE TABLE t (a)")
                con.close()
                r = subprocess.run(["echo", "hi"], capture_output=True, text=True)
                self.assertEqual(r.stdout.strip(), "hi")
        finally:
            g.remove()

    def test_guards_fire_on_real_path_open(self):
        g = _Guards()
        g.install()
        try:
            with self.assertRaises(LiveTouch):
                open(os.path.join(_REAL_STATE_ROOT, "runner-last.json"))
        finally:
            g.remove()


class SandboxedRun(unittest.TestCase):
    """Shared setUp: one throwaway root, WA_INTAKE_SANDBOX=1, and the live-touch guards
    installed for the duration of each test method."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wa-seal-run-")
        self._saved_env = {k: os.environ.get(k) for k in
                           ("WA_INTAKE_SANDBOX", "WA_INTAKE_STATE_ROOT",
                            "WA_INTAKE_DATA_ROOT", "WA_INTAKE_MSG_DB",
                            "WA_INTAKE_NO_TELEGRAM", "WA_INTAKE_NO_SEND")}
        os.environ["WA_INTAKE_SANDBOX"] = "1"
        os.environ["WA_INTAKE_STATE_ROOT"] = self.tmp
        os.environ["WA_INTAKE_DATA_ROOT"] = self.tmp
        os.environ["WA_INTAKE_MSG_DB"] = self.tmp
        P.sandbox_init()
        self.before_mtimes = self._live_mtimes()
        self.guards = _Guards()
        self.guards.install()

    def tearDown(self):
        self.guards.remove()
        shutil.rmtree(self.tmp, ignore_errors=True)
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    @staticmethod
    def _live_mtimes():
        out = {}
        for name in ("runner-last.json", "intake-state.json", "notify-muted.log"):
            p = os.path.join(_REAL_STATE_ROOT, name)
            out[p] = os.path.getmtime(p) if os.path.exists(p) else None
        return out

    def assert_live_state_untouched(self):
        after = self._live_mtimes()
        print("live mtimes before:", self.before_mtimes)
        print("live mtimes after: ", after)
        self.assertEqual(self.before_mtimes, after,
                         "a live runner-last.json / intake-state.json mtime changed")


class TestHarnessScenarioIsSandboxed(SandboxedRun):
    def test_one_full_scenario(self):
        harness = _load_script("wa_intake_attack_harness", "wa_intake_attack_harness.py")
        scenario = harness._self_test_scenario()
        result = harness.run_scenario(scenario)
        self.assertIn("final_records", result)
        self.assert_live_state_untouched()


def _fixture_msg_db(path, rows):
    con = sqlite3.connect(path)
    con.execute(_MESSAGES_SCHEMA)
    con.execute(_CHATS_SCHEMA)
    for r in rows:
        con.execute(
            "INSERT INTO messages (id, chat_jid, sender, content, timestamp, is_from_me, "
            "media_type) VALUES (?,?,?,?,?,?,?)", r)
    con.commit()
    con.close()


class TestShadowReplayTickIsSandboxed(SandboxedRun):
    def test_one_tick(self):
        import datetime
        msg_db = os.path.join(self.tmp, "messages.db")
        now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        _fixture_msg_db(msg_db, [
            ("S1", "6598887777@lid", "6598887777", "Name: Alex\nNationality: Singaporean\n"
             "Ethnicity: Chinese\nGender: Male\nPass type: Citizen\nNo of pax: 1\n"
             "Move in date: 1 Oct\nLease term: 12\nBudget: 1200", now, 0, ""),
        ])
        replay = _load_script("wa_intake_shadow_replay", "wa_intake_shadow_replay.py")
        chats = replay.discover_filled_form_chats(7)
        self.assertEqual(chats, ["6598887777@lid"])
        rows = replay.fetch_chat_rows(chats[0], 7)
        self.assertEqual(len(rows), 1)
        self.assert_live_state_untouched()


class TestResumeReplayTickIsSandboxed(SandboxedRun):
    def test_one_tick(self):
        import datetime
        msg_db = os.path.join(self.tmp, "messages.db")
        now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        _fixture_msg_db(msg_db, [
            ("R1", "6598887777@lid", "me", "Sure, what time works for you?", now, 1, ""),
        ])
        replay = _load_script("wa_intake_resume_replay", "wa_intake_resume_replay.py")
        chats = replay.discover_handtake_chats(7, landlords=set())
        self.assertEqual(chats, ["6598887777@lid"])
        rows = replay.fetch_full_chat(chats[0])
        self.assertEqual(len(rows), 1)
        self.assert_live_state_untouched()


class TestImporterDryRunIsSandboxed(SandboxedRun):
    def test_dry_run(self):
        landlord_db = os.path.join(self.tmp, "landlord-db.json")
        with open(landlord_db, "w") as f:
            json.dump({"landlords": [{"id": "LL999", "phone": "81234567",
                                      "status": "active"}]}, f)
        report = os.path.join(self.tmp, "report.md")
        with open(report, "w") as f:
            f.write("**LL999** (81234567)\n\n## Section C -- Questions to ask\n"
                    "- Does the unit have aircon?\n- What is the WiFi speed?\n")
        importer = _load_script("import_owner_questions", "import_owner_questions.py")
        rc = importer.run(report, apply_=False)
        self.assertEqual(rc, 0)
        # dry run: nothing enqueued anywhere, sandboxed or not
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "owner-questions.jsonl")))
        self.assert_live_state_untouched()


class TestRunnerRunIsSandboxed(SandboxedRun):
    def test_first_tick_sets_watermark_only(self):
        msg_db = os.path.join(self.tmp, "messages.db")
        _fixture_msg_db(msg_db, [])  # empty: first run should just set the watermark
        for name, doc in (("listing-index.json", {"listings": []}),
                          ("property-templates.json", {"listings": []}),
                          ("viewing-availability.json", {}),
                          ("landlord-db.json", {"landlords": []}),
                          ("cobroke-agents.json", {}),
                          ("intake-state.json", {"version": 1, "conversations": {}})):
            with open(os.path.join(self.tmp, name), "w") as f:
                json.dump(doc, f)
        import importlib
        import wa_intake_runner as R
        importlib.reload(R)   # picks up the sandboxed env vars fresh (module-level defaults
                              # are computed at import time; a prior test may have imported
                              # this module under different env vars already)
        with mock.patch.object(R, "_quiet_hours", lambda: False):  # determinism regardless
            R.run()                                                # of real time-of-day
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "runner-last.json")))
        with open(os.path.join(self.tmp, "runner-last.json")) as f:
            self.assertEqual(json.load(f), {"last_rowid": 0})
        self.assert_live_state_untouched()


class TestLandlordMatcherScriptIsSealed(unittest.TestCase):
    """item 4 (9 Sep 2026 merge review): src/wa-pipeline/test_landlord_matcher.py used to
    import intake_engine and call load_state()/save_state()/on_landlord_form_completed()
    with NO sandboxing at all -- a plain `python3 test_landlord_matcher.py`, run by hand or
    by a future CI step with no special environment, touched the REAL intake-state.json
    (and, via on_landlord_form_completed_for_99co, the real listing-index.json). The file
    now calls wa_intake_paths.sandbox_init() against its own throwaway tempdir before its
    first wa-pipeline import (see its own top-of-file comment). Proved here from the
    OUTSIDE, adversarially, exactly like every other case in this file: run it as a real
    subprocess with a DELIBERATELY CLEAN environment (no WA_INTAKE_SANDBOX pre-set by the
    caller -- the whole point is that an unsandboxed INVOKER still cannot reach live state,
    because the script protects itself) and confirm neither live file's mtime moved."""

    @staticmethod
    def _live_mtimes():
        out = {}
        for name in ("runner-last.json", "intake-state.json", "notify-muted.log"):
            p = os.path.join(_REAL_STATE_ROOT, name)
            out[p] = os.path.getmtime(p) if os.path.exists(p) else None
        return out

    def test_unsandboxed_invocation_never_touches_live_state(self):
        before = self._live_mtimes()
        script = os.path.join(_WA_DIR, "test_landlord_matcher.py")
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("WA_INTAKE_")}   # deliberately clean: no inherited sandbox
        r = subprocess.run([sys.executable, script], capture_output=True, text=True,
                           timeout=60, env=env)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        after = self._live_mtimes()
        self.assertEqual(before, after,
                         "test_landlord_matcher.py moved a live state file's mtime")


class TestDormantMatcherModulesAreSealed(unittest.TestCase):
    """ninety_nine_co_lister / landlord_tenant_matcher used bare os.path.expanduser
    constants, so they resolved (and add_listing_to_index WROTE) the REAL live
    listing-index.json even with WA_INTAKE_SANDBOX=1 and every root on a tempdir -- no env
    var could reach them, because they never consulted wa_intake_paths at all. Dormant in
    production (nothing in the runner calls them), but reachable from a test in the tree:
    src/wa-pipeline/test_landlord_matcher.py -> intake_engine.on_landlord_form_completed ->
    on_landlord_form_completed_for_99co -> add_listing_to_index -> _save_json(live path).
    Merge review, 9 Sep 2026."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wa-seal-dormant-")
        self._saved = {k: os.environ.get(k) for k in
                       ("WA_INTAKE_SANDBOX", "WA_INTAKE_STATE_ROOT", "WA_INTAKE_DATA_ROOT",
                        "WA_INTAKE_MSG_DB")}
        os.environ.update(WA_INTAKE_SANDBOX="1", WA_INTAKE_STATE_ROOT=self.tmp,
                          WA_INTAKE_DATA_ROOT=self.tmp, WA_INTAKE_MSG_DB=self.tmp)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_listing_index_redirects_under_sandbox(self):
        import importlib
        import ninety_nine_co_lister as L
        importlib.reload(L)
        self.assertTrue(L._listing_index().startswith(self.tmp),
                        f"lister still resolves {L._listing_index()!r} under a sandbox")

    def test_add_listing_to_index_never_writes_the_live_file(self):
        import importlib
        import ninety_nine_co_lister as L
        importlib.reload(L)
        with open(os.path.join(self.tmp, "listing-index.json"), "w") as f:
            json.dump({"listings": []}, f)
        guards = _Guards()
        guards.install()
        try:
            r = L.add_listing_to_index({"address": "Blk 123 Demo Ave 1"}, "6590000000")
        finally:
            guards.remove()
        self.assertEqual(guards.violations, [])
        self.assertTrue(r.get("success"), r)
        with open(os.path.join(self.tmp, "listing-index.json")) as f:
            self.assertEqual(len(json.load(f)["listings"]), 1)

    def test_matching_config_redirects_under_sandbox(self):
        import importlib
        import landlord_tenant_matcher as M
        importlib.reload(M)
        self.assertTrue(M.MATCHING_CONFIG.startswith(self.tmp),
                        f"matcher still resolves {M.MATCHING_CONFIG!r} under a sandbox")


class TestDormantMatcherModulesUnsetAreUnchanged(unittest.TestCase):
    """The mirror image: with the env vars unset, both modules resolve the historical real
    paths byte for byte -- production is unchanged by the seal fix."""

    def test_paths_unset_equal_real(self):
        import importlib
        saved = {k: os.environ.pop(k, None) for k in
                 ("WA_INTAKE_SANDBOX", "WA_INTAKE_STATE_ROOT", "WA_INTAKE_DATA_ROOT",
                  "WA_INTAKE_MSG_DB")}
        try:
            import ninety_nine_co_lister as L
            import landlord_tenant_matcher as M
            importlib.reload(L)
            importlib.reload(M)
            self.assertEqual(L.LISTING_INDEX, os.path.expanduser(
                "~/.claude/state/listing-templates/listing-index.json"))
            self.assertEqual(L._listing_index(), L.LISTING_INDEX)
            self.assertEqual(M.MATCHING_CONFIG, os.path.expanduser(
                "~/.claude/state/listing-templates/matching-config.json"))
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main(verbosity=2)
