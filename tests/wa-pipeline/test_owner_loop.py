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

# Telegram/bridge kill switch (incident, 9 Sep 2026 merge redo -- a sandbox harness run
# reached Winfred's real phone). Set BEFORE importing any wa-pipeline module: belt and
# suspenders alongside the per-test mock.patch calls, on top of the physical choke-point
# checks _tg_send/_send now do on their own. See wa_intake_notify.py's docstring.
os.environ["WA_INTAKE_NO_TELEGRAM"] = "1"
os.environ["WA_INTAKE_NO_SEND"] = "1"

sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
import wa_intake_owner as OWN
import wa_intake_owner_answers as OWNA
import wa_intake_resume as RES
import wa_intake_draft_worker as WORKER
import import_owner_questions as IMP


def _spawn_then_finish(con, result, notify_fn=lambda m: None, log_fn=lambda *a: None):
    """item 3 (9 Sep 2026 merge redo): OWNA.run_owner_answer_capture no longer exists --
    spawning and finishing an owner extract are now two separate calls, connected only
    through wa_intake_draft_worker's pending file. This helper drives both halves in one
    call for tests that only care about the end result: patches WORKER.spawn_request to
    capture its args instead of really Popen-ing claude-guard, runs the real
    spawn_owner_answer_extracts against `con`, then feeds each captured spawn straight into
    finish_owner_extract with `result` (a dict, JSON encoded here exactly the way a real
    claude-guard result string would arrive) as if the background job had already finished.
    Returns the list of finish_owner_extract call args, mostly for assertions that need to
    inspect the record itself."""
    calls = []

    def _fake_spawn(kind, key, jid, prompt, context=None):
        calls.append({"kind": kind, "pn": key, "jid": jid, "context": context})
        return "spawned"

    with mock.patch.object(WORKER, "spawn_request", side_effect=_fake_spawn):
        OWNA.spawn_owner_answer_extracts(con, log_fn=log_fn)
    text = json.dumps(result) if result is not None else None
    for rec in calls:
        OWNA.finish_owner_extract(rec, text, None, notify_fn, log_fn)
    return calls


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


class TestTwinSupersede(OwnerLoopTestBase):
    def test_skipped_twin_never_resends_next_day(self):
        # item 1, 11 Sep 2026 HOLD: a twin with the same landlord + code that loses the
        # dedup race (only the earliest queued entry per code is ever picked to send) must
        # be superseded the moment its sibling sends, or it sits in "queued" forever and
        # gets sent as a duplicate the next time this landlord has anything new to ask.
        self._write_landlord_db([ACTIVE_LL])
        qid1 = OWN.enqueue_owner_question("LL001", "grace-room-1", "PAX", "How many pax max",
                                           source="6598887777")
        # a pre existing twin that slipped past enqueue's own dedup (e.g. queued before the
        # 11 Sep merge, or imported directly) -- same landlord + code, still queued
        twin = dict(OWN.find_question(qid1))
        twin["id"] = "twin0001"
        twin["source"] = "6591112222"
        twin["created"] = twin["created"] + 1
        with open(OWN.QUEUE_FILE, "a") as f:
            f.write(json.dumps(twin) + "\n")
        path, con = _mkdb([])
        sent = []
        with mock.patch.object(OWN, "_now_sgt", return_value=_sgt(2026, 9, 9, 10, 0)):
            OWN.run_owner_asks(con, send_fn=lambda j, t: (sent.append((j, t)), True)[1],
                               guard_reserve_fn=lambda j: True, log_fn=lambda *a: None,
                               notify_fn=lambda m: None)
        self.assertEqual(len(sent), 1)
        self.assertEqual(OWN.find_question(qid1)["status"], "sent")
        self.assertEqual(OWN.find_question("twin0001")["status"], "superseded")

        # NEXT day: a brand new (different code) question comes in for this landlord -- the
        # superseded twin must never resend alongside it.
        OWN.enqueue_owner_question("LL001", "grace-room-1", "WIFI", "Is wifi included")
        sent2 = []
        with mock.patch.object(OWN, "_now_sgt", return_value=_sgt(2026, 9, 10, 10, 0)):
            OWN.run_owner_asks(con, send_fn=lambda j, t: (sent2.append((j, t)), True)[1],
                               guard_reserve_fn=lambda j: True, log_fn=lambda *a: None,
                               notify_fn=lambda m: None)
        self.assertEqual(len(sent2), 1)
        self.assertNotIn("pax", sent2[0][1].lower())
        self.assertEqual(OWN.find_question("twin0001")["status"], "superseded")


