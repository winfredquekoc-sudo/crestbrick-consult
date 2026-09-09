"""
test_owner_loop.py -- unittest coverage for the landlord side of the loop
(src/wa-pipeline/wa_intake_owner.py + wa_intake_owner_answers.py + scripts/
import_owner_questions.py). Everything here runs against temp files; nothing touches the
real landlord-db.json, listing-index.json, messages.db, or owner-questions.jsonl.

Run: /usr/bin/python3 tests/wa-pipeline/test_owner_loop.py
"""
import sys, os, json, time, sqlite3, tempfile, shutil, datetime, unittest
from unittest import mock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src", "wa-pipeline"))
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
import wa_intake_owner as OWN
import wa_intake_owner_answers as OWNA
import wa_intake_resume as RES
import import_owner_questions as IMP


def _sgt(y, mo, d, h, mi, s=0):
    return datetime.datetime(y, mo, d, h, mi, s, tzinfo=datetime.timezone(datetime.timedelta(hours=8)))


def _mkdb(rows):
    """rows: (chat_jid, is_from_me, content, timestamp_iso). File-based (not :memory:) so
    it behaves exactly like the real bridge for every query this module runs."""
    path = tempfile.mktemp(suffix=".db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE messages (chat_jid TEXT, is_from_me INTEGER, content TEXT, timestamp TEXT)")
    for jid, ifm, content, ts in rows:
        con.execute("INSERT INTO messages (chat_jid, is_from_me, content, timestamp) VALUES (?,?,?,?)",
                    (jid, int(ifm), content, ts))
    con.commit()
    return path, con


class OwnerLoopTestBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._orig = {
            "QUEUE_FILE": OWN.QUEUE_FILE, "LANDLORD_DB": OWN.LANDLORD_DB,
            "REFRESH_LOCK_DIR": OWN.REFRESH_LOCK_DIR, "IDX": OWNA.IDX,
            "DRAFTS_FILE": RES.DRAFTS_FILE,
        }
        OWN.QUEUE_FILE = os.path.join(self._tmpdir, "owner-questions.jsonl")
        OWN.LANDLORD_DB = os.path.join(self._tmpdir, "landlord-db.json")
        OWN.REFRESH_LOCK_DIR = os.path.join(self._tmpdir, "refresh.lock.d")
        OWNA.IDX = os.path.join(self._tmpdir, "listing-index.json")
        RES.DRAFTS_FILE = os.path.join(self._tmpdir, "drafts.jsonl")

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(OWN if k in ("QUEUE_FILE", "LANDLORD_DB", "REFRESH_LOCK_DIR") else
                    (OWNA if k == "IDX" else RES), k, v)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _write_landlord_db(self, landlords):
        json.dump({"landlords": landlords}, open(OWN.LANDLORD_DB, "w"))

    def _write_index(self, listings):
        json.dump({"listings": listings}, open(OWNA.IDX, "w"))


ACTIVE_LL = {"id": "LL001", "landlord_name": "Grace", "phone": "6591234567",
             "chat_jid": "6591234567@s.whatsapp.net", "status": "active",
             "listing_key": "grace-room-1"}


class TestQuestionSafety(OwnerLoopTestBase):
    def test_rejects_commission(self):
        self.assertIsNotNone(OWN._question_is_safe("What is the commission rate you want"))

    def test_rejects_price(self):
        self.assertIsNotNone(OWN._question_is_safe("What is the rent for the room"))

    def test_rejects_race(self):
        self.assertIsNotNone(OWN._question_is_safe("Would you consider a Chinese only tenant"))

    def test_rejects_deposit(self):
        self.assertIsNotNone(OWN._question_is_safe("What deposit amount do you want"))

    def test_accepts_factual(self):
        self.assertIsNone(OWN._question_is_safe("Is wifi included in the room"))

    def test_enqueue_rejects_unknown_code(self):
        self._write_landlord_db([ACTIVE_LL])
        qid = OWN.enqueue_owner_question("LL001", "grace-room-1", "BANANA", "Is wifi included")
        self.assertIsNone(qid)
        self.assertEqual(OWN._load_queue(), [])

    def test_enqueue_rejects_unsafe_question(self):
        qid = OWN.enqueue_owner_question("LL001", "grace-room-1", "WIFI",
                                          "What commission do you want on this")
        self.assertIsNone(qid)

    def test_enqueue_accepts_clean_question(self):
        qid = OWN.enqueue_owner_question("LL001", "grace-room-1", "WIFI", "Is wifi included")
        self.assertIsNotNone(qid)
        q = OWN.find_question(qid)
        self.assertEqual(q["status"], "queued")
        self.assertEqual(q["question_code"], "WIFI")


