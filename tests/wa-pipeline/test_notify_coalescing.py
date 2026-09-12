"""
test_notify_coalescing.py -- merge review (9 Sep 2026, four branch intake merge). The
attack fix rounds raised Winfred's Telegram notify volume from ~5 to ~27 pings a day, almost
all of it routine FLAG_HUMAN style flags (redirect/house_gate declines, held-by-cap replies).
notify_winfred_coalesced() (wa_intake_notify.py) holds a chat to ONE immediate ping per 30
minute window, rolling any further ones for that SAME chat into a single digest sent when the
window ends (_flush_stale_coalesce_windows, called once per runner tick). A dispute,
protected attribute, or legal threat marker in the message -- or an explicit bypass=True --
always goes out immediately, uncoalesced.

Run: /usr/bin/python3 tests/wa-pipeline/test_notify_coalescing.py
"""
import sys, os, time, json, tempfile, unittest
from unittest import mock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src", "wa-pipeline"))

# Telegram/bridge kill switch (incident, 9 Sep 2026 merge redo -- a sandbox harness run
# reached Winfred's real phone). Set BEFORE importing any wa-pipeline module: belt and
# suspenders alongside the per-test mock.patch calls, on top of the physical choke-point
# checks _tg_send/_send now do on their own. See wa_intake_notify.py's docstring.
os.environ["WA_INTAKE_NO_TELEGRAM"] = "1"
os.environ["WA_INTAKE_NO_SEND"] = "1"

import wa_intake_notify as N


