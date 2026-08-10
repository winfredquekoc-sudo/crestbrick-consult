import sys, json, sqlite3, os
sys.path.insert(0, os.path.expanduser("~/crestbrick-consult/src/wa-pipeline"))
import intake_engine as E

# The fixtures predate several listings closing (caspian tenanted 9 Jul 2026, others on
# hold). Force every fixture listing open for the test process only, so the suite keeps
# exercising the full enquiry flow instead of dead-ending on "room no longer available".
_FIXTURE_LISTINGS = ("caspian", "hougang-703", "bedok-north-522", "tampines-855",
                     "sunshine-terrace", "rivervale-185c", "bayshore", "eastpoint-green")
_orig_listing_reqs = E.listing_reqs
def _reqs_fixtures_open():
    r = dict(_orig_listing_reqs())
    for k in _FIXTURE_LISTINGS:
        if k in r:
            c = dict(r[k]); c["status"] = "open"; r[k] = c
    return r
E.listing_reqs = _reqs_fixtures_open

# 13 Jul 2026 hardening added a SECOND, independent closed gate: _listing_unavailable also
# cross checks the MASTER landlord DB status (not just the listing index "status" field
# patched above), so a fixture listing that is genuinely tenanted/archived in the real
# landlord DB (e.g. caspian) still reads as closed mid flow even with the override above.
# Force the master status to "active" for fixture listings too, for the same reason.
_orig_master_status = E._master_status
def _master_status_fixtures_active(lk, reqs=None):
    if lk in _FIXTURE_LISTINGS:
        return "active"
    return _orig_master_status(lk, reqs)
E._master_status = _master_status_fixtures_active

reqs = E.listing_reqs()
P = 0; F = 0
def ok(name, cond):
    global P,F
    if cond: P+=1; print("  PASS", name)
    else: F+=1; print("  FAIL", name)

print("== 1. COMPLIANCE: qualify() honours every gate ==")
# hougang-703 = Chinese ONLY (URA quota)
ok("Indian on hougang-703 -> DISQUALIFIED",
   E.qualify(reqs["hougang-703"], {"ethnicity":"Indian","gender":"Male","no_of_pax":1,"lease_term_months":12,"budget":1200})[0]=="DISQUALIFIED")
ok("Malay on hougang-703 -> DISQUALIFIED",
   E.qualify(reqs["hougang-703"], {"ethnicity":"Malay","no_of_pax":1,"lease_term_months":12,"budget":1200})[0]=="DISQUALIFIED")
ok("Chinese on hougang-703 -> not disqualified",
   E.qualify(reqs["hougang-703"], {"ethnicity":"Chinese","gender":"Male","no_of_pax":1,"lease_term_months":12,"budget":1200})[0]!="DISQUALIFIED")
# caspian = exclude Indian
ok("Indian on caspian -> DISQUALIFIED",
   E.qualify(reqs["caspian"], {"ethnicity":"Indian","gender":"Male","no_of_pax":1,"lease_term_months":12,"budget":1100})[0]=="DISQUALIFIED")
# bedok-north-522 = female_only + couple_ok (no single males)
ok("single male on bedok-north-522 -> DISQUALIFIED",
   E.qualify(reqs["bedok-north-522"], {"gender":"Male","no_of_pax":1,"ethnicity":"Chinese","lease_term_months":12,"budget":1300})[0]=="DISQUALIFIED")
ok("married couple on bedok-north-522 -> not disqualified (married check flagged)",
   E.qualify(reqs["bedok-north-522"], {"gender":"Male and Female","no_of_pax":2,"ethnicity":"Chinese","lease_term_months":12,"budget":1600})[0]!="DISQUALIFIED")
ok("female on bedok-north-522 -> not disqualified",
   E.qualify(reqs["bedok-north-522"], {"gender":"Female","no_of_pax":1,"ethnicity":"Chinese","lease_term_months":12,"budget":1300})[0]!="DISQUALIFIED")
# tampines-855 = female_only + min_age 30
ok("male on tampines-855 -> DISQUALIFIED",
   E.qualify(reqs["tampines-855"], {"gender":"Male","no_of_pax":1,"age":35,"lease_term_months":12,"budget":1000})[0]=="DISQUALIFIED")
ok("female age 23 on tampines-855 -> DISQUALIFIED (age)",
   E.qualify(reqs["tampines-855"], {"gender":"Female","no_of_pax":1,"age":23,"ethnicity":"Chinese","nationality":"SG","pass_type":"SC","lease_term_months":12,"budget":1000})[0]=="DISQUALIFIED")
ok("female age 32 on tampines-855 -> QUALIFIED",
   E.qualify(reqs["tampines-855"], {"gender":"Female","no_of_pax":1,"age":32,"ethnicity":"Chinese","nationality":"SG","pass_type":"SC","lease_term_months":12,"budget":1000})[0]=="QUALIFIED")
# sunshine-terrace = budget unknown -> never auto-pass
ok("sunshine-terrace budget unknown -> NEEDS_INFO not QUALIFIED",
   E.qualify(reqs["sunshine-terrace"], {"gender":"Female","ethnicity":"Chinese","no_of_pax":1,"age":28,"nationality":"SG","pass_type":"EP","lease_term_months":12,"budget":1500})[0]=="NEEDS_INFO")
# budget gate
ok("budget far below floor -> DISQUALIFIED",
   E.qualify(reqs["rivervale-185c"], {"gender":"Female","no_of_pax":1,"ethnicity":"Chinese","lease_term_months":12,"budget":600})[0]=="DISQUALIFIED")

print("== 2. STATE MODEL: form once, event dedup, no cap, manual takeover ==")
st = {"version":1,"conversations":{}}
jid = "6591234567@s.whatsapp.net"
a1 = E.handle_event(st, {"jid":jid,"msg_id":"m1","text":"Hi is Caspian still available?","is_from_me":0,"listing_key":"caspian"})
ok("first enquiry -> SEND_FORM", a1 and a1["type"]=="SEND_FORM")
ok("SEND_FORM is TWO messages (unit info, then form)", a1 and len(a1.get("texts",[]))==2)
ok("message 1 is unit info, NOT the form", a1 and "• Name:" not in a1["texts"][0])
ok("message 2 is the full 14 field form", all(x in a1["texts"][1] for x in ["Email address:","Name:","Nationality:","Ethnicity:","Gender:","Age:","Pass type","Occupation","Employment type","No. of pax","Move in date","Lease term","Budget:","Location:"]))
a1b = E.handle_event(st, {"jid":jid,"msg_id":"m1","text":"Hi is Caspian still available?","is_from_me":0,"listing_key":"caspian"})
ok("same msg id replayed -> None (event dedup)", a1b is None)
st["conversations"]["6591234567"]["form_sent_ts"] -= 300   # skip the 3 min anti-spam grace period
a2 = E.handle_event(st, {"jid":jid,"msg_id":"m2","text":"Name: Raj\nNationality: Indian\nGender: Male","is_from_me":0})
# nudge copy changed (July): no longer "Almost there", now "thanks for the details so far...
# could you also share your <fields>?" (_nudge_text). Same protective intent: one prospect
# facing nudge naming the gap, not silence.
ok("incomplete profile -> ONE nudge to prospect (share your...)", a2 and a2["type"]=="NUDGE_INCOMPLETE" and a2.get("text") and "could you also share your" in a2["text"].lower())
ok("form NOT resent on 2nd inbound", st["conversations"]["6591234567"]["form_sent"] is True and a2["type"]!="SEND_FORM")
# blast 5 more inbound at this prospect: the FORM must never go out again, no prospect message
forms_after=0; prospect_msgs=0
for i in range(5):
    z=E.handle_event(st, {"jid":jid,"msg_id":"z"+str(i),"text":"hello? you there?","is_from_me":0})
    if z and z.get("type")=="SEND_FORM": forms_after+=1
    if z and z.get("text"): prospect_msgs+=1
ok("intake form sent EXACTLY once, never re-sent under repeated messages", forms_after==0)
ok("no FURTHER prospect message after the single nudge", prospect_msgs==0)
# now complete profile -> Indian on caspian -> REDIRECT
a3 = E.handle_event(st, {"jid":jid,"msg_id":"m3","text":"Ethnicity: Indian\nAge: 30\nType of Pass: EP\nNo. of Pax: 1\nMove in Date: 1 Aug\nLease: 12 months\nBudget: 1200","is_from_me":0})
ok("Indian completes profile on caspian -> REDIRECT (not sent to landlord)", a3 and a3["type"]=="REDIRECT")
# manual takeover
st2 = {"version":1,"conversations":{}}
jid2="6599998888@s.whatsapp.net"
E.handle_event(st2, {"jid":jid2,"msg_id":"x1","text":"interested in room","is_from_me":0,"listing_key":"caspian"})
E.handle_event(st2, {"jid":jid2,"msg_id":"x2","text":"hi there, let me check for you","is_from_me":1,"engine":False})  # Winfred by hand
am = E.handle_event(st2, {"jid":jid2,"msg_id":"x3","text":"Name: Bob ...","is_from_me":0})
ok("after manual takeover -> engine silent", am is None and st2["conversations"]["6599998888"]["manual_takeover"] is True)

