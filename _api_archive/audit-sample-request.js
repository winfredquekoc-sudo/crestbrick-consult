// /api/audit-sample-request — handles I84 lead-magnet form submissions.
// Captures email + situation, fires email + Telegram notification.

import nodemailer from 'nodemailer';

export const config = { runtime: 'nodejs' };

export default async function handler(req, res) {
  if (req.method !== 'POST') return res.status(405).json({ error: 'method_not_allowed' });

  let body = req.body;
  if (typeof body === 'string') {
    try { body = JSON.parse(body); }
    catch { body = Object.fromEntries(new URLSearchParams(body)); }
  }

  const email = String(body?.email || '').trim().toLowerCase();
  const situation = String(body?.situation || 'unknown').slice(0, 64);
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    return res.status(400).json({ error: 'invalid_email' });
  }

  // Notify Winfred via Telegram (best-effort)
  if (process.env.TELEGRAM_BOT_TOKEN && process.env.TELEGRAM_WINFRED_CHAT_ID) {
    fetch(`https://api.telegram.org/bot${process.env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        chat_id: process.env.TELEGRAM_WINFRED_CHAT_ID,
        text: `📩 *Audit-sample requested*\nemail: ${email}\nsituation: ${situation}`,
        parse_mode: 'Markdown',
      }),
    }).catch(() => {});
  }

  // Send the PDF (or a link to it) via email
  const pdfUrl = process.env.AUDIT_SAMPLE_PDF_URL
    || 'https://winfredquek.com/assets/sample-audit.pdf';

  if (process.env.SMTP_HOST && process.env.SMTP_USER && process.env.SMTP_PASS) {
    try {
      const transporter = nodemailer.createTransport({
        host: process.env.SMTP_HOST,
        port: Number(process.env.SMTP_PORT || 587),
        secure: false,
        auth: { user: process.env.SMTP_USER, pass: process.env.SMTP_PASS },
      });
      await transporter.sendMail({
        from: process.env.SMTP_FROM || '"Winfred Quek" <hello@winfredquek.com>',
        to: email,
        subject: 'Your 4-Pillar Audit Sample (PDF)',
        text: `Thanks for asking — sample audit attached link below.\n\n${pdfUrl}\n\nI'll follow up in 3 days asking what you thought. If you'd rather skip the follow-up, just reply STOP.\n\n— Winfred`,
        html: `<p>Thanks for asking — here's the sample.</p><p><a href="${pdfUrl}">Download the 4-Pillar Audit Sample (PDF)</a></p><p>I'll follow up in 3 days asking what you thought. Reply STOP to opt out.</p><p>— Winfred</p>`,
      });
    } catch (e) {
      // Don't reveal SMTP errors to the client
    }
  }

  return res.status(200).json({ ok: true });
}
