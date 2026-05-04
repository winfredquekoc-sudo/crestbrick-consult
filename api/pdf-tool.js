// /api/pdf-tool — generates a PDF version of a calculator result + emails it (#288).
// POST { tool, inputs, results, email } → sends PDF via Resend.
// PDF generated as plain HTML email (Resend renders it well as PDF-printable).
// Could later swap to Puppeteer for true PDF generation.
export default async function handler(req, res) {
  if (req.method !== 'POST') return res.status(405).json({ ok: false });
  const { tool, inputs, results, email } = (typeof req.body === 'string' ? JSON.parse(req.body) : (req.body || {}));
  if (!email || !tool) return res.status(400).json({ ok: false, error: 'missing_fields' });

  const RESEND = process.env.RESEND_API_KEY;
  if (!RESEND) return res.status(503).json({ ok: false, error: 'mailer_not_configured' });

  // Build HTML email with the inputs + results formatted for print
  const inputRows = Object.entries(inputs || {}).map(([k,v]) => `<tr><td style="padding:6px 12px;color:#666;">${k}</td><td style="padding:6px 12px;font-weight:600;">${v}</td></tr>`).join('');
  const resultRows = Object.entries(results || {}).map(([k,v]) => `<tr><td style="padding:6px 12px;color:#666;">${k}</td><td style="padding:6px 12px;font-weight:600;color:#b48c50;">${v}</td></tr>`).join('');

  const html = `
<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="font-family:-apple-system,system-ui,sans-serif;color:#333;max-width:600px;margin:0 auto;padding:24px;">
<h1 style="font-family:Georgia,serif;color:#b48c50;border-bottom:2px solid #b48c50;padding-bottom:8px;">${tool} — Your Results</h1>
<p>Here's a record of the calculation you ran on winfredquek.com:</p>
<h3 style="color:#666;font-size:13px;letter-spacing:0.1em;text-transform:uppercase;">Inputs</h3>
<table style="width:100%;border-collapse:collapse;border:1px solid #eee;">${inputRows}</table>
<h3 style="color:#666;font-size:13px;letter-spacing:0.1em;text-transform:uppercase;margin-top:24px;">Results</h3>
<table style="width:100%;border-collapse:collapse;border:1px solid #eee;">${resultRows}</table>
<p style="margin-top:32px;padding:16px;background:#faf6ee;border-left:4px solid #b48c50;font-size:14px;">
This is an indicative model. Your actual numbers will differ based on your specific situation. If you'd like to walk through your real numbers with me, reply to this email or WhatsApp +6581618149.
</p>
<p style="margin-top:24px;font-size:12px;color:#999;">— Winfred Quek · CEA R073319H · Crestbrick Pte Ltd</p>
</body></html>`;

  try {
    await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: { Authorization: `Bearer ${RESEND}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        from: 'Winfred Quek <winfred@winfredquek.com>',
        to: [email],
        subject: `Your ${tool} results`,
        html
      })
    });
    return res.status(200).json({ ok: true });
  } catch (e) {
    return res.status(500).json({ ok: false, error: String(e).slice(0, 100) });
  }
}
