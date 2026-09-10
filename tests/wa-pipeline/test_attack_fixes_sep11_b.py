"""
test_attack_fixes_sep11_b.py -- unittest coverage for package B of the 11 Sep 2026 attack
replay findings (scripts/wa_intake_attack_harness.py, scratchpad/attack/flow/cycle*/*):
listing binding and enquiry classification. Each TestCase names the finding key it guards
and reproduces the exact (or an equivalent minimal) failure signature from the cited
scenario, so it fails against the pre fix engine and passes after.

Keys covered:
  unbound-chat-autobound-to-real-listing               (c1-04)
  fallback-binds-unbound-chat-to-unrelated-live-listing (c3rm05)
  unbound-enquiry-autobinds-unrelated-listing           (c5s02)
  buyer-enquiry-bound-to-unrelated-live-rental-listing  (c4rm07)
  tenant-misclassified-as-landlord                      (c1-07)
  malay-enquiry-misclassified-not-enquiry               (c2mix03, c2mix08)
  portal-enquiry-misclassified-not-enquiry              (c3rm05)
  self-declared-owner-treated-as-qualified-tenant       (c5s06)

Run: /usr/bin/python3 tests/wa-pipeline/test_attack_fixes_sep11_b.py
"""
import sys, os, unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src", "wa-pipeline"))

# Telegram/bridge kill switch -- set BEFORE importing any wa-pipeline module (see
# wa_intake_paths.py's docstring / test_listing_binding_fix.py for the reference pattern).
os.environ["WA_INTAKE_NO_TELEGRAM"] = "1"
os.environ["WA_INTAKE_NO_SEND"] = "1"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import intake_engine as E
import wa_intake_runner as R
from test_attack_hardening_sep9_p2 import _listing, _ReqsFixtureMixin


# a real production-shaped listing (mirrors the live master-room-blk-233-pending-rd-ll115
# row the harness's empty-fixtures scenarios matched against) -- (EA) is the real ensuite
# marker whose 2 letter paren token ("ea") was the actual root cause: a raw substring check
# matched it inside ordinary words (lease, area, overseas, earlier).
def _real_shaped_listing(lk="master-room-blk-233-pending-rd-ll115", deal_type="rent"):
    return {
        "listing_key": lk, "status": "open", "deal_type": deal_type,
        "property_name": "Blk 233 Pending Rd #11-03 (EA), S670233",
        "block_address": "Blk 233 Pending Rd #11-03 (EA), S670233",
        "pg_url_keywords": ["233 pending", "233 pending rd", "blk 233 pending rd"],
    }


# ---------------------------------------------------------------------------
# unbound-chat-autobound-to-real-listing / fallback-binds-unbound-chat-to-unrelated-live-
# listing / unbound-enquiry-autobinds-unrelated-listing: same root cause, three scenarios.
# ---------------------------------------------------------------------------
class TestFallbackNoLongerAutobindsOnGenericSubstring(unittest.TestCase):
    def setUp(self):
        self.reqs = {"master-room-blk-233-pending-rd-ll115": _real_shaped_listing()}

    def test_c1_04_overseas_till_dec_stays_unbound(self):
        self.assertIsNone(R.match_listing(
            "Hey admin, I'm staying overseas till Dec only & was wondering if it's possible "
            "to move in then. Not too keen on taking over ur lease mid term leh.", self.reqs))

    def test_c3rm05_which_area_stays_unbound(self):
        self.assertIsNone(R.match_listing("which area is this actually, you never say", self.reqs))

    def test_c5s02_photos_sent_earlier_stays_unbound(self):
        self.assertIsNone(R.match_listing(
            "the floor plan and photos u sent earlier, can resend? I never received", self.reqs))

    def test_ea_paren_token_never_generated(self):
        # direct unit test on the actual root cause: the (EA) marker must never become a
        # standalone fallback token at all.
        toks = R._fallback_tokens(_real_shaped_listing())
        self.assertNotIn("ea", toks)

    def test_distinctive_word_still_binds_with_word_boundary(self):
        # word boundary matching must not regress a genuine distinctive-word hit -- "pending"
        # said plainly still binds (P1 fix behaviour preserved).
        self.assertEqual(
            R.match_listing("hi is the pending rd room still available", self.reqs),
            "master-room-blk-233-pending-rd-ll115")

    def test_distinctive_word_does_not_match_inside_a_longer_word(self):
        # "pending" must not swallow "depending" -- the same substring-vs-word-boundary bug
        # class the (EA) fix addresses, defense in depth.
        self.assertIsNone(R.match_listing(
            "depending on my work schedule I might need a later move in date", self.reqs))


