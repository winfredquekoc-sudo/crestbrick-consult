"""
test_playbook_replies.py -- unittest coverage for wa_intake_replies.py (category 2:
acknowledge and pivot), built from Winfred's real 60 day WhatsApp corpus.

Three layers:
  1. TestClassifyCorpus -- runs 150+ real inbound examples (tenant_requests.json, mixed
     English/Chinese/Malay, several typos and shorthand) through classify() and asserts the
     expected bucket. The "stays human" set (price, deposit, legal, agent, bot check,
     protected attribute) is asserted with ZERO tolerance: classify() must return None for
     every single one, real or synthetic.
  2. TestReplyBuilders / TestAugmentAction -- exact reply text per type, the once per type
     per chat latch, and that a non tenant record (landlord/agent/colleague/supply/buyer)
     is never touched.
  3. TestSendPathIntegration -- runs the real wa_intake_runner.run() send choke end to end
     (via the existing _isolated_runner harness from test_takeover_resume.py) with
     handle_event stubbed to return a bare FLAG_HUMAN/ANSWER_QUESTION, proving the category 2
     text actually reaches _send, and that manual takeover / quiet hours / daily cap still
     hold exactly as they do for every other engine action.

Run: /usr/bin/python3 tests/wa-pipeline/test_playbook_replies.py
"""
import sys, os, unittest
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
import intake_engine as E
import wa_intake_replies as R
import wa_intake_runner as RUN
from test_takeover_resume import _isolated_runner, FAKE_PN, FAKE_JID, _sgt_ts

# ============================================================================================
# Real inbound examples, verbatim from the 60 day corpus (tenant_requests.json), grouped by
# the corpus's own request type. Mixed languages/shorthand exactly as tenants typed them.
# ============================================================================================
CAT2_EXAMPLES = [('availability_check', "Hi Winfred, I'm interested in renting 93 Paya Lebar Way S$ 900 /mo\nhttps://www.propertyguru.com.sg/l/500256368\nIs this room still available?"), ('availability_check', 'Your this room still available? I swing tenant over'), ('availability_check', 'It’s room still available?'), ('availability_check', 'Bro ur unit still available?'), ('availability_check', 'Still available this room?'), ('availability_check', 'the unit still available ?'), ('photos_video', 'plz send the video about this room for first glance :)'), ('photos_video', 'Can share me more photos? Are those same room or different?'), ('photos_video', 'Ha I do the pic finish send u also jot them u mean want to send them to see ? Later they take ur pic n ownself post'), ('photos_video', 'Haha later diff from video also no point'), ('photos_video', 'can i see picture'), ('photos_video', 'Can you share video of the room?'), ('photos_video', 'May I see common room pictures'), ('photos_video', 'hi i am interested in this room， could u please provide videos about this room'), ('pax_or_friends', 'But this one only for one pax\U0001F97A'), ('pax_or_friends', 'Hi Winfred, got any available room for couple ?'), ('pax_or_friends', 'No friend'), ('pax_or_friends', '.kotoe2327@gmail.com \n.Toe Hlyan \n. Myanmar \n. couple \n.25\n.WP\n10 Sep'), ('pax_or_friends', 'Sure. My friend is Female, PR, 30+, single. Working in zhongtai securities'), ('pax_or_friends', 'Actually we are looking for 2 common rooms - 1 pax per room'), ('pax_or_friends', 'I’m helping to view the unit for a friend'), ('address_or_unit_number', "what's the unit number?"), ('address_or_unit_number', 'Unit number all in ur google calendar alr'), ('address_or_unit_number', 'Windred. So sorry, what was the unit number ? Did not  look \U0001F602'), ('address_or_unit_number', 'Hi Winfred. Thank you for your kind assistance! My friend havnt come back to me which unit she is interested. Let me follow up with her.'), ('address_or_unit_number', 'anything she likes or dislikes? \nperhaps we can address it? Tks ya!'), ('address_or_unit_number', 'Can you please share address? Let me check if I can come today?'), ('address_or_unit_number', 'Wat does unit number add up to?'), ('utilities_wifi_aircon', 'Is that including the utilities?'), ('utilities_wifi_aircon', 'for 905, bill client $1025+GST or incllusive GST?'), ('utilities_wifi_aircon', 'Just need u to change the date and put wifi inclusive only'), ('utilities_wifi_aircon', 'U on all aircon alr ?'), ('utilities_wifi_aircon', 'This is the room. $1350/month include wifi/electricity and water'), ('utilities_wifi_aircon', 'No Aircon $1k\nAircon$1.1\nMorning & Rotate ship'), ('utilities_wifi_aircon', 'Not inclusive of utilities?'), ('mrt_location', 'close to MRT as well'), ('mrt_location', 'Ok can you share me the location'), ('mrt_location', 'From NTU to lakeside MRT pls'), ('mrt_location', 'Pinery below is mrt alr will be like a Bedok Mall'), ('mrt_location', 'Pioneer MRT'), ('mrt_location', 'MRT / bus stops nearby?'), ('mrt_location', 'Ok you have other rooms for rent available? Preferably 5mins walking distance from Pioneer MRT'), ('move_in_date', 'She’s a banker, move in date 14 Sept, 1 year lease. Her girl friend maybe come in SG overnight with her every one or two months, not often.'), ('cooking', 'Cooking allowed'), ('cooking', 'Cooking allowed ?'), ('cooking', "900/mth\n1year rental \nstarting date: Aug 1st week \nRequesting for Queen size bed\nWe won't be using much AC or Cooking as we'll. 1 day will be for washing/ once in a week"), ('cooking', 'no i cook steak'), ('cooking', 'Ouh my! I prefer lesser ppl. With cooking allowed'), ('cooking', 'The tenant mentioned that I can use their water dispenser, and some of the cooking appliances. Would this be possible to include in the contract?'), ('cooking', 'But we want full cooking'), ('cooking', 'Can cooking'), ('pets', 'any place that is pet friendly'), ('pets', "hahah really? I'm okay with dogs"), ('pets', 'Dog'), ('pets', 'East point green where got dog lol'), ('pets', 'Dog bodoh block head'), ('pets', 'Dog Sia keep Mia me'), ('pets', 'Yea i’m okay with dogs\U0001F60A'), ('pets', 'dog is no problem'), ('visitors_overnight', 'My partner Lester will be there'), ('visitors_overnight', 'I wanted to ask what is your visitor policy here'), ('visitors_overnight', 'thank you.\n\nI will discuss with my girlfriend and uodate you tomo\n\nWhen can we takeover.\n\nBtw. What is the estimated utilises for us'), ('visitors_overnight', 'But only my boyfriend can viee'), ('visitors_overnight', "Hi Winfred, this is Leonard. Joyce's partner and an appointed representative of Punkin."), ('visitors_overnight', 'Visitor allowed?'), ('visitors_overnight', 'My partner already secured a job and currently waiting for IPA letter (s pass) and I’m still interviewing'), ('follow_up_chaser', 'hihi'), ('follow_up_chaser', 'Hi Winfred, any update please?'), ('follow_up_chaser', 'Hi I have confirmed 1 unit, thank you for following up\U0001F44D'), ('follow_up_chaser', 'Hi bro, any update ya?'), ('follow_up_chaser', 'Thanks for following up. \n No. Meanwhile I will continue searching. Thanks'), ('follow_up_chaser', 'see u there'), ('follow_up_chaser', 'Haha. Terence called me, ask. If u are my Bro, I say yes. Den he say ask u dont stress his wife. I say we just following up'), ('follow_up_chaser', 'Hello Winfred, any update?')]

