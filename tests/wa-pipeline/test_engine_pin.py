"""
test_engine_pin.py -- the integrity pin (wa_intake_pin.py) that guards against the shape of
incident that hit production 9 Sep 2026: another session's git reset silently reverted a
deploy, and nothing noticed. digest()/uncommitted()/head_sha() all shell out to git and stat
real files, so every test here builds its OWN throwaway git repo (SRC/REPO monkeypatched to
it) rather than touching this checkout's actual history.

Run: /usr/bin/python3 tests/wa-pipeline/test_engine_pin.py
"""
import sys, os, json, tempfile, shutil, subprocess, unittest
from unittest import mock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src", "wa-pipeline"))

os.environ["WA_INTAKE_NO_TELEGRAM"] = "1"
os.environ["WA_INTAKE_NO_SEND"] = "1"

import wa_intake_pin as PIN
import wa_intake_paths as P


def _run_git(repo, *args):
    r = subprocess.run(("git", "-C", repo) + args, capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout


class _FakeCheckout(unittest.TestCase):
    """A throwaway git repo with the exact ENGINE_FILES layout, so PIN.digest()/
    PIN.uncommitted()/PIN.head_sha() operate on it instead of the real repo. PIN's file
    (its own state-root path) is likewise sandboxed to a tempdir so no test ever reads or
    writes the LIVE engine-pin.json."""

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="wa-pin-repo-")
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        self.src = os.path.join(self.repo, "src", "wa-pipeline")
        os.makedirs(self.src)
        _run_git(self.repo, "init", "-q")
        _run_git(self.repo, "config", "user.email", "test@example.com")
        _run_git(self.repo, "config", "user.name", "test")
        for name in PIN.ENGINE_FILES:
            with open(os.path.join(self.src, name), "w") as f:
                f.write("# " + name + " v1\n")
        _run_git(self.repo, "add", "-A")
        _run_git(self.repo, "commit", "-q", "-m", "initial")

        self._patches = [
            mock.patch.object(PIN, "REPO", self.repo),
            mock.patch.object(PIN, "SRC", self.src),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

        # sandbox the pin FILE itself (separate from the fake checkout above) so a bug in
        # a test can never touch ~/.claude/state/listing-templates/engine-pin.json.
        self.state_tmp = tempfile.mkdtemp(prefix="wa-pin-state-")
        self.addCleanup(shutil.rmtree, self.state_tmp, ignore_errors=True)
        self._saved_env = {k: os.environ.get(k) for k in
                           ("WA_INTAKE_SANDBOX", "WA_INTAKE_STATE_ROOT", "WA_INTAKE_MSG_DB")}
        os.environ["WA_INTAKE_SANDBOX"] = "1"
        os.environ["WA_INTAKE_STATE_ROOT"] = self.state_tmp
        os.environ["WA_INTAKE_MSG_DB"] = self.state_tmp
        P.sandbox_init()
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _touch(self, name, content):
        with open(os.path.join(self.src, name), "w") as f:
            f.write(content)


class TestNoPinProceeds(_FakeCheckout):
    def test_no_pin_file_yet(self):
        ok, reason, detail = PIN.verify()
        self.assertFalse(ok)
        self.assertEqual(reason, "no_pin")
        self.assertFalse(os.path.exists(PIN._pin_path()))


class TestOkProceeds(_FakeCheckout):
    def test_pin_then_verify_ok(self):
        rc = PIN.do_pin()
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(PIN._pin_path()))
        ok, reason, detail = PIN.verify()
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")

    def test_do_pin_refuses_over_a_dirty_tree(self):
        self._touch(PIN.ENGINE_FILES[0], "# dirty, never committed\n")
        rc = PIN.do_pin()
        self.assertEqual(rc, 1)
        self.assertFalse(os.path.exists(PIN._pin_path()))

    def test_do_pin_refuses_when_a_file_is_missing(self):
        os.remove(os.path.join(self.src, PIN.ENGINE_FILES[0]))
        rc = PIN.do_pin()
        self.assertEqual(rc, 1)


class TestEngineDriftHoldsAndAlarmsOnce(_FakeCheckout):
    def test_drift_after_pin_is_detected(self):
        self.assertEqual(PIN.do_pin(), 0)
        # simulate the incident: a file on disk changes AFTER the pin, with no new commit --
        # exactly what a silent revert/checkout/half-applied edit looks like.
        self._touch(PIN.ENGINE_FILES[0], "# reverted / tampered content\n")
        ok, reason, detail = PIN.verify()
        self.assertFalse(ok)
        self.assertEqual(reason, "engine_drift")
        self.assertIn(PIN.ENGINE_FILES[0], detail["drifted"])

    def test_drift_holds_every_call_not_just_the_first(self):
        # "holds" == the runner's send path never proceeds while the condition persists;
        # this is the pin's half of that contract -- verify() must keep failing closed on
        # every single call, not just transiently on the first one after the tamper.
        self.assertEqual(PIN.do_pin(), 0)
        self._touch(PIN.ENGINE_FILES[0], "# tampered\n")
        for _ in range(3):
            ok, reason, _ = PIN.verify()
            self.assertFalse(ok)
            self.assertEqual(reason, "engine_drift")

    def test_runner_alerts_hourly_not_once_per_tick(self):
        # the runner's own hook calls _alert_hourly("pin", ...) on every failed tick; that
        # primitive is what turns "holds forever" into "alarms ONCE per hour" rather than
        # once per 60s tick. Prove the rate limit itself here; the runner wiring that calls
        # it is proven in TestRunnerPinHook below (merge review, 9 Sep 2026: this comment
        # used to point at a test_runner_guards.py pin-hook test that never existed, so the
        # runner half of the contract was asserted nowhere).
        import wa_intake_notify as NOTIFY
        calls = []
        mark_dir = tempfile.mkdtemp(prefix="wa-pin-mark-")
        self.addCleanup(shutil.rmtree, mark_dir, ignore_errors=True)
        with mock.patch.object(NOTIFY, "notify_winfred", lambda msg: calls.append(msg)):
            with mock.patch("os.path.expanduser", side_effect=lambda p: (
                    os.path.join(mark_dir, os.path.basename(p))
                    if p.startswith("~/.claude/state/listing-templates/.alert-") else p)):
                NOTIFY._alert_hourly("pin", "first")
                NOTIFY._alert_hourly("pin", "second, same tick shape")
        self.assertEqual(len(calls), 1, "second call within the hour must be suppressed")


