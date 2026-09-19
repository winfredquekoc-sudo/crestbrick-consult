"""
test_time_reply_cap.py -- FIX 1 (9 Sep 2026): the daily send cap (DAILY_SEND_CAP=2) was
dead ending a tenant who proposed a viewing time right after the happy path form (2
touches). VIEWING_TIME_PROPOSED / ASK_TENANT_TIME are now exempt, but bounded separately to
ONE extra touch a client a SGT day (rec["time_reply_sent_date"]) -- never the ordinary
cap, and never a second reply the same day. Real incidents: pn 6589824485 ("Would tmr night
work?") and pn 6584553538 ("this weekend Saturday can?") both got DAILY_CAP_SKIP and heard
nothing until Winfred replied by hand.

Merge review (9 Sep 2026, four-branch intake merge): REDIRECT and LEASE_NOTE get the SAME
bounded-once treatment as the time reply, each via its own date stamp
(rec["redirect_sent_date"] / rec["lease_note_reply_sent_date"]) -- ONE free touch a SGT day,
never the ordinary ceiling, but never a second one the same day either. An earlier cut made
them fully exempt (see the attack-fixes cycle 3 commit); that assumed both were always
already-latched one-shots upstream in intake_engine, which is true in production but is not
something this runner-level safeguard should rely on -- defense in depth.

Exercises the real runner send choke point end to end (throwaway sqlite messages.db +
throwaway intake-state.json, _send stubbed) via the _isolated_runner harness already built
for the takeover resume send site tests -- never a live path.

Run: /usr/bin/python3 tests/wa-pipeline/test_time_reply_cap.py
"""
import sys, os, time, json, tempfile, unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src", "wa-pipeline"))

# Telegram/bridge kill switch (incident, 9 Sep 2026 merge redo -- a sandbox harness run
# reached Winfred's real phone). Set BEFORE importing any wa-pipeline module: belt and
# suspenders alongside the per-test mock.patch calls, on top of the physical choke-point
# checks _tg_send/_send now do on their own. See wa_intake_notify.py's docstring.
os.environ["WA_INTAKE_NO_TELEGRAM"] = "1"
os.environ["WA_INTAKE_NO_SEND"] = "1"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wa_intake_runner as R
import test_takeover_resume as TTR   # reuses _isolated_runner, FAKE_PN, FAKE_JID

FAKE_PN = TTR.FAKE_PN
FAKE_JID = TTR.FAKE_JID
_isolated_runner = TTR._isolated_runner


def _today_sgt():
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))


def _read_state(tmp_dir):
    with open(os.path.join(tmp_dir, "intake-state.json")) as f:
        return json.load(f)


