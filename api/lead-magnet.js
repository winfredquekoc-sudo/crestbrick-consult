// /api/lead-magnet — captures email + sends gated PDF/eBook via Resend.
// Used by exit-intent overlay, /resources page, tool email-gates.
// POST { email, magnet, source } → sends email with attachment + logs subscriber.
import crypto from 'crypto';

const MAGNETS = {
  '4-pillar-ebook': { title: 'The 4-Pillar Framework', file: '4-pillar-ebook.pdf' },
  'foreign-buyer-guide': { title: 'Foreign Buyer Survival Guide', file: 'foreign-buyer-guide.pdf' },
  'newsletter': { title: 'Newsletter subscription', file: null }
};

export default async function handler(req, res) {
  if (req.method === 'GET') {
    // Direct GET via /api/lead-magnet?magnet=X — render simple capture form
    const magnet = req.query.magnet || '4-pillar-ebook';
    const m = MAGNETS[magnet] || MAGNETS['4-pillar-ebook'];
    return res.setHeader('Content-Type', 'text/html').status(200).send(`
<!DOCTYPE html><html><head><meta charset="UTF-8"><title>${m.title} · Winfred Quek</title>
<style>body{font-family:-apple-system,system-ui,sans-serif;background:#0e0c08;color:#f0e9d8;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;}.box{background:#1a1610;border:1px solid rgba(180,140,80,0.3);padding:32px;border-radius:12px;max-width:420px;width:100%;text-align:center;}h1{font-family:Fraunces,serif;color:#b48c50;font-weight:400;margin-bottom:14px;}p{color:#a89980;margin-bottom:20px;line-height:1.6;}input{width:100%;padding:12px;background:#0e0c08;border:1px solid rgba(180,140,80,0.3);border-radius:6px;color:#f0e9d8;margin-bottom:12px;}button{background:#b48c50;color:#0e0c08;padding:12px;border-radius:8px;border:none;cursor:pointer;font-weight:500;width:100%;font-size:15px;}</style></head>
<body><div class="box"><h1>${m.title}</h1><p>Drop your email — I'll send it now.</p>
<form id="f"><input id="e" type="email" placeholder="your@email.com" required/>
<button type="submit">Send me the PDF</button></form>
<div id="msg" style="margin-top:14px;color:#7a6c54;font-size:13px;"></div></div>
<script>document.getElementById('f').onsubmit=async(e)=>{e.preventDefault();const email=document.getElementById('e').value;const msg=document.getElementById('msg');msg.textContent='Sending...';const r=await fetch('/api/lead-magnet',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email,magnet:'${magnet}',source:'direct'})});const d=await r.json();msg.textContent=d.ok?'✓ Check your inbox':'⚠ '+(d.error||'try again');};</script>
</body></html>`);
  }
  if (req.method !== 'POST') return res.status(405).json({ ok: false });

  const { email, magnet, source } = (typeof req.body === 'string' ? JSON.parse(req.body) : (req.body || {}));
  if (!email || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    return res.status(400).json({ ok: false, error: 'invalid_email' });
  }
  const m = MAGNETS[magnet] || MAGNETS['4-pillar-ebook'];
  const RESEND = process.env.RESEND_API_KEY;
  if (!RESEND) return res.status(503).json({ ok: false, error: 'mailer_not_configured' });

  // Send email (attachment served from public/ebooks/ if not 'newsletter')
  let html = '';
  if (magnet === 'newsletter') {
    html = `<p>Hi,</p><p>You're subscribed to the Winfred Quek SG property newsletter. Daily, investor-minded, no fluff.</p><p>First issue lands tomorrow morning.</p><p>— Winfred</p>`;
  } else {
    const url = `https://winfredquek.com/ebooks/${m.file}`;
    html = `<p>Hi,</p><p>Your free copy of <b>${m.title}</b>:</p><p><a href="${url}">Download PDF →</a></p><p>If you find it useful, the way I work with paying clients is at <a href="https://winfredquek.com/services/portfolio-audit">winfredquek.com/services/portfolio-audit</a>.</p><p>— Winfred</p><p style="font-size:11px;color:#999;">You requested this via ${source || 'winfredquek.com'}. Reply STOP to unsubscribe.</p>`;
  }
  try {
    await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: { Authorization: `Bearer ${RESEND}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        from: 'Winfred Quek <winfred@winfredquek.com>',
        to: [email],
        subject: magnet === 'newsletter' ? 'Welcome to the SG property newsletter' : `Your copy of ${m.title}`,
        html
      })
    });
    return res.status(200).json({ ok: true });
  } catch (e) {
    return res.status(500).json({ ok: false, error: String(e).slice(0, 100) });
  }
}