print("== 3. LIVE REPLAY: real threads, count actions per prospect ==")
mdb = sqlite3.connect(os.path.expanduser("~/whatsapp-mcp/whatsapp-bridge/store/messages.db"))
cols=[r[1] for r in mdb.execute("PRAGMA table_info(messages)").fetchall()]
idcol = "id" if "id" in cols else cols[0]
tdb = json.load(open(os.path.expanduser("~/crestbrick-consult/_templates/tenant-db.json")))
# map a few open tenants to their jid
jids = [t["jid"] for t in tdb["tenants"] if not t.get("excluded")][:12]
from collections import Counter
worst_proactive=0; max_forms=0; max_views=0; max_redirect=0; reactive_total=0; dup_field=False
for j in jids:
    rows = mdb.execute(f"SELECT {idcol}, is_from_me, content FROM messages WHERE chat_jid=? ORDER BY timestamp", (j,)).fetchall()
    st = {"version":1,"conversations":{}}
    c=Counter()
    for mid,ifm,content in rows:
        if ifm:
            E.handle_event(st,{"jid":j,"msg_id":str(mid),"text":content or "","is_from_me":1,"engine":True}); continue
        a=E.handle_event(st,{"jid":j,"msg_id":str(mid),"text":content or "","is_from_me":0,"listing_key":None})
        if a: c[a["type"]]+=1
    proactive = c["SEND_FORM"]+c["ASK_FIELDS"]+c["OFFER_VIEWING"]+c["REDIRECT"]+c["ASK_ONE"]+c["FLAG_HUMAN"]+c["NUDGE_INCOMPLETE"]
    reactive_total += c["ANSWER_QUESTION"]+c["CONFIRM_VIEWING"]
    worst_proactive=max(worst_proactive,proactive)
    max_forms=max(max_forms,c["SEND_FORM"]); max_views=max(max_views,c["OFFER_VIEWING"]); max_redirect=max(max_redirect,c["REDIRECT"])
    rec=list(st["conversations"].values())[0] if st["conversations"] else {}
    if rec and len(rec.get("asked_fields",[]))!=len(set(rec.get("asked_fields",[]))): dup_field=True
ok("intake form sent at most once per prospect", max_forms<=1)
ok("viewing offered at most once per prospect", max_views<=1)
ok("redirect sent at most once per prospect", max_redirect<=1)
ok("no required field ever re-asked", not dup_field)
ok("proactive messages per prospect stay small (<= 4)", worst_proactive<=4)
print(f"  replayed {len(jids)} real threads | worst proactive {worst_proactive} | reactive answers total {reactive_total} (one per inbound question, uncapped by design)")

print("== 4. SLOT CAPACITY: two YES on a one person slot, only one books ==")
import tempfile
tmp=tempfile.NamedTemporaryFile("w",suffix=".json",delete=False)
json.dump({"slots":{"demo":{"slots":[{"slot_id":"s1","capacity":1,"booked":0,"status":"open"}]}}}, tmp); tmp.close()
b1=E.book_slot("demo","s1",path=tmp.name)
b2=E.book_slot("demo","s1",path=tmp.name)
ok("first YES books the capacity 1 slot", b1 is True)
ok("second YES is refused (no double booking)", b2 is False)
after=json.load(open(tmp.name))["slots"]["demo"]["slots"][0]
ok("slot marked full after capacity reached", after["booked"]==1 and after["status"]=="full")
os.unlink(tmp.name)

print("== 5. VIEWING TIME ping (the only notification to Winfred) ==")
ok("detects 'sat 3pm'", E._has_viewing_time("sat 3pm works"))
ok("detects '16/6'", E._has_viewing_time("can i come 16/6"))
ok("detects 'tomorrow'", E._has_viewing_time("tomorrow evening"))
ok("ignores a plain question", not E._has_viewing_time("is there wifi included?"))
def _vo(pn):
    return {"version":1,"conversations":{pn:{"pn":pn,"listing_key":"caspian","stage":"VIEWING_OFFERED",
            "profile":{"name":"Test"},"processed_ids":[],"form_sent":True,"asked_fields":[],
            "viewing_asked":True,"viewing_confirmed":False,"manual_takeover":False,
            "status":"viewing_offered","offered_slot_id":"s1"}}}
s=_vo("6590000001")
a=E.handle_event(s,{"jid":"6590000001@s.whatsapp.net","msg_id":"t1","text":"I can do Saturday 3pm","is_from_me":0})
ok("prospect proposes a time -> VIEWING_TIME_PROPOSED, notify Winfred + acknowledge the prospect (no dead-end)",
   a and a["type"]=="VIEWING_TIME_PROPOSED" and a.get("notify") and a.get("text") and "confirm that slot" in a["text"].lower())
a=E.handle_event(s,{"jid":"6590000001@s.whatsapp.net","msg_id":"t2","text":"is there wifi?","is_from_me":0})
ok("plain question -> ANSWER_QUESTION, pings Winfred to reply (not silently dropped), no auto-text", a and a["type"]=="ANSWER_QUESTION" and a.get("notify") and a.get("text") is None)
s2=_vo("6590000002")
a=E.handle_event(s2,{"jid":"6590000002@s.whatsapp.net","msg_id":"y1","text":"yes please","is_from_me":0})
ok("prospect says yes -> CONFIRM_VIEWING, notify", a and a["type"]=="CONFIRM_VIEWING" and a.get("notify"))

print("== 6. SAFETY GATES: never message a landlord/agent/non-enquiry; send unit info ==")
# The gate is checked against the live landlord DB, so the test needs a genuinely
# known landlord. Draw one at runtime rather than hardcoding a real phone number
# into the repo: the assertion stays just as strong and no personal data is committed.
# Take it from the gate's own set so the number is in the exact normalised form the
# gate matches on (the DB stores "+65..." while the set holds the bare digits).
_lset = E._landlord_pn_set()
assert _lset, "landlord DB unreadable: cannot run the landlord safety gate tests"
LANDLORD_PN = next(p for p in sorted(_lset)
                   if len(p) == 10 and p.startswith("65") and p[2] in "89" and p.isdigit())
ok("known landlord flagged as landlord", E.excluded_reason(LANDLORD_PN)=="landlord")
ok("is_enquiry true for 'still available'", E.is_enquiry("Hi is Caspian still available?"))
ok("is_enquiry false for chit chat", not E.is_enquiry("hi how are you bro"))
# a landlord who sends an enquiry-looking message still gets NO form
sL={"version":1,"conversations":{}}
aL=E.handle_event(sL,{"jid":f"{LANDLORD_PN}@s.whatsapp.net","msg_id":"L1","text":"is the room still available","is_from_me":0,"listing_key":"bayshore"})
ok("known landlord -> silent, no tenant record created (never messaged)", aL is None and LANDLORD_PN not in sL["conversations"])
# a landlord we message FIRST (outbound) must also never become a tenant record
sLo={"version":1,"conversations":{}}
E.handle_event(sLo,{"jid":f"{LANDLORD_PN}@s.whatsapp.net","msg_id":"Lo1","text":"hi, following up on your unit","is_from_me":1,"engine":False})
ok("known landlord outbound-first -> no record (pollution vector closed)", LANDLORD_PN not in sLo["conversations"])
# a non-enquiry first message -> no form
sN={"version":1,"conversations":{}}
aN=E.handle_event(sN,{"jid":"6590001111@s.whatsapp.net","msg_id":"N1","text":"hello bro long time","is_from_me":0})
ok("non-enquiry -> FLAG_HUMAN, no message", aN and aN["type"]=="FLAG_HUMAN" and aN.get("text") is None)
# a genuine enquiry -> SEND_FORM WITH unit info + the corrected form
sG={"version":1,"conversations":{}}
aG=E.handle_event(sG,{"jid":"6590002222@s.whatsapp.net","msg_id":"G1","text":"Hi, I am interested in RENT Caspian, still available?","is_from_me":0,"listing_key":"caspian"})
ok("genuine enquiry -> SEND_FORM", aG and aG["type"]=="SEND_FORM")
ok("message 1 includes UNIT INFO (Lakeside/Caspian)", aG and ("Lakeside" in aG["texts"][0] or "Caspian" in aG["texts"][0]))
ok("message 2 includes the correct bulleted form", aG and "• Name:" in aG["texts"][1] and "Pass type" in aG["texts"][1])

