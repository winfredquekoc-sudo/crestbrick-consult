// Vercel Function: POST /api/audit-email
// Accepts {full_name, email, pdf_base64, summary_text, prep_notes} from /audit.
// Sends email from Winfred's Gmail via SMTP (App Password) with the PDF attached.
// BCCs Winfred's inbox. Falls back gracefully if Gmail creds not configured.
// Also pings Telegram with outcome.

import nodemailer from 'nodemailer';

export default async function handler(req, res) {
  if (req.method !== 'POST') return res.status(405).json({ ok: false, error: 'method_not_allowed' });

  let body = req.body;
  if (typeof body === 'string') {
    try { body = JSON.parse(body); } catch (_) { body = {}; }
  }
  body = body || {};

  const name = (body.full_name || '').trim();
  const email = (body.email || '').trim();
  const pdfBase64 = body.pdf_base64;
  const notes = Array.isArray(body.prep_notes) ? body.prep_notes : [];

  if (!email || !name) return res.status(400).json({ ok: false, error: 'missing_fields' });

  const GMAIL_USER = process.env.GMAIL_USER;
  const GMAIL_PASS = process.env.GMAIL_APP_PASSWORD;

  let emailed = false;
  let errMsg = '';

  if (GMAIL_USER && GMAIL_PASS) {
    try {
      const transporter = nodemailer.createTransport({
        host: 'smtp.gmail.com',
        port: 465,
        secure: true,
        auth: { user: GMAIL_USER, pass: GMAIL_PASS }
      });

      const html = buildEmailHTML(name, notes);
      // No BCC — when you send from your own Gmail, the Sent folder IS your copy.
      const mailOptions = {
        from: `Winfred Quek <${GMAIL_USER}>`,
        to: email,
        subject: `${name}, your Portfolio Strategy Audit is ready`,
        html,
        replyTo: GMAIL_USER
      };
      if (pdfBase64 && pdfBase64.length > 100) {
        mailOptions.attachments = [{
          filename: `Portfolio-Audit-${name.replace(/\s+/g, '-')}.pdf`,
          content: Buffer.from(pdfBase64, 'base64'),
          contentType: 'application/pdf'
        }];
      }
      await transporter.sendMail(mailOptions);
      emailed = true;
    } catch (err) {
      errMsg = (err && err.message) || String(err);
      emailed = false;
    }
  }

  // Telegram heads-up (non-blocking)
  const tgToken = process.env.TELEGRAM_BOT_TOKEN;
  const tgChat = process.env.TELEGRAM_WINFRED_CHAT_ID;
  if (tgToken && tgChat) {
    try {
      const tgText = emailed
        ? `📄 Audit PDF emailed to ${name} (${email}) from your Gmail. Sent folder has the record.`
        : `📄 Audit PDF generated for ${name} (${email})\n⚠️ Email NOT sent${errMsg ? ': ' + errMsg.slice(0, 200) : ' (creds missing)'}. Client has the download.`;
      await fetch(`https://api.telegram.org/bot${tgToken}/sendMessage`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: tgChat, text: tgText })
      });
    } catch (err) { /* ignore */ }
  }

  return res.status(200).json({ ok: true, emailed, ...(errMsg ? { error_preview: errMsg.slice(0, 120) } : {}) });
}

function buildEmailHTML(name, notes) {
  const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));
  const notesHtml = (notes || []).slice(0, 5).map(n => `<li style="margin-bottom:8px;">${esc(n)}</li>`).join('');
  return `<!doctype html>
<html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:#1a1a1a;line-height:1.6;max-width:560px;margin:0 auto;padding:24px;background:#faf8f4;">
  <h1 style="font-family:Georgia,serif;font-size:24px;margin:0 0 14px;color:#1a1a1a;">Hi ${esc(name)},</h1>
  <p>Thanks for completing the 4-Pillar Portfolio Strategy Audit.</p>
  <p>Your full audit summary is attached as a PDF — I personally review every submission before our consultation, so we'll skip the basic "tell me about yourself" and start with strategy.</p>
  ${notesHtml ? `
  <h3 style="font-family:Georgia,serif;font-size:16px;margin:22px 0 8px;color:#1a1a1a;">What I'm already thinking about for your case:</h3>
  <ul style="padding-left:20px;color:#4a4a4a;">${notesHtml}</ul>
  ` : ''}
  <p style="margin-top:22px;">Ready to lock in your strategy session?<br/>
  → <a href="https://calendly.com/winfredquekoc" style="color:#8b6f47;font-weight:600;">calendly.com/winfredquekoc</a>
  </p>
  <p>Or reply directly to this email — I read every message personally.</p>
  <p style="margin-top:28px;">— Winfred Quek<br/>
  <span style="color:#7a7a7a;font-size:13px;">CEA R073319H · Crestbrick, Singapore<br/>
  <a href="https://winfredquek.com" style="color:#7a7a7a;text-decoration:none;">winfredquek.com</a> · WhatsApp +65 8161 8149</span></p>
</body></html>`;
}