# Real messages that legitimately match a category 2 keyword but ALSO carry a stays human
# signal (a price figure, or "cheaper") -- safety wins, classify() must return None for
# these, never the category 2 type. Documented here as a POSITIVE safety assertion, not a
# gap: "539 angmokio" reads as a rent style digit next to rent adjacent words, and "cheaper"
# is an explicit stays human trigger regardless of what else the message asks.
SAFETY_OVERRIDE_EXAMPLES = [
    'Hello, may I check if 539 angmokio still available for rent ?',
    'Do you have any cheaper room for couple',
]

CAT2_TYPE_MAP = {
    'availability_check': 'availability', 'photos_video': 'photos_video',
    'pax_or_friends': 'pax_or_friends', 'address_or_unit_number': 'address_or_unit',
    'utilities_wifi_aircon': 'utilities_wifi_aircon', 'mrt_location': 'mrt_location',
    'move_in_date': 'move_in_date', 'cooking': 'cooking', 'pets': 'pets',
    'visitors_overnight': 'visitors_overnight', 'follow_up_chaser': 'follow_up_chaser',
}

STAYS_HUMAN_EXAMPLES = [('price_negotiation', 'Over budget le\U0001f979$1100 can ya hahaha'), ('price_negotiation', 'How much rent ?'), ('price_negotiation', '47 Marine Cresent \nCommon room $1300 a month\nIndian household'), ('price_negotiation', 'Price is negotiable?'), ('price_negotiation', 'How much is it?'), ('price_negotiation', 'Hi Winfred Quek,\nI am interested in:\nRENT - 703 Jurong West Street 71\nRoom /  S$ 900 /mo\n\nhttps://www.propertyguru.com.sg/l/500248838\nThanks'), ('price_negotiation', 'Hi Winfred Quek,\nI am interested in:\nRENT - 93 Paya Lebar Way\nRoom /  S$ 900 /mo\n\nhttps://www.propertyguru.com.sg/l/500256368\nThanks'), ('price_negotiation', 'Hi Winfred Quek, I am interested in your Sale property 908 Tampines Avenue 4, 3 bedroom, listed for S$ 650,000 (https://www.propertyguru.com.sg/l/500248498).'), ('deposit_or_refund', 'Can I confirm this is the correct number to transfer the deposit?  +65 9271 7571'), ('deposit_or_refund', 'Is a deposit required?'), ('deposit_or_refund', 'She alr trf deposit to me. \U0001f923'), ('deposit_or_refund', 'Maybe tmr den say \nOops cause like alr ask want view den say don\u2019t have like u not very credible \nOr maybe tmr when u show them den u say that one ppl deposit alr ahahahahaha'), ('deposit_or_refund', 'Deposit is to secure unit u pay alr where got refund one refund u half alr very good ask her go search what is a deposit'), ('deposit_or_refund', 'Yes , we confirm and can pay deposit .. Please help me check for date can flexible or not  (1st September or 30 Sept , probably 30 Sep ahh) .  \U0001f64f\U0001f3fb\U0001f604'), ('advice_or_legal', 'Thanks. For the stamp duty - I should transfer it to the same paynow account as the deposit?'), ('advice_or_legal', 'Any stamp duty or any other fees'), ('advice_or_legal', 'Hi,Mr Quek, Can I check how much the stamp duty?\nThanks'), ('advice_or_legal', 'Sue him'), ('advice_or_legal', 'I would be requiring the following documents: \n\na rental agreement and proof of HDB REGISTRATION. \n\nAND a stamp duty document/certificate by the IRAS will be covered by me.'), ('advice_or_legal', 'Wq, can u send me haoming rental contract,  stamp duty and agency contract i keep in my rental files. Pls chk when shld be the payment,  they hv not make payment yet. Is it by 1 aug or 2 Aug?'), ('advice_or_legal', 'Owner agreed to sublet'), ('advice_or_legal', 'Shall that Legal guardian have to be a Singapore citizen or PR?\nOr can my parent based in India sign it?'), ('agent_or_cobroke', 'Hi bro! Can cobroke share comm 50-50 for this listing? \n\nCan check which level as my client have viewed and shortlisted this development. \n\nCheryl, Huttons'), ('agent_or_cobroke', 'Ok got cobroke I alr use ur dam excuse to decline alr'), ('agent_or_cobroke', 'Btw when u say co broke share com 5050 means when buyer buy their unit the landlord give agent coms agent will give u half comms even though ubrepresent buyer ? Im confuse'), ('agent_or_cobroke', 'hi, im serving Malay race Buyer\n\nethnic quota ok?\nwhich floor level?\n\nHFE approved\nhouse sold\ncannot give extension\n\nNazri Baobed ERA'), ('agent_or_cobroke', 'Can Cobroke\nCommission collect from Tenant.'), ('ethnicity_nationality', 'I\u2019m Indian, from Tamil Nadu, and my ethnicity is Tamil.'), ('ethnicity_nationality', 'can I know their nationality and are they both working?'), ('ethnicity_nationality', 'May I check again, Landlord is single female? What are other ppls\u2019 nationality in this unit?'), ('ethnicity_nationality', 'What race is owner ?'), ('ethnicity_nationality', 'May I ask the nationality of people living and if more girls'), ('are_you_a_bot', 'Is this both same listing for the same room?'), ('landlord_posing_as_tenant', 'no I am the landlord agent'), ('landlord_posing_as_tenant', 'I am the owner')]

