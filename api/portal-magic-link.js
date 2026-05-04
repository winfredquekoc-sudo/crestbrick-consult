// Vercel Function: /api/portal-magic-link
// POST { email } → emails a signed magic-link token to that address.
// Token is a JWT-style HMAC of {email, exp} signed with PORTAL_SECRET.
// On click, the link lands on /portal?token=... which calls /api/portal-data.
import crypto from 'crypto';

function sign(payload, secret) {
  const body = Buffer.from(JSON.stringify(payload)).toString('base64url');
  const sig = crypto.createHmac('sha256', secret).update(body).digest('base64url');
  return `${body}.${sig}`;
}

export default async function handler(req, res) {
  if (req.method !== 'POST') return res.status(405).json({ ok: false });
  const { email } = (typeof req.body === 'string' ? JSON.parse(req.body) : (req.body || {}));
  if (!email || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    return res.status(400).json({ ok: false, error: 'invalid_email' });
  }

  const secret = process.env.PORTAL_SECRET;
  if (!secret) return res.status(500).json({ ok: false, error: 'not_configured' });

  // Token expires in 30 days
  const exp = Math.floor(Date.now() / 1000) + 30 * 86400;
  const token = sign({ email: email.toLowerCase(), exp }, secret);
  const link = `https://winfredquek.com/portal?token=${encodeURIComponent(token)}`;

  // Send via Resend (or fallback to plain SMTP)
  const RESEND_KEY = process.env.RESEND_API_KEY;
  if (RESEND_KEY) {
    try {
      await fetch('https://api.resend.com/emails', {
        method: 'POST',
        headers: { Authorization: `Bearer ${RESEND_KEY}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          from: 'Winfred <winfred@winfredquek.com>',
          to: [email],
          subject: 'Your Crestbrick portal link',
          html: `<p>Hi,</p><p>Click here to access your Crestbrick portal — valid for 30 days:</p><p><a href="${link}">${link}</a></p><p>If you didn't request this, ignore.</p><p>— Winfred</p>`
        })
      });
      return res.status(200).json({ ok: true });
    } catch (e) {
      return res.status(500).json({ ok: false, error: String(e).slice(0, 100) });
    }
  }
  return res.status(503).json({ ok: false, error: 'mailer_unavailable' });
}
