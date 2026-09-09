"""
test_takeover_resume.py -- unittest coverage for takeover resume (Feature 1, Winfred 8 Sep
2026): "for the hand reply to stop after I hand reply ... if I don't reply in the next 5
minutes after a hand reply you can help me come up with a reply after reading the entire
chat." AUTO SEND only fixed engine templates and category 1 facts; DRAFT everything else.

Run: /usr/bin/python3 tests/wa-pipeline/test_takeover_resume.py
"""
import sys, os, sqlite3, time, json, datetime, contextlib, tempfile, unittest
from unittest import mock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src", "wa-pipeline"))

# Telegram/bridge kill switch (incident, 9 Sep 2026 merge redo -- a sandbox harness run
# reached Winfred's real phone). Set BEFORE importing any wa-pipeline module: belt and
# suspenders alongside the per-test mock.patch calls, on top of the physical choke-point
# checks _tg_send/_send now do on their own. See wa_intake_notify.py's docstring.
os.environ["WA_INTAKE_NO_TELEGRAM"] = "1"
os.environ["WA_INTAKE_NO_SEND"] = "1"

import intake_engine as E
import wa_intake_resume as RES
import wa_intake_selfchat as RESC
import wa_intake_runner as R
import wa_intake_notify as NOTIFY
import wa_intake_draft as DRAFT
import wa_intake_send as SEND
import wa_intake_paths as PATHS
import wa_intake_draft_worker as WORKER


def _mem_db(rows):
    """rows: list of (rowid, chat_jid, is_from_me) newest-agnostic; builds a throwaway
    sqlite db with the one column set resume_reason_blocked/fetch_transcript actually
    touch (id mirrors rowid, matching the real bridge's PRAGMA-discovered id column)."""
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE messages (rowid INTEGER PRIMARY KEY, id TEXT, "
                "chat_jid TEXT, is_from_me INTEGER, content TEXT, timestamp TEXT)")
    # id is TEXT holding a hex WhatsApp message id, exactly as the real bridge stores it --
    # the old fixture mirrored rowid as an INTEGER, which hid a live ordering bug.
    for i, (rowid, jid, ifm) in enumerate(rows):
        con.execute("INSERT INTO messages (rowid, id, chat_jid, is_from_me, content, timestamp) "
                    "VALUES (?, ?, ?, ?, ?, '2026-09-08 10:00:00+08:00')",
                    (rowid, "%016X" % (0xF000000000000000 - i * 0x111111111111111), jid,
                     int(ifm), "msg%d" % rowid))
    con.commit()
    return con


class TestResumeTriggerTiming(unittest.TestCase):
    JID = "6598881111@s.whatsapp.net"

    def _rec(self, **fields):
        rec = {"manual_takeover": True, "human_takeover": True}
        rec.update(fields)
        return rec

    def test_4m59s_not_yet_eligible(self):
        con = _mem_db([(1, self.JID, 0)])
        hand_ts = "2026-09-08 10:00:00+08:00"
        inbound_ts = "2026-09-08 10:04:59+08:00"
        rec = self._rec(last_hand_reply_ts=hand_ts)
        reason = RES.resume_reason_blocked(con, "id", self.JID, rec, 1, inbound_ts)
        self.assertIsNotNone(reason)
        self.assertIn("300s", reason.replace(".0s", "s") if "s" in reason else reason)

    def test_5m01s_is_eligible(self):
        con = _mem_db([(1, self.JID, 0)])
        hand_ts = "2026-09-08 10:00:00+08:00"
        inbound_ts = "2026-09-08 10:05:01+08:00"
        rec = self._rec(last_hand_reply_ts=hand_ts)
        reason = RES.resume_reason_blocked(con, "id", self.JID, rec, 1, inbound_ts)
        self.assertIsNone(reason)

    def test_outbound_after_inbound_blocks(self):
        # rowid 1 = the qualifying inbound, rowid 2 = Winfred's own later reply in the SAME
        # chat -- he has already answered it, so resume must not fire again.
        con = _mem_db([(1, self.JID, 0), (2, self.JID, 1)])
        rec = self._rec(last_hand_reply_ts="2026-09-08 10:00:00+08:00")
        reason = RES.resume_reason_blocked(con, "id", self.JID, rec, 1,
                                           "2026-09-08 10:06:00+08:00")
        self.assertEqual(reason, "Winfred already answered this inbound")

    def test_outbound_in_a_different_chat_does_not_block(self):
        other_jid = "6598882222@s.whatsapp.net"
        con = _mem_db([(1, self.JID, 0), (2, other_jid, 1)])
        rec = self._rec(last_hand_reply_ts="2026-09-08 10:00:00+08:00")
        reason = RES.resume_reason_blocked(con, "id", self.JID, rec, 1,
                                           "2026-09-08 10:06:00+08:00")
        self.assertIsNone(reason)

    def test_new_hand_reply_restarts_the_clock(self):
        con = _mem_db([(1, self.JID, 0)])
        # Winfred replied again at 10:10; the inbound at 10:14 (only 4 min after the NEWER
        # reply) must not be eligible even though it is >5 min after the ORIGINAL reply.
        rec = self._rec(last_hand_reply_ts="2026-09-08 10:10:00+08:00")
        reason = RES.resume_reason_blocked(con, "id", self.JID, rec, 1,
                                           "2026-09-08 10:14:00+08:00")
        self.assertIsNotNone(reason)
        reason2 = RES.resume_reason_blocked(con, "id", self.JID, rec, 1,
                                            "2026-09-08 10:16:00+08:00")
        self.assertIsNone(reason2)

    def test_no_hand_reply_ts_never_eligible(self):
        con = _mem_db([(1, self.JID, 0)])
        rec = self._rec()   # no last_hand_reply_ts at all
        reason = RES.resume_reason_blocked(con, "id", self.JID, rec, 1,
                                           "2026-09-08 10:30:00+08:00")
        self.assertIsNotNone(reason)

    def test_not_under_takeover_never_eligible(self):
        con = _mem_db([(1, self.JID, 0)])
        rec = {"manual_takeover": False, "human_takeover": False,
               "last_hand_reply_ts": "2026-09-08 10:00:00+08:00"}
        reason = RES.resume_reason_blocked(con, "id", self.JID, rec, 1,
                                           "2026-09-08 10:30:00+08:00")
        self.assertIsNotNone(reason)

    def test_supply_side_record_never_eligible(self):
        con = _mem_db([(1, self.JID, 0)])
        rec = self._rec(last_hand_reply_ts="2026-09-08 10:00:00+08:00", supply_kind="landlord")
        reason = RES.resume_reason_blocked(con, "id", self.JID, rec, 1,
                                           "2026-09-08 10:30:00+08:00")
        self.assertEqual(reason, "landlord/seller onboarding, not a tenant resume")

    def test_no_record_never_eligible(self):
        con = _mem_db([(1, self.JID, 0)])
        reason = RES.resume_reason_blocked(con, "id", self.JID, None, 1,
                                           "2026-09-08 10:30:00+08:00")
        self.assertIsNotNone(reason)


class TestAllowListEnforcement(unittest.TestCase):
    def test_allowed_types_never_need_a_draft(self):
        for t in RES.ALLOWED_RESUME_TYPES:
            with self.subTest(type=t):
                self.assertFalse(RES.needs_draft({"type": t, "text": "hi"}))

    def test_redirect_always_needs_a_draft(self):
        self.assertTrue(RES.needs_draft({"type": "REDIRECT", "text": "some redirect copy"}))

    def test_suggest_alt_always_needs_a_draft(self):
        self.assertTrue(RES.needs_draft({"type": "SUGGEST_ALT", "text": "alt copy"}))

    def test_send_buyer_form_always_needs_a_draft(self):
        self.assertTrue(RES.needs_draft({"type": "SEND_BUYER_FORM", "text": "buyer form"}))

    def test_answer_question_with_text_is_allowed(self):
        # a category 1 fact answer (_tenant_fact_answer returned something)
        self.assertFalse(RES.needs_draft({"type": "ANSWER_QUESTION", "text": "no cooking allowed"}))

    def test_answer_question_without_text_needs_a_draft(self):
        # the fact table could not answer it -> Winfred's own judgement call
        self.assertTrue(RES.needs_draft({"type": "ANSWER_QUESTION", "text": None,
                                         "question": "can you do 1400?"}))

    def test_answer_question_with_text_but_no_bound_listing_needs_a_draft(self):
        """Review fix: needs_draft used to return on ANSWER_QUESTION BEFORE the established
        gate ever looked at rec_before, so a category 1 fact answer could auto-send landlord
        copy into a chat with listing_key still None. With a rec_before snapshot given, a
        fact answer now always requires a bound listing on top of being established."""
        rec_before = {"pn": FAKE_PN, "form_sent": True, "listing_key": None,
                     "listing_key_source": "inbound",
                     "profile": {"name": "Tester", "nationality": "Singaporean"},
                     "first_inbound_text": "is this room available",
                     "outbound_before_first_inbound": False}
        a = {"type": "ANSWER_QUESTION", "text": "no cooking allowed"}
        with mock.patch.object(RES.E, "excluded_reason", return_value=None):
            self.assertTrue(RES.needs_draft(a, rec_before))

    def test_no_action_at_all_needs_a_draft(self):
        self.assertTrue(RES.needs_draft(None))

    def test_end_to_end_redirect_from_engine_needs_a_draft(self):
        """The actual engine, in resume mode, on a policy-excluded profile really does emit
        REDIRECT -- confirms the allow list is exercised against a REAL action, not just a
        hand rolled dict."""
        st = {"version": 1, "conversations": {}}
        jid = "6598883333@s.whatsapp.net"
        pn = E.resolve_pn(jid)
        rec = E._rec(st, pn)
        rec["manual_takeover"] = True; rec["human_takeover"] = True
        rec["form_sent"] = True
        rec["profile"] = {"nationality": "India", "name": "Test", "no_of_pax": 1,
                          "budget": 1500, "gender": "Male", "move_in_date": "1 oct",
                          "lease_term_months": 12, "ethnicity": "Indian", "pass_type": "SC"}
        ev = {"jid": jid, "msg_id": "1", "text": "any update", "is_from_me": False,
              "resume": True}
        a = E.handle_event(st, ev)
        self.assertEqual(a["type"], "REDIRECT")
        self.assertTrue(RES.needs_draft(a))

    def test_end_to_end_send_form_from_engine_is_allowed(self):
        """The engine, bypassed via resume on a manual_takeover record with an incomplete
        profile, really does emit SEND_FORM for a plain tenant enquiry -- allowed straight
        through."""
        st = {"version": 1, "conversations": {}}
        jid = "6598884444@s.whatsapp.net"
        pn = E.resolve_pn(jid)
        rec = E._rec(st, pn)
        rec["manual_takeover"] = True; rec["human_takeover"] = True
        ev = {"jid": jid, "msg_id": "1", "text": "is this still available?",
              "is_from_me": False, "resume": True}
        a = E.handle_event(st, ev)
        self.assertEqual(a["type"], "SEND_FORM")
        self.assertFalse(RES.needs_draft(a))
        # confirm the SAME record, asked again WITHOUT resume, stays silent (copilot path,
        # profile still incomplete) -- resume changed nothing about the latch itself
        st2 = {"version": 1, "conversations": {}}
        rec2 = E._rec(st2, pn)
        rec2["manual_takeover"] = True; rec2["human_takeover"] = True
        ev2 = dict(ev, resume=False, msg_id="2")
        self.assertIsNone(E.handle_event(st2, ev2))


