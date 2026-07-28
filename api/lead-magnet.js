// /api/lead-magnet — captures email + delivers eBook.
// Delivery cascade (each step is independent + best-effort):
//   1. Telegram alert to Winfred (always works — TELEGRAM_BOT_TOKEN is set)
//   2. n8n webhook (only if it's responding — adds to the drip list)
//   3. Resend email (only if RESEND_API_KEY is set)
// Always returns the eBook URL in the response so the user can download
// instantly even if no email channel is configured. No more 503.
// POST { email, magnet, source } → JSON { ok: true, ebook_url, title }
import crypto from 'crypto';

// Hardcoded to the live leads->Sheet webhook. (A stale N8N_LEAD_MAGNET_WEBHOOK
// env var was overriding this with a dead URL, so site leads never reached the sheet.)
const N8N_LEAD_MAGNET_WEBHOOK = 'https://winfredquekoc.app.n8n.cloud/webhook/lead-magnet';

// Lentor leads also enter the 10-touch Gmail drip (one workflow per lead).
const N8N_LENTOR_DRIP_WEBHOOK =
  process.env.N8N_LENTOR_DRIP_WEBHOOK ||
  'https://winfredquekoc.app.n8n.cloud/webhook/lentor-funnel';

async function startLentorDrip(payload) {
  try {
    const r = await fetch(N8N_LENTOR_DRIP_WEBHOOK, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    return r.ok;
  } catch { return false; }
}

// eBooks live as HTML pages with a Print/Save-as-PDF button — instant access
// without needing the actual PDF file present.
const MAGNETS = {
  'property-portfolio-blueprint': {
    title: 'The Move Framework',
    url: '/ebooks/the-move-framework',
  },
  '4-pillar-ebook': { // backward-compat
    title: 'The Move Framework',
    url: '/ebooks/the-move-framework',
  },
  'foreign-buyer-guide': {
    title: 'Foreign Buyer Survival Guide',
    url: '/ebooks/foreign-buyer-guide',
  },
  'newsletter': { title: 'Newsletter subscription', url: null },
  'lentor-gardens-guide': {
    title: 'Lentor Gardens Residences: The Investor Case',
    url: '/launches/briefs/lentor-gardens-residences',
  },
};

async function notifyTelegram(lead) {
  const token = process.env.TELEGRAM_BOT_TOKEN;
  const chat = process.env.TELEGRAM_WINFRED_CHAT_ID;
  if (!token || !chat) return false;
  try {
    const esc = s => String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const lines = ['📥 <b>New lead</b>', '', `<b>${esc(lead.magnet_title)}</b>`];
    if (lead.name) lines.push(`Name: ${esc(lead.name)}`);
    lines.push(`<code>${esc(lead.email)}</code>`);
    if (lead.phone) lines.push(`Mobile: ${esc(lead.phone)}`);
    if (lead.intent) lines.push(`Goal: ${esc(lead.intent)}`);
    lines.push(`Source: ${esc(lead.source)}`);
    await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: chat, text: lines.join('\n'), parse_mode: 'HTML' }),
    });
    return true;
  } catch { return false; }
}