# Neutral corpus samples (greetings, thanks, declines, lease length, viewing scheduling,
# partial forms, generic chatter) -- run through classify() purely as a smoke/robustness
# check (real world noisy text must never raise), no strict assertion on the outcome.
NEUTRAL_EXAMPLES = [('greeting_only', 'Hi'), ('greeting_only', 'Hi'), ('greeting_only', 'Hi'), ('greeting_only', 'hello'), ('greeting_only', 'hi'), ('greeting_only', 'Hi'), ('greeting_only', 'Hi'), ('greeting_only', 'Hi'), ('thanks_only', 'thank you\U0001f64f'), ('thanks_only', 'thanks!'), ('thanks_only', 'Ok thanks'), ('thanks_only', 'Thanks'), ('thanks_only', 'Thank you'), ('thanks_only', 'Thanks'), ('thanks_only', 'Thanks'), ('thanks_only', 'Thank you!'), ('decline_or_found_elsewhere', 'I found a room already.  Tq'), ('decline_or_found_elsewhere', 'I already found actually'), ('decline_or_found_elsewhere', 'Hi Winfred, I\u2019m no longer looking for a unit. Thank you for the offer.'), ('decline_or_found_elsewhere', 'Hello, good morning. Thank you for the offer. However I already found the apartment. Thank you \U0001f64f\U0001f3fb'), ('decline_or_found_elsewhere', 'Hello i already found a flat, thanks\U0001f64f\U0001f3fb'), ('decline_or_found_elsewhere', 'no problem! i found a place. Thanks for assisting'), ('decline_or_found_elsewhere', 'They found room'), ('decline_or_found_elsewhere', "Hi Winfred \U0001f60a, thanks for your effort \U0001f64f\U0001f3fb, it's within our budget but we had already rented a room"), ('gender_or_household', 'And is it all girls ?'), ('gender_or_household', 'But is it all female?'), ('gender_or_household', 'All female? \nDo u hv pics of the room pls'), ('gender_or_household', '$950 /Mth including utilities\nFemale only\nBlk 403 Jurong West St 42\nIndian household'), ('gender_or_household', 'Or strictly female only'), ('gender_or_household', 'https://www.propertyguru.com.sg/listing/hdb-for-rent-539-ang-mo-kio-avenue-10-500220292\nFemale only \n$1000 for the smaller room \n$1200 for the bigger room'), ('gender_or_household', 'Also may I ask the gender of the other housemates'), ('gender_or_household', 'oh and may i know the nationality and gender of the other tenants?'), ('lease_length', 'I\u2019m interested in the room. May I ask if you would be open to a 6-month lease instead of a 1-year lease? Thank you!'), ('lease_length', 'Can help reply this , owner of the Bishan loft only want 3 full year lease'), ('lease_length', 'here are my details: \n1. looking at 3-month lease from end-Aug\n2. 2 pax mother and daughter\n3. can i confirm rate applies for 2 pax?\nthank you'), ('lease_length', 'Hi. For eastpoint room rent can 3 month lease/ male/ singaporean Indian'), ('lease_length', 'my lease ends 30 august so i can take my time'), ('viewing_request', 'I see.. would it possible to check from your friend.. if not then will go for viewing'), ('viewing_request', 'Den want schedule some viewing on wed?'), ('viewing_request', "Hii were you able to check? We would like to view if it's malay eligible"), ('viewing_request', 'Any viewing date on weekday but after 7pm?'), ('viewing_time_proposal', 'sunday 11am bro'), ('viewing_time_proposal', 'She coming this Saturday, can start to view on Sunday. Around 1.30pm can she view? Thanks'), ('viewing_time_proposal', 'If Sunday 5.15pm is ok'), ('viewing_time_proposal', 'Next sat 1 or 2pm on'), ('viewing_time_proposal', 'I prefer noon time'), ('viewing_time_proposal', 'Next sat 4pm'), ('viewing_time_proposal', 'Or view Sun Ard 7-8pm'), ('viewing_time_proposal', 'Ok noted with thanks. See u on sat 2.15pm.'), ('partial_form', 'budget is around 1.1k to 1.2k'), ('partial_form', '1 PAX\nIndian Nationality \nIndian Ethnic\nMail\nAge > 45+\nEP\nWorking in MNC\nNo pet\nNo smoking'), ('other_general_chat', 'https://www.propertyguru.com.sg/listing/for-rent-oxley-edge-500248513#'), ('other_general_chat', 'But no enquiries'), ('other_general_chat', 'Hi PropertyGuru, I am interested in: 2 Haig Road \nRef ID: ac533e94-75cc-4a50-af94-5eaab55e5e4e\nURL: https://www.propertyguru.com.sg/l/500218235'), ('other_general_chat', 'I love claude'), ('other_general_chat', 'https://bit.ly/49K5Xs3'), ('other_general_chat', '10:45'), ('other_general_chat', 'sure'), ('other_general_chat', 'Yes')]


