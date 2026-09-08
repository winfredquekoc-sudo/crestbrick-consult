"""
test_takeover_resume.py -- unittest coverage for takeover resume (Feature 1, Winfred 8 Sep
2026): "for the hand reply to stop after I hand reply ... if I don't reply in the next 5
minutes after a hand reply you can help me come up with a reply after reading the entire
chat." AUTO SEND only fixed engine templates and category 1 facts; DRAFT everything else.

Run: /usr/bin/python3 tests/wa-pipeline/test_takeover_resume.py
"""
import sys, os, sqlite3, time, unittest
from unittest import mock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src", "wa-pipeline"))
import intake_engine as E
import wa_intake_resume as RES


def _mem_db(rows):
    """rows: list of (rowid, chat_jid, is_from_me) newest-agnostic; builds a throwaway
    sqlite db with the one column set resume_reason_blocked/fetch_transcript actually
    touch (id mirrors rowid, matching the real bridge's PRAGMA-discovered id column)."""
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE messages (rowid INTEGER PRIMARY KEY, id INTEGER, "
                "chat_jid TEXT, is_from_me INTEGER, content TEXT, timestamp TEXT)")
    for rowid, jid, ifm in rows:
        con.execute("INSERT INTO messages (rowid, id, chat_jid, is_from_me, content, timestamp) "
                    "VALUES (?, ?, ?, ?, 'x', '2026-09-08 10:00:00+08:00')",
                    (rowid, rowid, jid, int(ifm)))
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
    def setUp(self):
        self._tmp = f"/tmp/test-drafts-{os.getpid()}-{time.time_ns()}.jsonl"
        self._orig = RES.DRAFTS_FILE
        RES.DRAFTS_FILE = self._tmp
        self.sent = []
        self.guard_calls = []

    def tearDown(self):
        RES.DRAFTS_FILE = self._orig
        try: os.remove(self._tmp)
        except OSError: pass

    def _send_fn(self, jid, text):
        self.sent.append((jid, text)); return True

    def _guard_fn(self, jid):
        self.guard_calls.append(jid); return True

    def test_send_marks_sent_and_calls_send_fn(self):
        did = RES.new_draft("6598880000", "6598880000@lid", "test-listing", "hello there")
        handled = RES.handle_self_chat_command(
            RES.OWN_JID, f"/send {did}", self._send_fn, self._guard_fn, lambda *a: None)
        self.assertTrue(handled)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0], ("6598880000@lid", "hello there"))
        self.assertEqual(RES.find_draft(did)["status"], "sent")

    def test_drop_marks_dropped_never_sends(self):
        did = RES.new_draft("6598880001", "6598880001@lid", "test-listing", "hello there")
        handled = RES.handle_self_chat_command(
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
        handled = RES.handle_self_chat_command(
            RES.OWN_JID, f"/send {did}", self._send_fn, self._guard_fn, lambda *a: None)
        self.assertTrue(handled)
        self.assertEqual(len(self.sent), 0)
        self.assertEqual(RES.find_draft(did)["status"], "expired")

    def test_already_sent_draft_cannot_be_sent_twice(self):
        did = RES.new_draft("6598880003", "6598880003@lid", "test-listing", "hello there")
        RES.handle_self_chat_command(RES.OWN_JID, f"/send {did}", self._send_fn,
                                     self._guard_fn, lambda *a: None)
        self.assertEqual(len(self.sent), 1)
        RES.handle_self_chat_command(RES.OWN_JID, f"/send {did}", self._send_fn,
                                     self._guard_fn, lambda *a: None)
        self.assertEqual(len(self.sent), 1)   # not sent again

    def test_unknown_draft_id_is_ignored(self):
        handled = RES.handle_self_chat_command(
            RES.OWN_JID, "/send deadbeef", self._send_fn, self._guard_fn, lambda *a: None)
        self.assertTrue(handled)
        self.assertEqual(len(self.sent), 0)

    def test_non_command_text_is_not_handled_here(self):
        handled = RES.handle_self_chat_command(
            RES.OWN_JID, "just a normal note to myself", self._send_fn, self._guard_fn,
            lambda *a: None)
        self.assertFalse(handled)

    def test_guard_reserve_failure_blocks_the_send(self):
        did = RES.new_draft("6598880004", "6598880004@lid", "test-listing", "hello there")
        handled = RES.handle_self_chat_command(
            RES.OWN_JID, f"/send {did}", self._send_fn, lambda jid: False, lambda *a: None)
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
        i_handle_call = src.index('RES.handle_self_chat_command(')
        self.assertLess(i_jid_check, i_handle_call)


class TestHaikuFailurePath(unittest.TestCase):
    def test_timeout_reports_error(self):
        import subprocess as sp
        with mock.patch.object(RES, "subprocess") as m:
            m.TimeoutExpired = sp.TimeoutExpired
            m.run.side_effect = sp.TimeoutExpired(cmd="x", timeout=25)
            text, err = RES.call_haiku("hello")
        self.assertIsNone(text)
        self.assertEqual(err, "timeout")

    def test_nonzero_exit_reports_error(self):
        with mock.patch.object(RES, "subprocess") as m:
            m.run.return_value = mock.Mock(returncode=1, stdout="", stderr="boom")
            text, err = RES.call_haiku("hello")
        self.assertIsNone(text)
        self.assertIn("exit 1", err)

    def test_success_returns_result_text(self):
        with mock.patch.object(RES, "subprocess") as m:
            m.run.return_value = mock.Mock(
                returncode=0, stdout='{"result": "sure, happy to help", "is_error": false}')
            text, err = RES.call_haiku("hello")
        self.assertIsNone(err)
        self.assertEqual(text, "sure, happy to help")

    def test_process_draft_needed_flags_human_on_failure(self):
        con = _mem_db([(1, "6598886666@lid", 0)])
        notified = []
        logged = []
        rec = {"profile": {"name": "Test"}, "listing_key": None, "last_inbound": "hmm"}
        with mock.patch.object(RES, "call_haiku", return_value=(None, "timeout")):
            RES.process_draft_needed(con, "id", "6598886666@lid", "6598886666", rec, None,
                                     notified.append, lambda k, p, m: logged.append((k, p, m)))
        self.assertEqual(len(notified), 1)
        self.assertIn("Reply by hand", notified[0])
        self.assertTrue(any(k == "RESUME_DRAFT_FAIL" for k, p, m in logged))

    def test_process_draft_needed_saves_draft_on_success(self):
        con = _mem_db([(1, "6598887777@lid", 0)])
        notified = []
        logged = []
        rec = {"profile": {"name": "Test"}, "listing_key": "test-listing", "last_inbound": "hmm"}
        tmp = f"/tmp/test-drafts-succ-{os.getpid()}.jsonl"
        orig = RES.DRAFTS_FILE
        RES.DRAFTS_FILE = tmp
        try:
            with mock.patch.object(RES, "call_haiku", return_value=("sure, come by anytime", None)):
                RES.process_draft_needed(con, "id", "6598887777@lid", "6598887777", rec, None,
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
        con = _mem_db([(1, "6598888888@lid", 0)])
        notified = []
        rec = {"profile": {"name": "Test"}, "listing_key": None, "last_inbound": "hmm"}
        tmp = f"/tmp/test-drafts-nw-{os.getpid()}.jsonl"
        orig = RES.DRAFTS_FILE
        RES.DRAFTS_FILE = tmp
        try:
            with mock.patch.object(RES, "call_haiku",
                                   return_value=("NEEDS_WINFRED: they are negotiating rent", None)):
                RES.process_draft_needed(con, "id", "6598888888@lid", "6598888888", rec, None,
                                         notified.append, lambda *a: None)
            self.assertEqual(len(notified), 1)
            self.assertIn("needs your own reply", notified[0])
            self.assertFalse(os.path.exists(tmp))
        finally:
            RES.DRAFTS_FILE = orig


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
            con = _mem_db([(1, "6598888888@lid", 0)])
            rec = {"profile": {"name": "Tester"}, "listing_key": "bayshore",
                   "last_inbound": "Roti prata couple"}
            notified, logged = [], []
            with mock.patch.object(RES, "call_haiku",
                                   return_value=("Tell me about it lol waste of time only", None)):
                RES.process_draft_needed(con, "id", "6598888888@lid", "6598888888", rec, None,
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
            RES.handle_self_chat_command(RES.OWN_JID, "/send " + did,
                                         send_fn=lambda j, t: sent.append((j, t)) or True,
                                         guard_reserve_fn=lambda j: True,
                                         log_fn=lambda *a: None)
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
            "ASK_TENANT_TIME", "LEASE_NOTE"})



if __name__ == "__main__":
    unittest.main(verbosity=2)
