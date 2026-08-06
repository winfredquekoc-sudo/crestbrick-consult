# The four things only you can do

Everything else in the SEO and GEO programme is automated. These four need your own
accounts or your own identity, so no agent can complete them. Rough total: 40 minutes,
and they unlock work that is already built and waiting.

## 1. Search Console — 10 minutes, highest value

Full walkthrough: [gsc-auth-setup.md](gsc-auth-setup.md).

Short version: create a Desktop OAuth client in Google Cloud Console, set the consent
screen to **In production** (not Testing — that is why it kept dying weekly), put the
client ID and secret in `~/.claude/.gsc.env`, then run:

```bash
~/.claude/bin/gsc-auth-bootstrap.sh
```

**Unlocks:** real query data, so the content backlog gets prioritised by what you
actually rank for instead of guesswork, plus daily rank drop alerts that self heal.

## 2. Bing Webmaster Tools — 5 minutes

bing.com/webmasters, sign in with any Microsoft account, add winfredquek.com, and use
**Import from Google Search Console** (fastest, once step 1 is done) or verify with the
meta tag it gives you — send me the tag and I will place it.

**Why bother:** Bing's index feeds ChatGPT search and Copilot. IndexNow is already
pinging Bing on every publish; this gives you the dashboard to see it working.

## 3. Google Business Profile — 15 minutes plus verification wait

business.google.com → add your business. Category: Real Estate Agent. Use the Oxley
BizHub 2 address already on the site (62 Ubi Road 1, #05-04, S408734), your CEA number
R073319H in the description, and link winfredquek.com. Verification usually arrives by
postcard or phone.

**Why bother:** "property agent near me" and the Maps pack are searches you are absent
from entirely, and the profile also feeds Gemini.

## 4. Wikidata entity — 10 minutes

Paste ready pack: [wikidata-entity-pack.md](wikidata-entity-pack.md). Create a Wikidata
account, then follow the statements in that file exactly.

**Why bother:** Wikidata feeds Google's Knowledge Graph and is used by AI engines to
resolve who "Winfred Quek" is. Note the pack deliberately does **not** attempt a
Wikipedia article — that would fail notability and be deleted.

---

### Not on this list, deliberately

- **SearXNG rank tracking** (`scripts/serp-snapshot.sh`) needs a local SearXNG instance
  on port 8888, which needs Docker, which is not installed on this Mac. Low priority:
  once Search Console is connected it gives better rank data for free.
- **Listing photos** are handled — no longer waiting on you.