class TestClassifyCorpus(unittest.TestCase):
    """150+ real inbound examples through classify()."""

    def test_total_examples_at_least_150(self):
        total = len(CAT2_EXAMPLES) + len(SAFETY_OVERRIDE_EXAMPLES) + len(STAYS_HUMAN_EXAMPLES) + len(NEUTRAL_EXAMPLES)
        self.assertGreaterEqual(total, 150, "need at least 150 real examples in the corpus run")

    def test_category2_examples_classify_correctly(self):
        failures = []
        for source_type, text in CAT2_EXAMPLES:
            expect = CAT2_TYPE_MAP[source_type]
            got = R.classify(text)
            if got != expect:
                failures.append((source_type, expect, got, text[:60]))
        self.assertEqual(failures, [], f"{len(failures)} of {len(CAT2_EXAMPLES)} misclassified: {failures}")

    def test_safety_override_examples_stay_human(self):
        """A rent style number, or an explicit 'cheaper', wins over any other topic in the
        SAME message -- never auto answered, even though it also reads as availability/pax."""
        for text in SAFETY_OVERRIDE_EXAMPLES:
            self.assertIsNone(R.classify(text), f"expected safety override to None: {text!r}")

    def test_stays_human_examples_never_classify_zero_tolerance(self):
        violations = [(k, t[:60]) for k, t in STAYS_HUMAN_EXAMPLES if R.classify(t) is not None]
        self.assertEqual(violations, [], f"stays human violation(s), MUST be empty: {violations}")

    def test_neutral_examples_do_not_crash(self):
        for _, t in NEUTRAL_EXAMPLES:
            R.classify(t)   # must not raise