print("== 7. SELF-RECOGNITION: bot's own send must NOT count as Winfred handling it ==")
ok("is_bot_message true for the form", E.is_bot_message(E.INTAKE_FORM))
ok("is_bot_message true for a viewing offer line", E.is_bot_message("The next viewing is Thu 18 Jun, 7 to 8pm"))
ok("is_bot_message false for a normal manual reply", not E.is_bot_message("ok can you come 3pm tomorrow"))
# bot's own outbound (engine=True) must NOT silence the bot
sB={"version":1,"conversations":{}}
E.handle_event(sB,{"jid":"6590007777@s.whatsapp.net","msg_id":"b1","text":"Hi I am interested in Caspian, still available","is_from_me":0,"listing_key":"caspian"})
E.handle_event(sB,{"jid":"6590007777@s.whatsapp.net","msg_id":"b2","text":E.INTAKE_FORM,"is_from_me":1,"engine":True})  # our own form echo
ok("bot's own send does NOT trigger manual takeover", sB["conversations"]["6590007777"]["manual_takeover"] is False)
# Winfred's genuine manual reply DOES silence the bot
E.handle_event(sB,{"jid":"6590007777@s.whatsapp.net","msg_id":"b3","text":"hey let me check with the landlord ah","is_from_me":1,"engine":False})
ok("Winfred's manual reply DOES trigger takeover", sB["conversations"]["6590007777"]["manual_takeover"] is True)

print("== 8. CAPTURE LANDLORD AVAILABILITY at first enquiry ==")
# listing with NO upcoming slot (bedok-north-522 has empty slots) -> flag capture
sA={"version":1,"conversations":{}}
aA=E.handle_event(sA,{"jid":"6590003333@s.whatsapp.net","msg_id":"av1","text":"Hi is the Bedok North room still available?","is_from_me":0,"listing_key":"bedok-north-522"})
ok("first enquiry still SEND_FORM with listing bound", aA and aA["type"]=="SEND_FORM" and aA.get("listing_key")=="bedok-north-522")
ok("SEND_FORM still carries the unit message + bulleted form", aA and any("• Name:" in t for t in aA.get("texts",[])))
ok("no captured slot -> capture_availability True", aA and aA.get("capture_availability") is True)
# wiring is correct regardless of date/data: the flag equals 'no open future slot'
sC={"version":1,"conversations":{}}
aC=E.handle_event(sC,{"jid":"6590004444@s.whatsapp.net","msg_id":"av2","text":"Hi is Caspian still available?","is_from_me":0,"listing_key":"caspian"})
ok("capture_availability key always present on SEND_FORM", aC and "capture_availability" in aC)
ok("capture_availability mirrors real slot state (caspian)", aC and aC.get("capture_availability") == (not E._has_open_future_slot("caspian")))
ok("capture_availability mirrors real slot state (bedok)", aA.get("capture_availability") == (not E._has_open_future_slot("bedok-north-522")))

print("== 9. AVAILABLE VIEWING SLOT + the two-message split ==")
sV={"version":1,"conversations":{}}
aV=E.handle_event(sV,{"jid":"6590005555@s.whatsapp.net","msg_id":"v1","text":"Hi is Caspian still available?","is_from_me":0,"listing_key":"caspian"})
ok("SEND_FORM carries two messages", aV and aV["type"]=="SEND_FORM" and len(aV.get("texts",[]))==2)
ok("msg1 is unit info, no form fields", aV and ("Caspian" in aV["texts"][0] or "Lakeside" in aV["texts"][0]) and "• Name:" not in aV["texts"][0])
ok("viewing line in msg1 iff listing has a real future slot",
   aV and (("Available viewing:" in aV["texts"][0]) == (E.next_future_slot("caspian") is not None)))
_sc=E.next_future_slot("caspian")
if _sc:
    ok("landlord slot label shown in msg1", _sc.get("label") in aV["texts"][0])
    ok("no redundant template 'Viewing slots' line when a live slot is shown", "viewing slots:" not in aV["texts"][0].lower())
sV2={"version":1,"conversations":{}}
aV2=E.handle_event(sV2,{"jid":"6590006666@s.whatsapp.net","msg_id":"v2","text":"Hi is the Bedok North room still available?","is_from_me":0,"listing_key":"bedok-north-522"})
ok("listing with no slot omits the viewing line in msg1",
   aV2 and (("Available viewing:" in aV2["texts"][0]) == (E.next_future_slot("bedok-north-522") is not None)))

print("== 10. SERVICE POLICY exclusions (India / family / pax-budget) ==")
ok("India nationality excluded", E.policy_excluded({"nationality":"Indian","no_of_pax":1,"budget":1500})=="nationality")
ok("India spelled out excluded", E.policy_excluded({"nationality":"India"})=="nationality")
ok("3 pax under 1400 excluded", E.policy_excluded({"nationality":"Chinese","no_of_pax":3,"budget":1300})=="pax_budget")
ok("3 pax at 1500 NOT excluded by pax-budget", E.policy_excluded({"nationality":"Chinese","no_of_pax":3,"budget":1500}) is None)
ok("2 pax with a baby excluded", E.policy_excluded({"nationality":"Filipino","no_of_pax":2,"budget":1600},"we have a baby with us")=="family")
ok("1 pax Singaporean NOT excluded", E.policy_excluded({"nationality":"Singaporean","no_of_pax":1,"budget":1200}) is None)
ok("SG citizen of Indian ethnicity NOT excluded (nationality is SG)", E.policy_excluded({"nationality":"Singaporean","ethnicity":"Indian","no_of_pax":1,"budget":1500}) is None)
# live flow: a complete India profile -> kind REDIRECT, terminal, once, no reason shown
sP={"version":1,"conversations":{}}; jp="6590008888@s.whatsapp.net"
E.handle_event(sP,{"jid":jp,"msg_id":"p1","text":"Hi is the room still available?","is_from_me":0,"listing_key":"bedok-north-522"})
aP=E.handle_event(sP,{"jid":jp,"msg_id":"p2","text":"Name: Raj\nNationality: Indian\nEthnicity: Indian\nGender: Female\nAge: 30\nType of Pass: EP\nNo. of Pax: 1\nIntended Move in Date: 1 Jul\nPreferred Lease Term: 12\nBudget: 1500","is_from_me":0})
# redirect copy changed (July): "your profile is not a fit for this unit" replaces the old
# "does not match" wording. Same protective intent: a kind, reason free redirect.
ok("India profile -> REDIRECT channel referral", aP and aP["type"]=="REDIRECT" and "not a fit" in aP["text"].lower())
ok("redirect reveals NO reason", aP and "india" not in aP["text"].lower() and "nationality" not in aP["text"].lower())
ok("excluded via service policy path", sP["conversations"]["6590008888"].get("status","").startswith("policy_excluded"))
aP2=E.handle_event(sP,{"jid":jp,"msg_id":"p3","text":"hello? you there?","is_from_me":0})
ok("excluded prospect NOT messaged again (terminal, no spam)", aP2 is None or aP2.get("text") is None)

print("== 11. TRANSACTION CLASSIFIER: rent vs sale (two entirely different flows) ==")
ok("PG 'RENT -' token -> rent", E.classify_transaction("I am interested in: RENT - 185C Rivervale Crescent / Room / S$ 1,250 /mo")[0]=="rent")
ok("PG 'SALE -' token -> sale", E.classify_transaction("I am interested in: SALE - 339A Sembawang Close / 4 Beds / S$ 629,999")[0]=="sale")
ok("99.co 'for Room' -> rent", E.classify_transaction("Common Room (Condo) for Room in *Caspian* / S$ 1,100")[0]=="rent")
ok("99.co 'for Sale' -> sale", E.classify_transaction("Premium HDB for Sale in *339A Sembawang Close* / S$ 630,000")[0]=="sale")
ok("/mo price -> rent", E.classify_transaction("interested in the room S$ 900 /mo")[0]=="rent")
ok("lump-sum price -> sale", E.classify_transaction("interested, asking S$ 2,050,000 for the unit")[0]=="sale")
ok("token-less enquiry bound to a rent listing -> rent (registry)", E.classify_transaction("interested in Caspian, ref id 7361", "caspian")[0]=="rent")
ok("explicit SALE token overrides a rental listing binding", E.classify_transaction("SALE - Caspian / 3 Beds / S$ 1,800,000", "caspian")[0]=="sale")
# end to end: a SALE enquiry must NEVER get the rental TENANT intake form. July design
# (buyer intake form concept): a sale enquiry now gets its OWN buyer form (SEND_BUYER_FORM),
# not a silent FLAG_HUMAN and not the tenant INTAKE_FORM. Protective intent preserved: the
# tenant form must never reach a buyer.
sS={"version":1,"conversations":{}}
aS=E.handle_event(sS,{"jid":"6590009999@s.whatsapp.net","msg_id":"S1","text":"I am interested in: SALE - 339A Sembawang Close / 4 Beds / S$ 629,999","is_from_me":0,"listing_key":None})
ok("sale enquiry -> SEND_BUYER_FORM (buyer intake), not FLAG_HUMAN", aS and aS["type"]=="SEND_BUYER_FORM" and "sale" in aS.get("reason","").lower())
ok("sale enquiry sends the BUYER form, never the tenant INTAKE_FORM", aS and aS.get("text") and E.INTAKE_FORM not in aS["text"] and "Citizenship" in aS["text"])
sR={"version":1,"conversations":{}}
aR=E.handle_event(sR,{"jid":"6590007777@s.whatsapp.net","msg_id":"R1","text":"I am interested in: RENT - Caspian / Room / S$ 1,100 /mo","is_from_me":0,"listing_key":"caspian"})
ok("rental enquiry still -> SEND_FORM", aR and aR["type"]=="SEND_FORM")
# a SALE enquiry must not permanently silence a person who later sends a RENTAL enquiry.
# TRUE REGRESSION (reported, not fixed here): once buyer_form_sent latches, EVERY subsequent
# inbound is routed to _buyer_followup with no rent/sale re-classification, so an unambiguous
# later "RENT - ..." enquiry (even with an explicit portal token and a bound listing) is
# swallowed as a buyer-flow ANSWER_QUESTION (silent to the prospect) instead of SEND_FORM.
# Left failing on purpose to surface this; see report.
sSR={"version":1,"conversations":{}}
E.handle_event(sSR,{"jid":"6590006611@s.whatsapp.net","msg_id":"sr1","text":"I am interested in: SALE - Clavon / S$2,050,000","is_from_me":0,"listing_key":None})
aSR=E.handle_event(sSR,{"jid":"6590006611@s.whatsapp.net","msg_id":"sr2","text":"RENT - Caspian / Room / S$1,100/mo still available?","is_from_me":0,"listing_key":"caspian"})
ok("sale enquiry does NOT block a later rental enquiry from same person", aSR and aSR["type"]=="SEND_FORM")
aSR2=E.handle_event(sSR,{"jid":"6590006611@s.whatsapp.net","msg_id":"sr0","text":"SALE - Clavon again","is_from_me":0,"listing_key":None})

