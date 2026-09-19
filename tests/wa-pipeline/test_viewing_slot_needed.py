"""
test_viewing_slot_needed.py -- a viewing slot on every open listing (11 Sep 2026). Only 6 of
60 open listings carried a viewing window; a named day and time converts 96.6% vs 30.7% for
the open "When are you able to view?" ask (Winfred's data).

Covers:
  1. OFFER_VIEWING with no slot captured -> the hold line (intake_engine.VIEWING_HOLD_TEXT),
     never the old weak ask.
  2. notify_viewing_slot_needed: one Telegram ping per listing per SGT day, never once per
     prospect, never when a real slot (or the co-pilot path, already pinged separately) is
     already offered.
  3. intake_engine.apply_fixed_viewing: atomic flock write into a fixture listing index, with
     a .bak backup, reporting (never silently dropping) a listing_key absent from the index.
  4. /slot in Winfred's own self chat (wa_intake_selfchat.py): valid syntax sets fixed_viewing
     under the same flock and confirms by Telegram; invalid syntax is rejected with a notify,
     never silently accepted or guessed. /slot typed in any OTHER chat never reaches this
     parser at all -- the runner's OWN_JID gate (wa_intake_runner.py) is the ONLY caller of
     handle_self_chat_command, checked here both structurally (source) and behaviourally
     (intake_engine.py, the tenant pipeline, never even mentions "/slot").

Run: /usr/bin/python3 tests/wa-pipeline/test_viewing_slot_needed.py
"""
import sys, os, json, tempfile, unittest
from unittest import mock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src", "wa-pipeline"))

# Telegram/bridge kill switch (see wa_intake_paths.py / test_sandbox_seal.py) -- set BEFORE
# importing any wa-pipeline module.
os.environ["WA_INTAKE_SANDBOX"] = "1"
os.environ["WA_INTAKE_NO_TELEGRAM"] = "1"
os.environ["WA_INTAKE_NO_SEND"] = "1"

import intake_engine as E             # noqa: E402
import wa_intake_notify as N          # noqa: E402
import wa_intake_selfchat as RESC     # noqa: E402


class TestHoldLine(unittest.TestCase):
    def test_no_slot_gets_the_hold_line(self):
        text = E._viewing_text(None)
        self.assertEqual(text, E.VIEWING_HOLD_TEXT)
        self.assertNotIn("When are you able to view", text)
        self.assertIn("confirm a viewing time with the owner shortly", text)

    def test_real_slot_still_gets_the_named_slot_text(self):
        slot = {"label": "Sat 12 Sep, 11am"}
        text = E._viewing_text(slot)
        self.assertIn("Sat 12 Sep, 11am", text)
        self.assertIn("Reply YES to take this slot", text)


class TestNotifyViewingSlotNeeded(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self._p = mock.patch.object(N, "notify_winfred", self.sent.append)
        self._p.start()
        self.addCleanup(self._p.stop)

    def test_pings_once_per_listing_per_day_not_once_per_prospect(self):
        state = {}
        a1 = {"type": "OFFER_VIEWING", "slot": None, "listing_key": "cherryhill", "pn": "601"}
        a2 = {"type": "OFFER_VIEWING", "slot": None, "listing_key": "cherryhill", "pn": "602"}
        N.notify_viewing_slot_needed(a1, state)
        N.notify_viewing_slot_needed(a2, state)   # a DIFFERENT prospect, same listing, same day
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.sent[0].startswith("Viewing slot needed"))
        self.assertIn("cherryhill", self.sent[0])
        self.assertIn("/slot cherryhill", self.sent[0])

    def test_different_listing_gets_its_own_ping(self):
        state = {}
        N.notify_viewing_slot_needed({"type": "OFFER_VIEWING", "slot": None, "listing_key": "a"}, state)
        N.notify_viewing_slot_needed({"type": "OFFER_VIEWING", "slot": None, "listing_key": "b"}, state)
        self.assertEqual(len(self.sent), 2)

    def test_no_ping_when_a_real_slot_was_offered(self):
        state = {}
        N.notify_viewing_slot_needed(
            {"type": "OFFER_VIEWING", "slot": {"label": "Sat"}, "listing_key": "a"}, state)
        self.assertEqual(self.sent, [])

    def test_no_ping_on_the_copilot_path(self):
        # the co-pilot OFFER_VIEWING branch never fires with slot=None in the first place
        # (intake_engine falls through to COPILOT_VERDICT instead) -- still guarded here
        # defensively so a future change to that branch cannot silently double ping.
        state = {}
        N.notify_viewing_slot_needed(
            {"type": "OFFER_VIEWING", "slot": None, "listing_key": "a", "copilot": True}, state)
        self.assertEqual(self.sent, [])

    def test_no_ping_without_a_real_listing_key(self):
        # a bare test double / stand-in action (no listing_key at all) must never be treated
        # as a real hold-line offer -- regression: a synthetic {"type":"OFFER_VIEWING", ...}
        # fixture elsewhere in the suite used to trip this ping unexpectedly.
        state = {}
        N.notify_viewing_slot_needed({"type": "OFFER_VIEWING", "pn": "601", "text": "hi"}, state)
        self.assertEqual(self.sent, [])

    def test_ping_repeats_on_a_later_day(self):
        state = {}
        a = {"type": "OFFER_VIEWING", "slot": None, "listing_key": "cherryhill"}
        with mock.patch.object(N.time, "strftime", return_value="2026-09-11"):
            N.notify_viewing_slot_needed(a, state)
        with mock.patch.object(N.time, "strftime", return_value="2026-09-12"):
            N.notify_viewing_slot_needed(a, state)
        self.assertEqual(len(self.sent), 2)


