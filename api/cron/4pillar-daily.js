// Vercel Cron: GET /api/cron/4pillar-daily
// Schedule: 00 23 * * *  (UTC) = 07:00 SGT daily
// Replaces ~/.claude/bin/4pillar-daily.sh
//
// Picks the single most relevant SG property news event from the last 24-48h
// and applies Winfred's 4-Pillar Framework. Output is delivered to Winfred's
// Telegram. Original script also wrote to ~/.claude/state/4pillar-daily/<date>.md
// — that local archive stays on the Mac (separate sync job, optional).

import { cronHandler, claudeCall, webSearchTool, tgWinfred, sgtDate } from './_lib.js';

const PROMPT = `You are Winfred Quek, a Singapore property advisor (CEA R073319H). Your investor-minded brand applies a 4-Pillar Framework to every property decision:
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
- If no significant news, output 'NO_SIGNAL_TODAY' on a single line and stop`;

export default cronHandler(async () => {
  const today = sgtDate();
  const { text, usage } = await claudeCall({
    prompt: PROMPT,
    model: 'claude-sonnet-4-5',
    maxTokens: 4096,
    tools: [webSearchTool],
  });

  if (!text || text.trim() === '') {
    return { skipped: 'empty_output', date: today };
  }
  if (text.split('\n')[0].trim() === 'NO_SIGNAL_TODAY') {
    return { skipped: 'no_signal', date: today, usage };
  }

  const preview = text.slice(0, 800);
  await tgWinfred(`📋 4-Pillar Daily — ${today}\n\n${preview}${text.length > 800 ? '...' : ''}`);

  return { date: today, bytes: text.length, usage };
});

// Vercel Function config: extend duration cap for the LLM call
export const config = { maxDuration: 300 };
