// Vercel Cron: GET /api/cron/overnight-new-launch-pov
// Schedule: 0 18 * * *  (UTC) = 02:00 SGT daily
// Picks one currently-live SG new launch and writes Winfred's investor POV on it.
// Runs overnight so the take is ready for the morning brief.

import { cronHandler, claudeCall, webSearchTool, tgWinfred, sgtDate } from './_lib.js';

const SITE = 'https://winfredquek.com';

const PROMPT = `You are Winfred Quek, SG property advisor (CEA R073319H), investor-minded.

TASK:
1. Use web_search + web_fetch on ${SITE}/new-launches and adjacent SG launch news.
2. Pick ONE currently-active new launch where you can add genuine investor POV (price-per-psf vs comparable, holding-power math, exit timeline).
3. Output a 200-300 word POV in Winfred's voice. Format:

# <Project name> — Winfred's POV

<paragraph 1: what's on offer + headline pricing>
<paragraph 2: investor lens — psf vs district median, rental yield range, ABSD/SSD considerations>
<paragraph 3: who this is for / who it isn't>

— Winfred · CEA R073319H

Constraints: Specific numbers only. No fluff. If no launches warrant a POV today, output 'NO_LAUNCH_TODAY' on a single line.`;

export default cronHandler(async () => {
  const today = sgtDate();
  const { text, usage } = await claudeCall({
    prompt: PROMPT,
    model: 'claude-sonnet-4-6',
    maxTokens: 3072,
    tools: [webSearchTool],
  });

  if (!text || text.trim() === '' || text.split('\n')[0].trim() === 'NO_LAUNCH_TODAY') {
    return { skipped: 'no_launch', date: today, usage };
  }

  await tgWinfred(`🏗 New Launch POV — ${today}\n\n${text.slice(0, 4000)}`);
  return { date: today, bytes: text.length, usage };
});

export const config = { maxDuration: 300 };
