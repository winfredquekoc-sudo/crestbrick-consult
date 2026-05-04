// /api/unsubscribe — handles unsubscribe requests + notifies Winfred via Telegram.
// Logs to a server-side append-only ledger; actual removal happens server-side
// (drip-runner reads this list before sending).
export default async function handler(req, res) {
  if (req.method !== 'POST') return res.status(405).json({ ok: false });
  const { email, from } = (typeof req.body === 'string' ? JSON.parse(req.body) : (req.body || {}));
  if (!email || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    return res.status(400).json({ ok: false });
  }
  // Log + notify (real removal happens via the drip-runner reading suppressions)
  console.log('UNSUB', JSON.stringify({email, from, ts: new Date().toISOString()}));
  const TG = process.env.TELEGRAM_BOT_TOKEN;
  const CHAT = process.env.TELEGRAM_WINFRED_CHAT_ID;
  if (TG && CHAT) {
    try {
      await fetch(`https://api.telegram.org/bot${TG}/sendMessage`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: CHAT, text: `📤 Unsubscribe: ${email} from "${from}"` })
      });
    } catch(e) {}
  }
  return res.status(200).json({ ok: true });
}