# ============================================================================================
# TestReplyBuilders -- exact text per category 2 type, against controlled listing fixtures.
# ============================================================================================
class TestReplyBuilders(unittest.TestCase):
    def setUp(self):
        self._patches = []

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def _patch(self, target, attr, value):
        p = mock.patch.object(target, attr, value)
        p.start()
        self._patches.append(p)

    def _rec(self, listing_key=None):
        return {"pn": "6590000001", "listing_key": listing_key, "profile": {},
                "form_sent": True, "category2_fired": {}}

    def test_availability_bound_open_with_slot(self):
        self._patch(E, "listing_reqs", lambda: {"lk1": {"status": "open"}})
        self._patch(E, "next_future_slot", lambda lk: {"label": "Sat 13 Sep, 3pm to 5pm"})
        self._patch(E, "_listing_unavailable", lambda lk: None)
        out = R._reply_availability(self._rec("lk1"), "still available?")
        self.assertEqual(out, "Yes still available \U0001F642 Are you free to view on Sat 13 Sep, 3pm to 5pm?")

    def test_availability_bound_open_no_slot(self):
        self._patch(E, "listing_reqs", lambda: {"lk1": {"status": "open"}})
        self._patch(E, "next_future_slot", lambda lk: None)
        self._patch(E, "_listing_unavailable", lambda lk: None)
        out = R._reply_availability(self._rec("lk1"), "still available?")
        self.assertEqual(out, "Yes still available \U0001F642 Are you free to view this week?")

    def test_availability_closed_uses_room_gone_text(self):
        self._patch(E, "listing_reqs", lambda: {"lk1": {"status": "closed (tenanted)"}})
        self._patch(E, "_listing_unavailable", lambda lk: "closed (tenanted)")
        out = R._reply_availability(self._rec("lk1"), "still available?")
        self.assertEqual(out, R._ROOM_GONE_TEXT)
        self.assertIn(E.CHANNEL, out)

    def test_availability_unbound_asks_which_unit(self):
        out = R._reply_availability(self._rec(None), "still available?")
        self.assertEqual(out, "Which unit are you asking about? Send me the listing link or "
                              "address and I will check for you \U0001F642")

    def test_photos_video_text(self):
        # no "shortly" (P3 fix, 11 Sep 2026 attack replay): nothing in the engine actually
        # sends media, so a timing word on an unbacked promise reads as broken if missed.
        self._patch(E, "next_future_slot", lambda lk: {"label": "Sun 14 Sep, 11am"})
        out = R._reply_photos_video(self._rec("lk1"), "any photos?")
        self.assertEqual(out, "Sure, let me get some photos and a short video over to you "
                              "\U0001F642 Meanwhile, are you free to view on Sun 14 "
                              "Sep, 11am?")

    def test_pax_known_max_pax(self):
        self._patch(E, "listing_reqs", lambda: {"lk1": {"requirements": {"max_pax": 2}}})
        self._patch(E, "next_future_slot", lambda lk: {"label": "Sat 13 Sep, 3pm"})
        out = R._reply_pax_or_friends(self._rec("lk1"), "room for couple?")
        self.assertEqual(out, "This room is for up to 2 pax \U0001F642 Are you free to view "
                              "on Sat 13 Sep, 3pm?")

    def test_pax_unknown_max_pax(self):
        self._patch(E, "listing_reqs", lambda: {"lk1": {"requirements": {}}})
        out = R._reply_pax_or_friends(self._rec("lk1"), "room for couple?")
        self.assertEqual(out, "Let me check with the owner how many can stay and get back "
                              "to you \U0001F642")

    def test_address_revealed_no_restriction_strips_unit(self):
        self._patch(E, "listing_reqs", lambda: {"lk1": {
            "block_address": "522 Bedok North Ave 1 #07-332", "marketing_restrictions": ""}})
        out = R._reply_address_or_unit(self._rec("lk1"), "what is the unit number?")
        self.assertEqual(out, "It is at 522 Bedok North Ave 1 \U0001F642 I will send the "
                              "exact unit once your viewing is confirmed.")
        self.assertNotIn("#07-332", out)

    def test_address_withheld_by_marketing_restrictions(self):
        self._patch(E, "listing_reqs", lambda: {"lk1": {
            "block_address": "11 Toh Tuck Rd, Singapore 596290",
            "marketing_restrictions": "never post to portals"}})
        self._patch(E, "next_future_slot", lambda lk: None)
        out = R._reply_address_or_unit(self._rec("lk1"), "what is the address?")
        self.assertEqual(out, "I will share the exact address once your viewing is confirmed "
                              "\U0001F642 Are you free to view this week?")

    def test_address_unbound(self):
        self._patch(E, "next_future_slot", lambda lk: None)
        out = R._reply_address_or_unit(self._rec(None), "what is the address?")
        self.assertIn("I will share the exact address", out)

    def test_fact_reuses_tenant_fact_answer_when_filled(self):
        self._patch(E, "listing_reqs", lambda: {"lk1": {"requirements": {"cooking": "all"}}})
        out = R._reply_fact(self._rec("lk1"), "is cooking allowed?")
        self.assertEqual(out, E._tenant_fact_answer("is cooking allowed?", {"requirements": {"cooking": "all"}}))
        self.assertIn("Cooking is allowed", out)

    def test_fact_falls_back_when_not_on_file(self):
        self._patch(E, "listing_reqs", lambda: {"lk1": {"requirements": {}}})
        out = R._reply_fact(self._rec("lk1"), "is there visitor policy?")
        self.assertEqual(out, "Let me check with the owner and get back to you shortly.")

    def test_follow_up_chaser_text(self):
        out = R._reply_follow_up_chaser(self._rec("lk1"), "any update?")
        self.assertEqual(out, "Sorry for the wait, still checking with the owner. I will "
                              "update you as soon as I hear back \U0001F64F")

    def test_photo_only_known_text(self):
        self.assertEqual(R._PHOTO_ONLY_KNOWN_TEXT,
                         "Thanks for sending this \U0001F642 What would you like to know about the unit?")

    def test_no_reply_texts_contain_a_hyphen_or_dash(self):
        dash_re = __import__("re").compile(r"[-‐‑‒–—―－]")
        self._patch(E, "listing_reqs", lambda: {"lk1": {
            "requirements": {"max_pax": 2}, "block_address": "1 Test Rd", "marketing_restrictions": ""}})
        self._patch(E, "next_future_slot", lambda lk: {"label": "Sat 13 Sep, 3pm"})
        self._patch(E, "_listing_unavailable", lambda lk: None)
        rec = self._rec("lk1")
        texts = [
            R._reply_availability(rec, "still available?"),
            R._reply_photos_video(rec, "photos?"),
            R._reply_pax_or_friends(rec, "couple ok?"),
            R._reply_address_or_unit(rec, "address?"),
            R._reply_follow_up_chaser(rec, "any update?"),
            R._PHOTO_ONLY_KNOWN_TEXT, R._FACT_FALLBACK, R._ROOM_GONE_TEXT,
        ]
        for t in texts:
            self.assertIsNone(dash_re.search(t), f"hyphen/dash found in: {t!r}")