print("== 12. CYCLE-1 HARDENING regressions ==")
# couple detection: 'female' must not be read as a couple via the 'male' substring
ok("single female on bedok-north-522 (female_only+couple+married) -> QUALIFIED not NEEDS_INFO couple",
   E.qualify(reqs["bedok-north-522"], {"gender":"Female","no_of_pax":1,"ethnicity":"Chinese","lease_term_months":12,"budget":1500})[0]=="QUALIFIED")
ok("single female on a male_only+couple_ok listing -> DISQUALIFIED (not wrongly QUALIFIED)",
   E.qualify({"gender":"male_only","couple_ok":True,"budget_floor":1000}, {"gender":"Female","no_of_pax":1,"lease_term_months":12,"budget":1200})[0]=="DISQUALIFIED")
ok("two same-sex females (pax 2) do NOT count as a couple on male_only",
   E.qualify({"gender":"male_only","couple_ok":True,"budget_floor":1000}, {"gender":"Female","no_of_pax":2,"lease_term_months":12,"budget":1200})[0]=="DISQUALIFIED")
ok("opposite-sex couple still recognised on female_only+couple_ok",
   E.qualify(reqs["bedok-north-522"], {"gender":"Male and Female","no_of_pax":2,"ethnicity":"Chinese","lease_term_months":12,"budget":1600})[0]!="DISQUALIFIED")
# number parsing
ok("'ok 900' budget does NOT x1000", E._to_int("ok 900")==900)
ok("'1.2k' -> 1200", E._to_int("1.2k")==1200)
ok("'1.5m' -> 1500000", E._to_int("1.5m")==1500000)
ok("extract budget '1.2' -> 1200", E.extract_profile("Budget: 1.2").get("budget")==1200)
ok("classify '$1.5m' -> sale (not tenant form)", E.classify_transaction("interested, my budget is $1.5m for the unit")[0]=="sale")
# listing status gate
ok("enquiry on a closed (tenanted) listing -> FLAG_HUMAN, no form",
   (lambda a: a and a["type"]=="FLAG_HUMAN" and a.get("text") is None)(
     E.handle_event({"version":1,"conversations":{}},{"jid":"6590112233@s.whatsapp.net","msg_id":"cl1","text":"Hi is this room still available?","is_from_me":0,"listing_key":"tampines-201"})))
# stage-3: a question is not a confirmation
def _vo2(pn): return {"version":1,"conversations":{pn:{"pn":pn,"listing_key":"caspian","stage":"VIEWING_OFFERED","profile":{"name":"T"},"processed_ids":[],"form_sent":True,"asked_fields":[],"viewing_asked":True,"viewing_confirmed":False,"manual_takeover":False,"status":"viewing_offered","offered_slot_id":"s1"}}}
sc=_vo2("6590445566")
ac=E.handle_event(sc,{"jid":"6590445566@s.whatsapp.net","msg_id":"c1","text":"can I view on Tuesday?","is_from_me":0})
ok("'can I view on Tuesday?' -> NOT a false CONFIRM_VIEWING", ac and ac["type"]!="CONFIRM_VIEWING")
sy=_vo2("6590445577")
ay=E.handle_event(sy,{"jid":"6590445577@s.whatsapp.net","msg_id":"y1","text":"yes confirm","is_from_me":0})
ok("'yes confirm' -> CONFIRM_VIEWING still works", ay and ay["type"]=="CONFIRM_VIEWING")
# backfill echo must be recognised as a bot message (not Winfred manual takeover)
ok("backfill opening recognised as bot message", E.is_bot_message("Hi! Following up on your rental enquiry :) could you help me fill this in"))

print("== 13. CYCLE-2 HARDENING regressions ==")
import importlib.util as _ilu
_rs = _ilu.spec_from_file_location("rnr", os.path.expanduser("~/crestbrick-consult/src/wa-pipeline/wa_intake_runner.py"))
RNR = _ilu.module_from_spec(_rs); _rs.loader.exec_module(RNR)
# bot-echo detection (the bridge echoes our own sends as is_from_me=0)
ok("blank intake form echo -> recognised as our echo (would poison if processed)", RNR._is_our_echo(E.INTAKE_FORM) is True)
ok("CONFIRM echo -> our echo (would self-confirm)", RNR._is_our_echo("Great, your viewing is confirmed. I will share...") is True)
ok("a prospect's FILLED form -> NOT an echo (must still process)", RNR._is_our_echo("Pls fill this in\nName: Suhas\nNationality: India\nGender: Male") is False)
ok("a genuine enquiry -> NOT an echo", RNR._is_our_echo("Hi is Caspian still available?") is False)
# extract_profile + qualify no longer corrupted by the blank form
ok("extract_profile(blank form) yields no real single-gender", E.extract_profile(E.INTAKE_FORM).get("gender") in (None,"") or "couple" in str(E.extract_profile(E.INTAKE_FORM).get("gender")).lower())
# couple bypass when couple_ok is False
ok("opposite-sex couple on female_only + couple_ok FALSE -> DISQUALIFIED",
   E.qualify({"gender":"female_only","couple_ok":False,"max_pax":2,"budget_floor":1000}, {"gender":"Male and Female","no_of_pax":2,"lease_term_months":12,"budget":1200})[0]=="DISQUALIFIED")
# _to_int overflow guard
ok("_to_int huge number -> None (no crash)", E._to_int("999999999999999999999") is None)
ok("extract_profile on a giant pasted number does not crash", isinstance(E.extract_profile("budget 999999999999999999999999"), dict))
# PDPA consent line on the form
# the Budget field hint ("(S$ per month)") was dropped from the live form (plain "• Budget:"
# now); extract_profile's hint tolerance is covered separately in section 18.
ok("INTAKE_FORM has the field labels and NO CEA signoff", "• Name:" in E.INTAKE_FORM and "• Budget:" in E.INTAKE_FORM and "R073319H" not in E.INTAKE_FORM)

print("== 14. CYCLE-3 HARDENING regressions ==")
# offered slot must be future-floored (no past-dated slot offered/confirmed)
ok("next_slot is future-floored (== next_future_slot)", E.next_slot("caspian") == E.next_future_slot("caspian"))
# married gate clears when the tenant already states married
ok("married couple stated -> female_only+couple+married is QUALIFIED (not asked to confirm marriage)",
   E.qualify(reqs["bedok-north-522"], {"gender":"married couple","no_of_pax":2,"ethnicity":"Chinese","lease_term_months":12,"budget":1500})[0]=="QUALIFIED")
# couple_ok caps pax at 2, does not make it unlimited
ok("couple_ok caps pax: a 'couple' of 4 on max_pax 2 -> DISQUALIFIED",
   E.qualify({"gender":"any","couple_ok":True,"max_pax":2,"budget_floor":1000}, {"gender":"Male and Female","no_of_pax":4,"lease_term_months":12,"budget":1200})[0]=="DISQUALIFIED")
ok("couple of 2 on max_pax 1 + couple_ok -> allowed (cap is max(mx,2))",
   E.qualify({"gender":"any","couple_ok":True,"max_pax":1,"budget_floor":1000}, {"gender":"Male and Female","no_of_pax":2,"lease_term_months":12,"budget":1200})[0]!="DISQUALIFIED")
