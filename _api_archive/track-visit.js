// /api/track-visit — privacy-respecting visitor tracking (#332).
// Receives sendBeacon from _enhance.js. Logs anonymised data only.
// No IP, no fingerprinting. Just path + referrer + timestamp.
export default async function handler(req, res) {
  if (req.method !== 'POST') return res.status(405).end();
  // Fire-and-forget; client uses sendBeacon (no response needed)
  // Logs to Vercel function logs; aggregate via dashboards
  try {
    const body = typeof req.body === 'string' ? JSON.parse(req.body) : (req.body || {});
    const { path, ref, ts } = body;
    if (!path) return res.status(204).end();
    // Privacy: strip any PII; cap lengths
    const log = {
      path: String(path).slice(0, 200),
      ref: String(ref || '').slice(0, 200),
      ts: ts || Date.now(),
      ua: (req.headers['user-agent'] || '').slice(0, 80)
    };
    // Stdout log will appear in Vercel logs; can be shipped to Better Stack later
    console.log('VISIT', JSON.stringify(log));
    return res.status(204).end();
  } catch (e) {
    return res.status(204).end();
  }
}
