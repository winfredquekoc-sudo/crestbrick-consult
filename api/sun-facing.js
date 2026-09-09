// /api/sun-facing — backend for the Sun Facing Checker page (public/sun-facing-checker.html).
// Delivery cascade per kind (each step is independent + best-effort, mirrors lead-magnet.js):
//   'valuation_click' — someone clicked the "get a valuation" CTA on the results screen.
//     1. Telegram alert to Winfred (fire and forget)
//     2. n8n webhook "sun-facing" (adds a row to the leads sheet)
//     Responds 204 immediately; sent via navigator.sendBeacon so it must never block the page.
//   'summary_email' — someone asked to have their sun exposure summary emailed to them.
//     1. Telegram alert (email + address + first sentence)
//     2. n8n webhook "sun-facing" (full payload)
//     3. Email the summary to them (Resend if RESEND_API_KEY set, else Gmail nodemailer)
//     All three run in parallel and none blocks the response.
// POST only. No property advice is ever generated here — this endpoint only relays and emails
// numbers/sentences the frontend already computed.
import nodemailer from 'nodemailer';

const N8N_SUN_FACING_WEBHOOK =
  process.env.N8N_SUN_FACING_WEBHOOK || 'https://winfredquekoc.app.n8n.cloud/webhook/sun-facing';
const N8N_SUN_FACING_FOLLOWUP_WEBHOOK =
  process.env.N8N_SUN_FACING_FOLLOWUP_WEBHOOK || 'https://winfredquekoc.app.n8n.cloud/webhook/sun-facing-followup';

const MAX_BODY_BYTES = 20 * 1024; // 20 KB cap on summary_email payloads

// Foolproof fetch: hard timeout + one retry, same as lead-magnet.js — a hung n8n
// or Telegram outage must never hold the function open or throw.
async function postJson(url, payload, { timeoutMs = 8000, retries = 1 } = {}) {
  for (let attempt = 0; attempt <= retries; attempt++) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const r = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: ctrl.signal,
      });
      clearTimeout(timer);
      if (r.ok) return true;
    } catch { clearTimeout(timer); }
    if (attempt < retries) await new Promise(r => setTimeout(r, 1500));
  }
  return false;
}

async function notifyTelegram(text) {
  const token = process.env.TELEGRAM_BOT_TOKEN;
  const chat = process.env.TELEGRAM_WINFRED_CHAT_ID;
  if (!token || !chat) return false;
  try {
    const r = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // Plain text, no parse_mode — unescaped Markdown has silently dropped alerts before.
      body: JSON.stringify({ chat_id: chat, text }),
    });
    const j = await r.json();
    return !!j.ok;
  } catch { return false; }
}

function notifyN8n(payload) {
  return postJson(N8N_SUN_FACING_WEBHOOK, payload);
}

// Strip control chars and cap length. Applied to every free text field before it
// reaches Telegram, n8n, or an outbound email.
function clean(v, maxLen = 500) {
  return String(v == null ? '' : v)
    // eslint-disable-next-line no-control-regex
    .replace(/[\x00-\x1F\x7F]/g, ' ')
    .trim()
    .slice(0, maxLen);
}

function isValidEmail(email) {
  return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email);
}

function isFiniteNum(n) {
  return typeof n === 'number' && Number.isFinite(n);
}

const MONTH_ROW_FIELDS = ['month', 'sunrise', 'sunset', 'side', 'westSunHrs', 'shadedHrs'];

function sanitizeMonths(months) {
  if (!Array.isArray(months)) return [];
  return months.slice(0, 12).map(row => {
    const out = {};
    for (const f of MONTH_ROW_FIELDS) {
      const v = row && row[f];
      out[f] = typeof v === 'number' ? v : clean(v, 60);
    }
    return out;
  });
}

function sanitizeSentences(sentences) {
  if (!Array.isArray(sentences)) return [];
  return sentences.slice(0, 20).map(s => clean(s, 400));
}