class TestToneTemplate(OwnerLoopTestBase):
    def test_no_hyphens(self):
        msg = OWN.build_owner_message("Grace", ["Is cooking allowed", "Is wifi included"])
        self.assertIsNotNone(msg)
        self.assertNotIn("-", msg)

    def test_no_price_words(self):
        msg = OWN.build_owner_message("Grace", ["Is cooking allowed"])
        low = msg.lower()
        for bad in ("rent", "price", "$", "commission"):
            self.assertNotIn(bad, low)

    def test_no_tenant_identity(self):
        msg = OWN.build_owner_message("Grace", ["Is cooking allowed"])
        self.assertNotIn("+65", msg)
        self.assertNotIn("tenant is called", msg.lower())

    def test_no_rush_phrase_present(self):
        msg = OWN.build_owner_message("Grace", ["Is cooking allowed"])
        self.assertIn("No rush", msg)

    def test_greets_by_name(self):
        msg = OWN.build_owner_message("Grace", ["Is cooking allowed"])
        self.assertTrue(msg.startswith("Hi Grace"))

    def test_unknown_name_falls_back_to_bare_hi(self):
        msg = OWN.build_owner_message("", ["Is cooking allowed"])
        self.assertTrue(msg.startswith("Hi,"))

    def test_annotation_stripped_from_name(self):
        msg = OWN.build_owner_message("Kumar (proxy for landlord; brother-in-law owner)",
                                       ["Is cooking allowed"])
        self.assertIsNotNone(msg)
        self.assertTrue(msg.startswith("Hi Kumar,"))
        self.assertNotIn("-", msg)

    def test_multiple_questions_numbered(self):
        msg = OWN.build_owner_message("Grace", ["Is cooking allowed", "Is wifi included",
                                                  "Is aircon serviced"])
        self.assertIn("1. ", msg)
        self.assertIn("2. ", msg)
        self.assertIn("3. ", msg)

    def test_caps_at_three_questions(self):
        msg = OWN.build_owner_message("Grace", ["Q1 is this ok", "Q2 is this ok",
                                                  "Q3 is this ok", "Q4 is this ok"])
        self.assertNotIn("Q4", msg)

    def test_message_with_unsafe_question_refused(self):
        # defense in depth: even if something bypassed enqueue's own check, the builder
        # itself refuses to emit a message containing a banned topic.
        msg = OWN.build_owner_message("Grace", ["What commission would you like"])
        self.assertIsNone(msg)


class TestAskWindow(OwnerLoopTestBase):
    def test_before_9am_not_in_window(self):
        with mock.patch.object(OWN, "_now_sgt", return_value=_sgt(2026, 9, 9, 8, 59)):
            self.assertFalse(OWN.in_ask_window())

    def test_9am_in_window(self):
        with mock.patch.object(OWN, "_now_sgt", return_value=_sgt(2026, 9, 9, 9, 0)):
            self.assertTrue(OWN.in_ask_window())

    def test_2059_in_window(self):
        with mock.patch.object(OWN, "_now_sgt", return_value=_sgt(2026, 9, 9, 20, 59)):
            self.assertTrue(OWN.in_ask_window())

    def test_9pm_not_in_window(self):
        with mock.patch.object(OWN, "_now_sgt", return_value=_sgt(2026, 9, 9, 21, 0)):
            self.assertFalse(OWN.in_ask_window())

    def test_3am_not_in_window(self):
        with mock.patch.object(OWN, "_now_sgt", return_value=_sgt(2026, 9, 9, 3, 0)):
            self.assertFalse(OWN.in_ask_window())


