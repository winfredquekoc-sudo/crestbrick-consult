"""
test_telegram_kill_switch.py -- proves the incident (9 Sep 2026: a sandbox harness run
reached Winfred's real phone with synthetic scenario numbers/listing keys) cannot recur.

Root cause was a monkeypatch aimed at the wrong binding (wa_intake_runner.notify_winfred,
a SEPARATE `from wa_intake_notify import notify_winfred` import, not the name
notify_for_action/notify_winfred_coalesced actually call, which resolve in wa_intake_notify's
OWN globals). The fix moved the kill switch to the one physical send call each channel ever
makes (wa_intake_notify._tg_send for Telegram, wa_intake_send._send for the WhatsApp bridge)
so it cannot be bypassed by patching the wrong name again.

This file proves that from the outside: every module under src/wa-pipeline must import
cleanly with the switch already on, and every notify_*/*_alert* function in
wa_intake_notify.py must be driveable with a dummy payload -- under WA_INTAKE_NO_TELEGRAM=1
-- without EVER reaching subprocess.run (the only way _tg_send calls telegram_send.sh, the
only path to api.telegram.org in this pipeline). Same proof for wa_intake_send._send against
requests.post (the only way it reaches the WhatsApp bridge).

Run: /usr/bin/python3 tests/wa-pipeline/test_telegram_kill_switch.py
"""
import sys, os, re, importlib, pkgutil, tempfile, unittest
from unittest import mock

os.environ["WA_INTAKE_NO_TELEGRAM"] = "1"
os.environ["WA_INTAKE_NO_SEND"] = "1"

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_WA_DIR = os.path.join(_REPO_ROOT, "src", "wa-pipeline")
sys.path.insert(0, _WA_DIR)


def _module_names():
    return sorted(name for _, name, ispkg in pkgutil.iter_modules([_WA_DIR]) if not ispkg)


class TestEveryModuleImportsUnderTheKillSwitch(unittest.TestCase):
    """Every .py file under src/wa-pipeline must import cleanly with the kill switch env
    vars already set (as every real entry point -- runner, harness, replay, tests -- now
    sets them at import time, before anything else)."""

    def test_every_module_imports(self):
        failures = []
        for name in _module_names():
            try:
                importlib.import_module(name)
            except Exception as e:
                failures.append((name, repr(e)))
        self.assertEqual(failures, [], f"modules failed to import: {failures}")