class TestOneSendPerInbound(unittest.TestCase):
    def test_handle_event_returns_at_most_one_action(self):
        """handle_event's own contract (unchanged by resume): a single dict or None, never a
        list -- so the runner's per row loop can never fan one inbound out into more than
        one send."""
        st = {"version": 1, "conversations": {}}
        jid = "6598885555@s.whatsapp.net"
        rec = E._rec(st, E.resolve_pn(jid))
        rec["manual_takeover"] = True; rec["human_takeover"] = True
        ev = {"jid": jid, "msg_id": "1", "text": "is this still available?",
              "is_from_me": False, "resume": True}
        a = E.handle_event(st, ev)
        self.assertIsInstance(a, dict)


class TestRunnerStructural(unittest.TestCase):
    """Source level checks (same style as test_runner_guards.py): the resume decision must
    sit BEFORE the existing hard send safeguards, so an allow listed resume action still
    passes quiet hours / daily cap / 5 day guard / manual takeover / guard reserve exactly
    like every other action -- resume only ever loosens the manual_takeover early return
    inside the engine, never any runner side guard."""

    def setUp(self):
        path = os.path.join(_REPO_ROOT, "src", "wa-pipeline", "wa_intake_runner.py")
        self.src = open(path).read()

    def test_resume_decision_precedes_hard_send_safeguards(self):
        i_resume = self.src.index('ev["resume"] = True')
        i_guard = self.src.index("HARD SEND SAFEGUARDS")
        self.assertLess(i_resume, i_guard)

    def test_needs_draft_short_circuits_before_hard_send_safeguards(self):
        i_draft = self.src.index("RES.process_draft_needed(")
        i_guard = self.src.index("HARD SEND SAFEGUARDS")
        self.assertLess(i_draft, i_guard)

    def test_own_jid_never_reaches_ecom_landlord_pipeline(self):
        self.assertIn('if jid == RES.OWN_JID:', self.src)

    def test_quiet_hours_still_the_very_first_gate_in_run(self):
        i_quiet = self.src.index("if _quiet_hours():")
        i_resume = self.src.index('ev["resume"] = True')
        self.assertLess(i_quiet, i_resume)


class TestSelfChatCommand(unittest.TestCase):
    """FIX 2 (Opus review, 9 Sep 2026) hardened /send to re-check excluded_reason on every
    call -- every test in this class runs against a clean (not excluded) contact by default,
    matching the old permissive behaviour these tests were written against; the excluded
    path itself gets its own dedicated test in TestSendCommandHardening below."""

    def setUp(self):
        self._tmp = f"/tmp/test-drafts-{os.getpid()}-{time.time_ns()}.jsonl"
        self._orig = RES.DRAFTS_FILE
        RES.DRAFTS_FILE = self._tmp
        self.sent = []
        self.guard_calls = []
        self.con = _mem_db([])   # clean, no dispute language -- B2 recheck must pass through
        self._exc_patch = mock.patch.object(E, "excluded_reason", return_value=None)
        self._exc_patch.start()

    def tearDown(self):
        self._exc_patch.stop()
        RES.DRAFTS_FILE = self._orig
        try: os.remove(self._tmp)
        except OSError: pass

    def _send_fn(self, jid, text):
        self.sent.append((jid, text)); return True

    def _guard_fn(self, jid):
        self.guard_calls.append(jid); return True

    def test_send_marks_sent_and_calls_send_fn(self):
        did = RES.new_draft("6598880000", "6598880000@lid", "test-listing", "hello there")
        handled = RESC.handle_self_chat_command(
            RES.OWN_JID, f"/send {did}", self._send_fn, self._guard_fn, lambda *a: None,
            con=self.con)
        self.assertTrue(handled)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0], ("6598880000@lid", "hello there"))
        self.assertEqual(RES.find_draft(did)["status"], "sent")

    def test_drop_marks_dropped_never_sends(self):
        did = RES.new_draft("6598880001", "6598880001@lid", "test-listing", "hello there")
        handled = RESC.handle_self_chat_command(
            RES.OWN_JID, f"/drop {did}", self._send_fn, self._guard_fn, lambda *a: None)
        self.assertTrue(handled)
        self.assertEqual(len(self.sent), 0)
        self.assertEqual(RES.find_draft(did)["status"], "dropped")

    def test_expired_draft_refused(self):
        did = RES.new_draft("6598880002", "6598880002@lid", "test-listing", "hello there")
        # force it old
        items = RES._load_drafts()
        for d in items:
            if d["id"] == did:
                d["created"] = time.time() - RES.DRAFT_EXPIRY_SEC - 60
        RES._rewrite_drafts(items)
        handled = RESC.handle_self_chat_command(
            RES.OWN_JID, f"/send {did}", self._send_fn, self._guard_fn, lambda *a: None)
        self.assertTrue(handled)
        self.assertEqual(len(self.sent), 0)
        self.assertEqual(RES.find_draft(did)["status"], "expired")

    def test_already_sent_draft_cannot_be_sent_twice(self):
        did = RES.new_draft("6598880003", "6598880003@lid", "test-listing", "hello there")
        RESC.handle_self_chat_command(RES.OWN_JID, f"/send {did}", self._send_fn,
                                     self._guard_fn, lambda *a: None, con=self.con)
        self.assertEqual(len(self.sent), 1)
        RESC.handle_self_chat_command(RES.OWN_JID, f"/send {did}", self._send_fn,
                                     self._guard_fn, lambda *a: None, con=self.con)
        self.assertEqual(len(self.sent), 1)   # not sent again

    def test_unknown_draft_id_is_ignored(self):
        handled = RESC.handle_self_chat_command(
            RES.OWN_JID, "/send deadbeef", self._send_fn, self._guard_fn, lambda *a: None)
        self.assertTrue(handled)
        self.assertEqual(len(self.sent), 0)

    def test_non_command_text_is_not_handled_here(self):
        handled = RESC.handle_self_chat_command(
            RES.OWN_JID, "just a normal note to myself", self._send_fn, self._guard_fn,
            lambda *a: None)
        self.assertFalse(handled)

    def test_guard_reserve_failure_blocks_the_send(self):
        did = RES.new_draft("6598880004", "6598880004@lid", "test-listing", "hello there")
        handled = RESC.handle_self_chat_command(
            RES.OWN_JID, f"/send {did}", self._send_fn, lambda jid: False, lambda *a: None,
            con=self.con)
        self.assertTrue(handled)
        self.assertEqual(len(self.sent), 0)
        self.assertEqual(RES.find_draft(did)["status"], "pending")

    def test_send_typed_outside_self_chat_is_ignored_by_the_runner(self):
        """The command handler itself is only ever invoked by the runner for OWN_JID rows
        (see test_own_jid_never_reaches...); this asserts the runner source actually gates
        on that jid before calling it, so a '/send' typed in a prospect's own chat is never
        even offered to the command handler."""
        path = os.path.join(_REPO_ROOT, "src", "wa-pipeline", "wa_intake_runner.py")
        src = open(path).read()
        i_jid_check = src.index('if jid == RES.OWN_JID:')
        i_handle_call = src.index('RESC.handle_self_chat_command(')
        self.assertLess(i_jid_check, i_handle_call)


