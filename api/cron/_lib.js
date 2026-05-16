// Shared helpers for Vercel Cron Functions.
// Calls Anthropic Messages API directly + posts to Telegram.
//
// Required env vars (set in Vercel project settings):
//   ANTHROPIC_API_KEY         — Anthropic console key
//   TELEGRAM_BOT_TOKEN        — existing bot token
//   TELEGRAM_WINFRED_CHAT_ID  — Winfred's private chat
//   TELEGRAM_CHANNEL_ID       — public channel (optional; some jobs broadcast)
//   CRON_SECRET               — Vercel-injected; verifies request came from cron
//
// Pure-fetch — no SDK — keeps bundle tiny and cold-start fast.

const ANTHROPIC_URL = 'https://api.anthropic.com/v1/messages';
const TELEGRAM_BASE = 'https://api.telegram.org';

export function isAuthorizedCron(req) {
  const expected = process.env.CRON_SECRET;
  if (!expected) return true; // dev/preview: skip check
  const got = req.headers['authorization'] || req.headers['Authorization'];
  return got === `Bearer ${expected}`;
}

export async function claudeCall({ prompt, model = 'claude-sonnet-4-6', maxTokens = 4096, system = null, tools = null }) {
  const key = process.env.ANTHROPIC_API_KEY;
  if (!key) throw new Error('ANTHROPIC_API_KEY missing');

  const body = {
    model,
    max_tokens: maxTokens,
    messages: [{ role: 'user', content: prompt }],
  };
  if (system) body.system = system;
  if (tools) body.tools = tools;

  const res = await fetch(ANTHROPIC_URL, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'x-api-key': key,
      'anthropic-version': '2023-06-01',
    },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const t = await res.text();
    throw new Error(`anthropic ${res.status}: ${t.slice(0, 400)}`);
  }
  const j = await res.json();
  const text = (j.content || [])
    .filter((b) => b.type === 'text')
    .map((b) => b.text)
    .join('\n');
  return { text, usage: j.usage, raw: j };
}

export const webSearchTool = {
  type: 'web_search_20250305',
  name: 'web_search',
  max_uses: 5,
};

export async function tg(chatIdEnv, text, opts = {}) {
  const token = process.env.TELEGRAM_BOT_TOKEN;
  const chatId = process.env[chatIdEnv];
  if (!token || !chatId) {
    return { ok: false, skipped: true, reason: `missing ${!token ? 'TELEGRAM_BOT_TOKEN' : chatIdEnv}` };
  }
  const params = new URLSearchParams({ chat_id: chatId, text });
  if (opts.parse_mode) params.set('parse_mode', opts.parse_mode);

  const res = await fetch(`${TELEGRAM_BASE}/bot${token}/sendMessage`, {
    method: 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded' },
    body: params.toString(),
  });
  const j = await res.json().catch(() => ({}));
  return { ok: res.ok && j.ok === true, response: j };
}

export const tgWinfred = (text, opts) => tg('TELEGRAM_WINFRED_CHAT_ID', text, opts);
export const tgChannel = (text, opts) => tg('TELEGRAM_CHANNEL_ID', text, opts);

export function sgtDate() {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Singapore',
    year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(new Date());
}

export function cronHandler(fn) {
  return async (req, res) => {
    if (!isAuthorizedCron(req)) {
      return res.status(401).json({ ok: false, error: 'unauthorized' });
    }
    const start = Date.now();
    try {
      const result = await fn(req);
      return res.status(200).json({ ok: true, ms: Date.now() - start, ...result });
    } catch (err) {
      try { await tgWinfred(`[cron-fail] ${req.url}\n${(err.message || err).toString().slice(0, 400)}`); } catch (_) {}
      return res.status(500).json({ ok: false, error: (err.message || String(err)).slice(0, 400), ms: Date.now() - start });
    }
  };
}