class TestNoTelegramCallEscapesTheKillSwitch(unittest.TestCase):
    """subprocess.run is the ONLY way wa_intake_notify._tg_send ever reaches
    telegram_send.sh (and hence api.telegram.org). Patched here to raise if called at all --
    then every notify_*/*_alert* function is driven with a dummy payload and must complete
    without reaching it."""

    def setUp(self):
        import wa_intake_notify as N
        self.N = N
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # redirect every real file path this module might otherwise touch -- belt and
        # suspenders on top of the env var switch, so this test can never leave a stray
        # file under the real ~/.claude/state/listing-templates even if the switch had a gap.
        for attr in ("COALESCE_FILE", "NOTIFY_Q", "ALLOW_FILE", "MUTED_LOG", "PREVIEW"):
            self._patch(N, attr, os.path.join(self._tmp.name, attr.lower() + ".tmp"))
        self._patch(N, "STATE_DIR", self._tmp.name)
        self._subprocess_patch = mock.patch(
            "subprocess.run",
            side_effect=AssertionError(
                "subprocess.run reached telegram_send.sh -- kill switch bypassed"))
        self._subprocess_patch.start()
        self.addCleanup(self._subprocess_patch.stop)

    def _patch(self, target, attr, value):
        p = mock.patch.object(target, attr, value)
        p.start()
        self.addCleanup(p.stop)

    def test_notify_winfred_direct(self):
        self.N.notify_winfred("dummy P0 test message")

    def test_notify_winfred_coalesced(self):
        self.N.notify_winfred_coalesced("6590000099", "dummy coalesced message")
        self.N.notify_winfred_coalesced("6590000099", "dummy coalesced message 2")
        self.N._flush_stale_coalesce_windows()

    def test_alert_hourly(self):
        self.N._alert_hourly("dummy-kill-switch-test-key", "dummy hourly alert")

    def test_notify_stale_backfill(self):
        self.N.notify_stale_backfill(3, {"6590000099": 3}, 6)

    def test_notify_for_action_every_branch(self):
        state = {"conversations": {"6590000099": {
            "profile": {"name": "Test Tenant"}, "listing_key": "lk-test"}}}
        dummy_actions = (
            {"notify": True, "type": "SEND_BUYER_FORM", "pn": "6590000099",
             "property_type": "hdb"},
            {"notify": True, "type": "VIEWING_TIME_PROPOSED", "pn": "6590000099",
             "when": "tmr 3pm"},
            {"notify": True, "type": "CONFIRM_VIEWING", "pn": "6590000099"},
            {"notify": True, "type": "ANSWER_QUESTION", "pn": "6590000099",
             "question": "dummy question?"},
            {"notify": True, "type": "COPILOT_VERDICT", "pn": "6590000099",
             "verdict": "QUALIFIED", "why": []},
            {"notify": True, "type": "OFFER_VIEWING", "pn": "6590000099", "copilot": True},
            {"notify": True, "type": "OFFER_VIEWING", "pn": "6590000099",
             "hot_matches": ["lk-other"]},
            {"notify": True, "type": "SUGGEST_ALT", "pn": "6590000099",
             "listing_key": "lk-other"},
            {"notify": True, "type": "CAP_REACHED", "pn": "6590000099"},
            {"notify": True, "type": "AUTO_CLOSED", "pn": "6590000099",
             "quote": "found a place already"},
            {"notify": True, "type": "SEND_SUPPLY_FORM", "pn": "6590000099",
             "supply": "landlord"},
            {"notify": True, "type": "BUYER_COMPLETE", "pn": "6590000099",
             "summary": "dummy summary"},
            {"notify": True, "type": "SUPPLY_INFO_NUDGE", "pn": "6590000099",
             "reason": "dummy reason"},
            {"notify": True, "type": "REDIRECT", "pn": "6590000099",
             "reason": "unit gone"},
            {"notify": True, "category2_code": "AIRCON", "pn": "6590000099",
             "question": "aircon ok?", "text": "yes, working"},
        )
        for a in dummy_actions:
            with self.subTest(kind=a.get("type") or a.get("category2_code")):
                self.N.notify_for_action(a, state)


class TestNoBridgeCallEscapesTheKillSwitch(unittest.TestCase):
    """requests.post is the ONLY way wa_intake_send._send ever reaches the WhatsApp bridge.
    Patched here to raise if called at all -- _send must still report success (True) without
    ever reaching it, purely off the WA_INTAKE_NO_SEND env var."""

    def test_send_suppressed_without_reaching_requests_post(self):
        import wa_intake_send as S
        with mock.patch(
                "requests.post",
                side_effect=AssertionError(
                    "requests.post reached the bridge -- kill switch bypassed")):
            ok = S._send("6590000099", "dummy bridge message")
        self.assertTrue(ok)


class TestTelegramSenderIsWaIntakeNotifyOnly(unittest.TestCase):
    """Grep guard: within the wa-intake pipeline (src/wa-pipeline), no .py file ever
    references the raw Telegram Bot API ('api.telegram.org' / 'sendMessage') -- the pipeline
    only ever shells out to telegram_send.sh (~/.claude/bin, outside this repo), and only
    from wa_intake_notify._tg_send (see TestNoTelegramCallEscapesTheKillSwitch for proof
    that call itself is gated). An empty result here means the single-sender doctrine holds
    for Telegram the same way it already holds for WhatsApp -- if this ever fails, a second
    Telegram sender has been added somewhere in the pipeline, which the doctrine forbids.
    (wa_post_processor.py and gsc-weekly-digest.py under scripts/ are unrelated automations
    outside this doctrine's scope and are not scanned.)"""

    _PATTERN = re.compile(r"api\.telegram\.org|sendMessage")

    def _scan(self, root):
        hits = []
        for dirpath, _dirnames, filenames in os.walk(root):
            if "__pycache__" in dirpath:
                continue
            for fn in filenames:
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(dirpath, fn)
                try:
                    with open(path, encoding="utf-8") as f:
                        text = f.read()
                except Exception:
                    continue
                if self._PATTERN.search(text):
                    hits.append(path)
        return hits

    def test_no_raw_telegram_api_reference_in_wa_pipeline(self):
        hits = self._scan(_WA_DIR)
        rel = [os.path.relpath(h, _REPO_ROOT) for h in hits]
        self.assertEqual(rel, [],
                          f"unexpected Telegram API reference(s) in wa-pipeline: {rel}")


if __name__ == "__main__":
    unittest.main()