class TestMixedSourceFrame(OwnerLoopTestBase):
    def test_mixed_sources_use_neutral_frame(self):
        # item 5, 11 Sep 2026 HOLD: any() called a batch tenant sourced off a single tenant
        # question even when another question in the same message was Winfred's own check
        # in -- must be all(), so a mixed batch gets the neutral frame instead of
        # misattributing the check in question to "a prospective tenant asked".
        self._write_landlord_db([ACTIVE_LL])
        OWN.enqueue_owner_question("LL001", "grace-room-1", "WIFI", "Is wifi included",
                                    source="6598887777")           # tenant sourced
        OWN.enqueue_owner_question("LL001", "grace-room-1", "AIRCON", "Is aircon serviced")
        # default source="clarity-report" -- Winfred's own check in
        path, con = _mkdb([])
        sent = []
        with mock.patch.object(OWN, "_now_sgt", return_value=_sgt(2026, 9, 9, 10, 0)):
            OWN.run_owner_asks(con, send_fn=lambda j, t: (sent.append((j, t)), True)[1],
                               guard_reserve_fn=lambda j: True, log_fn=lambda *a: None,
                               notify_fn=lambda m: None)
        self.assertEqual(len(sent), 1)
        text = sent[0][1]
        self.assertNotIn("A prospective tenant asked", text)
        self.assertIn("Could I check", text)


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
        _spawn_then_finish(con, {"WIFI": {"value": "included, unlimited",
                                          "quote": "yes wifi is included, unlimited"}},
                          notify_fn=lambda m: notes.append(m))
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
        _spawn_then_finish(con, {"WIFI": None})
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
        calls = _spawn_then_finish(con, None)
        self.assertEqual(calls, [])   # no reply yet -- nothing spawned at all
        q = OWN.find_question(qid)
        self.assertEqual(q["status"], "sent")