class TestNotifyCoalescing(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._patches = []
        self.sent = []
        self._patch(N, "notify_winfred", self.sent.append)
        self._patch(N, "COALESCE_FILE", os.path.join(self._tmp.name, "coalesce.json"))

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def _patch(self, target, attr, value):
        p = mock.patch.object(target, attr, value)
        p.start()
        self._patches.append(p)

    def test_first_ping_for_a_chat_goes_out_immediately(self):
        N.notify_winfred_coalesced("6590000001", "FLAG_HUMAN: on lk1 — house_gate:E1")
        self.assertEqual(self.sent, ["FLAG_HUMAN: on lk1 — house_gate:E1"])

    def test_second_ping_same_chat_inside_window_is_held_not_sent(self):
        N.notify_winfred_coalesced("6590000001", "first flag")
        N.notify_winfred_coalesced("6590000001", "second flag")
        self.assertEqual(self.sent, ["first flag"])   # second one held, not an immediate ping

    def test_different_chats_never_share_a_window(self):
        N.notify_winfred_coalesced("6590000001", "chat A flag")
        N.notify_winfred_coalesced("6590000002", "chat B flag")
        self.assertEqual(self.sent, ["chat A flag", "chat B flag"])   # both immediate

    def test_held_pings_land_as_one_digest_when_the_window_ends(self):
        with mock.patch.object(N.time, "time", return_value=1000.0):
            N.notify_winfred_coalesced("6590000001", "first flag")
            N.notify_winfred_coalesced("6590000001", "second flag")
            N.notify_winfred_coalesced("6590000001", "third flag")
        self.assertEqual(self.sent, ["first flag"])
        # window ends 30 minutes later
        with mock.patch.object(N.time, "time", return_value=1000.0 + N.COALESCE_WINDOW_SEC + 1):
            N._flush_stale_coalesce_windows()
        self.assertEqual(len(self.sent), 2)
        digest = self.sent[1]
        self.assertIn("2 more update", digest)
        self.assertIn("second flag", digest)
        self.assertIn("third flag", digest)

    def test_window_with_nothing_held_produces_no_extra_ping_on_flush(self):
        with mock.patch.object(N.time, "time", return_value=1000.0):
            N.notify_winfred_coalesced("6590000001", "only flag")
        with mock.patch.object(N.time, "time", return_value=1000.0 + N.COALESCE_WINDOW_SEC + 1):
            N._flush_stale_coalesce_windows()
        self.assertEqual(self.sent, ["only flag"])   # no digest, nothing was held

    def test_a_new_flag_after_the_window_ends_opens_a_fresh_window(self):
        with mock.patch.object(N.time, "time", return_value=1000.0):
            N.notify_winfred_coalesced("6590000001", "first flag")
        with mock.patch.object(N.time, "time", return_value=1000.0 + N.COALESCE_WINDOW_SEC + 1):
            N.notify_winfred_coalesced("6590000001", "later flag")
        self.assertEqual(self.sent, ["first flag", "later flag"])   # both immediate, fresh window

    def test_bypass_true_always_goes_out_immediately(self):
        N.notify_winfred_coalesced("6590000001", "first flag")
        N.notify_winfred_coalesced("6590000001", "urgent flag", bypass=True)
        self.assertEqual(self.sent, ["first flag", "urgent flag"])

    def test_dispute_marker_auto_bypasses_even_without_the_flag(self):
        N.notify_winfred_coalesced("6590000001", "first flag")
        N.notify_winfred_coalesced("6590000001",
            "FLAG_HUMAN: on lk1 — new message on a closed conversation: that is discrimination")
        self.assertEqual(len(self.sent), 2)   # both immediate, no holding

    def test_no_pn_always_goes_out_immediately(self):
        N.notify_winfred_coalesced(None, "unattributed flag")
        N.notify_winfred_coalesced(None, "another one")
        self.assertEqual(self.sent, ["unattributed flag", "another one"])

    def test_is_dispute_or_p0_detector(self):
        self.assertTrue(N._is_dispute_or_p0("sensitive content (protected attribute / dispute / legal)"))
        self.assertTrue(N._is_dispute_or_p0("that's discrimination you know"))
        self.assertFalse(N._is_dispute_or_p0("house_gate:E1"))
        self.assertFalse(N._is_dispute_or_p0(""))


class TestRunnerWiring(unittest.TestCase):
    """The runner's own generic FLAG_HUMAN catch all (wa_intake_notify.notify_for_action,
    split out 9 Sep 2026 merge review) and the DAILY_CAP_SKIP notify
    (wa_intake_send.daily_cap_should_skip + the runner's own call site) both route through
    notify_winfred_coalesced now, not the raw notify_winfred -- confirmed by source scan
    (behavioural coverage is TestNotifyCoalescing above and test_time_reply_cap.py /
    test_runner_guards.py's existing DAILY_CAP_SKIP coverage)."""
    def setUp(self):
        wap = os.path.join(_REPO_ROOT, "src", "wa-pipeline")
        self.runner_src = open(os.path.join(wap, "wa_intake_runner.py")).read()
        self.notify_src = open(os.path.join(wap, "wa_intake_notify.py")).read()
        self.send_src = open(os.path.join(wap, "wa_intake_send.py")).read()

    def test_generic_notify_catch_all_uses_coalesced(self):
        i = self.notify_src.index('elif a.get("notify"):')
        block = self.notify_src[i:i + 400]
        self.assertIn("notify_winfred_coalesced(", block)

    def test_daily_cap_skip_uses_coalesced(self):
        # the decision (does this action get held back) lives in wa_intake_send.py; the
        # actual notify_winfred_coalesced call is at the runner's own call site.
        self.assertIn("daily_cap_should_skip", self.send_src)
        marker = "_cap_skip = daily_cap_should_skip("
        block = self.runner_src[self.runner_src.index(marker):
                                self.runner_src.index(marker) + 500]
        self.assertIn("notify_winfred_coalesced(", block)

    def test_flush_called_once_per_tick(self):
        self.assertIn("_flush_stale_coalesce_windows()", self.runner_src)

    def test_viewing_time_proposed_still_direct_not_coalesced(self):
        i = self.notify_src.index('a["type"] == "VIEWING_TIME_PROPOSED"')
        block = self.notify_src[i:i + 300]
        self.assertIn("notify_winfred(", block)
        self.assertNotIn("notify_winfred_coalesced(", block)


if __name__ == "__main__":
    unittest.main(verbosity=2)
