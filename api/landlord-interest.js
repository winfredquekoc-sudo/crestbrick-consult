// Vercel Function: POST /api/landlord-interest
// Accepts landlord form submissions from /landlord page, forwards to Winfred via Telegram,
// returns JSON { ok: true } or { ok: false, error: '...' }

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

  const property_type = (body.property_type || '').toString().slice(0, 100).trim();
  const district = (body.district || '').toString().slice(0, 100).trim();
  const asking_rent = (body.asking_rent || '').toString().slice(0, 50).trim();
  const available_date = (body.available_date || '').toString().slice(0, 50).trim();
  const whatsapp = (body.whatsapp || '').toString().slice(0, 50).trim();
  const hp = (body.company || '').toString().trim();

  // Honeypot: if company field is filled, it's a bot
  if (hp) return res.status(200).json({ ok: true });

  // Validate required fields
  if (!property_type || !district || !asking_rent || !available_date || !whatsapp) {
    return res.status(400).json({ ok: false, error: 'missing_fields' });
  }

  const token = process.env.TELEGRAM_BOT_TOKEN;
  const chatId = process.env.TELEGRAM_WINFRED_CHAT_ID;
  let telegramOk = false;

  const text =
`🏠 NEW LANDLORD INTEREST
Property type: ${property_type}
District: ${district}
Asking rent: SGD ${asking_rent}/month
Available from: ${available_date}
WhatsApp: ${whatsapp}

(received via winfredquek.com /api/landlord-interest)`;

  if (token && chatId) {
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

  // Email fallback so a dead Telegram token can never silently swallow a lead
  let emailOk = false;
  if (!telegramOk && process.env.RESEND_API_KEY) {
    try {
      const r = await fetch('https://api.resend.com/emails', {
        method: 'POST',
        headers: { Authorization: `Bearer ${process.env.RESEND_API_KEY}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          from: 'Winfred Quek <winfred@winfredquek.com>',
          to: ['winfredquekoc@gmail.com'],
          reply_to: whatsapp,
          subject: `Landlord interest: ${property_type} in ${district} (${asking_rent}/mo)`,
          text,
        }),
      });
      emailOk = r.ok;
    } catch (err) {
      emailOk = false;
    }
  }

  const delivered = telegramOk || emailOk;
  const wantsHtml = (req.headers['content-type'] || '').includes('form-urlencoded')
    || (req.headers['accept'] || '').includes('text/html');

  if (wantsHtml) {
    res.writeHead(302, { Location: '/landlord?sent=1' });
    return res.end();
  }
  if (!delivered) {
    return res.status(500).json({ ok: false, error: 'delivery_failed' });
  }
  return res.status(200).json({ ok: true, telegram: telegramOk, email: emailOk });
}