class TestNeverTalkOverHim(OwnerLoopTestBase):
    def test_recent_outbound_blocks(self):
        now = _sgt(2026, 9, 9, 12, 0)
        path, con = _mkdb([("j@s.whatsapp.net", 1, "hi", (now - datetime.timedelta(minutes=10)).isoformat())])
        with mock.patch.object(OWN, "_now_sgt", return_value=now):
            self.assertIsNotNone(OWN._recent_conversation_blocks_send(con, "j@s.whatsapp.net"))

    def test_old_outbound_does_not_block(self):
        now = _sgt(2026, 9, 9, 12, 0)
        path, con = _mkdb([("j@s.whatsapp.net", 1, "hi", (now - datetime.timedelta(minutes=90)).isoformat())])
        with mock.patch.object(OWN, "_now_sgt", return_value=now):
            self.assertIsNone(OWN._recent_conversation_blocks_send(con, "j@s.whatsapp.net"))

    def test_recent_unanswered_inbound_blocks(self):
        now = _sgt(2026, 9, 9, 12, 0)
        path, con = _mkdb([("j@s.whatsapp.net", 0, "hello", (now - datetime.timedelta(hours=2)).isoformat())])
        with mock.patch.object(OWN, "_now_sgt", return_value=now):
            self.assertIsNotNone(OWN._recent_conversation_blocks_send(con, "j@s.whatsapp.net"))

    def test_old_unanswered_inbound_does_not_block(self):
        now = _sgt(2026, 9, 9, 12, 0)
        path, con = _mkdb([("j@s.whatsapp.net", 0, "hello", (now - datetime.timedelta(hours=8)).isoformat())])
        with mock.patch.object(OWN, "_now_sgt", return_value=now):
            self.assertIsNone(OWN._recent_conversation_blocks_send(con, "j@s.whatsapp.net"))

    def test_no_history_does_not_block(self):
        path, con = _mkdb([])
        self.assertIsNone(OWN._recent_conversation_blocks_send(con, "j@s.whatsapp.net"))


class TestDailyCap(OwnerLoopTestBase):
    def test_only_one_send_per_landlord_per_day(self):
        self._write_landlord_db([ACTIVE_LL])
        OWN.enqueue_owner_question("LL001", "grace-room-1", "WIFI", "Is wifi included")
        OWN.enqueue_owner_question("LL001", "grace-room-1", "AIRCON", "Is aircon serviced")
        path, con = _mkdb([])
        sent = []
        with mock.patch.object(OWN, "_now_sgt", return_value=_sgt(2026, 9, 9, 10, 0)):
            OWN.run_owner_asks(con, send_fn=lambda j, t: (sent.append((j, t)), True)[1],
                               guard_reserve_fn=lambda j: True, log_fn=lambda *a: None,
                               notify_fn=lambda m: None)
            self.assertEqual(len(sent), 1)
            # enqueue a THIRD question the same day -- must not trigger a second send today
            OWN.enqueue_owner_question("LL001", "grace-room-1", "MRT", "How far is the MRT")
            OWN.run_owner_asks(con, send_fn=lambda j, t: (sent.append((j, t)), True)[1],
                               guard_reserve_fn=lambda j: True, log_fn=lambda *a: None,
                               notify_fn=lambda m: None)
        self.assertEqual(len(sent), 1)

    def test_caps_at_three_questions_per_message(self):
        self._write_landlord_db([ACTIVE_LL])
        for code, txt in (("WIFI", "wifi ok"), ("AIRCON", "aircon ok"),
                          ("MRT", "mrt ok"), ("PAX", "pax ok")):
            OWN.enqueue_owner_question("LL001", "grace-room-1", code, txt)
        path, con = _mkdb([])
        sent = []
        with mock.patch.object(OWN, "_now_sgt", return_value=_sgt(2026, 9, 9, 10, 0)):
            OWN.run_owner_asks(con, send_fn=lambda j, t: (sent.append((j, t)), True)[1],
                               guard_reserve_fn=lambda j: True, log_fn=lambda *a: None,
                               notify_fn=lambda m: None)
        self.assertEqual(len(sent), 1)
        remaining_queued = [q for q in OWN._load_queue() if q["status"] == "queued"]
        self.assertEqual(len(remaining_queued), 1)   # 4 asked, cap 3 -> 1 left for tomorrow