async function notifyN8n(payload) {
  try {
    const r = await fetch(N8N_LEAD_MAGNET_WEBHOOK, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    return r.ok;
  } catch { return false; }
}

async function notifyResend(email, m, source) {
  const RESEND = process.env.RESEND_API_KEY;
  if (!RESEND) return false;
  const html = `<p>Hi,</p><p>Your free copy of <b>${m.title}</b>:</p><p><a href="https://winfredquek.com${m.url}">Open eBook →</a></p><p>If you find it useful, the way I work with paying clients is at <a href="https://winfredquek.com/services/property-portfolio-analysis">winfredquek.com/services/property-portfolio-analysis</a>.</p><p>— Winfred</p><p style="font-size:11px;color:#999;">You requested this via ${source}. Reply STOP to unsubscribe.</p>`;
  try {
    const r = await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: { Authorization: `Bearer ${RESEND}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        from: 'Winfred Quek <winfred@winfredquek.com>',
        to: [email],
        subject: `Your copy of ${m.title}`,
        html,
      }),
    });
    return r.ok;
  } catch { return false; }
}

export default async function handler(req, res) {
  if (req.method === 'GET') {
    const magnet = req.query.magnet || 'property-portfolio-blueprint';
    const m = MAGNETS[magnet] || MAGNETS['property-portfolio-blueprint'];
    return res.setHeader('Content-Type', 'text/html').status(200).send(`
<!DOCTYPE html><html><head><meta charset="UTF-8"><title>${m.title} · Winfred Quek</title>
<style>body{font-family:-apple-system,system-ui,sans-serif;background:#0e0c08;color:#f0e9d8;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;}.box{background:#1a1610;border:1px solid rgba(180,140,80,0.3);padding:32px;border-radius:12px;max-width:420px;width:100%;text-align:center;}h1{font-family:Fraunces,serif;color:#b48c50;font-weight:400;margin-bottom:14px;}p{color:#a89980;margin-bottom:20px;line-height:1.6;}input{width:100%;padding:12px;background:#0e0c08;border:1px solid rgba(180,140,80,0.3);border-radius:6px;color:#f0e9d8;margin-bottom:12px;}button{background:#b48c50;color:#0e0c08;padding:12px;border-radius:8px;border:none;cursor:pointer;font-weight:500;width:100%;font-size:15px;}a{color:#b48c50;}</style></head>
<body><div class="box"><h1>${m.title}</h1><p>Drop your email, I'll keep you on the (rare) updates list.</p>
<form id="f"><input id="e" type="email" placeholder="your@email.com" required/>
<button type="submit">Get the eBook</button></form>
<div id="msg" style="margin-top:14px;color:#7a6c54;font-size:13px;"></div></div>
<script>document.getElementById('f').onsubmit=async(e)=>{e.preventDefault();const email=document.getElementById('e').value;const msg=document.getElementById('msg');msg.textContent='Sending...';const r=await fetch('/api/lead-magnet',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email,magnet:'${magnet}',source:'direct'})});const d=await r.json();if(d.ok){msg.innerHTML='✓ Got it. Opening eBook...';setTimeout(()=>{window.location.href=d.ebook_url;},800);}else{msg.textContent='⚠ '+(d.error||'try again');}};</script>
</body></html>`);
  }
  if (req.method !== 'POST') return res.status(405).json({ ok: false });

  const { email, magnet, source, name, phone, intent } = (typeof req.body === 'string' ? JSON.parse(req.body) : (req.body || {}));
  if (!email || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    return res.status(400).json({ ok: false, error: 'invalid_email' });
  }
  const m = MAGNETS[magnet] || MAGNETS['property-portfolio-blueprint'];
  const src = source || 'resources_page';

  // Fan out — none of these block on each other failing.
  const [tg, n8n, resend, drip] = await Promise.all([
    notifyTelegram({ email, name, phone, intent, magnet_title: m.title, source: src }),
    notifyN8n({
      email,
      name: name || null,
      phone: phone || null,
      intent: intent || null,
      magnet,
      magnet_title: m.title,
      magnet_url: m.url ? `https://winfredquek.com${m.url}` : null,
      source: src,
      ts: new Date().toISOString(),
    }),
    notifyResend(email, m, src),
    magnet === 'lentor-gardens-guide'
      ? startLentorDrip({ email, name: name || '', phone: phone || '', intent: intent || '', magnet, source: src, ts: new Date().toISOString() })
      : Promise.resolve(false),
  ]);

  // Always succeed. The eBook URL is in the response so the page can redirect/show it.
  return res.status(200).json({
    ok: true,
    ebook_url: m.url,
    title: m.title,
    delivery: { telegram: tg, n8n, resend, drip },
  });
}
