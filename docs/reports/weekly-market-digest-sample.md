# Weekly Market Digest, sample output

This is a real sample produced from data pulled on 2026-06-16 (Singapore time) to show
Winfred exactly what the `weekly-market-digest` skill produces. The post below is the
channel ready draft. The skill saves a copy to `~/.claude/state/weekly-market-digest/2026-W25.md`
and sends it to Winfred (chat 540127870) for approval. It never posts to the public channel.

Every figure here was pulled live this run. See the source verification table at the bottom.

---

## The channel ready post (this is what Winfred reviews, then posts himself)

Private prices are barely moving while HDB resale quietly softens. This is a buyer's
window if you read it right.

URA private home prices rose just 0.3 percent in 1Q2026, the smallest q on q rise in six
quarters (URA flash estimate, latest available, released 1 Apr 2026).

Within that, mass market held up best: non landed OCR up 1.3 percent, RCR up 0.9 percent,
CCR up 0.4 percent. Landed slipped 1.8 percent q on q (URA, 1Q2026).

HDB resale is cooling in pockets. June 2026 four room median PSF: Jurong West down 17.2
percent and Ang Mo Kio down 12.2 percent month on month, while Toa Payoh held flat at
1,088 psf (HDB resale via data.gov.sg, Jun 2026). Note: single month medians on thin
volume, read the direction, not the decimal.

New launch demand is selective. ELTA in D5 is about 63 percent sold, while Narra
Residences in D23 sits near 27 percent at an indicative 1,930 to 2,292 psf (new launch
tracker, fetched 7 Jun 2026). Buyers are paying up for the right location and pausing
elsewhere.

Rates keep the door open: three month compounded SORA near 1.07 percent and the best two
year fixed around 1.40 percent (PropertyNet, early Jun 2026). Cheap money is doing a lot
of quiet work here.

My read: a flat headline index is not a dull market, it is a sorting market. Capital is
flowing to well located, right sized stock and stepping back from the rest. If you are an
upgrader, soft HDB pockets plus low fixed rates can actually improve your entry maths, the
trick is timing your sale and purchase so you are not exposed in the gap. If you are
holding, this is a cashflow and protection check, not a panic.

If you want to map your own position against these numbers, book a free session and we
will run your capital, cashflow and progression in plain figures.

Winfred Quek | CEA R073319H

---

Sources (for your check, not for the channel):
- URA private price index, 1Q2026 flash estimate, released 1 Apr 2026: overall +0.3% q on q; non landed +1.0% (CCR +0.4%, RCR +0.9%, OCR +1.3%); landed -1.8%. URA media release pr26-26, cross checked against EdgeProp.
- HDB resale four room median PSF, Jun 2026, via data.gov.sg dataset d_8b84c4ee58e3cfc0ece0d773c8ca6abc: Jurong West 444 (was 537, -17.2%), Ang Mo Kio 548 (was 624, -12.2%), Toa Payoh 1,088 (was 1,084, +0.3%). 100 txns sampled per town.
- New launch take up, snapshot fetched 7 Jun 2026: ELTA D5 ~63% sold; Narra Residences D23 ~27% sold, indicative 1,930 to 2,292 psf.
- Rates, PropertyNet, last updated 3 Jun 2026: 3M compounded SORA 1.07%; best 2 year fixed 1.40% p.a. (indicative for S$1M loan).

---

## Source verification (done live, 2026-06-16)

| Source | What it gives | How pulled | Status |
|--------|---------------|-----------|--------|
| URA media release `pr26-26` | 1Q2026 flash estimate, region breakdown, release date | WebFetch on ura.gov.sg, cross checked with EdgeProp via WebSearch | VERIFIED LIVE. URA over plain curl timed out, but WebFetch and WebSearch reached it reliably. |
| data.gov.sg `d_8b84c4ee58e3cfc0ece0d773c8ca6abc` (HDB Resale Flat Prices) | Latest month resale by town and flat type | Datastore API, plus precomputed `~/.claude/state/hdb-momentum/momentum.json` (updated 2026-06-14) | VERIFIED LIVE. API returned 2026-06 records (success: true). No key needed. |
| New launch snapshots | Active launches, take up, indicative PSF | Local `~/.claude/state/new-launch-snapshots/` and `~/.claude/state/new-launches/` (latest fetched 2026-06-07) | VERIFIED FROM LOCAL STATE. The upstream portals (EdgeProp, 99.co, PropertyGuru) returned 403 in the snapshot run, so figures come from newlaunchesreview plus prior good snapshots. Top up with a live web check if older than ~10 days. |
| SORA and fixed rate | 3M compounded SORA, best fixed package | WebSearch plus WebFetch on PropertyNet (cross checked with a second rate tracker) | VERIFIED LIVE. Note: the local `~/.claude/state/mortgage-rates.json` has an empty `sora_history`, so the skill pulls the figure live rather than reading that file. |

### Notes on keys and access
- **No API keys required** for any source used in this sample. data.gov.sg is open; URA and the rate trackers are fetched as public web pages.
- **URA over plain curl is unreliable** from this environment (timeouts). The skill therefore uses WebFetch and WebSearch for URA, which work. If a future run needs structured URA data beyond the press release (for example REALIS transaction level data), that would need a **URA API access key** from the URA developer portal, which is not set up. The flash estimate and media releases used here do not need it.
- **New launch portals (EdgeProp, 99.co, PropertyGuru) frequently return 403** to automated fetches. The skill leans on the maintained local snapshots first and only does a light live web top up, which matches how the new-launch-reel skill already handles this.

## How to run it
Say "weekly market digest", "/weekly", or "market update for my channel". The skill pulls
the four sources, drafts the post, saves it to
`~/.claude/state/weekly-market-digest/<ISO week>.md`, and sends it to Winfred for approval.
It is on demand only and is not scheduled. A weekly launchd job is available as an option
if Winfred wants it, and the approval step stays even then.