function monthsToHtmlRows(months) {
  return months.map(m => `<tr>
    <td style="padding:6px 10px;border-bottom:1px solid #e5e0d5;">${clean(m.month, 30)}</td>
    <td style="padding:6px 10px;border-bottom:1px solid #e5e0d5;">${clean(m.sunrise, 20)}</td>
    <td style="padding:6px 10px;border-bottom:1px solid #e5e0d5;">${clean(m.sunset, 20)}</td>
    <td style="padding:6px 10px;border-bottom:1px solid #e5e0d5;">${clean(m.side, 30)}</td>
    <td style="padding:6px 10px;border-bottom:1px solid #e5e0d5;">${typeof m.westSunHrs === 'number' ? m.westSunHrs : clean(m.westSunHrs, 20)}</td>
    <td style="padding:6px 10px;border-bottom:1px solid #e5e0d5;">${typeof m.shadedHrs === 'number' ? m.shadedHrs : clean(m.shadedHrs, 20)}</td>
  </tr>`).join('');
}

function monthsToTextRows(months) {
  return months.map(m =>
    `${clean(m.month, 30)}: sunrise ${clean(m.sunrise, 20)}, sunset ${clean(m.sunset, 20)}, ${clean(m.side, 30)}, west sun ${m.westSunHrs}h, shaded ${m.shadedHrs}h`
  ).join('\n');
}