# ---------------------------------------------------------------------------
# buyer-enquiry-bound-to-unrelated-live-rental-listing (c4rm07): intent must gate binding.
# ---------------------------------------------------------------------------
class TestSaleShapedMessageNeverBindsRentListing(unittest.TestCase):
    def test_c4rm07_woodlands_lease_question_stays_unbound(self):
        reqs = {"master-room-blk-233-pending-rd-ll115": _real_shaped_listing()}
        self.assertIsNone(R.match_listing(
            "Hiyah! How much of the lease left liao? Still 60 years min or not?", reqs))

    def test_decisive_sale_signal_excludes_a_rent_listing_even_on_keyword_hit(self):
        # construct the adversarial case directly: a decisive SALE portal token riding
        # alongside literal rent-listing keywords must not bind to the rent listing.
        reqs = {
            "rentroom": {"listing_key": "rentroom", "status": "open", "deal_type": "rent",
                         "pg_url_keywords": ["marine crescent"], "property_name": "Marine Crescent"},
        }
        self.assertIsNone(R.match_listing(
            "SALE - Marine Crescent condo, $980000, still available?", reqs))

    def test_decisive_rent_signal_excludes_a_sale_listing(self):
        reqs = {
            "saleunit": {"listing_key": "saleunit", "status": "open", "deal_type": "sale",
                         "pg_url_keywords": ["marine crescent"], "property_name": "Marine Crescent"},
        }
        self.assertIsNone(R.match_listing(
            "RENT - Marine Crescent room $1200/mo, can view?", reqs))

    def test_weak_keyword_signal_never_gates_a_real_keyword_hit(self):
        # a low confidence "lease" keyword mention alone (classify_transaction's weakest
        # tier) must not exclude every rent listing from an otherwise real keyword bind.
        reqs = {"rentroom": {"listing_key": "rentroom", "status": "open", "deal_type": "rent",
                              "pg_url_keywords": ["marine crescent"], "property_name": "Marine Crescent"}}
        self.assertEqual(
            R.match_listing("still keen on the lease for marine crescent, any update?", reqs),
            "rentroom")


# ---------------------------------------------------------------------------
# tenant-misclassified-as-landlord (c1-07): a demand question must veto the low context
# "carousell" landlord read.
# ---------------------------------------------------------------------------
class TestCarousellOpenerAskingToViewIsTenantNotLandlord(unittest.TestCase):
    def test_photos_and_viewing_today_is_not_supply(self):
        kind = E.supply_side_kind(
            "6598887777@s.whatsapp.net",
            "Been searching all day on Carousell! Do u have photos of ur place? Also, is it "
            "available for viewing today pls?")
        self.assertIsNone(kind)

    def test_bare_carousell_mention_with_no_demand_question_still_probable_landlord(self):
        # unchanged behaviour: a bare "saw on carousell" with nothing else stays a PROBABLE
        # (never confident) landlord read.
        kind = E.supply_side_kind("6598887778@s.whatsapp.net", "saw ur ad on carousell")
        self.assertEqual(kind, "landlord")


# ---------------------------------------------------------------------------
# malay-enquiry-misclassified-not-enquiry (c2mix03, c2mix08).
# ---------------------------------------------------------------------------
class TestMalayEnquiriesRecognised(unittest.TestCase):
    def test_c2mix03_cari_rumah_is_tenant_enquiry(self):
        self.assertTrue(E.is_tenant_enquiry(
            "Assalamu'alaikum, aku tengah cari rumah di Tampines, ada tahu harga rumah "
            "kosong di sana tak?"))

    def test_c2mix03_bilik_kosong_boleh_reserve_is_tenant_enquiry(self):
        self.assertTrue(E.is_tenant_enquiry(
            "kalau ada bilik kosong bulan depan boleh reserve tak?"))

    def test_c2mix03_budget_saya_is_tenant_enquiry(self):
        self.assertTrue(E.is_tenant_enquiry("budget saya sekitar 700 sebulan je"))

    def test_c2mix08_rumah_sewa_is_tenant_enquiry(self):
        self.assertTrue(E.is_tenant_enquiry(
            "Wassalamualaikum ustaz... Mencari rumah sewa dekat Kepong station lah, boleh "
            "tau budget saya cukup tak?"))