# CEA identity on message 1 (unit info)
ok("listing_unit_message has NO CEA signoff", "R073319H" not in (E.listing_unit_message("caspian") or ""))
# unit-info echo is recognised (bot-only markers) but a genuine 'still available' enquiry is not
ok("unit-info message 1 echo recognised as our echo", RNR._is_our_echo(E.listing_unit_message("caspian") or "✅ Suits: x") is True)
ok("genuine 'still available?' enquiry is NOT an echo (still served)", RNR._is_our_echo("Hi is Caspian still available?") is False)

print("== 15. FIXED (hard-coded) viewing slot ==")
_fx = E._fixed_viewing_slot("bayshore", "2026-06-17")
ok("bayshore has a fixed_viewing rule", _fx is not None)
_fxcfg = (E.listing_reqs().get("bayshore",{}) or {}); _fxcfg = _fxcfg.get("fixed_viewing") or (_fxcfg.get("requirements",{}) or {}).get("fixed_viewing") or {}
ok("fixed slot matches the configured time, marked fixed", _fx and _fx["start"]==_fxcfg.get("start") and _fx["end"]==_fxcfg.get("end") and _fx.get("fixed"))
# bayshore's fixed_viewing weekday moved from Sat to Fri 14 Jul 2026 (hardcoded slot config:
# eastpoint-green Fri 7.15 to 8pm, bayshore Fri 8 to 9pm; see reference_fixed_viewing_slots).
ok("fixed slot lands on the configured weekday (Fri)", _fx and __import__("datetime").date(*map(int,_fx["date"].split("-"))).weekday()==4)
ok("from a Wed -> offers the COMING Friday (06-19)", _fx and _fx["date"]=="2026-06-19")
ok("rolls to next week once that Friday passes", E._fixed_viewing_slot("bayshore","2026-06-22")["date"]=="2026-06-26")
ok("on the day itself it still offers that day", E._fixed_viewing_slot("bayshore","2026-06-19")["date"]=="2026-06-19")
ok("next_future_slot returns the fixed slot (overrides the availability file)", (E.next_future_slot("bayshore") or {}).get("fixed") is True)
ok("a listing with NO fixed rule is unaffected", E._fixed_viewing_slot("caspian","2026-06-17") is None)

print("== 16. CO-PILOT under manual takeover (screen; auto-offer viewing when QUALIFIED + slot) ==")
# manual takeover + incomplete profile -> engine stays silent
scp={"version":1,"conversations":{}}; jcp="6590221100@s.whatsapp.net"
E.handle_event(scp,{"jid":jcp,"msg_id":"cp1","text":"Hi is Caspian still available?","is_from_me":0,"listing_key":"caspian"})
E.handle_event(scp,{"jid":jcp,"msg_id":"cp2","text":"let me check with the landlord ah","is_from_me":1,"engine":False})  # Winfred by hand
ok("manual takeover latched", scp["conversations"]["6590221100"]["manual_takeover"] is True)
aI=E.handle_event(scp,{"jid":jcp,"msg_id":"cp3","text":"Name: Mei","is_from_me":0})
ok("incomplete profile under manual takeover -> still silent", aI is None)
# manual takeover + DISQUALIFIED (Indian on caspian) -> COPILOT_VERDICT, NO prospect message
sdq={"version":1,"conversations":{}}; jdq="6590222200@s.whatsapp.net"
E.handle_event(sdq,{"jid":jdq,"msg_id":"dq1","text":"Hi is Caspian still available?","is_from_me":0,"listing_key":"caspian"})
E.handle_event(sdq,{"jid":jdq,"msg_id":"dq2","text":"let me check ah","is_from_me":1,"engine":False})
_dqf="Name: Raj\nNationality: Indian\nEthnicity: Indian\nGender: Male\nAge: 30\nType of Pass: EP\nNo. of Pax: 1\nIntended Move in Date: 1 Aug\nPreferred Lease Term: 12 months\nBudget: 1200"
aDq=E.handle_event(sdq,{"jid":jdq,"msg_id":"dq3","text":_dqf,"is_from_me":0})
ok("DISQUALIFIED under manual -> COPILOT_VERDICT (notify), no prospect message", aDq and aDq["type"]=="COPILOT_VERDICT" and aDq.get("notify") is True and aDq.get("text") is None)
# manual takeover + QUALIFIED + open slot (bayshore has a fixed weekly slot).
# bayshore's master room rent rose to $2,300/mth (budget_floor 2300 in the live listing index);
# the old $1,500 fixture budget now DISQUALIFIES on price alone, so bump it above the floor.
# NOTE 31 Jul 2026: this used to assert an auto-offer (OFFER_VIEWING) fired here. The 26 Jul
# hardening (c634271) tightened the single-sender rule so manual_takeover and copilot_muted
# are now ALWAYS set together (every code path that sets one sets both) -- there is no longer
# any way to reach "manual takeover, not muted" through a real inbound event, which is the
# correct, doctrine-aligned behavior ("after a hand reply the copilot may never message this
# prospect again"). The autonomous (non-manual) auto-offer -> confirm flow is still fully
# covered elsewhere (see "prospect says yes -> CONFIRM_VIEWING" and the open-intake
# OFFER_VIEWING test) and unaffected. This scenario now correctly stays silent throughout,
# only ever pinging Winfred with a screening verdict.
_qf="Name: Mei\nNationality: Singaporean\nEthnicity: Chinese\nGender: Female\nAge: 30\nType of Pass: SC\nNo. of Pax: 1\nIntended Move in Date: 1 Aug\nPreferred Lease Term: 12 months\nBudget: 2500"
sql={"version":1,"conversations":{}}; jql="6590223300@s.whatsapp.net"
E.handle_event(sql,{"jid":jql,"msg_id":"q1","text":"Hi is the Bayshore room still available?","is_from_me":0,"listing_key":"bayshore"})
E.handle_event(sql,{"jid":jql,"msg_id":"q2","text":"let me check with owner ah","is_from_me":1,"engine":False})
aQ=E.handle_event(sql,{"jid":jql,"msg_id":"q3","text":_qf,"is_from_me":0})
ok("QUALIFIED under manual (muted) + slot -> COPILOT_VERDICT, no prospect send", aQ and aQ["type"]=="COPILOT_VERDICT" and aQ.get("verdict")=="QUALIFIED" and aQ.get("text") is None)
ok("verdict still pings Winfred (notify), never the prospect", aQ and aQ.get("notify") is True)
ok("verdict not repeated on a neutral reply (same profile, same sig)", E.handle_event(sql,{"jid":jql,"msg_id":"q4","text":"hmm let me think about it","is_from_me":0}) is None)
ok("prospect YES under muted manual takeover -> still silent, no stray CONFIRM_VIEWING", E.handle_event(sql,{"jid":jql,"msg_id":"q5","text":"yes sounds good","is_from_me":0}) is None)
sqn={"version":1,"conversations":{"6590224400":{"pn":"6590224400","listing_key":"bayshore","stage":"VIEWING_OFFERED","profile":{"name":"T"},"processed_ids":[],"form_sent":True,"asked_fields":[],"viewing_asked":True,"viewing_confirmed":False,"manual_takeover":True,"status":"manual","offered_slot_id":"s1"}}}
aQn=E.handle_event(sqn,{"jid":"6590224400@s.whatsapp.net","msg_id":"qn1","text":"is parking included?","is_from_me":0})
ok("question after auto-offer (manual) -> ANSWER_QUESTION ping, no auto-answer", aQn and aQn["type"]=="ANSWER_QUESTION" and aQn.get("notify") is True and aQn.get("text") is None)
# a known landlord under manual takeover -> NO co-pilot / no auto-offer
slr={"version":1,"conversations":{}}; jlr=f"{LANDLORD_PN}@s.whatsapp.net"
E.handle_event(slr,{"jid":jlr,"msg_id":"lr1","text":"hi can you confirm","is_from_me":1,"engine":False})
E.handle_event(slr,{"jid":jlr,"msg_id":"lr2","text":_qf,"is_from_me":0,"listing_key":"bayshore"})
aLr=E.handle_event(slr,{"jid":jlr,"msg_id":"lr3","text":_qf,"is_from_me":0,"listing_key":"bayshore"})
ok("known landlord under manual takeover -> NO co-pilot/auto-offer", aLr is None)

print("== 17. FORM: Preferred Location captured + NO CEA signoff in any tenant-facing copy ==")
# the form field itself was shortened to "Location" (still parsed into preferred_location by
# extract_profile's "preferred location|preferred area|location" fallback, checked below).
ok("INTAKE_FORM includes a Location field", "• Location:" in E.INTAKE_FORM)
ok("INTAKE_FORM still has all 10 required field labels",
   all(x in E.INTAKE_FORM for x in ["Name:","Nationality:","Ethnicity:","Gender:","Age:","Pass type","No. of pax","Move in date","Lease term","Budget"]))
