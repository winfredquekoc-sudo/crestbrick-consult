// Vercel Function: /api/hot-lead
// Receives second-visit beacons from _visit-tracker.js, logs to a Telegram alert.
// No DB needed; this is a fire-and-forget signal channel.
export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ ok: false, error: 'method' });
  }
  try {
    const body = typeof req.body === 'string' ? JSON.parse(req.body) : (req.body || {});
    const { path, total_visits, first_seen, ua_fragment } = body;
    const days = first_seen ? Math.round((Date.now() - first_seen) / 86400000) : '?';

    const TG_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
    const TG_CHAT = process.env.TELEGRAM_WINFRED_CHAT_ID;
    if (TG_TOKEN && TG_CHAT) {
      const msg = `🔥 Hot lead — second visit\n\nPage: ${path}\nFirst seen: ${days}d ago\nTotal visits across site: ${total_visits}\nUA: ${ua_fragment}`;
      await fetch(`https://api.telegram.org/bot${TG_TOKEN}/sendMessage`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: TG_CHAT, text: msg })
      });
    }
    return res.status(200).json({ ok: true });
  } catch (e) {
    return res.status(200).json({ ok: false, error: String(e).slice(0, 100) });
  }
}