# ---------------------------------------------------------------------------
# portal-enquiry-misclassified-not-enquiry (c3rm05).
# ---------------------------------------------------------------------------
class TestPortalNamePlusMonthlyFigureIsTenantEnquiry(unittest.TestCase):
    def test_99co_plus_per_month_is_tenant_enquiry(self):
        self.assertTrue(E.is_tenant_enquiry(
            "Utilities included if I pay $500 per month or more? Asking for property on 99.co"))

    def test_propertyguru_plus_dollar_figure_is_tenant_enquiry(self):
        self.assertTrue(E.is_tenant_enquiry(
            "saw this on propertyguru, is $1200 the final price"))

    def test_portal_name_alone_with_no_price_does_not_trigger_this_rule(self):
        # do not overreach: a bare portal mention with no monthly figure at all is not, on
        # its own, forced true by this new rule.
        self.assertFalse(E.is_tenant_enquiry("just checking propertyguru later"))


# ---------------------------------------------------------------------------
# self-declared-owner-treated-as-qualified-tenant (c5s06): a mid thread ownership claim must
# not be shielded by the sender's own earlier portal boilerplate opener.
# ---------------------------------------------------------------------------
class TestSelfDeclaredOwnerFlipsEvenAfterPortalOpener(unittest.TestCase):
    def setUp(self):
        self._orig_hist = E.recent_inbound_text

    def tearDown(self):
        E.recent_inbound_text = self._orig_hist

    def test_owner_claim_confident_despite_earlier_interested_in_history(self):
        E.recent_inbound_text = lambda jid, limit=25: (
            "Hi Winfred Quek,\nI am interested in:\nRENT - Tampines Fake Condo\n"
            "Room /  S$ 2200 /mo\n\nhttps://www.propertyguru.com.sg/l/500711884\nThanks\n"
            "Hi i saw the condo on propertyguru and just wanted to ask abt pricing")
        rec = {"listing_key": "tampines-owner-posing-test", "profile": {}, "qualify": None,
               "form_sent": True}
        kind, confident = E.supply_side_kind(
            "7020355418@lid",
            "actually can I be honest, I'm the owner of this unit lah, just testing how the "
            "chat responds",
            with_confidence=True, rec=rec)
        self.assertEqual(kind, "landlord")
        self.assertTrue(confident)

    def test_handle_event_flips_out_of_tenant_funnel_on_owner_claim(self):
        orig_reqs = E.listing_reqs
        orig_master = E._master_status
        try:
            E.listing_reqs = lambda: {
                "tampines-owner-posing-test": _listing(
                    "tampines-owner-posing-test", budget_floor=1000)}
            E._master_status = lambda lk, reqs=None: None
            st = {"version": 1, "conversations": {}}
            jid = "7020355418@lid"
            pn = E.resolve_pn(jid)
            rec = E._rec(st, pn)
            rec.update(listing_key="tampines-owner-posing-test", form_sent=True,
                       profile={})
            a = E.handle_event(st, {
                "jid": jid, "msg_id": "m3",
                "text": "actually can I be honest, I'm the owner of this unit lah, just "
                        "testing how the chat responds",
                "is_from_me": False})
            self.assertTrue(rec.get("supply_flagged"))
            self.assertEqual(rec.get("supply_kind"), "landlord")
            self.assertNotEqual((a or {}).get("type"), "SEND_FORM")
            self.assertTrue(rec.get("manual_takeover"))
        finally:
            E.listing_reqs = orig_reqs
            E._master_status = orig_master


if __name__ == "__main__":
    unittest.main(verbosity=2)
