// /api/drip-signup — captures name/email/phone, picks track, forwards to n8n,
// logs locally, sends instant welcome email, alerts Telegram.
// POST { name, email, phone, track: 'empire'|'equity'|'reinvest', source }

const N8N_WEBHOOK = process.env.N8N_DRIP_WEBHOOK || 'https://winfredquekoc.app.n8n.cloud/webhook/drip-signup';
const TG_BOT = process.env.TELEGRAM_BOT_TOKEN;
const TG_CHAT = process.env.TELEGRAM_CHAT_ID;

const TRACK_META = {
  empire: { title: 'Empire Blueprint', tagline: 'Building 3+ properties without ABSD destroying you.' },
  equity: { title: 'Equity Unlock', tagline: 'Pulling out trapped equity from your existing property.' },
  reinvest: { title: 'Reinvest Strategy', tagline: 'Selling smart and reinvesting before tax + interest erode you.' }
};

function sgPhone(p) {
  const digits = String(p || '').replace(/[^0-9+]/g, '');
  if (!digits) return null;
  if (digits.startsWith('+')) return digits;
  if (digits.length === 8) return `+65${digits}`;
  if (digits.startsWith('65') && digits.length === 10) return `+${digits}`;
  return digits;
}

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(204).end();
  if (req.method !== 'POST') return res.status(405).json({ ok: false });

  const body = typeof req.body === 'string' ? JSON.parse(req.body) : (req.body || {});
  const { name, email, phone, track, source, consent } = body;

  if (!name || name.length < 2) return res.status(400).json({ ok: false, error: 'name_required' });
  if (!email || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) return res.status(400).json({ ok: false, error: 'invalid_email' });
  const normalisedPhone = sgPhone(phone);
  if (!normalisedPhone || normalisedPhone.length < 8) return res.status(400).json({ ok: false, error: 'invalid_phone' });
  if (!TRACK_META[track]) return res.status(400).json({ ok: false, error: 'invalid_track' });
  if (!consent) return res.status(400).json({ ok: false, error: 'consent_required' });

  const meta = TRACK_META[track];
  const lead = {
    name: String(name).trim().slice(0, 80),
    email: String(email).trim().toLowerCase(),
    phone: normalisedPhone,
    track,
    source: source || 'start_page',
    consent: true,
    ip: (req.headers['x-forwarded-for'] || '').split(',')[0].trim() || null,
    ua: (req.headers['user-agent'] || '').slice(0, 200),
    ts: new Date().toISOString()
  };

  const tasks = [];

  tasks.push(
    fetch(N8N_WEBHOOK, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: lead.name, email: lead.email, phone: lead.phone, track: lead.track })
    }).catch(e => ({ error: 'n8n_failed', detail: String(e).slice(0, 100) }))
  );

  if (process.env.RESEND_API_KEY) {
    tasks.push(
      fetch('https://api.resend.com/emails', {
        method: 'POST',
        headers: { Authorization: `Bearer ${process.env.RESEND_API_KEY}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          from: 'Winfred Quek <winfred@winfredquek.com>',
          to: [lead.email],
          subject: `${meta.title} — your first lesson lands tomorrow`,
          html: `
            <p>Hi ${lead.name.split(' ')[0]},</p>
            <p>You're in the <b>${meta.title}</b> track. ${meta.tagline}</p>
            <p>Day 1 lands tomorrow morning. 10 emails over 14 days. Reply STOP any time.</p>
            <p>If something is urgent, WhatsApp me directly: <a href="https://wa.me/6581618149">+65 8161 8149</a>.</p>
            <p>— Winfred<br/>CEA R073319H · Crestbrick Pte Ltd</p>
            <p style="font-size:11px;color:#999;">You signed up at winfredquek.com. <a href="https://winfredquek.com/unsubscribe?email=${encodeURIComponent(lead.email)}">Unsubscribe</a>.</p>
          `
        })
      }).catch(e => ({ error: 'resend_failed', detail: String(e).slice(0, 100) }))
    );
  }

  if (TG_BOT && TG_CHAT) {
    const msg = `🎯 *New drip signup*\n\n*${lead.name}*\n\`${lead.email}\`\n\`${lead.phone}\`\nTrack: *${meta.title}*\nSource: ${lead.source}`;
    tasks.push(
      fetch(`https://api.telegram.org/bot${TG_BOT}/sendMessage`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: TG_CHAT, text: msg, parse_mode: 'Markdown' })
      }).catch(() => null)
    );
  }

  await Promise.allSettled(tasks);

  return res.status(200).json({ ok: true, track });
}