class TestHaikuFailurePath(unittest.TestCase):
    # call_haiku itself lives in wa_intake_draft.py now (9 Sep 2026 merge review split) --
    # its "subprocess.run(...)" resolves in THAT module's own globals, so the mock must
    # patch DRAFT.subprocess, not RES.subprocess (which is a separate, unrelated binding
    # here). An earlier cut of this test patched the wrong module and silently fell through
    # to the REAL claude-guard binary on every run -- caught only because it actually
    # returned a live model reply instead of the stubbed one.
    def test_timeout_reports_error(self):
        import subprocess as sp
        with mock.patch.object(DRAFT, "subprocess") as m:
            m.TimeoutExpired = sp.TimeoutExpired
            m.run.side_effect = sp.TimeoutExpired(cmd="x", timeout=25)
            text, err = RES.call_haiku("hello")
        self.assertIsNone(text)
        self.assertEqual(err, "timeout")

    def test_nonzero_exit_reports_error(self):
        with mock.patch.object(DRAFT, "subprocess") as m:
            m.run.return_value = mock.Mock(returncode=1, stdout="", stderr="boom")
            text, err = RES.call_haiku("hello")
        self.assertIsNone(text)
        self.assertIn("exit 1", err)

    def test_success_returns_result_text(self):
        with mock.patch.object(DRAFT, "subprocess") as m:
            m.run.return_value = mock.Mock(
                returncode=0, stdout='{"result": "sure, happy to help", "is_error": false}')
            text, err = RES.call_haiku("hello")
        self.assertIsNone(err)
        self.assertEqual(text, "sure, happy to help")

    def test_process_draft_needed_spawns_instead_of_blocking(self):
        """item 3 (9 Sep 2026 merge redo): process_draft_needed no longer calls call_haiku
        at all -- it spawns a background request through wa_intake_draft_worker and returns
        immediately. Never blocks the tick waiting on claude-guard."""
        con = _mem_db([(1, "6598886666@lid", 0)])
        logged = []
        rec = {"profile": {"name": "Test"}, "listing_key": "test-listing", "last_inbound": "hmm"}
        captured = {}

        def fake_spawn(kind, key, jid, prompt, context=None):
            captured.update(kind=kind, key=key, jid=jid, context=context)
            return "spawned"

        with mock.patch.object(WORKER, "spawn_request", side_effect=fake_spawn):
            RES.process_draft_needed(con, "id", "6598886666@lid", "6598886666", rec, None,
                                     lambda m: None, lambda k, p, m: logged.append((k, p, m)))
        self.assertEqual(captured["kind"], "resume_draft")
        self.assertEqual(captured["key"], "6598886666")
        self.assertEqual(captured["context"]["listing_key"], "test-listing")
        self.assertTrue(any(k == "RESUME_DRAFT_SPAWNED" for k, p, m in logged))

    def test_finish_resume_draft_saves_draft_on_success(self):
        notified = []
        logged = []
        record = {"pn": "6598887777", "jid": "6598887777@lid",
                 "context": {"name": "Test", "listing_key": "test-listing",
                            "last_inbound": "hmm", "transcript_tail": []}}
        tmp = f"/tmp/test-drafts-succ-{os.getpid()}.jsonl"
        orig = RES.DRAFTS_FILE
        RES.DRAFTS_FILE = tmp
        try:
            RES.finish_resume_draft(record, "sure, come by anytime", None,
                                    notified.append, lambda k, p, m: logged.append((k, p, m)))
            self.assertEqual(len(notified), 1)
            self.assertIn("/send", notified[0])
            self.assertTrue(any(k == "RESUME_DRAFT" for k, p, m in logged))
            drafts = RES._load_drafts()
            self.assertEqual(len(drafts), 1)
            self.assertEqual(drafts[0]["text"], "sure, come by anytime")
        finally:
            RES.DRAFTS_FILE = orig
            try: os.remove(tmp)
            except OSError: pass

    def test_needs_winfred_reply_flags_without_saving_a_draft(self):
        notified = []
        record = {"pn": "6598888888", "jid": "6598888888@lid",
                 "context": {"name": "Test", "listing_key": None, "last_inbound": "hmm",
                            "transcript_tail": []}}
        tmp = f"/tmp/test-drafts-nw-{os.getpid()}.jsonl"
        orig = RES.DRAFTS_FILE
        RES.DRAFTS_FILE = tmp
        try:
            RES.finish_resume_draft(record, "NEEDS_WINFRED: they are negotiating rent", None,
                                    notified.append, lambda *a: None)
            self.assertEqual(len(notified), 1)
            self.assertIn("needs your own reply", notified[0])
            self.assertFalse(os.path.exists(tmp))
        finally:
            RES.DRAFTS_FILE = orig

    def test_finish_resume_draft_silent_on_trivial_timeout(self):
        """Item 3: a background request that produced nothing usable stays SILENT (log
        DRAFT_TIMEOUT only) when the message it would have answered was trivial -- pinging
        Winfred for every timed out 'ok thanks' would be noisier than the timeout itself."""
        logged = []
        record = {"pn": "6598889999", "jid": "6598889999@lid",
                 "context": {"name": "Test", "listing_key": None,
                            "last_inbound": "ok thanks", "transcript_tail": []}}
        with mock.patch.object(RES, "notify_winfred_coalesced") as coalesced:
            RES.finish_resume_draft(record, None, "timeout", lambda m: None,
                                    lambda k, p, m: logged.append(k), timed_out=True)
        coalesced.assert_not_called()
        self.assertIn("DRAFT_TIMEOUT", logged)

    def test_finish_resume_draft_flags_when_undrafted_message_needed_an_answer(self):
        """A timeout on a message that DID demand an answer (here, a real question) still
        reaches Winfred, through the coalesced notify -- never silently dropped."""
        record = {"pn": "6598880000", "jid": "6598880000@lid",
                 "context": {"name": "Test", "listing_key": "bayshore",
                            "last_inbound": "what time can I view tomorrow?",
                            "transcript_tail": []}}
        with mock.patch.object(RES, "notify_winfred_coalesced") as coalesced:
            RES.finish_resume_draft(record, None, "empty result", lambda m: None,
                                    lambda *a: None, timed_out=False)
        coalesced.assert_called_once()
        args, _ = coalesced.call_args
        self.assertEqual(args[0], "6598880000")
        self.assertIn("what time can I view", args[1])


class TestDraftValidator(unittest.TestCase):
    """Opus review, 9 Sep 2026: a draft is one /send away from a real client, so the model's
    line is never trusted. The sample pass produced 'Tell me about it lol waste of time only'
    for a live tenant chat -- exactly what this must catch."""

    def test_clean_draft_passes(self):
        self.assertIsNone(RES.validate_draft(
            "Sure, I can arrange a viewing for you. When are you free this week?"))

    def test_slang_rejected(self):
        self.assertIsNotNone(RES.validate_draft("Tell me about it lol waste of time only"))

    def test_laughter_rejected(self):
        self.assertIsNotNone(RES.validate_draft("Haha ok noted"))

    def test_profanity_rejected(self):
        self.assertIsNotNone(RES.validate_draft("That landlord is damn stupid"))

    def test_more_than_three_sentences_rejected(self):
        self.assertIsNotNone(RES.validate_draft("One. Two. Three. Four."))

    def test_hyphen_rejected(self):
        self.assertIsNotNone(RES.validate_draft("It is a well-kept unit"))

    def test_em_dash_rejected(self):
        self.assertIsNotNone(RES.validate_draft("Sure \u2014 I will check"))

    def test_rent_figure_rejected(self):
        self.assertIsNotNone(RES.validate_draft("The room is $1400 per month"))

    def test_bare_four_digit_figure_rejected(self):
        self.assertIsNotNone(RES.validate_draft("I can do 1350 for you"))

    def test_cea_number_rejected(self):
        self.assertIsNotNone(RES.validate_draft("Winfred Quek CEA R073319H"))

    def test_advice_keyword_rejected(self):
        self.assertIsNotNone(RES.validate_draft("You should check your TDSR first"))
        self.assertIsNotNone(RES.validate_draft("I would advise you to sign now"))

    def test_link_rejected(self):
        self.assertIsNotNone(RES.validate_draft("See https://example.com for the unit"))

    def test_empty_rejected(self):
        self.assertIsNotNone(RES.validate_draft("   "))

    def test_overlong_rejected(self):
        self.assertIsNotNone(RES.validate_draft("ok " * 200))

    def test_rejected_draft_flags_winfred_and_saves_nothing(self):
        tmp = f"/tmp/test-drafts-reject-{os.getpid()}-{time.time_ns()}.jsonl"
        orig = RES.DRAFTS_FILE
        RES.DRAFTS_FILE = tmp
        try:
            record = {"pn": "6598888888", "jid": "6598888888@lid",
                     "context": {"name": "Tester", "listing_key": "bayshore",
                                "last_inbound": "Roti prata couple", "transcript_tail": []}}
            notified, logged = [], []
            RES.finish_resume_draft(record, "Tell me about it lol waste of time only", None,
                                    notified.append, lambda k, p, m: logged.append(k))
            self.assertEqual(len(notified), 1)
            self.assertIn("needs your own reply", notified[0])
            self.assertIn("RESUME_DRAFT_REJECTED", logged)
            self.assertFalse(os.path.exists(tmp))
        finally:
            RES.DRAFTS_FILE = orig

    def test_send_command_revalidates_before_sending(self):
        """Defence in depth: even a draft already on disk is re-validated at /send time."""
        tmp = f"/tmp/test-drafts-resend-{os.getpid()}-{time.time_ns()}.jsonl"
        orig = RES.DRAFTS_FILE
        RES.DRAFTS_FILE = tmp
        try:
            did = RES.new_draft("6598888888", "6598888888@lid", "bayshore",
                                "haha waste of time only")
            sent = []
            RESC.handle_self_chat_command(RES.OWN_JID, "/send " + did,
                                         send_fn=lambda j, t: sent.append((j, t)) or True,
                                         guard_reserve_fn=lambda j: True,
                                         log_fn=lambda *a: None, con=_mem_db([]))
            self.assertEqual(sent, [])
            self.assertEqual(RES.find_draft(did)["status"], "rejected")
        finally:
            RES.DRAFTS_FILE = orig
            if os.path.exists(tmp):
                os.remove(tmp)


class TestRecordTypeGate(unittest.TestCase):
    JID = "6598887777@lid"

    def _rec(self, **kw):
        r = {"manual_takeover": True, "human_takeover": True,
             "last_hand_reply_ts": "2026-09-08 10:00:00+08:00"}
        r.update(kw)
        return r

    def _blocked(self, rec):
        con = _mem_db([(1, self.JID, 0)])
        return RES.resume_reason_blocked(con, "id", self.JID, rec, 1,
                                         "2026-09-08 10:30:00+08:00")

    def test_supply_form_sent_blocks(self):
        self.assertIsNotNone(self._blocked(self._rec(supply_form_sent=True)))

    def test_buyer_record_blocks(self):
        self.assertIsNotNone(self._blocked(self._rec(buyer_form_sent=True)))

    def test_excluded_status_blocks(self):
        self.assertIsNotNone(self._blocked(self._rec(status="excluded:agent")))

    def test_excluded_reason_consulted_every_resume(self):
        with mock.patch.object(RES.E, "excluded_reason", return_value="agent"):
            self.assertIn("agent", self._blocked(self._rec()) or "")

    def test_unreadable_contact_db_fails_closed(self):
        with mock.patch.object(RES.E, "excluded_reason", side_effect=RuntimeError("locked")):
            self.assertIsNotNone(self._blocked(self._rec()))

    def test_clean_tenant_record_still_eligible(self):
        with mock.patch.object(RES.E, "excluded_reason", return_value=None):
            self.assertIsNone(self._blocked(self._rec()))