# ============================================================================================
# TestAugmentAction -- the hook itself: fires once per type per chat, never touches a non
# tenant record, stays silent for stays human types, and handles the bare photo case.
# ============================================================================================
class TestAugmentAction(unittest.TestCase):
    def setUp(self):
        self._patches = []
        self._patch(E, "listing_reqs", lambda: {"lk1": {"status": "open", "requirements": {}}})
        self._patch(E, "next_future_slot", lambda lk: None)
        self._patch(E, "_listing_unavailable", lambda lk: None)
        self._patch(E, "resolve_pn", lambda jid: jid.split("@")[0])

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def _patch(self, target, attr, value):
        p = mock.patch.object(target, attr, value)
        p.start()
        self._patches.append(p)

    def _state(self, **rec_fields):
        rec = {"pn": FAKE_PN, "status": "form_sent", "listing_key": "lk1",
               "form_sent": True, "profile": {}}
        rec.update(rec_fields)
        return {"conversations": {FAKE_PN: rec}}

    def _ev(self, text, media_type=""):
        return {"jid": FAKE_JID, "text": text, "media_type": media_type}

    def test_fires_on_bare_flag_human_for_availability(self):
        state = self._state()
        action = {"type": "FLAG_HUMAN", "pn": FAKE_PN, "text": None, "reason": "not a clear tenant enquiry"}
        out = R.augment_action(state, self._ev("still available?"), action)
        self.assertEqual(out["text"], "Yes still available \U0001F642 Are you free to view this week?")
        self.assertTrue(out["notify"])
        self.assertEqual(out["category2_code"], "AVAILABILITY")

    def test_fires_on_bare_answer_question(self):
        state = self._state()
        action = {"type": "ANSWER_QUESTION", "pn": FAKE_PN, "text": None, "question": "any photos?"}
        out = R.augment_action(state, self._ev("any photos?"), action)
        self.assertTrue(out["text"].startswith("Sure, let me get some photos"))

    def test_never_overrides_action_that_already_has_text(self):
        state = self._state()
        action = {"type": "ANSWER_QUESTION", "pn": FAKE_PN, "text": "Cooking is allowed \U0001F642"}
        out = R.augment_action(state, self._ev("still available?"), action)
        self.assertIs(out, action)

    def test_never_touches_other_action_types(self):
        state = self._state()
        action = {"type": "OFFER_VIEWING", "pn": FAKE_PN, "text": "Keen to view Sat 3pm?"}
        out = R.augment_action(state, self._ev("still available?"), action)
        self.assertIs(out, action)

    def test_once_per_type_per_chat_second_fire_is_silent(self):
        state = self._state()
        action = {"type": "FLAG_HUMAN", "pn": FAKE_PN, "text": None}
        first = R.augment_action(state, self._ev("still available?"), dict(action))
        self.assertIsNotNone(first["text"])
        second = R.augment_action(state, self._ev("still available anot?"), dict(action))
        self.assertIsNone(second["text"])   # second availability question in the same chat: silent again

    def test_stays_human_type_never_gets_text(self):
        state = self._state()
        action = {"type": "FLAG_HUMAN", "pn": FAKE_PN, "text": None}
        out = R.augment_action(state, self._ev("can you do 900 instead?"), dict(action))
        self.assertIsNone(out["text"])

    def test_non_tenant_excluded_status_never_augmented(self):
        state = self._state(status="excluded:agent")
        action = {"type": "FLAG_HUMAN", "pn": FAKE_PN, "text": None, "reason": "excluded agent"}
        out = R.augment_action(state, self._ev("still available?"), dict(action))
        self.assertIsNone(out["text"])

    def test_non_tenant_supply_flagged_never_augmented(self):
        state = self._state(status="supply_side:landlord", supply_flagged=True)
        action = {"type": "FLAG_HUMAN", "pn": FAKE_PN, "text": None}
        out = R.augment_action(state, self._ev("still available?"), dict(action))
        self.assertIsNone(out["text"])

    def test_non_tenant_buyer_never_augmented(self):
        state = self._state(buyer_form_sent=True)
        action = {"type": "ANSWER_QUESTION", "pn": FAKE_PN, "text": None}
        out = R.augment_action(state, self._ev("still available?"), dict(action))
        self.assertIsNone(out["text"])

    def test_not_enquiry_status_never_augmented(self):
        state = self._state(status="not_enquiry")
        action = {"type": "FLAG_HUMAN", "pn": FAKE_PN, "text": None}
        out = R.augment_action(state, self._ev("still available?"), dict(action))
        self.assertIsNone(out["text"])

    def test_photo_only_known_tenant_fires_on_none_action(self):
        state = self._state(form_sent=True)
        out = R.augment_action(state, self._ev("", media_type="image"), None)
        self.assertEqual(out["text"], R._PHOTO_ONLY_KNOWN_TEXT)
        self.assertEqual(out["category2_code"], "PHOTO_ONLY_KNOWN")

    def test_photo_from_unknown_sender_not_touched(self):
        """No form_sent, no listing_key -- the engine's own photo gate owns this, not us."""
        state = self._state(status="new", form_sent=False, listing_key=None)
        out = R.augment_action(state, self._ev("", media_type="image"), None)
        self.assertIsNone(out)

    def test_photo_only_known_fires_once_per_chat(self):
        state = self._state()
        first = R.augment_action(state, self._ev("", media_type="image"), None)
        self.assertIsNotNone(first)
        second = R.augment_action(state, self._ev("", media_type="video"), None)
        self.assertIsNone(second)