class TestImporterSkip(unittest.TestCase):
    def test_classify_banned_topics(self):
        for bullet in ("What commission would you like",
                       "What is the asking rent for the room",
                       "What deposit amount would you like",
                       "Chinese only or any race",
                       "Is there a dispute over the refund"):
            code, reason = IMP.classify(bullet)
            self.assertIsNone(code, bullet)
            self.assertIsNotNone(reason)

    def test_classify_factual_matches(self):
        cases = {"Is wifi included in the room": "WIFI",
                 "Is cooking allowed in the unit": "COOKING",
                 "Is smoking allowed at the unit": "SMOKING",
                 "How far is the nearest MRT": "MRT",
                 "Are pets allowed": "PETS"}
        for bullet, code in cases.items():
            got, reason = IMP.classify(bullet)
            self.assertEqual(got, code, bullet)
            self.assertIsNone(reason)

    def test_parse_section_c_extracts_bullets(self):
        text = ("## Section C -- Questions to ask\n\n"
                "**Grace** (+6591234567)\n"
                "- Is wifi included\n"
                "- What is the rent\n\n"
                "## Section D -- something else\n"
                "- should not be parsed\n")
        got = list(IMP.parse_section_c(text))
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0], ("Grace", "+6591234567", "Is wifi included"))


class TestAnswerExtraction(OwnerLoopTestBase):
    def test_answered_records_fact_and_drafts_tenant_followup(self):
        self._write_landlord_db([ACTIVE_LL])
        self._write_index([{"listing_key": "grace-room-1", "facts": {}, "requirements": {}}])
        qid = OWN.enqueue_owner_question("LL001", "grace-room-1", "WIFI",
                                          "Is wifi included", source="6598887777",
                                          source_jid="6598887777@s.whatsapp.net")
        OWN.mark_question(qid, "sent", asked_at=(datetime.datetime.now(datetime.timezone.utc)
                                                  - datetime.timedelta(hours=2)).isoformat())
        path, con = _mkdb([("6591234567@s.whatsapp.net", 0, "yes wifi is included, unlimited",
                            datetime.datetime.now(datetime.timezone.utc).isoformat())])
        notes = []
        with mock.patch.object(OWNA, "call_haiku_extract",
                               return_value=({"WIFI": {"value": "included, unlimited",
                                                       "quote": "yes wifi is included, unlimited"}}, None)):
            OWNA.run_owner_answer_capture(con, notify_fn=lambda m: notes.append(m),
                                          log_fn=lambda *a: None)
        q = OWN.find_question(qid)
        self.assertEqual(q["status"], "answered")
        self.assertEqual(q["answer"], "included, unlimited")
        db = json.load(open(OWN.LANDLORD_DB))
        self.assertEqual(len(db["landlords"][0]["wa_evidence"]), 1)
        self.assertEqual(db["landlords"][0]["wa_evidence"][0]["code"], "WIFI")
        idx = json.load(open(OWNA.IDX))
        self.assertEqual(idx["listings"][0]["facts"]["wifi"], "included, unlimited")
        drafts = RES._load_drafts()
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0]["pn"], "6598887777")
        self.assertTrue(any("wifi" in n.lower() for n in notes))

    def test_unclear_answer_marks_drafted(self):
        self._write_landlord_db([ACTIVE_LL])
        self._write_index([{"listing_key": "grace-room-1", "facts": {}, "requirements": {}}])
        qid = OWN.enqueue_owner_question("LL001", "grace-room-1", "WIFI", "Is wifi included",
                                          source="6598887777")
        OWN.mark_question(qid, "sent", asked_at=(datetime.datetime.now(datetime.timezone.utc)
                                                  - datetime.timedelta(hours=2)).isoformat())
        path, con = _mkdb([("6591234567@s.whatsapp.net", 0, "let me check with my wife",
                            datetime.datetime.now(datetime.timezone.utc).isoformat())])
        with mock.patch.object(OWNA, "call_haiku_extract", return_value=({"WIFI": None}, None)):
            OWNA.run_owner_answer_capture(con, notify_fn=lambda m: None, log_fn=lambda *a: None)
        q = OWN.find_question(qid)
        self.assertEqual(q["status"], "drafted")
        drafts = RES._load_drafts()
        self.assertEqual(len(drafts), 1)   # a clarifying follow up to the LANDLORD

    def test_no_reply_yet_leaves_question_untouched(self):
        self._write_landlord_db([ACTIVE_LL])
        qid = OWN.enqueue_owner_question("LL001", "grace-room-1", "WIFI", "Is wifi included")
        OWN.mark_question(qid, "sent", asked_at=(datetime.datetime.now(datetime.timezone.utc)
                                                  - datetime.timedelta(hours=2)).isoformat())
        path, con = _mkdb([])   # nothing from the landlord at all
        OWNA.run_owner_answer_capture(con, notify_fn=lambda m: None, log_fn=lambda *a: None)
        q = OWN.find_question(qid)
        self.assertEqual(q["status"], "sent")