class TestNotifyOnlyAllowList(unittest.TestCase):
    """Opus review, 9 Sep 2026: FLAG_HUMAN and VIEWING_TIME_PROPOSED are notify-only in most
    branches but each has one that carries a canned prospect line. Neither line is on
    Winfred's approved resume list, so a textful one must DRAFT, not send."""

    def test_flag_human_without_text_is_allowed(self):
        self.assertFalse(RES.needs_draft({"type": "FLAG_HUMAN", "text": None}))

    def test_flag_human_with_the_declined_cta_closer_drafts(self):
        self.assertTrue(RES.needs_draft(
            {"type": "FLAG_HUMAN", "text": "No worries \U0001F642 You can see my other "
                                           "available rooms here:\nhttps://example"}))

    def test_viewing_time_proposed_with_reschedule_line_drafts(self):
        self.assertTrue(RES.needs_draft(
            {"type": "VIEWING_TIME_PROPOSED", "text":
             "No worries, which day and time would work better for you?"}))

    def test_viewing_time_proposed_notify_only_is_allowed(self):
        self.assertFalse(RES.needs_draft({"type": "VIEWING_TIME_PROPOSED", "when": "sat 3pm"}))

    def test_copilot_verdict_with_texts_drafts(self):
        self.assertTrue(RES.needs_draft({"type": "COPILOT_VERDICT", "texts": ["hi"]}))

    def test_allow_list_is_exactly_winfreds_approved_set(self):
        self.assertEqual(set(RES.ALLOWED_RESUME_TYPES), {
            "SEND_FORM", "NUDGE_INCOMPLETE", "ASK_ONE", "OFFER_VIEWING", "CONFIRM_VIEWING",
            "ASK_TENANT_TIME", "LEASE_NOTE", "AUTO_CLOSED"})


class TestAutoClosedResumePath(unittest.TestCase):
    """AUTO_CLOSED added to ALLOWED_RESUME_TYPES (Winfred, 9 Sep 2026 merge redo): the fixed
    closing pleasantry ("No worries ... Reach out anytime if you need a room again.") auto
    sends once per chat under manual takeover too, same as the autonomous flow -- no draft,
    no Telegram ping (notify is always False on this action type, engine side)."""

    def test_auto_closed_never_needs_a_draft(self):
        a = {"type": "AUTO_CLOSED", "text": E.CLOSING_TEXT_GENERIC, "notify": False}
        self.assertFalse(RES.needs_draft(a))

    def test_auto_closed_carries_no_notify_regardless_of_resume(self):
        # notify=False is stamped by the engine itself, not by the resume plumbing -- prove
        # it holds for both a resumed and a non resumed withdrawal signal.
        for resume_flag in (True, False):
            with self.subTest(resume=resume_flag):
                st = {"version": 1, "conversations": {}}
                jid = "6598885555@s.whatsapp.net"
                pn = E.resolve_pn(jid)
                rec = E._rec(st, pn)
                if resume_flag:
                    rec["manual_takeover"] = True; rec["human_takeover"] = True
                ev = {"jid": jid, "msg_id": "1", "text": "found a place already, thanks!",
                      "is_from_me": False, "resume": resume_flag}
                a = E.handle_event(st, ev)
                self.assertEqual(a["type"], "AUTO_CLOSED")
                self.assertFalse(a.get("notify"))
                self.assertEqual(a["text"], E.CLOSING_TEXT_NEW_PLACE)
                self.assertTrue(st["conversations"][pn].get("terminal"))

    def test_end_to_end_auto_closed_from_engine_is_allowed_under_resume(self):
        """The actual engine, in resume mode on a manual_takeover record, really does emit
        AUTO_CLOSED for a withdrawal signal -- allowed straight through, not drafted."""
        st = {"version": 1, "conversations": {}}
        jid = "6598886677@s.whatsapp.net"
        pn = E.resolve_pn(jid)
        rec = E._rec(st, pn)
        rec["manual_takeover"] = True; rec["human_takeover"] = True
        ev = {"jid": jid, "msg_id": "1", "text": "no longer looking, thanks anyway",
              "is_from_me": False, "resume": True}
        a = E.handle_event(st, ev)
        self.assertEqual(a["type"], "AUTO_CLOSED")
        self.assertFalse(RES.needs_draft(a))
        self.assertFalse(a.get("notify"))

    def test_terminal_latch_blocks_a_second_auto_closed_same_chat(self):
        """'Once per chat' -- the engine's own terminal gate, not a resume specific check;
        exercised here through the resume path since that is what item 2 is about."""
        st = {"version": 1, "conversations": {}}
        jid = "6598887788@s.whatsapp.net"
        pn = E.resolve_pn(jid)
        rec = E._rec(st, pn)
        rec["manual_takeover"] = True; rec["human_takeover"] = True
        ev1 = {"jid": jid, "msg_id": "1", "text": "thanks, not interested anymore",
               "is_from_me": False, "resume": True}
        a1 = E.handle_event(st, ev1)
        self.assertEqual(a1["type"], "AUTO_CLOSED")
        ev2 = {"jid": jid, "msg_id": "2", "text": "ok bye", "is_from_me": False,
               "resume": True}
        a2 = E.handle_event(st, ev2)
        self.assertIsNone(a2)



class TestRowidNotTextId(unittest.TestCase):
    """The bridge's id column is TEXT (hex message id). Comparing or ordering by it against a
    decimal rowid is a string compare that succeeds at random. Opus review, 9 Sep 2026."""
    JID = "6598886666@s.whatsapp.net"

    def test_later_outbound_still_blocks_with_hex_ids(self):
        con = _mem_db([(100, self.JID, 0), (101, self.JID, 1)])
        rec = {"manual_takeover": True, "human_takeover": True,
               "last_hand_reply_ts": "2026-09-08 10:00:00+08:00"}
        with mock.patch.object(RES.E, "excluded_reason", return_value=None):
            reason = RES.resume_reason_blocked(con, "id", self.JID, rec, 100,
                                               "2026-09-08 10:30:00+08:00")
        self.assertEqual(reason, "Winfred already answered this inbound")

    def test_earlier_outbound_does_not_block_with_hex_ids(self):
        con = _mem_db([(99, self.JID, 1), (100, self.JID, 0)])
        rec = {"manual_takeover": True, "human_takeover": True,
               "last_hand_reply_ts": "2026-09-08 10:00:00+08:00"}
        with mock.patch.object(RES.E, "excluded_reason", return_value=None):
            reason = RES.resume_reason_blocked(con, "id", self.JID, rec, 100,
                                               "2026-09-08 10:30:00+08:00")
        self.assertIsNone(reason)

    def test_transcript_is_in_chronological_order(self):
        con = _mem_db([(10, self.JID, 0), (11, self.JID, 1), (12, self.JID, 0)])
        got = [m["text"] for m in RES.fetch_transcript(con, "id", self.JID)]
        self.assertEqual(got, ["msg10", "msg11", "msg12"])


class TestResumeGateHelpers(unittest.TestCase):
    """Unit coverage for the two small pieces the runner's send choke calls directly:
    RES.mark_resume (the ONLY place a["resume"] is ever set) and RES.resume_send_gate (the
    whole bypass decision in one call, so it is testable without a live tick)."""

    def test_mark_resume_tags_the_action(self):
        a = {"type": "OFFER_VIEWING", "text": "hi"}
        out = RES.mark_resume(a)
        self.assertIs(out, a)
        self.assertTrue(a["resume"])

    def test_mark_resume_tolerates_none(self):
        self.assertIsNone(RES.mark_resume(None))

    def test_gate_not_attempted_when_action_not_tagged_resume(self):
        attempted, why = RES.resume_send_gate({"type": "OFFER_VIEWING"},
                                              {"manual_takeover": True}, False)
        self.assertFalse(attempted)
        self.assertIsNone(why)

    def test_gate_not_attempted_when_record_not_under_takeover(self):
        attempted, why = RES.resume_send_gate({"type": "OFFER_VIEWING", "resume": True},
                                              {"manual_takeover": False}, False)
        self.assertFalse(attempted)

    def test_gate_passes_clean_contact(self):
        with mock.patch.object(E, "excluded_reason", return_value=None):
            attempted, why = RES.resume_send_gate({"type": "OFFER_VIEWING", "resume": True},
                                                  {"manual_takeover": True}, False)
        self.assertTrue(attempted)
        self.assertIsNone(why)

    def test_gate_blocks_on_excluded_reason(self):
        with mock.patch.object(E, "excluded_reason", return_value="agent"):
            attempted, why = RES.resume_send_gate({"type": "OFFER_VIEWING", "resume": True},
                                                  {"manual_takeover": True}, False)
        self.assertTrue(attempted)
        self.assertIn("agent", why)

    def test_gate_fails_closed_on_excluded_reason_error(self):
        with mock.patch.object(E, "excluded_reason", side_effect=RuntimeError("locked")):
            attempted, why = RES.resume_send_gate({"type": "OFFER_VIEWING", "resume": True},
                                                  {"manual_takeover": True}, False)
        self.assertTrue(attempted)
        self.assertIsNotNone(why)

    def test_gate_blocks_on_unreadable_landlord_db(self):
        with mock.patch.object(E, "excluded_reason", return_value=None):
            attempted, why = RES.resume_send_gate({"type": "OFFER_VIEWING", "resume": True},
                                                  {"manual_takeover": True}, True)
        self.assertTrue(attempted)
        self.assertIn("landlord-db", why)


# ============================================================================================
# FIX 1 integration: the runner's send choke point, exercised through a real run() tick
# against a throwaway sqlite messages.db and a throwaway intake-state.json -- NEVER the live
# ~/whatsapp-mcp or ~/.claude/state paths. E.handle_event is stubbed (it has its own full
# coverage elsewhere) so these tests isolate exactly the code this task changed: the choke
# point's resume bypass, its gates, and the actual _send call site. (Opus review, 9 Sep 2026:
# the prior test suite only ever inspected wa_intake_runner.py's source text for this.)
# ============================================================================================
FAKE_PN = "6598889999"
FAKE_JID = FAKE_PN + "@lid"


def _sgt_ts(minutes_ago=0):
    dt = (datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
          - datetime.timedelta(minutes=minutes_ago))
    return dt.isoformat()


def _resolve_pn_stub(jid):
    if jid == FAKE_JID:
        return FAKE_PN
    if not jid:
        return None
    return jid.split("@")[0]


