// #184 Contract-comparison AI.
// POST { contract_a, contract_b } → diff + risk per clause.
export default async function handler(req, res) {
  if (req.method !== 'POST') return res.status(405).json({ ok: false });
  const { contract_a, contract_b } = (typeof req.body === 'string' ? JSON.parse(req.body) : (req.body || {}));
  if (!contract_a || !contract_b) return res.status(400).json({ ok: false, error: 'both contracts required' });

  const KEY = process.env.ANTHROPIC_API_KEY;
  if (!KEY) return res.status(503).json({ ok: false, error: 'not_configured' });

  const sys = `You compare two Singapore property contracts (SPA / OTP / Tenancy). Identify material differences and rank each by risk/benefit.

Output JSON only:
{
  "differences": [{"clause": "...", "version_a": "...", "version_b": "...", "favours": "A|B|NEUTRAL", "impact": "HIGH|MEDIUM|LOW", "comment": "..."}],
  "summary_a": "...",
  "summary_b": "...",
  "recommendation": "..."
}`;

  try {
    const r = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: { 'x-api-key': KEY, 'anthropic-version': '2023-06-01', 'content-type': 'application/json' },
      body: JSON.stringify({
        model: 'claude-haiku-4-5',
        max_tokens: 4000,
        system: sys,
        messages: [{ role: 'user', content: `Contract A:\n${contract_a}\n\n---\n\nContract B:\n${contract_b}` }]
      })
    });
    const d = await r.json();
    const text = d?.content?.[0]?.text || '{}';
    let parsed;
    try { parsed = JSON.parse(text.replace(/```json\n?|\n?```/g,'')); } catch(e) { parsed = { raw: text }; }
    return res.status(200).json({ ok: true, comparison: parsed });
  } catch (e) {
    return res.status(500).json({ ok: false, error: String(e).slice(0, 100) });
  }
}
