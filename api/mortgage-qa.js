// Vercel Function: /api/mortgage-qa
// POST { q } → returns { a } using Anthropic Claude Haiku + live rates.json data.
export default async function handler(req, res) {
  if (req.method !== 'POST') return res.status(405).json({ ok: false });
  const { q } = (typeof req.body === 'string' ? JSON.parse(req.body) : (req.body || {}));
  if (!q || q.length > 500) return res.status(400).json({ a: 'Question too long or empty.' });

  const KEY = process.env.ANTHROPIC_API_KEY;
  if (!KEY) return res.status(503).json({ a: 'Bot not configured. WA me directly.' });

  // Fetch rates.json for live data
  let rates = '';
  try {
    const proto = req.headers['x-forwarded-proto'] || 'https';
    const r = await fetch(`${proto}://${req.headers.host}/rates.json`);
    if (r.ok) {
      const d = await r.json();
      const lowest_2yr = (d.packages || []).filter(p => /2yr/i.test(p.type) && /fixed/i.test(p.type)).sort((a,b)=>a.rate_pct-b.rate_pct)[0];
      rates = `3M SORA: ${d.sora_3m_pct}% (as of ${d.updated_sgt}). Lowest 2yr fixed: ${lowest_2yr?.bank} at ${lowest_2yr?.rate_pct}%.`;
    }
  } catch(e){}

  const sys = `You are a Singapore property mortgage Q&A bot, voiced as Winfred Quek (CEA R073319H, Crestbrick).

Ground rules:
- ONLY answer SG mortgage / property finance questions.
- Use the live rate facts when relevant. Cite specific numbers.
- Voice: investor-minded, calm authority, never agent-pitchy. Never "feel free to" or fluff.
- 100-200 words max.
- If question is outside SG mortgage scope, say so and suggest WA.
- End with a "WA me for your specific numbers" if the question is materially personal.

Live rate facts:
${rates}`;

  try {
    const r = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'x-api-key': KEY,
        'anthropic-version': '2023-06-01',
        'content-type': 'application/json'
      },
      body: JSON.stringify({
        model: 'claude-haiku-4-5',
        max_tokens: 500,
        system: sys,
        messages: [{ role: 'user', content: q }]
      })
    });
    const d = await r.json();
    const a = d?.content?.[0]?.text || 'Couldn\'t generate an answer. WA me.';
    return res.status(200).json({ a });
  } catch (e) {
    return res.status(500).json({ a: 'Service error. WA me.' });
  }
}