ok("extract_profile captures preferred_location", E.extract_profile("Preferred Location: Tampines").get("preferred_location")=="Tampines")
ok("preferred_location is NOT a required field (does not gate qualify)", "preferred_location" not in E.REQUIRED_FIELDS)
ok("INTAKE_FORM has no CEA signoff", "R073319H" not in E.INTAKE_FORM)
ok("viewing text (no slot) has no CEA signoff", "R073319H" not in E._viewing_text(None))
ok("viewing text (with slot) has no CEA signoff", "R073319H" not in E._viewing_text({"label":"Sat 3pm"}))
ok("redirect text has no CEA signoff", "R073319H" not in E._redirect_text([], {}, {}))
ok("needs-info text has no CEA signoff", "R073319H" not in E._needs_info_text(["budget"]))
_svt=E.handle_event({"version":1,"conversations":{"6590111222":{"pn":"6590111222","listing_key":"caspian","stage":"VIEWING_OFFERED","profile":{"name":"T"},"processed_ids":[],"form_sent":True,"asked_fields":[],"viewing_asked":True,"viewing_confirmed":False,"manual_takeover":False,"status":"viewing_offered","offered_slot_id":"s1"}}},{"jid":"6590111222@s.whatsapp.net","msg_id":"vt1","text":"I can do Saturday 3pm","is_from_me":0})
ok("VIEWING_TIME_PROPOSED text has no CEA signoff", _svt and "R073319H" not in (_svt.get("text") or ""))

print("== 18. FORMAT HINTS + COMPLETION NUDGE ==")
ok("hinted budget filled -> parsed cleanly", E.extract_profile("• Budget (S$ per month): 1300").get("budget")==1300)
ok("hinted move-in filled -> parsed cleanly", E.extract_profile("• Intended Move in Date (e.g. 1 Aug): 15 Aug").get("move_in_date")=="15 Aug")
ok("blank hinted field -> hint NOT captured as a value", E.extract_profile("• Budget (S$ per month):").get("budget") is None)
ok("blank INTAKE_FORM still yields zero required fields", E.missing_required(E.extract_profile(E.INTAKE_FORM))==E.REQUIRED_FIELDS)
sn={"version":1,"conversations":{}}; jn="6590778899@s.whatsapp.net"
E.handle_event(sn,{"jid":jn,"msg_id":"n1","text":"Hi is Caspian still available?","is_from_me":0,"listing_key":"caspian"})
sn["conversations"]["6590778899"]["form_sent_ts"] -= 300   # skip the 3 min anti-spam grace period
an=E.handle_event(sn,{"jid":jn,"msg_id":"n2","text":"Name: Mei\nBudget: 1300","is_from_me":0})
ok("partial profile -> NUDGE_INCOMPLETE to prospect", an and an["type"]=="NUDGE_INCOMPLETE" and an.get("text"))
ok("nudge names a missing field, no CEA", an and "nationality" in an["text"].lower() and "R073319H" not in an["text"])
an2=E.handle_event(sn,{"jid":jn,"msg_id":"n3","text":"still thinking","is_from_me":0})
ok("nudge fires once only, then silent", an2 is None)
# completing the profile after a nudge still flows to qualify (REDIRECT here: Indian on caspian)
an3=E.handle_event(sn,{"jid":jn,"msg_id":"n4","text":"Nationality: Indian\nEthnicity: Indian\nGender: Female\nAge: 30\nType of Pass: EP\nNo. of Pax: 1\nMove in: 1 Aug\nLease: 12 months","is_from_me":0})
ok("profile completed after nudge -> engine proceeds (not stuck silent)", an3 is not None)

print("== 19. 10-MESSAGE CAP per prospect ==")
ok("MAX_PROSPECT_MSGS is 10", E.MAX_PROSPECT_MSGS == 10)
def _vo_cap(pn, n):
    return {"version":1,"conversations":{pn:{"pn":pn,"listing_key":"caspian","stage":"VIEWING_OFFERED",
            "profile":{"name":"T"},"processed_ids":[],"form_sent":True,"asked_fields":[],
            "viewing_asked":True,"viewing_confirmed":False,"manual_takeover":False,
            "status":"viewing_offered","offered_slot_id":"s1","sent_count":n}}}
su=_vo_cap("6590333400",3)
aU=E.handle_event(su,{"jid":"6590333400@s.whatsapp.net","msg_id":"u1","text":"saturday 3pm works","is_from_me":0})
ok("under cap -> prospect message still sent", aU and aU.get("text"))
ok("counter increments after a send", su["conversations"]["6590333400"].get("sent_count")==4)
sc=_vo_cap("6590333300",10)
aCap=E.handle_event(sc,{"jid":"6590333300@s.whatsapp.net","msg_id":"c1","text":"I can view saturday 3pm","is_from_me":0})
ok("at 10-message cap -> CAP_REACHED, no prospect text, ping Winfred", aCap and aCap["type"]=="CAP_REACHED" and aCap.get("text") is None and aCap.get("notify") is True)
aCap2=E.handle_event(sc,{"jid":"6590333300@s.whatsapp.net","msg_id":"c2","text":"3pm please","is_from_me":0})
ok("past the cap -> silent (flagged once only)", aCap2 is None)
sq=_vo_cap("6590333600",10)
aQ=E.handle_event(sq,{"jid":"6590333600@s.whatsapp.net","msg_id":"qq1","text":"is there wifi?","is_from_me":0})
ok("notify-only action (question) is NOT capped at 10", aQ and aQ["type"]=="ANSWER_QUESTION" and aQ.get("text") is None)

print("== 20. CLOSED / terminal conversations stay fully silent ==")
st_term={"version":1,"conversations":{"6590555500":{"pn":"6590555500","listing_key":"caspian","stage":"DISQUALIFIED","profile":{"name":"T"},"processed_ids":[],"form_sent":True,"asked_fields":[],"viewing_asked":False,"viewing_confirmed":False,"manual_takeover":True,"status":"closed (found elsewhere)","terminal":True}}}
ok("terminal conversation -> engine silent even on 'yes 3pm'", E.handle_event(st_term,{"jid":"6590555500@s.whatsapp.net","msg_id":"tm1","text":"yes 3pm works","is_from_me":0}) is None)
ok("terminal status NOT overwritten to manual", st_term["conversations"]["6590555500"]["status"]=="closed (found elsewhere)")
st_term2={"version":1,"conversations":{"6590555600":{"pn":"6590555600","listing_key":"caspian","stage":"DISQUALIFIED","profile":{"name":"T"},"processed_ids":[],"form_sent":True,"asked_fields":[],"viewing_asked":True,"viewing_confirmed":False,"manual_takeover":False,"status":"disqualified","terminal":True}}}
ok("terminal + non-manual + 'yes' -> still silent (no stray CONFIRM_VIEWING)", E.handle_event(st_term2,{"jid":"6590555600@s.whatsapp.net","msg_id":"tm2","text":"yes please","is_from_me":0}) is None)

print("== 21. WITHDRAWAL auto-close (found another place / no longer renting) ==")
def _wstate(pn, manual=False):
    return {"version":1,"conversations":{pn:{"pn":pn,"listing_key":"caspian","stage":"NEEDS_INFO","profile":{"name":"W"},"processed_ids":[],"form_sent":True,"asked_fields":[],"viewing_asked":False,"viewing_confirmed":False,"manual_takeover":manual,"status":("manual" if manual else "needs_info"),"sent_count":1}}}