class TestChaseAndExpiry(OwnerLoopTestBase):
    def test_chase_fires_after_48h_no_answer(self):
        self._write_landlord_db([ACTIVE_LL])
        qid = OWN.enqueue_owner_question("LL001", "grace-room-1", "WIFI", "Is wifi included")
        old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=49)).isoformat()
        OWN.mark_question(qid, "sent", asked_at=old)
        path, con = _mkdb([])
        sent = []
        with mock.patch.object(OWN, "_now_sgt", return_value=_sgt(2026, 9, 9, 10, 0)):
            OWN.run_owner_chases(con, send_fn=lambda j, t: (sent.append(t), True)[1],
                                 guard_reserve_fn=lambda j: True, log_fn=lambda *a: None,
                                 notify_fn=lambda m: None)
        self.assertEqual(len(sent), 1)
        self.assertIn("checking in", sent[0].lower())
        q = OWN.find_question(qid)
        self.assertEqual(q["status"], "chased")

    def test_no_chase_before_48h(self):
        self._write_landlord_db([ACTIVE_LL])
        qid = OWN.enqueue_owner_question("LL001", "grace-room-1", "WIFI", "Is wifi included")
        recent = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=10)).isoformat()
        OWN.mark_question(qid, "sent", asked_at=recent)
        path, con = _mkdb([])
        sent = []
        with mock.patch.object(OWN, "_now_sgt", return_value=_sgt(2026, 9, 9, 10, 0)):
            OWN.run_owner_chases(con, send_fn=lambda j, t: (sent.append(t), True)[1],
                                 guard_reserve_fn=lambda j: True, log_fn=lambda *a: None,
                                 notify_fn=lambda m: None)
        self.assertEqual(len(sent), 0)

    def test_expires_48h_after_chase_never_a_third_message(self):
        self._write_landlord_db([ACTIVE_LL])
        qid = OWN.enqueue_owner_question("LL001", "grace-room-1", "WIFI", "Is wifi included")
        old_chase = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=49)).isoformat()
        OWN.mark_question(qid, "chased", asked_at=old_chase, chased_at=old_chase)
        path, con = _mkdb([])
        sent = []
        notes = []
        with mock.patch.object(OWN, "_now_sgt", return_value=_sgt(2026, 9, 9, 10, 0)):
            OWN.run_owner_chases(con, send_fn=lambda j, t: (sent.append(t), True)[1],
                                 guard_reserve_fn=lambda j: True, log_fn=lambda *a: None,
                                 notify_fn=lambda m: notes.append(m))
        self.assertEqual(len(sent), 0)   # never a 3rd message
        q = OWN.find_question(qid)
        self.assertEqual(q["status"], "expired")
        self.assertTrue(notes)


