// /api/health — service-health endpoint for uptime monitoring (#307).
// Returns 200 OK with timestamp + version. Cache-control:no-store.
export default async function handler(req, res) {
  res.setHeader('Cache-Control', 'no-store');
  return res.status(200).json({
    ok: true,
    service: 'winfredquek.com',
    ts: new Date().toISOString(),
    version: '2026.05.04'
  });
}