# ============================================================================================
# TestSendPathIntegration -- the real runner send choke, via the existing isolated harness.
# ============================================================================================
class TestSendPathIntegration(unittest.TestCase):
    def _rec(self, **kw):
        r = {"pn": FAKE_PN, "profile": {}, "processed_ids": [], "form_sent": True,
             "listing_key": "test-listing", "status": "form_sent"}
        r.update(kw)
        return r

    def test_category2_reply_reaches_real_send(self):
        with mock.patch.object(E, "listing_reqs", lambda: {"test-listing": {"status": "open"}}), \
             mock.patch.object(E, "next_future_slot", lambda lk: None), \
             mock.patch.object(E, "_listing_unavailable", lambda lk: None), \
             _isolated_runner(
                self._tmpdir(),
                inbound_content="is this room still available?",
                conversations={FAKE_PN: self._rec()},
                handle_event_return={"type": "FLAG_HUMAN", "pn": FAKE_PN, "text": None,
                                     "reason": "not a clear tenant enquiry"}) as calls:
            pass
        self.assertEqual(calls["sent"], [(FAKE_JID, "Yes still available \U0001F642 Are you "
                                                     "free to view this week?")])
        self.assertTrue(any("Auto reply sent [AVAILABILITY]" in m for m in calls["notified"]))

    def test_stays_human_never_reaches_send(self):
        with _isolated_runner(
                self._tmpdir(),
                inbound_content="can you lower the price, cheaper anot?",
                conversations={FAKE_PN: self._rec()},
                handle_event_return={"type": "FLAG_HUMAN", "pn": FAKE_PN, "text": None}) as calls:
            pass
        self.assertEqual(calls["sent"], [])

    def test_manual_takeover_silence_not_bypassed(self):
        """A record under a genuine hand takeover (no resume eligibility) must stay silent --
        category 2 never sends around manual_takeover any more than any other engine action
        does. copilot_muted=True keeps handle_event out of the viewing_reaction co-pilot path
        entirely (real handle_event runs here, not a stub), so this proves the choke's own
        TAKEOVER_SKIP still holds with the hook wired in."""
        rec = self._rec(manual_takeover=True, human_takeover=True, copilot_muted=True)
        with _isolated_runner(
                self._tmpdir(),
                inbound_content="is this room still available?",
                conversations={FAKE_PN: rec}) as calls:
            pass
        self.assertEqual(calls["sent"], [])

    def test_daily_cap_still_applies_to_category2_reply(self):
        import time
        today = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))
        rec = self._rec(sends_today_date=today, sends_today=RUN.DAILY_SEND_CAP)
        with mock.patch.object(E, "listing_reqs", lambda: {"test-listing": {"status": "open"}}), \
             mock.patch.object(E, "next_future_slot", lambda lk: None), \
             mock.patch.object(E, "_listing_unavailable", lambda lk: None), \
             _isolated_runner(
                self._tmpdir(),
                inbound_content="is this room still available?",
                conversations={FAKE_PN: rec},
                handle_event_return={"type": "FLAG_HUMAN", "pn": FAKE_PN, "text": None}) as calls:
            pass
        self.assertEqual(calls["sent"], [])
        self.assertTrue(any(k == "DAILY_CAP_SKIP" for k, p, m in calls["logged"]))

    def test_quiet_hours_still_blocks_category2_reply(self):
        with _isolated_runner(
                self._tmpdir(),
                inbound_content="is this room still available?",
                conversations={FAKE_PN: self._rec()},
                handle_event_return={"type": "FLAG_HUMAN", "pn": FAKE_PN, "text": None},
                quiet_hours=True) as calls:
            pass
        self.assertEqual(calls["sent"], [])

    def _tmpdir(self):
        import tempfile
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return d.name