class TestViewingWindowParsing(unittest.TestCase):
    POSITIVE = [
        "Saturdays 10am to 12pm", "every Saturday 10am to 12pm", "Sat 2pm to 4pm",
        "Sunday 9am to 11am", "every Sunday 2pm to 4pm", "Fridays 6pm to 8pm",
        "Wed 7pm to 9pm", "Monday 10am to 12pm", "Tuesdays 3pm to 5pm",
        "Thursday 6.30pm to 8pm",
    ]
    NEGATIVE = [
        "weekday after 7.30pm, Sat 10 to 12", "weekends 10am to 12pm", "Sat 10 to 12",
        "anytime, just message me", "flexible, whenever you're free",
        "Sat and Sun 10am to 12pm", "after 7.30pm on weekdays", "Saturday",
        "10am to 12pm", "most evenings work",
    ]

    def test_positive_cases_parse_unambiguously(self):
        for text in self.POSITIVE:
            fv, free = OWNA.parse_viewing_window(text)
            self.assertIsNotNone(fv, text)
            self.assertIsNone(free, text)
            self.assertIn(fv["weekday"], ("mon", "tue", "wed", "thu", "fri", "sat", "sun"))
            self.assertRegex(fv["start"], r"^\d{2}:\d{2}$")
            self.assertRegex(fv["end"], r"^\d{2}:\d{2}$")

    def test_negative_cases_stay_free_text(self):
        for text in self.NEGATIVE:
            fv, free = OWNA.parse_viewing_window(text)
            self.assertIsNone(fv, text)
            self.assertEqual(free, text)

    def test_pm_hour_converted_to_24h(self):
        fv, _ = OWNA.parse_viewing_window("Saturdays 2pm to 4pm")
        self.assertEqual(fv["start"], "14:00")
        self.assertEqual(fv["end"], "16:00")

    def test_am_hour_stays_as_is(self):
        fv, _ = OWNA.parse_viewing_window("Sunday 9am to 11am")
        self.assertEqual(fv["start"], "09:00")
        self.assertEqual(fv["end"], "11:00")


class TestEndToEnd(OwnerLoopTestBase):
    def test_ask_answer_record_and_tenant_followup(self):
        self._write_landlord_db([ACTIVE_LL])
        self._write_index([{"listing_key": "grace-room-1", "facts": {}, "requirements": {}}])
        qid = OWN.enqueue_owner_question("LL001", "grace-room-1", "AIRCON",
                                          "Is aircon serviced regularly", source="6598887777",
                                          source_jid="6598887777@s.whatsapp.net")
        path, con = _mkdb([])
        sent_to_owner = []
        with mock.patch.object(OWN, "_now_sgt", return_value=_sgt(2026, 9, 9, 11, 0)):
            OWN.run_owner_asks(con, send_fn=lambda j, t: (sent_to_owner.append((j, t)), True)[1],
                               guard_reserve_fn=lambda j: True, log_fn=lambda *a: None,
                               notify_fn=lambda m: None)
        self.assertEqual(len(sent_to_owner), 1)
        jid, text = sent_to_owner[0]
        self.assertEqual(jid, "6591234567@s.whatsapp.net")
        self.assertIn("aircon", text.lower())
        q = OWN.find_question(qid)
        self.assertEqual(q["status"], "sent")

        # landlord replies in the sandbox db
        con.execute("INSERT INTO messages (chat_jid, is_from_me, content, timestamp) VALUES (?,?,?,?)",
                    ("6591234567@s.whatsapp.net", 0, "yes serviced every 3 months",
                     datetime.datetime.now(datetime.timezone.utc).isoformat()))
        con.commit()
        with mock.patch.object(OWNA, "call_haiku_extract",
                               return_value=({"AIRCON": {"value": "serviced every 3 months",
                                                         "quote": "yes serviced every 3 months"}}, None)):
            OWNA.run_owner_answer_capture(con, notify_fn=lambda m: None, log_fn=lambda *a: None)

        q = OWN.find_question(qid)
        self.assertEqual(q["status"], "answered")
        db = json.load(open(OWN.LANDLORD_DB))
        self.assertEqual(db["landlords"][0]["wa_evidence"][0]["code"], "AIRCON")
        idx = json.load(open(OWNA.IDX))
        self.assertEqual(idx["listings"][0]["facts"]["aircon"], "serviced every 3 months")
        drafts = RES._load_drafts()
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0]["pn"], "6598887777")
        self.assertIn("aircon", drafts[0]["text"].lower())
        self.assertIsNone(RES.validate_draft(drafts[0]["text"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
