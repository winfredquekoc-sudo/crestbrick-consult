// Vercel Cron dispatcher — single function, 4 jobs via ?job= param.
// Schedules (UTC → SGT):
//   overnight-new-launch-pov : 0 18 * * *  → 02:00
//   4pillar-daily            : 0 23 * * *  → 07:00
//   daily-property-news      : 30 6 * * *  → 14:30
//   night-brief              : 0 14 * * *  → 22:00

import { cronHandler, claudeCall, webSearchTool, tgWinfred, tgChannel, sgtDate } from './_lib.js';

const SITE = 'https://winfredquek.com';

const PROMPTS = {
  'overnight-new-launch-pov': `You are Winfred Quek, SG property advisor (CEA R073319H), investor-minded.

TASK:
1. Use web_search + web_fetch on ${SITE}/new-launches and adjacent SG launch news.
2. Pick ONE currently-active new launch where you can add genuine investor POV (price-per-psf vs comparable, holding-power math, exit timeline).
3. Output a 200-300 word POV in Winfred's voice. Format:

# <Project name> — Winfred's POV

<paragraph 1: what's on offer + headline pricing>
<paragraph 2: investor lens — psf vs district median, rental yield range, ABSD/SSD considerations>
<paragraph 3: who this is for / who it isn't>

— Winfred · CEA R073319H

Constraints: Specific numbers only. No fluff. If no launches warrant a POV today, output 'NO_LAUNCH_TODAY' on a single line.`,

  '4pillar-daily': `You are Winfred Quek, a Singapore property advisor (CEA R073319H). Your investor-minded brand applies a 4-Pillar Framework to every property decision:
1. Affordability — TDSR/MSR, cash + CPF mechanics
2. Stamp Duty + Structure — BSD/ABSD/SSD optimization, ownership structures
3. Cash Flow + Holding Power — stress test on rates, vacancy, lifecycle
4. Exit Strategy — when and how to get out

TASK: Use web_search to identify the SINGLE most relevant SG property news event from the last 24-48 hours. Sources to check:
- URA press releases (ura.gov.sg/Corporate/Media-Room/Press-Releases)
- HDB news (hdb.gov.sg)
- MAS announcements (mas.gov.sg/news)
- SG Business Times property section
- The Straits Times property section
- PropertyGuru editorial

Pick ONE event that matters most to retail SG property investors. Apply the 4-Pillar Framework to it.

OUTPUT a markdown post in Winfred's voice — investor-minded, casual but never sloppy, no fluff, no pitch. Format:

---
# <punchy headline tied to the event>

<2-3 sentence hook explaining what changed and why it matters>

## Pillar 1 — Affordability
<2-4 sentences with specific numbers>

## Pillar 2 — Stamp Duty + Structure
<2-4 sentences>

## Pillar 3 — Cash Flow + Holding Power
<2-4 sentences>

## Pillar 4 — Exit Strategy
<2-4 sentences>

## The investor take
<one sentence>

— Winfred · CEA R073319H
---

Constraints:
- Use only verified data — cite source URL at end
- Specific numbers > generalities
- No 'now is a good time to buy' fluff
- If no significant news, output 'NO_SIGNAL_TODAY' on a single line and stop`,

  'daily-property-news': `You are scanning SG property news for Winfred Quek (CEA R073319H), a private property advisor.

TASK: Use web_search across CNA property, EdgeProp, Straits Times property, URA announcements, HDB news, MAS releases. Identify the TOP 3 most actionable items from the last 24h for a retail SG property investor.

Format each item as:
*<short title>*
<1-2 sentence what + why it matters>
Source: <url>

If fewer than 3 newsworthy items, return only what's actionable. If nothing newsworthy, output 'NO_SIGNAL_TODAY' on a single line.

Tone: terse, investor-minded, no fluff, no emojis besides what's already in this prompt.`,

  'night-brief': `You are Winfred Quek's evening market assistant. Winfred is a Singapore property advisor (CEA R073319H).

It's 22:00 SGT. Run a quick end-of-day scan and produce a night brief.

Use web_search to check:
1. Any noteworthy SG property developments today (URA, HDB, MAS, EdgeProp, CNA, Straits Times property)
2. Known calendar items for tomorrow — new launch previews, ballot dates, government announcements, BTO exercise dates
3. Macro signals relevant to SG property buyers — SORA, fixed deposit rates, USD/SGD, SG equities close

OUTPUT (tight — phone read before bed):

🌙 NIGHT BRIEF — <date>

MARKET WRAP
• <1-2 bullets on today's significant developments, or "Quiet day.">

TOMORROW WATCH
• <up to 3 bullets — specific events or data drops to follow>

MACRO SIGNAL
• <1 bullet — rates/currency/equity signal if relevant>

Constraints:
- Specific names and numbers only. No generalities.
- Omit any section if there is nothing real to say.
- If nothing significant across all sections, output "Nothing material today." and stop.`,
};

async function runJob(job) {
  const today = sgtDate();
  const prompt = PROMPTS[job];
  if (!prompt) throw new Error(`Unknown job: ${job}`);

  const { text, usage } = await claudeCall({
    prompt,
    model: 'claude-sonnet-4-6',
    maxTokens: job === '4pillar-daily' ? 4096 : job === 'overnight-new-launch-pov' ? 3072 : 2048,
    tools: [webSearchTool],
  });

  if (!text || text.trim() === '') return { skipped: 'empty_output', date: today, usage };

  const firstLine = text.split('\n')[0].trim();
  if (firstLine === 'NO_SIGNAL_TODAY' || firstLine === 'NO_LAUNCH_TODAY' || firstLine === 'Nothing material today.') {
    return { skipped: 'no_signal', date: today, usage };
  }

  const labels = {
    'overnight-new-launch-pov': '🏗 New Launch POV',
    '4pillar-daily':            '📋 4-Pillar Daily',
    'daily-property-news':      '📰 SG Property',
    'night-brief':              '🌙 Night Brief',
  };
  const msg = `${labels[job]} — ${today}\n\n${text.slice(0, 4000)}`;

  await tgWinfred(msg);
  if (job === 'daily-property-news') await tgChannel(msg);

  return { job, date: today, bytes: text.length, usage };
}

export default cronHandler(async (req) => {
  const job = (req.query?.job || new URL(req.url, 'https://x').searchParams.get('job') || '').trim();
  return runJob(job);
});

export const config = { maxDuration: 300 };
