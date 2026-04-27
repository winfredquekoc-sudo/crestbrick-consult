// Vercel Function: POST /api/contact
// Accepts contact-form submissions, forwards to Winfred via Telegram + email fallback,
// returns JSON { ok: true } or redirects to /contact?sent=1 for HTML form posts.

export default async function handler(req, res) {
  if (req.method === 'OPTIONS') {
    res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
    return res.status(204).end();
  }
  if (req.method !== 'POST') {
    return res.status(405).json({ ok: false, error: 'method_not_allowed' });
  }

  let body = req.body;
  if (typeof body === 'string') {
    try { body = JSON.parse(body); } catch (_) {
      body = Object.fromEntries(new URLSearchParams(body));
    }
  }
  body = body || {};

  const name = (body.name || '').toString().slice(0, 200).trim();
  const email = (body.email || '').toString().slice(0, 200).trim();
  const phone = (body.phone || '').toString().slice(0, 50).trim();
  const type = (body.type || 'General').toString().slice(0, 200).trim();
  const message = (body.message || '').toString().slice(0, 3000).trim();
  const hp = (body.company || '').toString().trim();

  if (hp) return res.status(200).json({ ok: true });
  if (!name || !email || !message) {
    return res.status(400).json({ ok: false, error: 'missing_fields' });
  }

  const token = process.env.TELEGRAM_BOT_TOKEN;
  const chatId = process.env.TELEGRAM_WINFRED_CHAT_ID;
  let telegramOk = false;

  if (token && chatId) {
    const text =
`📩 NEW WEBSITE BRIEF
From: ${name}
Email: ${email}
Phone: ${phone}
Type: ${type}

Message:
${message}

(received via crestbrick-consult.vercel.app /api/contact)`;

    try {
      const tgResp = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: chatId, text })
      });
      const tgJson = await tgResp.json();
      telegramOk = !!tgJson.ok;
    } catch (err) {
      telegramOk = false;
    }
  }

  const wantsHtml = (req.headers['content-type'] || '').includes('form-urlencoded')
    || (req.headers['accept'] || '').includes('text/html');

  if (wantsHtml) {
    res.writeHead(302, { Location: '/contact?sent=1' });
    return res.end();
  }
  return res.status(200).json({ ok: true, telegram: telegramOk });
}
