// Vercel Cron: GET /api/cron/daily-property-news
// Schedule: 30 6 * * *  (UTC) = 14:30 SGT daily
// Scans SG property sources, picks top 3 actionable items, sends to Winfred + channel.

import { cronHandler, claudeCall, webSearchTool, tgWinfred, tgChannel, sgtDate } from './_lib.js';

const PROMPT = `You are scanning SG property news for Winfred Quek (CEA R073319H), a private property advisor.

TASK: Use web_search across CNA property, EdgeProp, Straits Times property, URA announcements, HDB news, MAS releases. Identify the TOP 3 most actionable items from the last 24h for a retail SG property investor.

Format each item as:
*<short title>*
<1-2 sentence what + why it matters>
Source: <url>

If fewer than 3 newsworthy items, return only what's actionable. If nothing newsworthy, output 'NO_SIGNAL_TODAY' on a single line.

Tone: terse, investor-minded, no fluff, no emojis besides what's already in this prompt.`;

export default cronHandler(async () => {
  const today = sgtDate();
  const { text, usage } = await claudeCall({
    prompt: PROMPT,
    model: 'claude-sonnet-4-6',
    maxTokens: 2048,
    tools: [webSearchTool],
  });

  if (!text || text.trim() === '' || text.split('\n')[0].trim() === 'NO_SIGNAL_TODAY') {
    return { skipped: 'no_signal', date: today, usage };
  }

  const msg = `📰 SG Property — ${today}\n\n${text}`;
  const winfred = await tgWinfred(msg);
  const channel = await tgChannel(msg);

  return { date: today, winfred: winfred.ok, channel: channel.ok, usage };
});

export const config = { maxDuration: 300 };