async function emailSummary(email, address, sentences, months) {
  const subject = `Your sun facing summary for ${address}`;
  const disclaimer = "Shadows use OpenStreetMap building heights; where a height is not recorded a typical block is assumed, so treat this as a strong guide, not a survey.";
  const signOff = `If it is useful to talk it through, message me on WhatsApp (https://wa.me/6581618149) or grab a 30 minute slot on my calendar (https://calendly.com/winfredquekoc).\n\n— Winfred Quek\nhttps://winfredquek.com/sun-facing-checker`;

  const text = [
    `Sun facing summary for ${address}`,
    '',
    ...sentences,
    '',
    monthsToTextRows(months),
    '',
    disclaimer,
    '',
    signOff,
  ].join('\n');

  const html = `
    <p>Sun facing summary for <b>${clean(address, 200)}</b></p>
    ${sentences.map(s => `<p>${s}</p>`).join('')}
    <table style="border-collapse:collapse;font-size:13px;margin:16px 0;">
      <thead><tr>
        <th style="text-align:left;padding:6px 10px;border-bottom:2px solid #b48c50;">Month</th>
        <th style="text-align:left;padding:6px 10px;border-bottom:2px solid #b48c50;">Sunrise</th>
        <th style="text-align:left;padding:6px 10px;border-bottom:2px solid #b48c50;">Sunset</th>
        <th style="text-align:left;padding:6px 10px;border-bottom:2px solid #b48c50;">Side</th>
        <th style="text-align:left;padding:6px 10px;border-bottom:2px solid #b48c50;">West sun (hrs)</th>
        <th style="text-align:left;padding:6px 10px;border-bottom:2px solid #b48c50;">Shaded (hrs)</th>
      </tr></thead>
      <tbody>${monthsToHtmlRows(months)}</tbody>
    </table>
    <p style="font-size:12px;color:#888;">${disclaimer}</p>
    <p>If it is useful to talk it through, message me on <a href="https://wa.me/6581618149">WhatsApp</a> or grab a
    <a href="https://calendly.com/winfredquekoc">30 minute slot</a> on my calendar.</p>
    <p>— Winfred Quek<br/><a href="https://winfredquek.com/sun-facing-checker">winfredquek.com/sun-facing-checker</a></p>
  `;

  // PRIMARY = Resend if configured, FALLBACK = Gmail nodemailer (same order as lead-magnet.js's
  // reliability note reversed is fine either way; both are best-effort here).
  const RESEND = process.env.RESEND_API_KEY;
  if (RESEND) {
    try {
      const r = await fetch('https://api.resend.com/emails', {
        method: 'POST',
        headers: { Authorization: `Bearer ${RESEND}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ from: 'Winfred Quek <winfred@winfredquek.com>', to: [email], subject, html, text }),
      });
      if (r.ok) return true;
    } catch { /* fall through to Gmail */ }
  }
  const user = process.env.GMAIL_USER, pass = process.env.GMAIL_APP_PASSWORD;
  if (user && pass) {
    try {
      const t = nodemailer.createTransport({ service: 'gmail', auth: { user, pass } });
      await t.sendMail({ from: `Winfred Quek <${user}>`, to: email, subject, html, text });
      return true;
    } catch { return false; }
  }
  return false;
}

export default async function handler(req, res) {
  if (req.method !== 'POST') return res.status(405).json({ ok: false, error: 'method_not_allowed' });

  // navigator.sendBeacon delivers the body as text/plain, so req.body may already be a
  // string, a raw Buffer, or (when Vercel did parse it) an object — handle all three.
  let raw = req.body;
  if (Buffer.isBuffer(raw)) raw = raw.toString('utf8');
  if (typeof raw === 'string') {
    if (raw.length > MAX_BODY_BYTES) return res.status(413).json({ ok: false, error: 'too_large' });
    try { raw = JSON.parse(raw); } catch { return res.status(400).json({ ok: false, error: 'bad_json' }); }
  }
  const body = raw && typeof raw === 'object' ? raw : {};

  const kind = clean(body.kind, 40);

  if (kind === 'valuation_click') {
    const address = clean(body.address, 200);
    const lat = isFiniteNum(body.lat) ? body.lat : clean(body.lat, 20);
    const lng = isFiniteNum(body.lng) ? body.lng : clean(body.lng, 20);
    const month = clean(body.month, 30);
    const time = clean(body.time, 20);

    // Best effort, never fails the response — sendBeacon has no way to read errors anyway.
    Promise.all([
      notifyTelegram(`Sun Facing Checker: valuation click for ${address} (${lat}, ${lng}) at ${month} ${time}`),
      notifyN8n({ kind, address, lat, lng, month, time, ts: new Date().toISOString(), ua: clean(req.headers['user-agent'], 300) }),
    ]).catch(() => {});

    res.status(204).end();
    return;
  }

  if (kind === 'summary_email') {
    // Honeypot: silently accept and do nothing further.
    if (clean(body.website, 200)) return res.status(200).json({ ok: true });

    // Size cap applies here too, not just on the raw-string sendBeacon path above —
    // Vercel may have already parsed a JSON-content-type POST into an object by this point.
    if (Buffer.byteLength(JSON.stringify(body), 'utf8') > MAX_BODY_BYTES) {
      return res.status(413).json({ ok: false, error: 'too_large' });
    }

    const email = clean(body.email, 200);
    if (!email || !isValidEmail(email)) {
      return res.status(400).json({ ok: false, error: 'invalid_email' });
    }

    const address = clean(body.address, 200);
    const lat = isFiniteNum(body.lat) ? body.lat : clean(body.lat, 20);
    const lng = isFiniteNum(body.lng) ? body.lng : clean(body.lng, 20);
    const source = clean(body.source, 100);
    const summary = body.summary && typeof body.summary === 'object' ? body.summary : {};
    const months = sanitizeMonths(summary.months);
    const sentences = sanitizeSentences(summary.sentences);
    const ts = new Date().toISOString();
    const ua = clean(req.headers['user-agent'], 300);

    // Kick off the follow up drip, best effort, fire and forget -- must never block the response.
    postJson(N8N_SUN_FACING_FOLLOWUP_WEBHOOK, { email, address, ts }).catch(() => {});

    const [, , emailed] = await Promise.all([
      notifyTelegram(`Sun Facing Checker: summary request from ${email} for ${address}${sentences[0] ? ` — ${sentences[0]}` : ''}`),
      notifyN8n({ kind, email, address, lat, lng, summary: { months, sentences }, source, ts, ua }),
      emailSummary(email, address, sentences, months),
    ]);

    return res.status(200).json({ ok: true, emailed: !!emailed });
  }

  return res.status(400).json({ ok: false, error: 'unknown_kind' });
}