s1=_wstate("6590010001"); a1=E.handle_event(s1,{"jid":"6590010001@s.whatsapp.net","msg_id":"w1","text":"Hi, I found another place already. Thank you!","is_from_me":0})
ok("'found another place' -> AUTO_CLOSED, no prospect text", a1 and a1.get("type")=="AUTO_CLOSED" and a1.get("text") is None)
ok("'found another place' -> terminal set", s1["conversations"]["6590010001"].get("terminal") is True)
s2=_wstate("6590010002"); a2=E.handle_event(s2,{"jid":"6590010002@s.whatsapp.net","msg_id":"w2","text":"sorry, i do not wish to rent anymore","is_from_me":0})
ok("'do not wish to rent anymore' -> AUTO_CLOSED", a2 and a2.get("type")=="AUTO_CLOSED")
s3=_wstate("6590010003"); a3=E.handle_event(s3,{"jid":"6590010003@s.whatsapp.net","msg_id":"w3","text":"no longer looking, thanks anyway","is_from_me":0})
ok("'no longer looking' -> AUTO_CLOSED", a3 and a3.get("type")=="AUTO_CLOSED")
s4=_wstate("6590010004",manual=True); a4=E.handle_event(s4,{"jid":"6590010004@s.whatsapp.net","msg_id":"w4","text":"we went with another unit in the end","is_from_me":0})
ok("withdrawal closes even under MANUAL takeover", a4 and a4.get("type")=="AUTO_CLOSED" and s4["conversations"]["6590010004"].get("terminal") is True)
s5=_wstate("6590010005"); a5=E.handle_event(s5,{"jid":"6590010005@s.whatsapp.net","msg_id":"w5","text":"found another place but is yours still available?","is_from_me":0})
ok("keep-open veto: 'still available' does NOT auto-close", not (a5 and a5.get("type")=="AUTO_CLOSED") and s5["conversations"]["6590010005"].get("terminal") is not True)
s6=_wstate("6590010006"); a6=E.handle_event(s6,{"jid":"6590010006@s.whatsapp.net","msg_id":"w6","text":"i found the place a bit small to be honest","is_from_me":0})
ok("unit feedback 'found the place small' does NOT auto-close", not (a6 and a6.get("type")=="AUTO_CLOSED") and s6["conversations"]["6590010006"].get("terminal") is not True)
a4b=E.handle_event(s4,{"jid":"6590010004@s.whatsapp.net","msg_id":"w4b","text":"yes 3pm works","is_from_me":0})
ok("after withdrawal-close, later 'yes 3pm' stays silent", a4b is None)
ok("withdrawal_signal direct: positive", E.withdrawal_signal("i already rented somewhere else") is True)
ok("withdrawal_signal direct: negative (plain enquiry)", E.withdrawal_signal("hi is the room still available to rent?") is False)
ok("withdrawal_signal direct: landlord availability question is NOT a withdrawal", E.withdrawal_signal("so the landlord still not going to rent?") is False)
ok("FP fixed: 'no longer looking at the east, only west now' stays open", E.withdrawal_signal("no longer looking at the east side, only west now") is False)
ok("FP fixed: comparison 'found a place already, similar to yours?' stays open", E.withdrawal_signal("found a place already thats perfect, similar to yours?") is False)
ok("FP fixed: 'staying put for the viewing' stays open", E.withdrawal_signal("im staying put for the viewing tomorrow") is False)
ok("FP fixed: 'thanks anyway, can you send the other unit' stays open", E.withdrawal_signal("thanks anyway, can you send the other unit") is False)
ok("no over-correction: 'no longer interested in renting' still closes", E.withdrawal_signal("no longer interested in renting, sorry") is True)
ok("no over-correction: 'found another place' still closes", E.withdrawal_signal("hi i found another place already") is True)

print("== 22. OPEN_INTAKE (owner accepts all except baby; + kept ethnicity/nationality gate) ==")
_BAY={"requirements":{"open_intake":True,"ethnicity_rule":{"mode":"any","list":[]},"nationality_pref":{"mode":"any","list":[]}}}
_EAS={"requirements":{"open_intake":True,"ethnicity_rule":{"mode":"exclude","list":["Indian"]},"nationality_pref":{"mode":"exclude","list":["India","Indian"]}}}
ok("open: name+pax alone -> QUALIFIED", E.qualify(_BAY,{"name":"A","no_of_pax":1})[0]=="QUALIFIED")
ok("open: accepts all nationalities incl Indian", E.qualify(_BAY,{"name":"A","no_of_pax":1,"nationality":"Indian"})[0]=="QUALIFIED")
# a GLOBAL 1 year lease floor (Winfred, 11 Jul 2026) now applies even to open_intake listings,
# so a lease_term_months below 12 is no longer silently ignored (SHORT_LEASE, not QUALIFIED).
# budget/occupation/age remain ungated for an open listing with no budget_floor configured.
ok("open: ignores budget/occupation/age (lease has its own global 1yr floor)",
   E.qualify(_BAY,{"name":"A","no_of_pax":1,"budget":1,"occupation":"student","lease_term_months":12,"age":18})[0]=="QUALIFIED")
ok("open+kept gate: eastpoint India -> DISQUALIFIED", E.qualify(_EAS,{"name":"A","no_of_pax":1,"nationality":"India"})[0]=="DISQUALIFIED")
ok("open+kept gate: eastpoint non-India -> QUALIFIED", E.qualify(_EAS,{"name":"A","no_of_pax":1,"nationality":"Singaporean"})[0]=="QUALIFIED")
ok("open+kept gate: eastpoint no nationality -> NEEDS_INFO", E.qualify(_EAS,{"name":"A","no_of_pax":1})[0]=="NEEDS_INFO")
# missing_required for an open listing now ALSO requires budget + lease_term_months (must
# knows for affordability + the 1yr floor, Winfred 11 Jul 2026), not just name+pax.
ok("open missing_required: name+pax for bayshore still needs budget+lease", E.missing_required({"name":"A","no_of_pax":1},_BAY)==["budget","lease_term_months"])
ok("open missing_required: eastpoint also needs nationality", E.missing_required({"name":"A","no_of_pax":1},_EAS)==["budget","lease_term_months","nationality"])
ok("open: baby ALWAYS blocked", E.policy_excluded({"no_of_pax":2},"coming with a baby",open_intake=True)=="family")
ok("open: India NOT blocked by global policy (per-listing instead)", E.policy_excluded({"nationality":"India"},"",open_intake=True) is None)
ok("non-open regression: India still globally blocked", E.policy_excluded({"nationality":"India"},"",open_intake=False)=="nationality")
ok("non-open regression: full form still required", set(E.missing_required({"name":"A"}))>= {"nationality","budget"})
# end to end: open listing enquiry -> FULL form (the short open-intake form was retired 13 Jul
# 2026 -- "the short open-intake form caused form-after-form sequences and violated the
# standing full-form rule", so every enquiry, open_intake or not, now gets the same full
# 14 field INTAKE_FORM) -> brief reply with the open-intake must-knows -> viewing offered.
_so={"version":1,"conversations":{"6590999009":{"pn":"6590999009","listing_key":"bayshore","stage":"NEW","profile":{},"processed_ids":[],"form_sent":False,"asked_fields":[],"viewing_asked":False,"viewing_confirmed":False,"manual_takeover":False,"status":"new","last_inbound":None}}}
_so1=E.handle_event(_so,{"jid":"6590999009@s.whatsapp.net","msg_id":"o1","text":"hi is the bayshore room available to rent?","is_from_me":0})
_form=(_so1.get("texts") or [_so1.get("text") or ""])[-1]
ok("open enquiry -> SEND_FORM with the FULL form (short form retired 13 Jul 2026)",
   _so1.get("type")=="SEND_FORM" and "Name:" in _form and "Budget:" in _form and "Occupation" in _form)
_so["conversations"]["6590999009"]["form_sent_ts"] -= 300   # skip the 3 min anti-spam grace period
_so2=E.handle_event(_so,{"jid":"6590999009@s.whatsapp.net","msg_id":"o2","text":"Name: John\nNationality: Malaysian\nNo. of Pax: 1\nBudget: 2500\nLease: 12 months","is_from_me":0})
ok("open brief reply (name+pax+budget+lease, the open-intake must knows) -> OFFER_VIEWING", _so2 and _so2.get("type")=="OFFER_VIEWING")

print("== LEAD SOURCE: first-touch attribution (website CTA vs portal vs unknown) ==")
# unit tests on the classifier itself
ok("portal wins regardless of text", E.classify_lead_source("hi is this still available", "bayshore")=="portal")
ok("website: matches a real site CTA phrase, 'Hi Winfred,' anchored",
   E.classify_lead_source("Hi Winfred, I have a property question.", None)=="website")
ok("website: 'I'd like to discuss' family",
   E.classify_lead_source("Hi Winfred, I'd like to discuss Lentor Gardens Residences.", None)=="website")
ok("website: 'I read your article' family (insights CTA)",
   E.classify_lead_source("Hi Winfred, I read your article and would like a portfolio enquiry.", None)=="website")
ok("unknown: organic Carousell-style message, no CTA phrasing, no listing_key",
   E.classify_lead_source("hey is the flat still up for rent", None)=="unknown")
ok("unknown: CTA phrase present but NOT anchored at 'Hi Winfred,' start (unlikely to be the real CTA)",
   E.classify_lead_source("someone told me you can help, i have a property question", None)=="unknown")
ok("unknown: no text at all", E.classify_lead_source(None, None)=="unknown")

# end to end via handle_event: stamped once on first genuine inbound, never re-classified
_sp = {"version":1,"conversations":{}}
E.handle_event(_sp, {"jid":"6590055501@s.whatsapp.net","msg_id":"sp1","text":"hi still available?","is_from_me":0,"listing_key":"bayshore"})
ok("portal-attributed record stores source=portal", _sp["conversations"]["6590055501"]["source"]=="portal")

_sw = {"version":1,"conversations":{}}
E.handle_event(_sw, {"jid":"6590055502@s.whatsapp.net","msg_id":"sw1","text":"Hi Winfred, I have a question about upgrading from my HDB.","is_from_me":0})
ok("website-attributed record stores source=website", _sw["conversations"]["6590055502"]["source"]=="website")

_su = {"version":1,"conversations":{}}
E.handle_event(_su, {"jid":"6590055503@s.whatsapp.net","msg_id":"su1","text":"saw ur listing on carousell, still got?","is_from_me":0})
ok("unattributed record stores source=unknown", _su["conversations"]["6590055503"]["source"]=="unknown")

E.handle_event(_su, {"jid":"6590055503@s.whatsapp.net","msg_id":"su2","text":"Hi Winfred, I have a property question.","is_from_me":0})
ok("source is first-touch only: a later CTA-shaped reply does NOT overwrite the original unknown",
   _su["conversations"]["6590055503"]["source"]=="unknown")

