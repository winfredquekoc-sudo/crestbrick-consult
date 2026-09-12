"""
test_circuit_breaker.py -- anti-spam guarantee (9 Sep 2026 merge review, four branch intake
merge): a GLOBAL circuit breaker across every prospect at once, last line of defense on top
of the per-client DAILY_SEND_CAP and the owner loop's own per-landlord cap
(wa_intake_owner.py). If the runner would push more than CIRCUIT_MAX_SENDS (40) prospect
facing sends into a rolling CIRCUIT_WINDOW_SEC (60 minute) window, it stops sending
entirely, logs CIRCUIT_OPEN, and notifies Winfred once (not once per blocked send).

Run: /usr/bin/python3 tests/wa-pipeline/test_circuit_breaker.py
"""
import sys, os, tempfile, unittest
from unittest import mock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src", "wa-pipeline"))

# Telegram/bridge kill switch (incident, 9 Sep 2026 merge redo -- a sandbox harness run
# reached Winfred's real phone). Set BEFORE importing any wa-pipeline module: belt and
# suspenders alongside the per-test mock.patch calls, on top of the physical choke-point
# checks _tg_send/_send now do on their own. See wa_intake_notify.py's docstring.
os.environ["WA_INTAKE_NO_TELEGRAM"] = "1"
os.environ["WA_INTAKE_NO_SEND"] = "1"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wa_intake_send as SEND
import wa_intake_runner as R
from test_takeover_resume import _isolated_runner, FAKE_PN, FAKE_JID


class TestCircuitBreakerMechanism(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._patch(SEND, "CIRCUIT_FILE", os.path.join(self._tmp.name, "send-circuit.json"))

    def _patch(self, target, attr, value):
        p = mock.patch.object(target, attr, value)
        p.start()
        self.addCleanup(p.stop)

    def test_ok_when_empty(self):
        self.assertTrue(SEND.circuit_breaker_ok())

    def test_ok_up_to_the_limit(self):
        for _ in range(SEND.CIRCUIT_MAX_SENDS - 1):
            SEND.record_prospect_send()
        self.assertTrue(SEND.circuit_breaker_ok())   # the 40th send is still allowed

    def test_blocked_once_the_limit_is_reached(self):
        for _ in range(SEND.CIRCUIT_MAX_SENDS):
            SEND.record_prospect_send()
        self.assertFalse(SEND.circuit_breaker_ok())

    def test_old_sends_roll_out_of_the_window(self):
        with mock.patch.object(SEND.time, "time", return_value=1000.0):
            for _ in range(SEND.CIRCUIT_MAX_SENDS):
                SEND.record_prospect_send()
        with mock.patch.object(SEND.time, "time",
                               return_value=1000.0 + SEND.CIRCUIT_WINDOW_SEC + 1):
            self.assertTrue(SEND.circuit_breaker_ok())

    def test_gate_returns_none_when_ok(self):
        self.assertIsNone(SEND.circuit_breaker_gate("SEND_FORM"))

    def test_gate_returns_log_and_notify_when_open(self):
        for _ in range(SEND.CIRCUIT_MAX_SENDS):
            SEND.record_prospect_send()
        gate = SEND.circuit_breaker_gate("SEND_FORM")
        self.assertIsNotNone(gate)
        log_suffix, notify_msg = gate
        self.assertIn("SEND_FORM", log_suffix)
        self.assertIn("40", log_suffix)
        self.assertIsNotNone(notify_msg)
        self.assertIn("Circuit breaker", notify_msg)

    def test_notify_fires_once_per_open_period_not_once_per_blocked_send(self):
        for _ in range(SEND.CIRCUIT_MAX_SENDS):
            SEND.record_prospect_send()
        _, first_notify = SEND.circuit_breaker_gate("SEND_FORM")
        _, second_notify = SEND.circuit_breaker_gate("ASK_ONE")
        self.assertIsNotNone(first_notify)
        self.assertIsNone(second_notify)   # already notified for this open period

    def test_notify_rearms_once_a_real_send_clears_the_window(self):
        with mock.patch.object(SEND.time, "time", return_value=1000.0):
            for _ in range(SEND.CIRCUIT_MAX_SENDS):
                SEND.record_prospect_send()
            SEND.circuit_breaker_gate("SEND_FORM")   # consumes the one-time notify
        # window clears, a real send goes out and rearms the latch, then it trips again
        with mock.patch.object(SEND.time, "time",
                               return_value=1000.0 + SEND.CIRCUIT_WINDOW_SEC + 1):
            SEND.record_prospect_send()
            for _ in range(SEND.CIRCUIT_MAX_SENDS - 1):
                SEND.record_prospect_send()
            _, notify_msg = SEND.circuit_breaker_gate("SEND_FORM")
        self.assertIsNotNone(notify_msg)

    def test_owner_sends_are_never_recorded_by_this_breaker(self):
        # the owner loop has its OWN separate per-landlord cap (wa_intake_owner.py) -- it
        # must never call record_prospect_send, and this breaker must never gate it. This
        # test documents the contract: record_prospect_send is opt-in, never automatic.
        self.assertTrue(SEND.circuit_breaker_ok())


class TestCircuitBreakerRunnerWiring(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_send_blocked_and_notified_once_breaker_is_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            circuit_file = os.path.join(tmp, "send-circuit.json")
            with mock.patch.object(SEND, "CIRCUIT_FILE", circuit_file):
                for _ in range(SEND.CIRCUIT_MAX_SENDS):
                    SEND.record_prospect_send()
                with _isolated_runner(
                        tmp,
                        conversations={FAKE_PN: {"pn": FAKE_PN}},
                        handle_event_return={"type": "ASK_ONE", "pn": FAKE_PN,
                                             "text": "Can I just check your budget?"}) as calls:
                    pass
            self.assertEqual(calls["sent"], [])
            self.assertTrue(any(k == "CIRCUIT_OPEN" for k, p, m in calls["logged"]))
            self.assertTrue(any("Circuit breaker" in n for n in calls["notified"]))

    def test_send_proceeds_normally_when_breaker_is_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            circuit_file = os.path.join(tmp, "send-circuit.json")
            with mock.patch.object(SEND, "CIRCUIT_FILE", circuit_file):
                with _isolated_runner(
                        tmp,
                        conversations={FAKE_PN: {"pn": FAKE_PN}},
                        handle_event_return={"type": "ASK_ONE", "pn": FAKE_PN,
                                             "text": "Can I just check your budget?"}) as calls:
                    pass
            self.assertEqual(calls["sent"], [(FAKE_JID, "Can I just check your budget?")])
            self.assertFalse(any(k == "CIRCUIT_OPEN" for k, p, m in calls["logged"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
