// #182 Lease agreement clause analyzer.
// POST { lease_text } → returns { risks: [...], suggestions: [...] }
// Uses Anthropic Claude Haiku.
export default async function handler(req, res) {
  if (req.method !== 'POST') return res.status(405).json({ ok: false });
  const { lease_text } = (typeof req.body === 'string' ? JSON.parse(req.body) : (req.body || {}));
  if (!lease_text || lease_text.length < 50) return res.status(400).json({ ok: false, error: 'lease_text too short' });
  if (lease_text.length > 100000) return res.status(400).json({ ok: false, error: 'lease_text too long' });

  const KEY = process.env.ANTHROPIC_API_KEY;
  if (!KEY) return res.status(503).json({ ok: false, error: 'not_configured' });

  const sys = `You are a Singapore tenancy agreement reviewer. Analyse the lease for risks per clause.

Output ONLY valid JSON:
{
  "risks": [{"clause": "...", "risk_level": "RED|AMBER|GREEN", "issue": "...", "negotiation_suggestion": "..."}],
  "missing_clauses": ["..."],
  "overall_grade": "A|B|C|D|F",
  "summary": "1-paragraph"
}

Common SG tenancy issues to check:
- Diplomatic clause (only 1y leases for some tenants)
- Reinstatement obligation (often unfair on tenant)
- 1-month security deposit (standard) vs 2+ months (excessive)
- Air-con servicing responsibility (usually tenant pays first $200/incident)
- Minor repairs cap (typical $150-250)
- Stamp duty responsibility (usually tenant)
- Early termination (typically lock-in 12mo for 2y leases)
- Subletting clauses
- Pet policy
- Renovation restrictions`;

  try {
    const r = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: { 'x-api-key': KEY, 'anthropic-version': '2023-06-01', 'content-type': 'application/json' },
      body: JSON.stringify({
        model: 'claude-haiku-4-5',
        max_tokens: 4000,
        system: sys,
        messages: [{ role: 'user', content: `Analyse this Singapore tenancy agreement:\n\n${lease_text}` }]
      })
    });
    const d = await r.json();
    const text = d?.content?.[0]?.text || '{}';
    let parsed;
    try { parsed = JSON.parse(text.replace(/```json\n?|\n?```/g,'')); } catch(e) { parsed = { raw: text }; }
    return res.status(200).json({ ok: true, analysis: parsed });
  } catch (e) {
    return res.status(500).json({ ok: false, error: String(e).slice(0, 100) });
  }
}