print("== NAME EXTRACTION: 'name' matched in prose, not just 'Name:' fields (31 Jul 2026 fix) ==")
# grab() has no way to tell a structured "Name:" field apart from the bare word "name"
# inside a sentence -- "my name is Ruth" used to capture "is Ruth" as the name (also seen
# live as garbled "an"). A leading connector verb is now stripped.
ok("free-text 'my name is Ruth' -> name is 'Ruth', not 'is Ruth'",
   E.extract_profile("my name is Ruth").get("name")=="Ruth")
ok("free-text \"name's Ruth\" (apostrophe-s, no space) -> 'Ruth'",
   E.extract_profile("name's Ruth").get("name")=="Ruth")
ok("structured 'Name: Ruth' still works (regression guard)",
   E.extract_profile("Name: Ruth\nBudget: 500000").get("name")=="Ruth")
ok("buyer flow: 'my name is Ruth' on its own line -> name 'Ruth'",
   E.extract_buyer("my name is Ruth\nBudget: 500k").get("name")=="Ruth")
ok("buyer flow free-form 'im Ken' fallback still works",
   E.extract_buyer("im Ken, budget 800k").get("name")=="Ken")

print("== FIXED VIEWING SLOT offered on a BUYER (sale) enquiry (3 Aug 2026: Kembangan Villas Sat 11-12noon, 23 Sin Ming Road Sat 1-2.30pm) ==")
# registry-level: same fixed_viewing mechanism rentals use (weekday/start/end/time_label),
# just now also read on the sale/buyer branch. "2026-07-30" is a Thursday, so the next-Sat
# rollover is exercised deterministically regardless of the wall clock the suite runs at.
ok("kembangan-villas has a fixed_viewing rule -> next Sat 1 Aug, 11:00-12:00",
   (lambda s: s and s["date"]=="2026-08-01" and s["start"]=="11:00" and s["end"]=="12:00")(
     E._fixed_viewing_slot("kembangan-villas","2026-07-30")))
ok("sin-ming-rd-23 has a fixed_viewing rule -> next Sat 1 Aug, 13:00-14:30",
   (lambda s: s and s["date"]=="2026-08-01" and s["start"]=="13:00" and s["end"]=="14:30")(
     E._fixed_viewing_slot("sin-ming-rd-23","2026-07-30")))
# end to end: the first buyer-form message includes the fixed slot, and it is tracked under
# its OWN state key (buyer_offered_slot_id) so it never touches the rental viewing_asked /
# offered_slot_id machinery a later rental enquiry from the same person might rely on.
sK={"version":1,"conversations":{}}
aK=E.handle_event(sK,{"jid":"6590221100@s.whatsapp.net","msg_id":"K1",
   "text":"Hi Winfred Quek, I am interested in your Sale property Kembangan Villas, 6 bedroom, listed for S$ 7,299,999.",
   "is_from_me":0,"listing_key":"kembangan-villas"})
ok("Kembangan Villas buyer enquiry -> SEND_BUYER_FORM with the fixed slot appended",
   aK and aK["type"]=="SEND_BUYER_FORM" and "Viewing:" in aK["text"] and "11 to 12noon" in aK["text"])
ok("buyer-side slot tracked separately (buyer_offered_slot_id), rental viewing_asked untouched",
   sK["conversations"]["6590221100"].get("buyer_offered_slot_id","").startswith("kembangan-villas-fixed-")
   and sK["conversations"]["6590221100"].get("viewing_asked") is False)

sM={"version":1,"conversations":{}}
aM=E.handle_event(sM,{"jid":"6590221101@s.whatsapp.net","msg_id":"M1",
   "text":"Hi Winfred Quek,\nI am interested in:\nSALE - 23 Sin Ming Road\n2 Beds /  S$ 368,000\n\nThanks",
   "is_from_me":0,"listing_key":"sin-ming-rd-23"})
ok("23 Sin Ming Road buyer enquiry -> SEND_BUYER_FORM with the fixed slot appended",
   aM and aM["type"]=="SEND_BUYER_FORM" and "Viewing:" in aM["text"] and "1 to 2.30pm" in aM["text"])

# regression: a sale enquiry with no listing_key match (or a listing with no fixed_viewing
# rule) still sends the plain buyer form -- no "Viewing:" line, no crash on next_slot(None).
ok("un-matched sale enquiry (no listing_key) -> buyer form with NO viewing line",
   aS and "Viewing:" not in aS["text"])   # aS defined in section 11 above (Sembawang, listing_key=None)

print("== BACKTEST FIXES (5 Aug 2026): fixed_viewing must not leak across deal_type/status ==")
# Finding 1: a RENTAL listing (ang-mo-kio-539) can still classify as tx=="sale" via an
# explicit portal SALE token overriding the registry -- its fixed_viewing (a rental viewing
# slot) must never be appended to the buyer/purchase intake message.
sR={"version":1,"conversations":{}}
aR2=E.handle_event(sR,{"jid":"6590331199@s.whatsapp.net","msg_id":"r1",
   "text":"hi is the ang mo kio 539 unit for sale? what price","is_from_me":0,"listing_key":"ang-mo-kio-539"})
ok("rental listing misclassified as sale -> buyer form with NO viewing line (deal_type gate)",
   aR2 and aR2["type"]=="SEND_BUYER_FORM" and "Viewing:" not in aR2["text"])

# Finding 2: sin-ming-rd-23's keywords must be specific to that address, not the bare road
# name (Sin Ming Road spans many unrelated AMK/Bishan blocks).
ok("generic 'sin ming road' text does NOT bind to the sin-ming-rd-23 sale listing",
   RNR.match_listing("hi is the common room along sin ming road still up? budget 900", E.listing_reqs()) is None)

# Finding 4: a fixed_viewing sale listing put on hold/closed must not auto-commit a buyer to
# a concrete viewing time for a property that is no longer available.
_orig_reqs_hold = E.listing_reqs
def _reqs_kembangan_hold():
    r = {k: dict(v) for k, v in _orig_reqs_hold().items()}
    r["kembangan-villas"]["status"] = "hold"
    return r
E.listing_reqs = _reqs_kembangan_hold
sH={"version":1,"conversations":{}}
aH=E.handle_event(sH,{"jid":"6590331188@s.whatsapp.net","msg_id":"h1",
   "text":"Hi Winfred Quek, I am interested in your Sale property Kembangan Villas, 6 bedroom, listed for S$ 7,299,999.",
   "is_from_me":0,"listing_key":"kembangan-villas"})
ok("kembangan-villas on hold -> buyer form with NO viewing line (status gate)",
   aH and aH["type"]=="SEND_BUYER_FORM" and "Viewing:" not in aH["text"])
E.listing_reqs = _orig_reqs_hold
# regression guard: the same active listing still offers its slot normally (gate isn't over-broad)
sK2={"version":1,"conversations":{}}
aK2=E.handle_event(sK2,{"jid":"6590331177@s.whatsapp.net","msg_id":"k2",
   "text":"Hi Winfred Quek, I am interested in your Sale property Kembangan Villas, 6 bedroom, listed for S$ 7,299,999.",
   "is_from_me":0,"listing_key":"kembangan-villas"})
ok("kembangan-villas still active -> viewing slot still offered (gate not over-broad)",
   aK2 and aK2["type"]=="SEND_BUYER_FORM" and "Viewing:" in aK2["text"])

# Pre-existing bug (predates tonight, since commit c634271 29 Jul), fixed 5 Aug 2026:
# _unit_rejection fired before the buyer_form_sent gate, so a buyer commenting "too small"/
# "too far" on a PURCHASE enquiry got misrouted into the rental cross-sell/redirect path and
# the conversation was permanently closed (terminal=True), silently swallowing every message
# after -- including their name/budget/financing, which never reached Winfred.
sU={"version":1,"conversations":{}}
jidU = "6598765432@s.whatsapp.net"
E.handle_event(sU, {"jid":jidU,"msg_id":"u1","text":"Hi is Kembangan Villas still for sale? Keen to view","is_from_me":0,"listing_key":"kembangan-villas"})
pnU = E.resolve_pn(jidU); recU = sU["conversations"][pnU]
aU2 = E.handle_event(sU, {"jid":jidU,"msg_id":"u2","text":"hmm too small for us, but my name is John, budget 2m, HFE valid, own stay","is_from_me":0,"listing_key":"kembangan-villas"})
ok("buyer 'too small for us' reply -> profile captured (name/budget/financing), NOT swallowed",
   aU2 is not None and recU.get("buyer",{}).get("budget")==2000000 and recU.get("buyer",{}).get("financing")=="valid")
ok("buyer 'too small for us' reply -> conversation stays open (no rental terminal/redirect)",
   not recU.get("terminal") and recU.get("stage") != "CLOSED_UNIT_REJECTED")

print(f"\nRESULT: {P} passed, {F} failed")
sys.exit(1 if F else 0)
