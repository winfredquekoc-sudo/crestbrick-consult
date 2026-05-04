// /api/geo — returns visitor's country code from Vercel headers (#289).
// Vercel populates x-vercel-ip-country automatically.
export default async function handler(req, res) {
  res.setHeader('Cache-Control', 'public, max-age=3600');
  const country = req.headers['x-vercel-ip-country'] || 'XX';
  return res.status(200).json({ country });
}
