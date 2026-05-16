// Vercel Cron: GET /api/cron/night-brief
// Schedule: 0 14 * * *  (UTC) = 22:00 SGT daily
// EOD wrap — today's SG property market moves, tomorrow's watchlist, macro signal.
// Short phone-read format delivered to Winfred's Telegram before bed.

import { cronHandler, claudeCall, webSearchTool, tgWinfred, sgtDate } from './_lib.js';

const PROMPT = `You are Winfred Quek's evening market assistant. Winfred is a Singapore property advisor (CEA R073319H).

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
- If nothing significant across all sections, output "Nothing material today." and stop.`;

export default cronHandler(async () => {
  const today = sgtDate();
  const { text, usage } = await claudeCall({
    prompt: PROMPT,
    model: 'claude-sonnet-4-6',
    maxTokens: 1024,
    tools: [webSearchTool],
  });

  if (!text || text.trim() === '') {
    return { skipped: 'empty_output', date: today };
  }

  await tgWinfred(`🌙 Night Brief — ${today}\n\n${text.slice(0, 4000)}`);
  return { date: today, bytes: text.length, usage };
});

export const config = { maxDuration: 300 };
