import sys, json, sqlite3, os
sys.path.insert(0, os.path.expanduser("~/crestbrick-consult/src/wa-pipeline"))
import intake_engine as E

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
ok("message 2 is the form with the 10 fields", all(x in a1["texts"][1] for x in ["Name:","Nationality:","Ethnicity:","Gender:","Age:","Type of Pass","No. of Pax","Move in Date","Lease Term","Budget"]))
a1b = E.handle_event(st, {"jid":jid,"msg_id":"m1","text":"Hi is Caspian still available?","is_from_me":0,"listing_key":"caspian"})
ok("same msg id replayed -> None (event dedup)", a1b is None)
a2 = E.handle_event(st, {"jid":jid,"msg_id":"m2","text":"Name: Raj\nNationality: Indian\nGender: Male","is_from_me":0})
ok("incomplete profile -> ONE nudge to prospect (almost there)", a2 and a2["type"]=="NUDGE_INCOMPLETE" and a2.get("text") and "almost there" in a2["text"].lower())
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
# sample of real prospect threads (matched tenants)
sample = ["100000000000000@lid"]  # will expand below from tenant-db
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
ok("known landlord (Example Landlord) flagged as landlord", E.excluded_reason("6500000000")=="landlord")
ok("is_enquiry true for 'still available'", E.is_enquiry("Hi is Caspian still available?"))
ok("is_enquiry false for chit chat", not E.is_enquiry("hi how are you bro"))
# a landlord who sends an enquiry-looking message still gets NO form
sL={"version":1,"conversations":{}}
aL=E.handle_event(sL,{"jid":"6500000000@s.whatsapp.net","msg_id":"L1","text":"is the room still available","is_from_me":0,"listing_key":"bayshore"})
ok("known landlord -> silent, no tenant record created (never messaged)", aL is None and "6500000000" not in sL["conversations"])
# a landlord we message FIRST (outbound) must also never become a tenant record
sLo={"version":1,"conversations":{}}
E.handle_event(sLo,{"jid":"6500000000@s.whatsapp.net","msg_id":"Lo1","text":"hi, following up on your unit","is_from_me":1,"engine":False})
ok("known landlord outbound-first -> no record (pollution vector closed)", "6500000000" not in sLo["conversations"])
# a non-enquiry first message -> no form
sN={"version":1,"conversations":{}}
aN=E.handle_event(sN,{"jid":"6590001111@s.whatsapp.net","msg_id":"N1","text":"hello bro long time","is_from_me":0})
ok("non-enquiry -> FLAG_HUMAN, no message", aN and aN["type"]=="FLAG_HUMAN" and aN.get("text") is None)
# a genuine enquiry -> SEND_FORM WITH unit info + the corrected form
sG={"version":1,"conversations":{}}
aG=E.handle_event(sG,{"jid":"6590002222@s.whatsapp.net","msg_id":"G1","text":"Hi, I am interested in RENT Caspian, still available?","is_from_me":0,"listing_key":"caspian"})
ok("genuine enquiry -> SEND_FORM", aG and aG["type"]=="SEND_FORM")
ok("message 1 includes UNIT INFO (Lakeside/Caspian)", aG and ("Lakeside" in aG["texts"][0] or "Caspian" in aG["texts"][0]))
ok("message 2 includes the correct bulleted form", aG and "• Name:" in aG["texts"][1] and "Type of Pass" in aG["texts"][1])

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
ok("India profile -> REDIRECT channel referral", aP and aP["type"]=="REDIRECT" and "does not match" in aP["text"])
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
# end to end: a SALE enquiry must never get the rental tenant intake form
sS={"version":1,"conversations":{}}
aS=E.handle_event(sS,{"jid":"6590009999@s.whatsapp.net","msg_id":"S1","text":"I am interested in: SALE - 339A Sembawang Close / 4 Beds / S$ 629,999","is_from_me":0,"listing_key":None})
ok("sale enquiry -> FLAG_HUMAN, NOT SEND_FORM", aS and aS["type"]=="FLAG_HUMAN" and "sale" in aS.get("reason","").lower())
ok("sale enquiry sends NO tenant-facing message", aS and aS.get("text") is None)
sR={"version":1,"conversations":{}}
aR=E.handle_event(sR,{"jid":"6590007777@s.whatsapp.net","msg_id":"R1","text":"I am interested in: RENT - Caspian / Room / S$ 1,100 /mo","is_from_me":0,"listing_key":"caspian"})
ok("rental enquiry still -> SEND_FORM", aR and aR["type"]=="SEND_FORM")
# a SALE enquiry must not permanently silence a person who later sends a RENTAL enquiry
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
ok("INTAKE_FORM has the field labels and NO CEA signoff", "• Name:" in E.INTAKE_FORM and "• Budget (" in E.INTAKE_FORM and "R073319H" not in E.INTAKE_FORM)

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
ok("fixed slot is the configured time (15:00-16:00, marked fixed)", _fx and _fx["start"]=="15:00" and _fx["end"]=="16:00" and _fx.get("fixed"))
ok("fixed slot lands on the configured weekday (Sat)", _fx and __import__("datetime").date(*map(int,_fx["date"].split("-"))).weekday()==5)
ok("from a Wed -> offers the COMING Saturday (06-20)", _fx and _fx["date"]=="2026-06-20")
ok("rolls to next week once that Saturday passes", E._fixed_viewing_slot("bayshore","2026-06-22")["date"]=="2026-06-27")
ok("on the day itself it still offers that day", E._fixed_viewing_slot("bayshore","2026-06-20")["date"]=="2026-06-20")
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
# manual takeover + QUALIFIED + open slot (bayshore has a fixed weekly slot) -> AUTO-OFFER viewing
_qf="Name: Mei\nNationality: Singaporean\nEthnicity: Chinese\nGender: Female\nAge: 30\nType of Pass: SC\nNo. of Pax: 1\nIntended Move in Date: 1 Aug\nPreferred Lease Term: 12 months\nBudget: 1500"
sql={"version":1,"conversations":{}}; jql="6590223300@s.whatsapp.net"
E.handle_event(sql,{"jid":jql,"msg_id":"q1","text":"Hi is the Bayshore room still available?","is_from_me":0,"listing_key":"bayshore"})
E.handle_event(sql,{"jid":jql,"msg_id":"q2","text":"let me check with owner ah","is_from_me":1,"engine":False})
aQ=E.handle_event(sql,{"jid":jql,"msg_id":"q3","text":_qf,"is_from_me":0})
ok("QUALIFIED under manual + slot -> AUTO-OFFER viewing to prospect", aQ and aQ["type"]=="OFFER_VIEWING" and aQ.get("text"))
ok("auto-offer also pings Winfred (copilot + notify)", aQ and aQ.get("copilot") is True and aQ.get("notify") is True)
ok("auto-offer not repeated on a neutral reply", E.handle_event(sql,{"jid":jql,"msg_id":"q4","text":"hmm let me think about it","is_from_me":0}) is None)
aYes=E.handle_event(sql,{"jid":jql,"msg_id":"q5","text":"yes sounds good","is_from_me":0})
ok("prospect YES after auto-offer -> bot CONFIRMS the viewing + pings Winfred", aYes and aYes["type"]=="CONFIRM_VIEWING" and aYes.get("text") and aYes.get("notify") is True)
sqn={"version":1,"conversations":{"6590224400":{"pn":"6590224400","listing_key":"bayshore","stage":"VIEWING_OFFERED","profile":{"name":"T"},"processed_ids":[],"form_sent":True,"asked_fields":[],"viewing_asked":True,"viewing_confirmed":False,"manual_takeover":True,"status":"manual","offered_slot_id":"s1"}}}
aQn=E.handle_event(sqn,{"jid":"6590224400@s.whatsapp.net","msg_id":"qn1","text":"is parking included?","is_from_me":0})
ok("question after auto-offer (manual) -> ANSWER_QUESTION ping, no auto-answer", aQn and aQn["type"]=="ANSWER_QUESTION" and aQn.get("notify") is True and aQn.get("text") is None)
# a known landlord (Example Landlord) under manual takeover -> NO co-pilot / no auto-offer
slr={"version":1,"conversations":{}}; jlr="6500000000@s.whatsapp.net"
E.handle_event(slr,{"jid":jlr,"msg_id":"lr1","text":"hi can you confirm","is_from_me":1,"engine":False})
E.handle_event(slr,{"jid":jlr,"msg_id":"lr2","text":_qf,"is_from_me":0,"listing_key":"bayshore"})
aLr=E.handle_event(slr,{"jid":jlr,"msg_id":"lr3","text":_qf,"is_from_me":0,"listing_key":"bayshore"})
ok("known landlord under manual takeover -> NO co-pilot/auto-offer", aLr is None)

print("== 17. FORM: Preferred Location captured + NO CEA signoff in any tenant-facing copy ==")
ok("INTAKE_FORM includes a Preferred Location field", "Preferred Location" in E.INTAKE_FORM)
ok("INTAKE_FORM still has all 10 required field labels",
   all(x in E.INTAKE_FORM for x in ["Name:","Nationality:","Ethnicity:","Gender:","Age:","Type of Pass","No. of Pax","Move in Date","Lease Term","Budget"]))
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

print(f"\nRESULT: {P} passed, {F} failed")
sys.exit(1 if F else 0)
