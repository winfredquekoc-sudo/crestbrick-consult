#!/usr/bin/env python3
"""Labeled eval for intake_engine.withdrawal_signal — measures precision & recall on crafted
edge cases so the fuzzy matcher can be hardened without regressions.

Precision is the HARD GATE: a false positive wrongly closes a live lead (the unacceptable error),
so the script exits non-zero if any NEGATIVE is flagged. Recall (missed withdrawals) is reported
but not a hard fail — some misses (voice notes, pure ghosting) are out of a text matcher's reach."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src", "wa-pipeline"))
import intake_engine as E

# --- POSITIVES: a real withdrawal, the bot SHOULD close (expect True) ---
POSITIVES = [
    # explicit english
    "hi i found another place already, thanks",
    "i found another unit nearer my office",
    "i do not wish to rent anymore",
    "dont wish to rent anymore",
    "no longer interested in renting",
    "no longer looking, thank you",
    "we went with another unit in the end",
    "i already rented somewhere else",
    "we've secured another place",
    "decided not to rent for now",
    "i'd like to withdraw my application",
    "please withdraw my interest",
    "found somewhere else already",
    "sorted my housing, thanks",
    "we settled on another place",
    # singlish
    "found liao thanks",
    "got already lah",
    "settled liao",
    "rented liao",
    "no need already thanks",
    "dun need already",
    "already got a place liao",
    # indirect
    "we decided to buy instead",
    "we're buying our own instead",
    "going to buy instead of renting",
    "i'll pass on this one",
    "gonna give it a miss",
    "we'll give this a pass",
    # other languages
    "已经租到了",
    "找到房子了",
    "不租了 谢谢",
    "sudah dapat tempat lain",
]

# --- NEGATIVES: still an active prospect, the bot must NOT close (expect False) ---
NEGATIVES = [
    # enquiries / interest
    "hi is the room still available to rent?",
    "can i view this weekend?",
    "whats the monthly rental?",
    "is it still available?",
    "i'll take it!",
    "yes lets proceed",
    "sounds good, when can i view",
    "thanks im keen to view",
    # comparison / narrowing (the false-positive class)
    "no longer looking at the east side, only west now",
    "found a place already that's perfect, similar to yours?",
    "im staying put for the viewing tomorrow",
    "thanks anyway, can you send the other unit",
    "no longer keen on the master, just the common room",
    "found another listing like yours, can we compare?",
    # room clarification
    "we only want to view the common room",
    "not renting the master, just the common room",
    # landlord / availability questions
    "is the landlord still not renting?",
    "did the owner rent it out already?",
    "got already or not?",
    # tricky
    "found the place a bit small",
    "i already rented out my old place, now keen on yours",
    "found another place but is yours still available?",
    # neutral
    "ok thanks",
    "noted thanks",
    # real-data traps: substring of a withdrawal phrase inside an innocent word/handover msg
    "will pass the keys to the lawyer next week",
    "will pass all these to the lawyer",
    "sure, will pass you my details",
    "i forgot already, whats the address again",
    "pass me the form please",
    # "buy" that is not buy-vs-rent (propertyguru package, advice, idle musing)
    "i going to buy pg tml",
    "do you also help clients buy?",
    "thinking of buying property someday maybe",
]


def run():
    tp = [p for p in POSITIVES if E.withdrawal_signal(p)]
    fn = [p for p in POSITIVES if not E.withdrawal_signal(p)]
    fp = [n for n in NEGATIVES if E.withdrawal_signal(n)]
    tn = [n for n in NEGATIVES if not E.withdrawal_signal(n)]
    prec = len(tp) / (len(tp) + len(fp)) if (tp or fp) else 1.0
    rec = len(tp) / (len(tp) + len(fn)) if (tp or fn) else 1.0
    print(f"precision={prec:.2f}  recall={rec:.2f}  (TP={len(tp)} FP={len(fp)} FN={len(fn)} TN={len(tn)})")
    if fp:
        print("\nFALSE POSITIVES (wrongly close a LIVE lead — MUST be 0):")
        for n in fp:
            print(f"  ! {n}")
    if fn:
        print("\nFALSE NEGATIVES (missed withdrawal — recall gap):")
        for n in fn:
            print(f"  - {n}")
    return 0 if not fp else 1


if __name__ == "__main__":
    sys.exit(run())