class TestTimeReplyExemption(unittest.TestCase):
    """Never exempt SEND_FORM, NUDGE_INCOMPLETE or a category 2 reply (ANSWER_QUESTION) at
    all. VIEWING_TIME_PROPOSED / ASK_TENANT_TIME / REDIRECT / LEASE_NOTE each get ONE bounded
    touch a day (their own date stamp), never the ordinary cap -- but never a second one the
    same day either."""

    def _run(self, conversations, handle_event_return):
        with tempfile.TemporaryDirectory() as tmp:
            with _isolated_runner(tmp, conversations=conversations,
                                  handle_event_return=handle_event_return) as calls:
                state = _read_state(tmp)
            return calls, state

    def test_after_form_and_offer_a_tenant_time_proposal_gets_one_reply(self):
        # simulates the real incident state: 2 form touches + 1 exempt OFFER_VIEWING
        # already sent today, no time reply sent yet
        today = _today_sgt()
        rec = {"pn": FAKE_PN, "sends_today_date": today, "sends_today": 3}
        calls, state = self._run(
            {FAKE_PN: rec},
            {"type": "VIEWING_TIME_PROPOSED", "pn": FAKE_PN,
             "text": "Got it, let me confirm that slot with the owner and get back to you shortly."})
        self.assertEqual(calls["sent"],
                         [(FAKE_JID, "Got it, let me confirm that slot with the owner and "
                                     "get back to you shortly.")])
        self.assertFalse(any(k == "DAILY_CAP_SKIP" for k, p, m in calls["logged"]))
        self.assertEqual(state["conversations"][FAKE_PN].get("time_reply_sent_date"), today)

    def test_ask_tenant_time_also_gets_one_reply(self):
        today = _today_sgt()
        rec = {"pn": FAKE_PN, "sends_today_date": today, "sends_today": 3}
        calls, state = self._run(
            {FAKE_PN: rec},
            {"type": "ASK_TENANT_TIME", "pn": FAKE_PN,
             "text": "No worries. When are you free to view? Just let me know a day and "
                     "time and I will arrange it."})
        self.assertEqual(len(calls["sent"]), 1)
        self.assertEqual(state["conversations"][FAKE_PN].get("time_reply_sent_date"), today)

    def test_a_second_time_proposal_the_same_day_does_not_send(self):
        today = _today_sgt()
        rec = {"pn": FAKE_PN, "sends_today_date": today, "sends_today": 4,
               "time_reply_sent_date": today}
        calls, state = self._run(
            {FAKE_PN: rec},
            {"type": "VIEWING_TIME_PROPOSED", "pn": FAKE_PN,
             "text": "Got it, let me confirm that slot with the owner and get back to you shortly."})
        self.assertEqual(calls["sent"], [])
        self.assertTrue(any(k == "DAILY_CAP_SKIP" for k, p, m in calls["logged"]))

    def test_yesterdays_time_reply_does_not_block_a_new_one_today(self):
        rec = {"pn": FAKE_PN, "time_reply_sent_date": "2026-01-01"}
        calls, state = self._run(
            {FAKE_PN: rec},
            {"type": "VIEWING_TIME_PROPOSED", "pn": FAKE_PN, "text": "Got it, noted."})
        self.assertEqual(calls["sent"], [(FAKE_JID, "Got it, noted.")])

    def test_nudge_after_the_time_reply_still_hits_the_ordinary_cap(self):
        today = _today_sgt()
        rec = {"pn": FAKE_PN, "sends_today_date": today, "sends_today": R.DAILY_SEND_CAP,
               "time_reply_sent_date": today}
        calls, state = self._run(
            {FAKE_PN: rec},
            {"type": "NUDGE_INCOMPLETE", "pn": FAKE_PN, "text": "hi"})
        self.assertEqual(calls["sent"], [])
        self.assertTrue(any(k == "DAILY_CAP_SKIP" for k, p, m in calls["logged"]))

    def test_send_form_never_exempt_even_at_the_cap(self):
        today = _today_sgt()
        rec = {"pn": FAKE_PN, "sends_today_date": today, "sends_today": R.DAILY_SEND_CAP}
        calls, state = self._run(
            {FAKE_PN: rec},
            {"type": "SEND_FORM", "pn": FAKE_PN, "texts": ["unit info", "form"]})
        self.assertEqual(calls["sent"], [])
        self.assertTrue(any(k == "DAILY_CAP_SKIP" for k, p, m in calls["logged"]))

    def test_redirect_gets_one_reply_even_at_the_cap(self):
        # bounded-once exemption, same shape as the time reply -- first REDIRECT today still
        # goes out even though the ordinary sends_today cap is already reached.
        today = _today_sgt()
        rec = {"pn": FAKE_PN, "sends_today_date": today, "sends_today": R.DAILY_SEND_CAP}
        calls, state = self._run(
            {FAKE_PN: rec},
            {"type": "REDIRECT", "pn": FAKE_PN, "text": "No worries, other rooms here."})
        self.assertEqual(calls["sent"], [(FAKE_JID, "No worries, other rooms here.")])
        self.assertFalse(any(k == "DAILY_CAP_SKIP" for k, p, m in calls["logged"]))
        self.assertEqual(state["conversations"][FAKE_PN].get("redirect_sent_date"), today)

    def test_a_second_redirect_the_same_day_does_not_send(self):
        today = _today_sgt()
        rec = {"pn": FAKE_PN, "redirect_sent_date": today}
        calls, state = self._run(
            {FAKE_PN: rec},
            {"type": "REDIRECT", "pn": FAKE_PN, "text": "No worries, other rooms here."})
        self.assertEqual(calls["sent"], [])
        self.assertTrue(any(k == "DAILY_CAP_SKIP" for k, p, m in calls["logged"]))

    def test_lease_note_gets_one_reply_even_at_the_cap(self):
        today = _today_sgt()
        rec = {"pn": FAKE_PN, "sends_today_date": today, "sends_today": R.DAILY_SEND_CAP}
        calls, state = self._run(
            {FAKE_PN: rec},
            {"type": "LEASE_NOTE", "pn": FAKE_PN, "text": "minimum 1 year lease note"})
        self.assertEqual(calls["sent"], [(FAKE_JID, "minimum 1 year lease note")])
        self.assertFalse(any(k == "DAILY_CAP_SKIP" for k, p, m in calls["logged"]))
        self.assertEqual(state["conversations"][FAKE_PN].get("lease_note_reply_sent_date"), today)

    def test_a_second_lease_note_the_same_day_does_not_send(self):
        today = _today_sgt()
        rec = {"pn": FAKE_PN, "lease_note_reply_sent_date": today}
        calls, state = self._run(
            {FAKE_PN: rec},
            {"type": "LEASE_NOTE", "pn": FAKE_PN, "text": "minimum 1 year lease note"})
        self.assertEqual(calls["sent"], [])
        self.assertTrue(any(k == "DAILY_CAP_SKIP" for k, p, m in calls["logged"]))

    def test_answer_question_category2_reply_never_exempt_even_at_the_cap(self):
        today = _today_sgt()
        rec = {"pn": FAKE_PN, "sends_today_date": today, "sends_today": R.DAILY_SEND_CAP}
        calls, state = self._run(
            {FAKE_PN: rec},
            {"type": "ANSWER_QUESTION", "pn": FAKE_PN, "text": "The lease is minimum 1 year."})
        self.assertEqual(calls["sent"], [])
        self.assertTrue(any(k == "DAILY_CAP_SKIP" for k, p, m in calls["logged"]))


if __name__ == "__main__":
    unittest.main()