@contextlib.contextmanager
def _isolated_runner(tmp_dir, inbound_content="any updates?", conversations=None,
                      handle_event_return=None, quiet_hours=False, guard_ok=True,
                      dry_run=False, landlord_db_unreadable=False, watermark=0,
                      inbound_minutes_ago=6, extra_history=None):
    """Runs wa_intake_runner.run() ONCE inside a fully sandboxed harness and yields a dict of
    everything the choke point did: sent / guard_calls / notified / logged. Every path the
    real runner touches on disk (messages.db, the watermark, intake-state.json, the listing
    index, the run lock) is redirected under TMP_DIR; every path it touches on the network or
    via subprocess (the bridge send, the cross sender guard, Telegram) is a recording stub.

    extra_history (optional): earlier messages in the SAME chat (list of (is_from_me,
    content, minutes_ago) tuples), inserted at rowid <= 0 so they are already-seen context
    (never reprocessed as a new inbound) but still visible to anything that scans chat
    history directly (e.g. B2's dispute_language_recent, the resume draft transcript)."""
    msg_db = os.path.join(tmp_dir, "messages.db")
    con = sqlite3.connect(msg_db)
    con.execute("CREATE TABLE messages (rowid INTEGER PRIMARY KEY, id TEXT, chat_jid TEXT, "
                "is_from_me INTEGER, content TEXT, timestamp TEXT, media_type TEXT)")
    for i, (ifm, content, minutes_ago) in enumerate(extra_history or []):
        rid = -(i + 1)
        con.execute("INSERT INTO messages (rowid, id, chat_jid, is_from_me, content, "
                    "timestamp, media_type) VALUES (?, ?, ?, ?, ?, ?, '')",
                    (rid, "HIST%d" % rid, FAKE_JID, int(ifm), content, _sgt_ts(minutes_ago)))
    con.execute("INSERT INTO messages (rowid, id, chat_jid, is_from_me, content, timestamp, "
                "media_type) VALUES (1, 'AAAA1', ?, 0, ?, ?, '')",
                (FAKE_JID, inbound_content, _sgt_ts(inbound_minutes_ago)))
    con.commit(); con.close()

    lastf = os.path.join(tmp_dir, "runner-last.json")
    with open(lastf, "w") as f:
        json.dump({"last_rowid": watermark}, f)
    lockf = os.path.join(tmp_dir, ".lock")
    state_path = os.path.join(tmp_dir, "intake-state.json")
    conv = conversations if conversations is not None else {}
    with open(state_path, "w") as f:
        json.dump({"version": 1, "conversations": conv}, f)

    sent, guard_calls, notified, logged = [], [], [], []
    calls = {"sent": sent, "guard_calls": guard_calls, "notified": notified, "logged": logged}

    def fake_send(pn, text):
        sent.append((pn, text)); return True

    def fake_guard(jid):
        guard_calls.append(jid); return guard_ok

    # STEP 0 sandbox seal (9 Sep 2026 merge redo): LASTF (wa_intake_send._write_last) and
    # the owner loop (wa_intake_owner.py / wa_intake_owner_answers.py, which R.run() calls
    # on every tick but which this helper never patched at all) read their paths via THEIR
    # OWN defining module's globals, not wa_intake_runner's separate name-import binding --
    # mock.patch.object(R, "LASTF", lastf) below never reached them (a real run watermark
    # was being read/written by this "isolated" runner). WA_INTAKE_STATE_ROOT/
    # WA_INTAKE_DATA_ROOT/WA_INTAKE_MSG_DB redirect every module uniformly, at call time,
    # regardless of which module a function lives in -- see wa_intake_paths.resolved.
    _saved_env = {k: os.environ.get(k) for k in
                  ("WA_INTAKE_SANDBOX", "WA_INTAKE_STATE_ROOT", "WA_INTAKE_DATA_ROOT",
                   "WA_INTAKE_MSG_DB")}
    os.environ["WA_INTAKE_SANDBOX"] = "1"
    os.environ["WA_INTAKE_STATE_ROOT"] = tmp_dir
    os.environ["WA_INTAKE_DATA_ROOT"] = tmp_dir
    os.environ["WA_INTAKE_MSG_DB"] = tmp_dir
    PATHS.sandbox_init()

    stack = contextlib.ExitStack()

    def _restore_env():
        for k, v in _saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    stack.callback(_restore_env)

    stack.enter_context(mock.patch.object(R, "MSG_DB", msg_db))
    stack.enter_context(mock.patch.object(R, "LASTF", lastf))
    stack.enter_context(mock.patch.object(R, "LOCKF", lockf))
    # sweep_approved_drafts (called unconditionally, once per tick) reads this -- never the
    # live drafts.jsonl, even read only.
    stack.enter_context(mock.patch.object(RES, "DRAFTS_FILE", os.path.join(tmp_dir, "drafts.jsonl")))
    stack.enter_context(mock.patch.object(E, "STATE", state_path))
    stack.enter_context(mock.patch.object(E, "IDX", os.path.join(tmp_dir, "no-idx.json")))
    stack.enter_context(mock.patch.object(E, "TEMPLATES", os.path.join(tmp_dir, "no-tmpl.json")))
    stack.enter_context(mock.patch.object(E, "DRY_RUN", dry_run))
    stack.enter_context(mock.patch.object(
        E, "_landlord_pn_set", lambda: (None if landlord_db_unreadable else frozenset())))
    stack.enter_context(mock.patch.object(E, "_landlord_form_recipients", lambda: frozenset()))
    stack.enter_context(mock.patch.object(E, "_contact_names", lambda pn: ([], True)))
    stack.enter_context(mock.patch.object(E, "_cobroke_agent_pn_set", lambda: frozenset()))
    stack.enter_context(mock.patch.object(E, "resolve_pn", _resolve_pn_stub))
    stack.enter_context(mock.patch.object(R, "_send", fake_send))
    stack.enter_context(mock.patch.object(R, "_guard_reserve", fake_guard))
    # patch BOTH bindings: R.notify_winfred is called directly by wa_intake_runner.run()
    # itself, but notify_for_action/notify_stale_backfill/notify_winfred_coalesced (split
    # into wa_intake_notify.py, 9 Sep 2026 merge review) call the bare name resolved in
    # THEIR OWN module's globals -- patching only R's imported reference misses those.
    stack.enter_context(mock.patch.object(R, "notify_winfred", notified.append))
    stack.enter_context(mock.patch.object(NOTIFY, "notify_winfred", notified.append))
    # the per-chat notify coalescing window file AND the global circuit breaker's send log
    # must never touch the real state dir in a test (9 Sep 2026 near miss: an earlier cut of
    # this harness left a real notify-coalesce.json behind under
    # ~/.claude/state/listing-templates) -- always redirected under tmp_dir.
    stack.enter_context(mock.patch.object(
        NOTIFY, "COALESCE_FILE", os.path.join(tmp_dir, "notify-coalesce.json")))
    stack.enter_context(mock.patch.object(
        SEND, "CIRCUIT_FILE", os.path.join(tmp_dir, "send-circuit.json")))
    stack.enter_context(mock.patch.object(
        R, "_log", lambda k, p, m: logged.append((k, p, m))))
    stack.enter_context(mock.patch.object(R, "_alert_hourly", lambda *a: None))
    stack.enter_context(mock.patch.object(R, "_drain_notify_queue", lambda: None))
    stack.enter_context(mock.patch.object(R, "_quiet_hours", lambda: quiet_hours))
    stack.enter_context(mock.patch("time.sleep", lambda *a: None))   # no real 4-9s throttle
    if handle_event_return is not None:
        stack.enter_context(mock.patch.object(
            E, "handle_event", lambda state, ev: dict(handle_event_return)))
    with stack:
        R.run()
    yield calls