# ============================================================================================
# TestOwnerLoopWiring -- enqueue_owner_question() is called exactly when a reply above is
# genuinely "let me check with the owner" (pax unknown, a fact not on file, the follow up
# chaser), never when the listing's own data already answered, and never without a
# landlord_id to ask against.
# ============================================================================================
class TestOwnerLoopWiring(unittest.TestCase):
    def setUp(self):
        self._patches = []
        self._patch(E, "resolve_pn", lambda jid: jid.split("@")[0])
        self.calls = []
        self._patch(R.OWN, "enqueue_owner_question", self._fake_enqueue)

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def _patch(self, target, attr, value):
        p = mock.patch.object(target, attr, value)
        p.start()
        self._patches.append(p)

    def _fake_enqueue(self, landlord_id, listing_key, code, text, source=None, **kw):
        self.calls.append((landlord_id, listing_key, code, text, source))
        return "fakeid"

    def _state(self, listing_reqs):
        self._patch(E, "listing_reqs", lambda: listing_reqs)
        rec = {"pn": FAKE_PN, "status": "form_sent", "listing_key": "lk1",
               "form_sent": True, "profile": {}}
        return {"conversations": {FAKE_PN: rec}}

    def _ev(self, text):
        return {"jid": FAKE_JID, "text": text, "media_type": ""}

    def test_pax_unknown_enqueues_pax_question(self):
        state = self._state({"lk1": {"status": "open", "landlord_id": "LL001", "requirements": {}}})
        action = {"type": "FLAG_HUMAN", "pn": FAKE_PN, "text": None}
        R.augment_action(state, self._ev("room for couple?"), action)
        self.assertEqual(len(self.calls), 1)
        lid, lk, code, text, source = self.calls[0]
        self.assertEqual((lid, lk, code, source), ("LL001", "lk1", "PAX", FAKE_PN))
        self.assertTrue(text)

    def test_pax_known_never_enqueues(self):
        state = self._state({"lk1": {"status": "open", "landlord_id": "LL001",
                                     "requirements": {"max_pax": 2}}})
        action = {"type": "FLAG_HUMAN", "pn": FAKE_PN, "text": None}
        R.augment_action(state, self._ev("room for couple?"), action)
        self.assertEqual(self.calls, [])

    def test_fact_not_on_file_enqueues_mapped_code(self):
        state = self._state({"lk1": {"status": "open", "landlord_id": "LL002", "requirements": {}}})
        action = {"type": "FLAG_HUMAN", "pn": FAKE_PN, "text": None}
        R.augment_action(state, self._ev("visitor policy?"), action)
        self.assertEqual(len(self.calls), 1)
        lid, lk, code, text, source = self.calls[0]
        self.assertEqual((lid, lk, code), ("LL002", "lk1", "VISITORS"))

    def test_fact_answered_from_listing_never_enqueues(self):
        state = self._state({"lk1": {"status": "open", "landlord_id": "LL003",
                                     "requirements": {"cooking": "all"}}})
        action = {"type": "FLAG_HUMAN", "pn": FAKE_PN, "text": None}
        R.augment_action(state, self._ev("is cooking allowed?"), action)
        self.assertEqual(self.calls, [])

    def test_follow_up_chaser_enqueues_availability(self):
        state = self._state({"lk1": {"status": "open", "landlord_id": "LL004", "requirements": {}}})
        action = {"type": "FLAG_HUMAN", "pn": FAKE_PN, "text": None}
        R.augment_action(state, self._ev("hi any update?"), action)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][2], "AVAILABILITY")

    def test_no_landlord_id_never_enqueues_but_still_replies(self):
        state = self._state({"lk1": {"status": "open", "requirements": {}}})   # no landlord_id
        action = {"type": "FLAG_HUMAN", "pn": FAKE_PN, "text": None}
        out = R.augment_action(state, self._ev("room for couple?"), action)
        self.assertEqual(self.calls, [])
        self.assertTrue(out["text"])   # the tenant still gets an acknowledgement either way

    def test_availability_never_enqueues(self):
        # availability is answered straight from listing status/slot, never routed to the owner
        state = self._state({"lk1": {"status": "open", "landlord_id": "LL005", "requirements": {}}})
        action = {"type": "FLAG_HUMAN", "pn": FAKE_PN, "text": None}
        R.augment_action(state, self._ev("still available?"), action)
        self.assertEqual(self.calls, [])

    def test_enqueue_never_raises_out_of_the_tenant_reply(self):
        def _boom(*a, **kw):
            raise RuntimeError("owner queue disk full")
        R.OWN.enqueue_owner_question = _boom
        state = self._state({"lk1": {"status": "open", "landlord_id": "LL006", "requirements": {}}})
        action = {"type": "FLAG_HUMAN", "pn": FAKE_PN, "text": None}
        out = R.augment_action(state, self._ev("room for couple?"), action)
        self.assertTrue(out["text"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