class TestExtractFailureHandling(OwnerLoopTestBase):
    def test_timeout_never_resurrects_a_merged_twin(self):
        # item 2, 11 Sep 2026 HOLD: a stale context snapshot (captured when the background
        # extract was spawned) must never overwrite a twin's CURRENT status -- e.g. 'merged',
        # set in the meantime by a different extract that already answered this same
        # landlord + code -- with what its status used to be. Doing so resurrects the twin.
        self._write_landlord_db([ACTIVE_LL])
        qid_a = OWN.enqueue_owner_question("LL001", "grace-room-1", "WIFI", "Is wifi included")
        twin_b = {"id": "twinB001", "landlord_id": "LL001", "listing_key": "grace-room-1",
                  "question_code": "WIFI", "question_text": "Is wifi ok too",
                  "source": "6591112222", "source_jid": None, "created": time.time(),
                  "status": "queued", "asked_at": None, "answer": None, "evidence": None,
                  "sources": [{"source": "6591112222", "source_jid": None}]}
        with open(OWN.QUEUE_FILE, "a") as f:
            f.write(json.dumps(twin_b) + "\n")
        asked = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)).isoformat()
        OWN.mark_question(qid_a, "sent", asked_at=asked)
        OWN.mark_question("twinB001", "sent", asked_at=asked)

        # a's extract answers WIFI, merging b
        OWN.mark_question(qid_a, "answered", answer="included")
        OWN.merge_twins("LL001", "WIFI", qid_a)
        self.assertEqual(OWN.find_question("twinB001")["status"], "merged")

        # b's OWN extract (spawned before the merge, so its context snapshot still says
        # 'sent') now times out
        stale_b_snapshot = {"id": "twinB001", "question_code": "WIFI",
                             "question_text": "Is wifi ok too"}
        record = {"context": {"lid": "LL001", "qs": [stale_b_snapshot], "landlord_name": "Grace"},
                  "jid": "6591234567@s.whatsapp.net"}
        OWNA.finish_owner_extract(record, None, None, lambda m: None, lambda *a: None, timed_out=True)
        q = OWN.find_question("twinB001")
        self.assertEqual(q["status"], "merged")           # never resurrected to 'sent'
        self.assertNotIn("extract_attempts", q)            # not touched -- it was not pending

    def test_parse_failure_counts_toward_cap(self):
        # item 3, 11 Sep 2026 HOLD: a JSON parse failure used to just log and return, never
        # counting against MAX_EXTRACT_ATTEMPTS -- a landlord whose reply Haiku could never
        # parse would get re-spawned forever. Must count exactly like a timeout, and hit
        # extract_failed + one ping at the cap.
        self._write_landlord_db([ACTIVE_LL])
        qid = OWN.enqueue_owner_question("LL001", "grace-room-1", "WIFI", "Is wifi included")
        asked = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)).isoformat()
        OWN.mark_question(qid, "sent", asked_at=asked)
        q_ctx = OWN.find_question(qid)
        record = {"context": {"lid": "LL001", "qs": [q_ctx], "landlord_name": "Grace"},
                  "jid": "6591234567@s.whatsapp.net"}
        notes = []

        # first parse failure: attempt 1 of 2, still under the cap
        OWNA.finish_owner_extract(record, "not json at all", None, lambda m: notes.append(m),
                                  lambda *a: None)
        q = OWN.find_question(qid)
        self.assertEqual(q["status"], "sent")
        self.assertEqual(q["extract_attempts"], 1)
        self.assertEqual(notes, [])

        # second parse failure hits the cap: extract_failed + exactly one ping
        OWNA.finish_owner_extract(record, "still not json", None, lambda m: notes.append(m),
                                  lambda *a: None)
        q = OWN.find_question(qid)
        self.assertEqual(q["status"], "extract_failed")
        self.assertEqual(q["extract_attempts"], 2)
        self.assertEqual(len(notes), 1)


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
        _spawn_then_finish(con, {"AIRCON": {"value": "serviced every 3 months",
                                            "quote": "yes serviced every 3 months"}})

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


class TestOwnerCheckInFrame(OwnerLoopTestBase):
    def test_tenant_sourced_one_question(self):
        msg = OWN.build_owner_message("Grace", ["Is cooking allowed"], tenant_sourced=True)
        self.assertIsNotNone(msg)
        self.assertIn("thank you for your time", msg)
        self.assertIn("A prospective tenant asked:", msg)
        self.assertNotIn("-", msg)
        self.assertTrue(msg.endswith("No rush, whenever convenient 🙏"))

    def test_not_tenant_sourced_one_question(self):
        msg = OWN.build_owner_message("Grace", ["Is cooking allowed"], tenant_sourced=False)
        self.assertIsNotNone(msg)
        self.assertIn("hope all is well", msg)
        self.assertIn("Could I check one thing when you have a moment:", msg)
        self.assertNotIn("tenant asked", msg.lower())
        self.assertNotIn("-", msg)
        self.assertTrue(msg.endswith("No rush, whenever convenient 🙏"))

    def test_not_tenant_sourced_two_questions(self):
        msg = OWN.build_owner_message("Grace", ["Is cooking allowed", "Is wifi included"], tenant_sourced=False)
        self.assertIsNotNone(msg)
        self.assertIn("Could I check a few things when you have a moment:", msg)
        self.assertIn("1. ", msg)
        self.assertIn("2. ", msg)
        self.assertNotIn("-", msg)
        self.assertTrue(msg.endswith("No rush, whenever convenient 🙏"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