class TestResumeSendSite(unittest.TestCase):
    """FIX 1: resume-mode actions used to be tagged nowhere, so the choke point's
    manual_takeover check TAKEOVER_SKIP'd every single one -- the auto send half of the
    feature never actually sent anything. These exercise the real send site end to end."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmpdir.cleanup()

    def _rec(self, **kw):
        # B1 (Sep 2026): needs_draft() now also requires form_sent True AND listing_key
        # bound (an established tenant prospect) before an allow listed type reaches the
        # send choke at all -- these fixtures represent that legitimate, already qualified
        # case; the "not yet established" case is covered separately below.
        r = {"pn": FAKE_PN, "manual_takeover": True, "human_takeover": True,
             "last_hand_reply_ts": _sgt_ts(20),
             # B established (Sep 2026 review): shaped as a genuinely established prospect
             # (real inbound bind + 2 profile fields + no outbound before the first inbound)
             # so these send-site tests keep exercising the choke's OWN gates (cap/cold/
             # excluded/guard) unchanged by the established rewrite -- the "not yet
             # established" case is covered separately in TestB1EstablishedProspectGate.
             "profile": {"name": "Tester", "nationality": "Singaporean"},
             "listing_key_source": "inbound", "first_inbound_text": "is this room available",
             "outbound_before_first_inbound": False,
             "processed_ids": [], "form_sent": True, "listing_key": "test-listing"}
        r.update(kw)
        return r

    def _run(self, **kw):
        with _isolated_runner(self._tmpdir.name, **kw) as calls:
            return calls

    def test_resume_offer_viewing_reaches_send(self):
        calls = self._run(
            conversations={FAKE_PN: self._rec()},
            handle_event_return={"type": "OFFER_VIEWING", "pn": FAKE_PN,
                                 "text": "Keen to view? I can put you in for Sat 3pm."})
        self.assertEqual(calls["sent"], [(FAKE_JID, "Keen to view? I can put you in for Sat 3pm.")])
        self.assertTrue(any(k == "RESUME_SENT" for k, p, m in calls["logged"]))
        self.assertFalse(any(k == "TAKEOVER_SKIP" for k, p, m in calls["logged"]))

    def test_resume_redirect_never_reaches_send(self):
        with mock.patch.object(RES, "process_draft_needed", lambda *a, **k: None):
            calls = self._run(
                conversations={FAKE_PN: self._rec()},
                handle_event_return={"type": "REDIRECT", "pn": FAKE_PN,
                                     "text": "No worries, here are my other rooms."})
        self.assertEqual(calls["sent"], [])

    def test_non_resume_action_under_takeover_still_takeover_skips(self):
        """Same allow listed type and text as the first test, but the record does NOT clear
        resume_reason_blocked (no last_hand_reply_ts at all) -- ev['resume'] is never set, so
        a['resume'] is never tagged, and the ordinary TAKEOVER_SKIP choke applies."""
        rec = self._rec(); rec.pop("last_hand_reply_ts", None)
        calls = self._run(
            conversations={FAKE_PN: rec},
            handle_event_return={"type": "OFFER_VIEWING", "pn": FAKE_PN,
                                 "text": "Keen to view? I can put you in for Sat 3pm."})
        self.assertEqual(calls["sent"], [])
        self.assertTrue(any(k == "TAKEOVER_SKIP" for k, p, m in calls["logged"]))

    def test_quiet_hours_blocks_resume_send(self):
        calls = self._run(
            conversations={FAKE_PN: self._rec()},
            handle_event_return={"type": "OFFER_VIEWING", "pn": FAKE_PN, "text": "hi"},
            quiet_hours=True)
        self.assertEqual(calls["sent"], [])

    def test_daily_cap_blocks_resume_send(self):
        today = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))
        rec = self._rec(sends_today_date=today, sends_today=2)
        calls = self._run(
            conversations={FAKE_PN: rec},
            # NUDGE_INCOMPLETE is on the allow list and NOT cap exempt (unlike
            # CONFIRM_VIEWING/OFFER_VIEWING/ASK_ONE) -- proves resume sends count toward it
            handle_event_return={"type": "NUDGE_INCOMPLETE", "pn": FAKE_PN, "text": "hi"})
        self.assertEqual(calls["sent"], [])
        self.assertTrue(any(k == "DAILY_CAP_SKIP" for k, p, m in calls["logged"]))

    def test_excluded_contact_blocks_resume_send(self):
        """Simulates a same-tick DB flip: excluded_reason says clean the first two times
        (resume_reason_blocked, then the established gate's own recheck inside needs_draft)
        and excluded the THIRD time (the choke's own independent re-check) -- proving the
        choke does not just trust the earlier gates."""
        with mock.patch.object(E, "excluded_reason", side_effect=[None, None, "agent"]):
            calls = self._run(
                conversations={FAKE_PN: self._rec()},
                handle_event_return={"type": "OFFER_VIEWING", "pn": FAKE_PN, "text": "hi"})
        self.assertEqual(calls["sent"], [])
        self.assertTrue(any(k == "RESUME_SKIP" for k, p, m in calls["logged"]))

    def test_landlord_db_unreadable_blocks_resume_send(self):
        with mock.patch.object(E, "excluded_reason", return_value=None):
            calls = self._run(
                conversations={FAKE_PN: self._rec()},
                handle_event_return={"type": "OFFER_VIEWING", "pn": FAKE_PN, "text": "hi"},
                landlord_db_unreadable=True)
        self.assertEqual(calls["sent"], [])
        self.assertTrue(any(k == "RESUME_SKIP" for k, p, m in calls["logged"]))

    def test_one_send_per_inbound(self):
        """A single inbound row can never produce more than the one _send call the runner's
        per-row loop naturally makes (handle_event's own one-dict-or-None contract already
        guarantees this; this confirms the choke path does not fan it out)."""
        calls = self._run(
            conversations={FAKE_PN: self._rec()},
            handle_event_return={"type": "OFFER_VIEWING", "pn": FAKE_PN, "text": "hi"})
        self.assertEqual(len(calls["sent"]), 1)


# ============================================================================================
# FIX 2: '/send <id>' hardening -- cold guard, excluded recheck, quiet hours queueing, daily
# cap, and a Winfred notification for every outcome (Opus review, 9 Sep 2026: none of this
# existed; /send was a bare bypass of every one of these).
# ============================================================================================
class TestSendCommandHardening(unittest.TestCase):
    def setUp(self):
        self._tmp = f"/tmp/test-drafts-hard-{os.getpid()}-{time.time_ns()}.jsonl"
        self._orig = RES.DRAFTS_FILE
        RES.DRAFTS_FILE = self._tmp
        self.sent, self.notified, self.logged = [], [], []
        self.con = _mem_db([])   # clean, no dispute language -- B2 recheck must pass through

    def tearDown(self):
        RES.DRAFTS_FILE = self._orig
        try: os.remove(self._tmp)
        except OSError: pass

    def _send_fn(self, jid, text):
        self.sent.append((jid, text)); return True

    def _state(self, **rec_fields):
        rec = {"pn": FAKE_PN}
        rec.update(rec_fields)
        return {"version": 1, "conversations": {FAKE_PN: rec}}

    def test_cold_lead_refused_with_last_message_date(self):
        did = RES.new_draft(FAKE_PN, FAKE_JID, "test-listing", "Keen to view this week?")
        old_ts = _sgt_ts(6 * 24 * 60)   # 6 days ago
        with mock.patch.object(E, "excluded_reason", return_value=None):
            handled = RESC.handle_self_chat_command(
                RES.OWN_JID, f"/send {did}", self._send_fn, lambda j: True,
                lambda k, p, m: self.logged.append((k, p, m)), notify_fn=self.notified.append,
                state=self._state(last_inbound_ts=old_ts), con=self.con)
        self.assertTrue(handled)
        self.assertEqual(self.sent, [])
        self.assertEqual(RES.find_draft(did)["status"], "cold")
        self.assertTrue(any("gone cold" in m and old_ts in m for m in self.notified))

    def test_fresh_lead_still_sends(self):
        did = RES.new_draft(FAKE_PN, FAKE_JID, "test-listing", "Keen to view this week?")
        with mock.patch.object(E, "excluded_reason", return_value=None):
            handled = RESC.handle_self_chat_command(
                RES.OWN_JID, f"/send {did}", self._send_fn, lambda j: True,
                lambda k, p, m: self.logged.append((k, p, m)), notify_fn=self.notified.append,
                state=self._state(last_inbound_ts=_sgt_ts(10)), con=self.con)
        self.assertTrue(handled)
        self.assertEqual(self.sent, [(FAKE_JID, "Keen to view this week?")])
        self.assertEqual(RES.find_draft(did)["status"], "sent")
        self.assertTrue(any("Sent your drafted reply" in m for m in self.notified))

    def test_excluded_contact_refused(self):
        did = RES.new_draft(FAKE_PN, FAKE_JID, "test-listing", "Keen to view this week?")
        with mock.patch.object(E, "excluded_reason", return_value="agent"):
            handled = RESC.handle_self_chat_command(
                RES.OWN_JID, f"/send {did}", self._send_fn, lambda j: True,
                lambda k, p, m: self.logged.append((k, p, m)), notify_fn=self.notified.append,
                state=self._state(), con=self.con)
        self.assertTrue(handled)
        self.assertEqual(self.sent, [])
        self.assertEqual(RES.find_draft(did)["status"], "excluded")
        self.assertTrue(any("excluded" in m for m in self.notified))

    def test_excluded_reason_db_error_fails_closed(self):
        did = RES.new_draft(FAKE_PN, FAKE_JID, "test-listing", "Keen to view this week?")
        with mock.patch.object(E, "excluded_reason", side_effect=RuntimeError("locked")):
            RESC.handle_self_chat_command(
                RES.OWN_JID, f"/send {did}", self._send_fn, lambda j: True,
                lambda k, p, m: self.logged.append((k, p, m)), notify_fn=self.notified.append,
                state=self._state(), con=self.con)
        self.assertEqual(self.sent, [])
        self.assertEqual(RES.find_draft(did)["status"], "excluded")

    def test_daily_cap_leaves_draft_pending_for_a_later_retry(self):
        did = RES.new_draft(FAKE_PN, FAKE_JID, "test-listing", "Keen to view this week?")
        today = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))
        with mock.patch.object(E, "excluded_reason", return_value=None):
            handled = RESC.handle_self_chat_command(
                RES.OWN_JID, f"/send {did}", self._send_fn, lambda j: True,
                lambda k, p, m: self.logged.append((k, p, m)), notify_fn=self.notified.append,
                state=self._state(sends_today_date=today, sends_today=2,
                                  last_inbound_ts=_sgt_ts(10)), con=self.con)
        self.assertTrue(handled)
        self.assertEqual(self.sent, [])
        self.assertEqual(RES.find_draft(did)["status"], "pending")   # unchanged -- retried later
        self.assertTrue(any("cap" in m for m in self.notified))

    def test_successful_send_counts_toward_the_daily_cap(self):
        did = RES.new_draft(FAKE_PN, FAKE_JID, "test-listing", "Keen to view this week?")
        state = self._state(last_inbound_ts=_sgt_ts(10))
        with mock.patch.object(E, "excluded_reason", return_value=None):
            RESC.handle_self_chat_command(
                RES.OWN_JID, f"/send {did}", self._send_fn, lambda j: True,
                lambda *a: None, notify_fn=self.notified.append, state=state, con=self.con)
        rec = state["conversations"][FAKE_PN]
        self.assertEqual(rec["sends_today"], 1)

    def test_quiet_hours_queues_instead_of_sending(self):
        did = RES.new_draft(FAKE_PN, FAKE_JID, "test-listing", "Keen to view this week?")
        handled = RESC.handle_self_chat_command(
            RES.OWN_JID, f"/send {did}", self._send_fn, lambda j: True,
            lambda k, p, m: self.logged.append((k, p, m)), notify_fn=self.notified.append,
            quiet_hours_fn=lambda: True, state=self._state(last_inbound_ts=_sgt_ts(10)),
            con=self.con)
        self.assertTrue(handled)
        self.assertEqual(self.sent, [])
        self.assertEqual(RES.find_draft(did)["status"], "approved")
        self.assertTrue(any("queued" in m for m in self.notified))

    def test_sweep_sends_a_queued_draft_once_quiet_hours_are_over(self):
        did = RES.new_draft(FAKE_PN, FAKE_JID, "test-listing", "Keen to view this week?")
        RES.mark_draft(did, "approved")
        state = self._state(last_inbound_ts=_sgt_ts(10))
        with mock.patch.object(E, "excluded_reason", return_value=None):
            RESC.sweep_approved_drafts(state, send_fn=self._send_fn, guard_reserve_fn=lambda j: True,
                                      log_fn=lambda *a: None, notify_fn=self.notified.append,
                                      con=self.con)
        self.assertEqual(self.sent, [(FAKE_JID, "Keen to view this week?")])
        self.assertEqual(RES.find_draft(did)["status"], "sent")

    def test_sweep_expires_a_queued_draft_past_24h(self):
        did = RES.new_draft(FAKE_PN, FAKE_JID, "test-listing", "Keen to view this week?")
        items = RES._load_drafts()
        for d in items:
            if d["id"] == did:
                d["status"] = "approved"
                d["created"] = time.time() - RES.DRAFT_EXPIRY_SEC - 60
        RES._rewrite_drafts(items)
        RESC.sweep_approved_drafts(self._state(), send_fn=self._send_fn,
                                   guard_reserve_fn=lambda j: True, log_fn=lambda *a: None,
                                   notify_fn=self.notified.append, con=self.con)
        self.assertEqual(self.sent, [])
        self.assertEqual(RES.find_draft(did)["status"], "expired")

    def test_guard_reserve_failure_leaves_draft_pending(self):
        did = RES.new_draft(FAKE_PN, FAKE_JID, "test-listing", "Keen to view this week?")
        with mock.patch.object(E, "excluded_reason", return_value=None):
            RESC.handle_self_chat_command(
                RES.OWN_JID, f"/send {did}", self._send_fn, lambda j: False,
                lambda k, p, m: self.logged.append((k, p, m)), notify_fn=self.notified.append,
                state=self._state(last_inbound_ts=_sgt_ts(10)), con=self.con)
        self.assertEqual(self.sent, [])
        self.assertEqual(RES.find_draft(did)["status"], "pending")
        self.assertTrue(any("reserved" in m for m in self.notified))

    def test_dispute_language_recent_blocks_send_even_without_manual_takeover(self):
        """Review fix: /send re-runs dispute_language_recent regardless of _under_takeover --
        this record carries no manual_takeover/human_takeover flag at all (a plain resume
        draft state), proving the check is not conditioned on takeover state the way the
        autonomous resume path's check is."""
        did = RES.new_draft(FAKE_PN, FAKE_JID, "test-listing", "Keen to view this week?")
        con = _mem_db([])
        con.execute("INSERT INTO messages (rowid, id, chat_jid, is_from_me, content, timestamp) "
                    "VALUES (1, 'D1', ?, 0, 'thinking of getting a lawyer over this', "
                    "'2026-09-08 10:00:00+08:00')", (FAKE_JID,))
        con.commit()
        with mock.patch.object(E, "excluded_reason", return_value=None):
            handled = RESC.handle_self_chat_command(
                RES.OWN_JID, f"/send {did}", self._send_fn, lambda j: True,
                lambda k, p, m: self.logged.append((k, p, m)), notify_fn=self.notified.append,
                state=self._state(last_inbound_ts=_sgt_ts(10)), con=con)
        self.assertTrue(handled)
        self.assertEqual(self.sent, [])
        self.assertEqual(RES.find_draft(did)["status"], "disputed")
        self.assertTrue(any("dispute" in m.lower() for m in self.notified))
        self.assertTrue(any(k == "SEND_CMD_DISPUTE" for k, p, m in self.logged))

    def test_sweep_also_blocks_a_disputed_chat(self):
        """Same recheck applies to the queued-drafts sweep, not just the immediate /send."""
        did = RES.new_draft(FAKE_PN, FAKE_JID, "test-listing", "Keen to view this week?")
        RES.mark_draft(did, "approved")
        con = _mem_db([])
        con.execute("INSERT INTO messages (rowid, id, chat_jid, is_from_me, content, timestamp) "
                    "VALUES (1, 'D2', ?, 0, 'I want a refund for this', "
                    "'2026-09-08 10:00:00+08:00')", (FAKE_JID,))
        con.commit()
        state = self._state(last_inbound_ts=_sgt_ts(10))
        with mock.patch.object(E, "excluded_reason", return_value=None):
            RESC.sweep_approved_drafts(state, send_fn=self._send_fn, guard_reserve_fn=lambda j: True,
                                      log_fn=lambda *a: None, notify_fn=self.notified.append,
                                      con=con)
        self.assertEqual(self.sent, [])
        self.assertEqual(RES.find_draft(did)["status"], "disputed")

    def test_missing_connection_fails_closed_never_sends(self):
        """con omitted entirely (a caller that forgot to wire it) must refuse, never
        silently send -- dispute_language_recent's own fail-closed exception path covers a
        bad/absent connection the same way it covers a real query error."""
        did = RES.new_draft(FAKE_PN, FAKE_JID, "test-listing", "Keen to view this week?")
        with mock.patch.object(E, "excluded_reason", return_value=None):
            RESC.handle_self_chat_command(
                RES.OWN_JID, f"/send {did}", self._send_fn, lambda j: True,
                lambda k, p, m: self.logged.append((k, p, m)), notify_fn=self.notified.append,
                state=self._state(last_inbound_ts=_sgt_ts(10)))
        self.assertEqual(self.sent, [])
        self.assertEqual(RES.find_draft(did)["status"], "disputed")


class TestB1EstablishedProspectGate(unittest.TestCase):
    """B1 (Sep 2026): a resume auto-send (or the short lease auto reply) only ever bypasses
    the draft gate for an ESTABLISHED tenant prospect -- form_sent True AND listing_key
    bound. Real incidents this fixes: a SEND_FORM auto-sent into a personal friend chat
    (pn 6581894357, "Where ah bro", bound off Winfred's own casual outbound mention of
    Eastpoint Green) and a LEASE_NOTE auto-sent into a chat with no confirmed listing_key
    (pn 6590590183, wandering across 3 different properties)."""

    def test_needs_draft_blocks_send_form_with_no_prior_form_sent_or_listing(self):
        # mirrors the real "Where ah bro" record: manual_takeover latched from ordinary
        # friend chatter, listing_key only just bound off Winfred's own outbound mention,
        # form_sent still False (never legitimately qualified as a tenant).
        rec_before = {"form_sent": False, "listing_key": "eastpoint-green"}
        a = {"type": "SEND_FORM", "texts": ["unit info + form"]}
        self.assertTrue(RES.needs_draft(a, rec_before))

    def test_needs_draft_blocks_lease_note_when_unbound(self):
        # mirrors the real pn 6590590183 record: form WAS sent at some point (to a DIFFERENT
        # property earlier in a wandering chat) but listing_key is not currently bound.
        rec_before = {"form_sent": True, "listing_key": None}
        a = {"type": "LEASE_NOTE", "text": "Just to share, the landlord prefers..."}
        self.assertTrue(RES.needs_draft(a, rec_before))

    def test_needs_draft_allows_send_form_when_no_rec_snapshot_given(self):
        # backward compatible default: a caller that does not pass rec_before (existing
        # tests, other call sites not yet updated) keeps the old type only behaviour.
        a = {"type": "SEND_FORM", "texts": ["unit info + form"]}
        self.assertFalse(RES.needs_draft(a))

    def test_needs_draft_allows_offer_viewing_for_an_established_prospect(self):
        # B established (Sep 2026 review): form_sent + listing_key alone no longer suffices
        # -- the fixture below is genuinely established per is_established_prospect() (real
        # inbound bind, 2 profile fields, no outbound before the first inbound).
        rec_before = {"pn": FAKE_PN, "form_sent": True, "listing_key": "eastpoint-green",
                     "listing_key_source": "inbound",
                     "profile": {"name": "Tester", "nationality": "Singaporean"},
                     "first_inbound_text": "is this room available",
                     "outbound_before_first_inbound": False}
        a = {"type": "OFFER_VIEWING", "text": "Keen to view?"}
        with mock.patch.object(RES.E, "excluded_reason", return_value=None):
            self.assertFalse(RES.needs_draft(a, rec_before))

    def test_revert_unsent_form_undoes_the_optimistic_mutation_when_drafted(self):
        # real incident, pn 6590590183: handle_event stamps form_sent=True the instant it
        # DECIDES to send the form, even when that send never actually reaches the runner's
        # own choke (drafted instead) -- the next inbound must not see a false form_sent.
        rec_before = {"form_sent": False, "listing_key": None}
        rec = {"form_sent": True, "form_sent_ts": 12345.0, "listing_key": "eastpoint-green"}
        a = {"type": "SEND_FORM", "texts": ["unit info", "form"]}
        RES.revert_unsent_form(a, rec, rec_before)
        self.assertFalse(rec["form_sent"])
        self.assertNotIn("form_sent_ts", rec)
        self.assertEqual(rec["listing_key"], "eastpoint-green")   # binding itself is untouched

    def test_revert_unsent_form_leaves_a_genuinely_already_sent_record_alone(self):
        rec_before = {"form_sent": True, "listing_key": "eastpoint-green"}
        rec = {"form_sent": True, "form_sent_ts": 12345.0, "listing_key": "eastpoint-green"}
        a = {"type": "OFFER_VIEWING", "text": "hi"}
        RES.revert_unsent_form(a, rec, rec_before)
        self.assertTrue(rec["form_sent"])
        self.assertEqual(rec["form_sent_ts"], 12345.0)

    def test_runner_drafts_instead_of_sending_send_form_into_an_unbound_friend_chat(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(RES, "process_draft_needed") as mock_draft:
                with _isolated_runner(
                        tmp,
                        inbound_content="chio mimi ah! hahaha",
                        conversations={FAKE_PN: {
                            "pn": FAKE_PN, "manual_takeover": True, "human_takeover": True,
                            "last_hand_reply_ts": _sgt_ts(20), "profile": {},
                            "processed_ids": [], "form_sent": False,
                            "listing_key": "eastpoint-green"}},
                        handle_event_return={"type": "SEND_FORM", "pn": FAKE_PN,
                                             "texts": ["unit info", "the intake form"]}) as calls:
                    pass
            self.assertEqual(calls["sent"], [])
            self.assertTrue(mock_draft.called)

    def test_runner_sends_offer_viewing_for_a_genuinely_established_prospect(self):
        with tempfile.TemporaryDirectory() as tmp:
            with _isolated_runner(
                    tmp,
                    conversations={FAKE_PN: {
                        "pn": FAKE_PN, "manual_takeover": True, "human_takeover": True,
                        "last_hand_reply_ts": _sgt_ts(20),
                        "profile": {"name": "Tester", "nationality": "Singaporean"},
                        "processed_ids": [], "form_sent": True,
                        "listing_key": "eastpoint-green", "listing_key_source": "inbound",
                        "first_inbound_text": "is this room available",
                        "outbound_before_first_inbound": False}},
                    handle_event_return={"type": "OFFER_VIEWING", "pn": FAKE_PN,
                                         "text": "Keen to view Fri 3pm?"}) as calls:
                pass
            self.assertEqual(calls["sent"], [(FAKE_JID, "Keen to view Fri 3pm?")])

    def test_friend_chat_outbound_first_no_portal_no_profile_is_never_established(self):
        """Real incident shape, pn 6581894357 (review fix): Winfred messages a friend
        first, casually naming a listing in his own hand typed text; the friend's only
        reply is idle chatter ("Where ah bro") -- no portal link, no profile data. Even if
        form_sent later ends up True (a stray hand paste, exactly what happened for real),
        this must never read as established: listing_key_source stays 'outbound' and
        outbound_before_first_inbound is True."""
        state = {"version": 1, "conversations": {}}
        pn = "6581894357"
        jid = pn + "@s.whatsapp.net"
        with mock.patch.object(E, "_landlord_pn_set", lambda: frozenset()):
            E.handle_event(state, {"jid": jid, "msg_id": "m1",
                                   "text": "eh you still looking? I got a room at "
                                           "eastpoint green if keen",
                                   "is_from_me": True, "engine": False,
                                   "listing_key": "eastpoint-green", "ts": _sgt_ts(120)})
            E.handle_event(state, {"jid": jid, "msg_id": "m2", "text": "Where ah bro",
                                   "is_from_me": False, "listing_key": None, "ts": _sgt_ts(100)})
        rec = state["conversations"][pn]
        self.assertEqual(rec["listing_key"], "eastpoint-green")
        self.assertEqual(rec["listing_key_source"], "outbound")
        self.assertTrue(rec["outbound_before_first_inbound"])
        rec["form_sent"] = True   # exactly the real incident: form_sent True anyway
        with mock.patch.object(RES.E, "excluded_reason", return_value=None):
            self.assertFalse(RES.is_established_prospect(rec))

    def test_genuine_portal_lead_with_boilerplate_and_form_sent_is_established(self):
        """A real tenant enquiry: the FIRST message is the tenant's own, carries portal
        boilerplate and names the listing through their own text (no prior outbound at
        all), and the form later gets filled -- the genuinely established shape
        is_established_prospect() must accept."""
        state = {"version": 1, "conversations": {}}
        pn = "6591230000"
        jid = pn + "@s.whatsapp.net"
        with mock.patch.object(E, "_landlord_pn_set", lambda: frozenset()):
            E.handle_event(state, {"jid": jid, "msg_id": "m1",
                                   "text": "Hi I am interested in your listing "
                                           "https://www.propertyguru.com.sg/l/12345678, "
                                           "is it still available?",
                                   "is_from_me": False, "listing_key": "genuine-listing",
                                   "ts": _sgt_ts(30)})
        rec = state["conversations"][pn]
        self.assertEqual(rec["listing_key"], "genuine-listing")
        self.assertEqual(rec["listing_key_source"], "inbound")
        self.assertFalse(rec["outbound_before_first_inbound"])
        rec["form_sent"] = True   # form went out and was filled in -- established from here
        with mock.patch.object(RES.E, "excluded_reason", return_value=None):
            self.assertTrue(RES.is_established_prospect(rec))


class TestB2DisputeBlock(unittest.TestCase):
    """B2 (Sep 2026): dispute/legal escalation language anywhere in the last 10 messages of
    a chat (either direction) blocks resume entirely -- no auto send, no draft -- and flags
    Winfred once with 'dispute language', never again while it stays in that window."""

    def test_dispute_language_recent_detects_each_keyword(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "m.db")
            con = sqlite3.connect(db)
            con.execute("CREATE TABLE messages (rowid INTEGER PRIMARY KEY, chat_jid TEXT, content TEXT)")
            for kw in RES.DISPUTE_KEYWORDS:
                con.execute("DELETE FROM messages")
                con.execute("INSERT INTO messages (chat_jid, content) VALUES (?, ?)",
                           (FAKE_JID, f"I want to {kw} this, it is not okay"))
                con.commit()
                with self.subTest(keyword=kw):
                    self.assertTrue(RES.dispute_language_recent(con, FAKE_JID))
            con.close()

    def test_winfreds_own_cea_signature_is_not_a_dispute(self):
        """Opus review, 9 Sep 2026: a bare "cea" keyword matched Winfred's OWN sign off
        ("Winfred Quek | CEA R073319H"), which appears in his own outbound in 5 of the 260
        hand takeover chats -- every one of them a false positive that silently disabled
        resume for that chat. Only explicit escalation phrasing counts now."""
        with tempfile.TemporaryDirectory() as tmp:
            con = sqlite3.connect(os.path.join(tmp, "m.db"))
            con.execute("CREATE TABLE messages (rowid INTEGER PRIMARY KEY, chat_jid TEXT, content TEXT)")
            for benign in ("Winfred Quek Crestbrick, CEA Reg No R073319H",
                           "Winfred Quek | CEA R073319H",
                           "can i get your CEA No?"):
                con.execute("DELETE FROM messages")
                con.execute("INSERT INTO messages (chat_jid, content) VALUES (?, ?)",
                            (FAKE_JID, benign))
                con.commit()
                with self.subTest(text=benign):
                    self.assertFalse(RES.dispute_language_recent(con, FAKE_JID))
            con.close()

    def test_real_cea_escalation_still_blocks(self):
        """The narrowing must not lose a real threat: an explicit "report to CEA", and the
        one genuine case in the live corpus ("They will complaint to CEA"), both still block."""
        with tempfile.TemporaryDirectory() as tmp:
            con = sqlite3.connect(os.path.join(tmp, "m.db"))
            con.execute("CREATE TABLE messages (rowid INTEGER PRIMARY KEY, chat_jid TEXT, content TEXT)")
            for bad in ("I will report to CEA if you do not return my deposit",
                        "Already reported to CEA",
                        "Cos if other peoples see. They will complaint to CEA.",
                        "I am reporting to CEA"):
                con.execute("DELETE FROM messages")
                con.execute("INSERT INTO messages (chat_jid, content) VALUES (?, ?)",
                            (FAKE_JID, bad))
                con.commit()
                with self.subTest(text=bad):
                    self.assertTrue(RES.dispute_language_recent(con, FAKE_JID))
            con.close()

    def test_dispute_language_recent_false_for_ordinary_chat(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "m.db")
            con = sqlite3.connect(db)
            con.execute("CREATE TABLE messages (rowid INTEGER PRIMARY KEY, chat_jid TEXT, content TEXT)")
            con.execute("INSERT INTO messages (chat_jid, content) VALUES (?, ?)",
                       (FAKE_JID, "is this room still available? keen to view"))
            con.commit()
            self.assertFalse(RES.dispute_language_recent(con, FAKE_JID))
            con.close()

    def test_dispute_language_recent_only_looks_at_the_last_n_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "m.db")
            con = sqlite3.connect(db)
            con.execute("CREATE TABLE messages (rowid INTEGER PRIMARY KEY, chat_jid TEXT, content TEXT)")
            con.execute("INSERT INTO messages (chat_jid, content) VALUES (?, ?)",
                       (FAKE_JID, "thinking of calling a lawyer about this"))
            for i in range(10):
                con.execute("INSERT INTO messages (chat_jid, content) VALUES (?, ?)",
                           (FAKE_JID, "ordinary message " + str(i)))
            con.commit()
            self.assertFalse(RES.dispute_language_recent(con, FAKE_JID, limit=10))
            con.close()

    def test_dispute_language_recent_fails_closed_on_query_error(self):
        """Review fix: a query error (closed connection, locked/corrupt store) must be
        treated as dispute PRESENT, never as 'no dispute seen' -- the old behaviour failed
        OPEN, so a DB hiccup could silently let a disputed chat through /send."""
        con = sqlite3.connect(":memory:")
        con.close()   # any query on a closed connection raises
        self.assertTrue(RES.dispute_language_recent(con, FAKE_JID))

    def test_dispute_language_recent_fails_closed_on_missing_table(self):
        con = sqlite3.connect(":memory:")   # no messages table created at all
        self.assertTrue(RES.dispute_language_recent(con, FAKE_JID))
        con.close()

    def test_runner_blocks_resume_send_and_flags_once_on_dispute_language(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(RES, "process_draft_needed") as mock_draft:
                with _isolated_runner(
                        tmp,
                        inbound_content="ok will wait",
                        extra_history=[(False, "I want a refund, this is unacceptable", 30)],
                        conversations={FAKE_PN: {
                            "pn": FAKE_PN, "manual_takeover": True, "human_takeover": True,
                            "last_hand_reply_ts": _sgt_ts(20), "profile": {},
                            "processed_ids": [], "form_sent": True,
                            "listing_key": "eastpoint-green"}},
                        handle_event_return={"type": "OFFER_VIEWING", "pn": FAKE_PN,
                                             "text": "Keen to view?"}) as calls:
                    pass
            self.assertEqual(calls["sent"], [])
            mock_draft.assert_not_called()
            self.assertTrue(any("dispute" in m.lower() for m in calls["notified"]))
            self.assertTrue(any(k == "RESUME_DISPUTE" for k, p, m in calls["logged"]))

    def test_already_flagged_dispute_never_re_notifies_but_still_blocks_send(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(RES, "process_draft_needed") as mock_draft:
                with _isolated_runner(
                        tmp,
                        inbound_content="still there?",
                        extra_history=[(False, "considering a police report over this", 30)],
                        conversations={FAKE_PN: {
                            "pn": FAKE_PN, "manual_takeover": True, "human_takeover": True,
                            "last_hand_reply_ts": _sgt_ts(20), "profile": {},
                            "processed_ids": [], "form_sent": True,
                            "listing_key": "test-listing", "dispute_flagged": True}},
                        handle_event_return={"type": "OFFER_VIEWING", "pn": FAKE_PN,
                                             "text": "hi"}) as calls:
                    pass
            self.assertEqual(calls["sent"], [])
            mock_draft.assert_not_called()
            self.assertFalse(any(k == "RESUME_DISPUTE" for k, p, m in calls["logged"]))
            self.assertEqual(calls["notified"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