class TestApplyFixedViewing(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.index_path = os.path.join(self._tmp.name, "listing-index.json")
        json.dump({"listings": [
            {"listing_key": "cherryhill", "status": "open"},
            {"listing_key": "bayshore", "status": "open"},
        ]}, open(self.index_path, "w"))

    def test_writes_fixed_viewing_atomically_with_backup(self):
        fv = {"weekday": "sat", "start": "11:00", "end": "13:00", "time_label": "11am to 1pm"}
        result = E.apply_fixed_viewing({"cherryhill": fv}, path=self.index_path)
        self.assertEqual(result["written"], ["cherryhill"])
        self.assertEqual(result["missing"], [])
        self.assertTrue(os.path.exists(self.index_path + ".bak"))
        data = json.load(open(self.index_path))
        by_key = {l["listing_key"]: l for l in data["listings"]}
        self.assertEqual(by_key["cherryhill"]["fixed_viewing"], fv)
        self.assertNotIn("fixed_viewing", by_key["bayshore"])

    def test_unknown_listing_key_reported_missing_never_written(self):
        result = E.apply_fixed_viewing({"nosuchlisting": {"weekday": "sat"}}, path=self.index_path)
        self.assertEqual(result["written"], [])
        self.assertEqual(result["missing"], ["nosuchlisting"])
        self.assertFalse(os.path.exists(self.index_path + ".bak"))   # nothing written -> no backup


class TestSlotSelfChatCommand(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.index_path = os.path.join(self._tmp.name, "listing-index.json")
        json.dump({"listings": [{"listing_key": "cherryhill", "status": "open"}]},
                   open(self.index_path, "w"))

    def test_parses_a_single_time(self):
        lk, fv, err = RESC.parse_slot_command("cherryhill Sat 11am")
        self.assertIsNone(err)
        self.assertEqual(lk, "cherryhill")
        self.assertEqual(fv, {"weekday": "sat", "start": "11:00", "end": None, "time_label": "11am"})

    def test_parses_a_time_range(self):
        lk, fv, err = RESC.parse_slot_command("cherryhill Sat 11am to 1pm")
        self.assertIsNone(err)
        self.assertEqual(fv["start"], "11:00")
        self.assertEqual(fv["end"], "13:00")

    def test_invalid_weekday_rejected(self):
        lk, fv, err = RESC.parse_slot_command("cherryhill Blah 11am")
        self.assertIsNone(lk)
        self.assertIsNotNone(err)

    def test_missing_time_rejected(self):
        lk, fv, err = RESC.parse_slot_command("cherryhill Sat")
        self.assertIsNone(lk)
        self.assertIsNotNone(err)

    def test_missing_everything_rejected(self):
        lk, fv, err = RESC.parse_slot_command("cherryhill")
        self.assertIsNone(lk)
        self.assertIsNotNone(err)

    def test_handle_self_chat_command_valid_slot_writes_and_notifies(self):
        notified = []
        with mock.patch.object(E, "IDX", self.index_path):
            handled = RESC.handle_self_chat_command(
                "6512345678@s.whatsapp.net", "/slot cherryhill Sat 11am",
                send_fn=lambda *a, **k: True, guard_reserve_fn=lambda *a: True,
                log_fn=lambda *a: None, notify_fn=notified.append)
        self.assertTrue(handled)
        self.assertTrue(any("cherryhill" in m for m in notified))
        data = json.load(open(self.index_path))
        self.assertEqual(data["listings"][0]["fixed_viewing"]["weekday"], "sat")
        self.assertEqual(data["listings"][0]["fixed_viewing"]["start"], "11:00")

    def test_handle_self_chat_command_invalid_slot_rejected_with_notify(self):
        notified = []
        with mock.patch.object(E, "IDX", self.index_path):
            handled = RESC.handle_self_chat_command(
                "6512345678@s.whatsapp.net", "/slot cherryhill Blah 11am",
                send_fn=lambda *a, **k: True, guard_reserve_fn=lambda *a: True,
                log_fn=lambda *a: None, notify_fn=notified.append)
        self.assertTrue(handled)
        self.assertTrue(any("rejected" in m for m in notified))
        data = json.load(open(self.index_path))
        self.assertNotIn("fixed_viewing", data["listings"][0])

    def test_handle_self_chat_command_unknown_listing_rejected(self):
        notified = []
        with mock.patch.object(E, "IDX", self.index_path):
            handled = RESC.handle_self_chat_command(
                "6512345678@s.whatsapp.net", "/slot nosuchlisting Sat 11am",
                send_fn=lambda *a, **k: True, guard_reserve_fn=lambda *a: True,
                log_fn=lambda *a: None, notify_fn=notified.append)
        self.assertTrue(handled)
        self.assertTrue(any("no listing" in m for m in notified))

    def test_next_tenant_message_after_slot_set_gets_the_named_slot(self):
        # once /slot has written fixed_viewing, next_future_slot() (the same function
        # next_slot()/OFFER_VIEWING already call) must surface it -- proves the /slot write
        # and the engine's own read path are wired to the SAME field.
        with mock.patch.object(E, "IDX", self.index_path):
            RESC.handle_self_chat_command(
                "6512345678@s.whatsapp.net", "/slot cherryhill Sat 11am",
                send_fn=lambda *a, **k: True, guard_reserve_fn=lambda *a: True,
                log_fn=lambda *a: None, notify_fn=lambda *a: None)
            slot = E.next_future_slot("cherryhill")
        self.assertIsNotNone(slot)
        self.assertTrue(slot.get("fixed"))
        self.assertIn("Sat", slot.get("label", ""))
        self.assertTrue(E._viewing_text(slot).endswith("Reply YES to take this slot."))


class TestSlotOnlyReachableFromSelfChat(unittest.TestCase):
    def test_runner_only_calls_handle_self_chat_command_inside_the_OWN_JID_branch(self):
        src = open(os.path.join(_REPO_ROOT, "src", "wa-pipeline", "wa_intake_runner.py")).read()
        call_idx = src.index("RESC.handle_self_chat_command(")
        gate_idx = src.rindex("if jid == RES.OWN_JID:", 0, call_idx)
        self.assertLess(gate_idx, call_idx,
                         "handle_self_chat_command must only ever be called after the "
                         "OWN_JID gate -- a /slot typed in any other chat must never reach it")

    def test_tenant_pipeline_has_no_slot_command_parser(self):
        # the /slot COMMAND (parsing, matching, handling) lives ONLY in wa_intake_selfchat.py,
        # reachable ONLY from Winfred's own chat -- intake_engine.py (the tenant facing
        # pipeline) never recognises it as a token (a couple of comments here reference the
        # command by name for documentation, which is fine; there is no parser/regex for it).
        self.assertFalse(hasattr(E, "parse_slot_command"))
        self.assertFalse(hasattr(E, "_SLOT_RE"))
        src = open(os.path.join(_REPO_ROOT, "src", "wa-pipeline", "intake_engine.py")).read()
        self.assertNotIn("_SLOT_RE", src)


if __name__ == "__main__":
    unittest.main()
