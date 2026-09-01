"""Owner vs agent classification for Carousell FSBO leads.

Layered per spec: hard AGENT signals are checked first (a real agent's post
can still say "seller"/"owner wants X", so agent evidence must win over
loose owner-ish wording), then hard OWNER phrases, then an Ollama fallback
for anything still ambiguous. Ollama is best effort -- if it's down, or the
model returns nothing parseable, the listing is marked UNSURE rather than
guessed. Model selection is dynamic (first entry in `ollama list`), per
spec -- on this machine that's moondream (vision-tuned); it returns an
empty completion for plain text prompts (verified live), so the ambiguous
bucket resolves to UNSURE in practice until Winfred reorders/installs a
text model.
"""

import json
import re
import urllib.request
from typing import Tuple

OLLAMA_HOST = "http://127.0.0.1:11434"

CEA_RE = re.compile(r"\bR\d{6}[A-Za-z]\b")
COBROKE_RE = re.compile(r"\bco[\s\-]?broke\b", re.IGNORECASE)

AGENCY_NAMES = [
    "propnex", "era", "huttons", "orangetee", "orange tee", "realstar",
    "knight frank", "savills", "srx", "dennis wee", "century21", "century 21",
    "redbrick", "propertylimbrothers",
]
AGENT_PHRASES = [
    "my client", "my seller", "my listing", "serving a client", "represent the seller",
    "commission split", "share comm", "co-agent", "co agent",
    "i'm an agent", "i am an agent",
]
DEALER_NAME_RE = re.compile(
    r"rental|specialist|propert|realt|agent|homes|housing|estate|room4u|sgroom", re.IGNORECASE)
CONTENT_MARKETING_RE = re.compile(
    r"\b(mistakes|guide|tips|how to|before selling|before you sell|what you need to know|free valuation|seminar)\b",
    re.IGNORECASE)

OWNER_PHRASES = [
    "direct owner", "no agent", "i am the owner", "owner sale", "sale by owner",
    "no agents please", "owner selling", "by owner",
]


def _first_ollama_model(logger) -> str:
    try:
        with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=5) as resp:
            data = json.loads(resp.read())
        models = data.get("models", [])
        return models[0]["name"] if models else ""
    except Exception as e:
        logger.debug(f"Ollama tags fetch failed: {e}")
        return ""


def classify_with_ollama(text: str, logger) -> Tuple[str, str]:
    model = _first_ollama_model(logger)
    if not model:
        return "UNSURE", "Ollama unavailable, no hard signals matched"

    prompt = (
        "You are screening a Singapore property listing to decide if the poster "
        "is the property OWNER selling directly, or a real-estate AGENT. "
        "Reply with exactly one word: OWNER, AGENT, or UNSURE.\n\n"
        f"Listing text:\n{text[:1500]}\n\nAnswer:"
    )
    try:
        body = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
        req = urllib.request.Request(
            f"{OLLAMA_HOST}/api/generate", data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        raw = (data.get("response") or "").strip()
        m = re.search(r"\b(OWNER|AGENT|UNSURE)\b", raw, re.IGNORECASE)
        if m:
            return m.group(1).upper(), f"ollama ({model}) classified: {raw[:80]!r}"
        return "UNSURE", f"ollama ({model}) returned no parseable verdict: {raw[:80]!r}"
    except Exception as e:
        logger.debug(f"Ollama classify error: {e}")
        return "UNSURE", "ollama call failed, no hard signals matched"


def classify_listing(text: str, seller_username: str, seller_name: str, logger) -> Tuple[str, str]:
    blob = f"{text} {seller_username} {seller_name}".lower()

    if CONTENT_MARKETING_RE.search(text):
        return "AGENT", "content-marketing style post (agent ad, not a unit listing)"

    if CEA_RE.search(text) or CEA_RE.search(seller_username) or CEA_RE.search(seller_name):
        return "AGENT", "CEA registration number found in listing text"
    if COBROKE_RE.search(blob):
        return "AGENT", "co-broke language"
    for name in AGENCY_NAMES:
        if name in blob:
            return "AGENT", f"seller name/username matches known agency ({name})"
    for phrase in AGENT_PHRASES:
        if phrase in blob:
            return "AGENT", f"agent phrasing: '{phrase}'"

    dealer_name = DEALER_NAME_RE.search(seller_username) or DEALER_NAME_RE.search(seller_name)
    for phrase in OWNER_PHRASES:
        if phrase in blob:
            if dealer_name:
                return "UNSURE", f"owner phrasing '{phrase}' BUT dealer-style seller name — verify by hand"
            return "OWNER", f"owner phrasing: '{phrase}'"

    return classify_with_ollama(text, logger)
