"""
test_draft_worker.py -- wa_intake_draft_worker.py (9 Sep 2026 merge redo, item 3: drafts
off the critical path). Exercises the ACTUAL background job mechanics end to end against a
small fake claude-guard script (a real subprocess, not a python level mock) -- spawn_request
must return before the child finishes, sweep() must collect a finished job's result, kill
and report a job stuck past its wall budget, and enforce the per-chat/per-tick caps.

Run: /usr/bin/python3 tests/wa-pipeline/test_draft_worker.py
"""
import sys, os, time, json, stat, tempfile, shutil, unittest
from unittest import mock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src", "wa-pipeline"))

os.environ["WA_INTAKE_NO_TELEGRAM"] = "1"
os.environ["WA_INTAKE_NO_SEND"] = "1"

import wa_intake_draft_worker as WORKER

# A fake claude-guard: reads the SAME argv shape as the real binary (-p PROMPT --model ...
# --strict-mcp-config ... --mcp-config ... --output-format json) and behaves according to a
# marker word inside the prompt itself, so a single script covers every scenario below
# without any env var plumbing.
_FAKE_SCRIPT = """#!/usr/bin/env python3
import sys, json, time
args = sys.argv[1:]
prompt = args[args.index("-p") + 1] if "-p" in args else ""
if "SLEEP_FOREVER" in prompt:
    time.sleep(3600)
elif "ERROR_EXIT" in prompt:
    sys.exit(1)
elif "IS_ERROR" in prompt:
    print(json.dumps({"is_error": True, "result": "boom"}))
elif "EMPTY_RESULT" in prompt:
    print(json.dumps({"result": "", "is_error": False}))
else:
    print(json.dumps({"result": "DRAFT for: " + prompt, "is_error": False}))
"""


class DraftWorkerTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wa-draftworker-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.fake_bin = os.path.join(self.tmp, "fake-claude-guard")
        with open(self.fake_bin, "w") as f:
            f.write(_FAKE_SCRIPT)
        st = os.stat(self.fake_bin)
        os.chmod(self.fake_bin, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        self._patches = [
            mock.patch.object(WORKER, "HAIKU_BIN", self.fake_bin),
            mock.patch.object(WORKER, "PENDING", os.path.join(self.tmp, "pending.json")),
            mock.patch.object(WORKER, "RESULTS_DIR", os.path.join(self.tmp, "results")),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)
        WORKER.reset_tick_budget()

    def _wait_until_finished(self, timeout=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            items = WORKER._load_pending()
            if not items or not any(WORKER._pid_alive(r.get("pid")) for r in items):
                return
            time.sleep(0.05)

    @staticmethod
    def _handlers(results, timeouts):
        return {"resume_draft": {
            "on_result": lambda rec, text, err: results.append((rec, text, err)),
            "on_timeout": lambda rec: timeouts.append(rec),
        }}


class TestSpawnReturnsImmediately(DraftWorkerTestBase):
    def test_spawn_request_does_not_block_on_a_slow_child(self):
        started = time.time()
        status = WORKER.spawn_request("resume_draft", "pn1", "jid1", "SLEEP_FOREVER", {"k": "v"})
        elapsed = time.time() - started
        self.assertEqual(status, "spawned")
        self.assertLess(elapsed, 2.0, "spawn_request must return long before a slow child finishes")
        pending = WORKER._load_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["pn"], "pn1")
        self.assertEqual(pending[0]["context"], {"k": "v"})
        # cleanup: kill the sleeping child so it does not outlive the test process
        try:
            os.kill(pending[0]["pid"], 9)
        except OSError:
            pass


class TestSweepCollectsFinishedJob(DraftWorkerTestBase):
    def test_success_result_is_read_and_retired(self):
        status = WORKER.spawn_request("resume_draft", "pn2", "jid2", "hello world", {})
        self.assertEqual(status, "spawned")
        self._wait_until_finished()
        results, timeouts = [], []
        WORKER.sweep(self._handlers(results, timeouts))
        self.assertEqual(len(results), 1)
        rec, text, err = results[0]
        self.assertIsNone(err)
        self.assertEqual(text, "DRAFT for: hello world")
        self.assertEqual(rec["pn"], "pn2")
        self.assertEqual(WORKER._load_pending(), [])   # retired, never swept twice
        self.assertFalse(os.listdir(WORKER._results_dir()))  # result file cleaned up

    def test_is_error_result_reports_err(self):
        WORKER.spawn_request("resume_draft", "pn3", "jid3", "IS_ERROR", {})
        self._wait_until_finished()
        results, timeouts = [], []
        WORKER.sweep(self._handlers(results, timeouts))
        self.assertEqual(len(results), 1)
        _, text, err = results[0]
        self.assertIsNone(text)
        self.assertIn("is_error", err)

    def test_empty_result_reports_err(self):
        WORKER.spawn_request("resume_draft", "pn4", "jid4", "EMPTY_RESULT", {})
        self._wait_until_finished()
        results, timeouts = [], []
        WORKER.sweep(self._handlers(results, timeouts))
        self.assertEqual(len(results), 1)
        _, text, err = results[0]
        self.assertIsNone(text)
        self.assertEqual(err, "empty result")

    def test_nonzero_exit_with_no_stdout_reports_err(self):
        WORKER.spawn_request("resume_draft", "pn5", "jid5", "ERROR_EXIT", {})
        self._wait_until_finished()
        results, timeouts = [], []
        WORKER.sweep(self._handlers(results, timeouts))
        self.assertEqual(len(results), 1)
        _, text, err = results[0]
        self.assertIsNone(text)
        self.assertIsNotNone(err)

    def test_still_running_within_budget_is_left_pending(self):
        WORKER.spawn_request("resume_draft", "pn6", "jid6", "SLEEP_FOREVER", {})
        results, timeouts = [], []
        WORKER.sweep(self._handlers(results, timeouts))   # swept immediately, still running
        self.assertEqual(results, [])
        self.assertEqual(timeouts, [])
        self.assertEqual(len(WORKER._load_pending()), 1)  # left for a later sweep
        for r in WORKER._load_pending():
            try:
                os.kill(r["pid"], 9)
            except OSError:
                pass


class TestWallBudgetTimeout(DraftWorkerTestBase):
    def test_stuck_job_is_killed_and_on_timeout_fires(self):
        with mock.patch.object(WORKER, "WALL_BUDGET_SEC", 1):
            WORKER.spawn_request("resume_draft", "pn7", "jid7", "SLEEP_FOREVER", {"why": "test"})
            time.sleep(1.3)
            results, timeouts = [], []
            WORKER.sweep(self._handlers(results, timeouts))
        self.assertEqual(results, [])
        self.assertEqual(len(timeouts), 1)
        self.assertEqual(timeouts[0]["context"], {"why": "test"})
        self.assertEqual(WORKER._load_pending(), [])   # retired, not retried


class TestInFlightAndTickCap(DraftWorkerTestBase):
    def test_second_spawn_same_chat_is_in_flight(self):
        st1 = WORKER.spawn_request("resume_draft", "pnX", "jidX", "SLEEP_FOREVER", {})
        st2 = WORKER.spawn_request("resume_draft", "pnX", "jidY", "SLEEP_FOREVER", {})
        self.assertEqual(st1, "spawned")
        self.assertEqual(st2, "in_flight")
        self.assertEqual(len(WORKER._load_pending()), 1)   # the second one never spawned

    def test_different_chat_is_not_blocked_by_another_in_flight(self):
        WORKER.spawn_request("resume_draft", "pnA", "jidA", "SLEEP_FOREVER", {})
        st2 = WORKER.spawn_request("resume_draft", "pnB", "jidB", "SLEEP_FOREVER", {})
        self.assertEqual(st2, "spawned")

    def test_cap_reached_after_max_spawns_per_tick(self):
        for i in range(WORKER.MAX_SPAWNS_PER_TICK):
            st = WORKER.spawn_request("resume_draft", f"pn{i}", f"jid{i}", "SLEEP_FOREVER", {})
            self.assertEqual(st, "spawned")
        over = WORKER.spawn_request("resume_draft", "pn_over", "jid_over", "SLEEP_FOREVER", {})
        self.assertEqual(over, "cap_reached")

    def test_reset_tick_budget_gives_a_fresh_cap_next_tick(self):
        for i in range(WORKER.MAX_SPAWNS_PER_TICK):
            WORKER.spawn_request("resume_draft", f"pnr{i}", f"jidr{i}", "SLEEP_FOREVER", {})
        self.assertEqual(WORKER.spawn_request("resume_draft", "pnr_over", "jidr_over",
                                              "SLEEP_FOREVER", {}), "cap_reached")
        WORKER.reset_tick_budget()
        self.assertEqual(WORKER.spawn_request("resume_draft", "pnr_new", "jidr_new",
                                              "SLEEP_FOREVER", {}), "spawned")


class TestSandboxNeverSpawns(DraftWorkerTestBase):
    def test_sandboxed_short_circuits_before_any_subprocess(self):
        saved = os.environ.get("WA_INTAKE_SANDBOX")
        os.environ["WA_INTAKE_SANDBOX"] = "1"
        try:
            status = WORKER.spawn_request("resume_draft", "pnS", "jidS", "hello", {})
        finally:
            if saved is None:
                os.environ.pop("WA_INTAKE_SANDBOX", None)
            else:
                os.environ["WA_INTAKE_SANDBOX"] = saved
        self.assertEqual(status, "sandboxed")
        self.assertEqual(WORKER._load_pending(), [])


class TestUnknownKindIsDropped(DraftWorkerTestBase):
    def test_sweep_with_no_handler_for_the_kind_drops_it_silently(self):
        WORKER.spawn_request("owner_extract", "pnU", "jidU", "hello", {})
        self._wait_until_finished()
        # sweep with handlers for a DIFFERENT kind only -- must not raise, and must retire
        # the unhandled record rather than leak it forever.
        WORKER.sweep({"resume_draft": {"on_result": lambda *a: None, "on_timeout": lambda *a: None}})
        self.assertEqual(WORKER._load_pending(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