class TestEngineFileMissing(_FakeCheckout):
    def test_missing_file_before_no_pin_check(self):
        os.remove(os.path.join(self.src, PIN.ENGINE_FILES[-1]))
        ok, reason, detail = PIN.verify()
        self.assertFalse(ok)
        self.assertEqual(reason, "engine_file_missing")
        self.assertIn(PIN.ENGINE_FILES[-1], detail["missing"])


class TestPinnedStateUncommitted(_FakeCheckout):
    def test_pin_taken_by_hand_over_dirty_tree_then_files_restored(self):
        # A pin recorded once, then the SAME bytes exist on disk again but the tree is no
        # longer clean vs HEAD (e.g. a file was `git rm --cached` but left on disk, or
        # re-added but not committed) -- the digest matches the blessed hashes, but
        # uncommitted() is non empty, so verify() must still refuse: matching bytes alone
        # is not proof the state is a real reviewed commit.
        self.assertEqual(PIN.do_pin(), 0)
        target = PIN.ENGINE_FILES[0]
        with open(os.path.join(self.src, target)) as f:
            content = f.read()
        _run_git(self.repo, "rm", "-q", "--cached", os.path.join("src", "wa-pipeline", target))
        with open(os.path.join(self.src, target), "w") as f:
            f.write(content)  # restore identical bytes -- digest() will match the pin again
        ok, reason, detail = PIN.verify()
        self.assertFalse(ok)
        self.assertEqual(reason, "pinned_state_uncommitted")


class TestEngineFilesCoverage(unittest.TestCase):
    """The extension this merge made: every module wa_intake_runner.py imports at import
    time must be pinned, not just intake_engine.py -- an unpinned edit to any of them is
    exactly as live-facing."""

    def test_every_runner_import_time_module_is_pinned(self):
        runner_path = os.path.join(_REPO_ROOT, "src", "wa-pipeline", "wa_intake_runner.py")
        with open(runner_path) as f:
            src = f.read()
        import re
        head = src.split("\ndef run", 1)[0]  # import-time section only
        imported = set(re.findall(r"^(?:import|from)\s+(wa_intake_\w+)", head, re.M))
        pinned = set(n[:-3] for n in PIN.ENGINE_FILES)  # strip ".py"
        missing = imported - pinned
        self.assertEqual(missing, set(),
                         f"modules imported by the runner but not pinned: {missing}")

    def test_pin_guards_itself(self):
        self.assertIn("wa_intake_pin.py", PIN.ENGINE_FILES)


class TestRunnerPinHook(unittest.TestCase):
    """The runner half of the contract the pin exists for. verify() failing is worthless if
    wa_intake_runner.run() does not actually stop on it, so drive run() itself:
      engine_drift -> returns before the send path is reached, one "pin" alarm;
      no_pin       -> proceeds past the gate (first deploy, nothing pinned yet).
    Every root is redirected to a throwaway tempdir first, so the live state dir, the live
    watermark and the real messages.db are never opened (STEP 0 seal, see
    test_sandbox_seal.py)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wa-pin-runner-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self._saved = {k: os.environ.get(k) for k in
                       ("WA_INTAKE_SANDBOX", "WA_INTAKE_STATE_ROOT", "WA_INTAKE_DATA_ROOT",
                        "WA_INTAKE_MSG_DB")}

        def _restore():
            for k, v in self._saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.addCleanup(_restore)
        os.environ["WA_INTAKE_SANDBOX"] = "1"
        for k in ("WA_INTAKE_STATE_ROOT", "WA_INTAKE_DATA_ROOT", "WA_INTAKE_MSG_DB"):
            os.environ[k] = self.tmp
        P.sandbox_init()
        import wa_intake_runner
        self.R = wa_intake_runner

    def _tick(self, verify_ret):
        alerts, sent = [], []
        with mock.patch.object(PIN, "verify", lambda: verify_ret), \
             mock.patch.object(self.R, "_alert_hourly", lambda k, m: alerts.append((k, m))), \
             mock.patch.object(self.R, "_send", lambda pn, t: sent.append((pn, t)) or True):
            self.R.run()
        return alerts, sent

    def test_drift_holds_the_tick_and_alarms_once(self):
        alerts, sent = self._tick((False, "engine_drift", {"drifted": ["intake_engine.py"]}))
        self.assertEqual(sent, [], "a drifted engine must not reach the send path")
        self.assertEqual([k for k, _ in alerts], ["pin"])
        self.assertIn("HOLDING", alerts[0][1])

    def test_no_pin_proceeds_past_the_gate(self):
        alerts, sent = self._tick((False, "no_pin", {"hint": "run --pin"}))
        self.assertNotIn("pin", [k for k, _ in alerts],
                         "a missing pin only logs; it must never hold the pipeline")


if __name__ == "__main__":
    unittest.main(verbosity=2)
