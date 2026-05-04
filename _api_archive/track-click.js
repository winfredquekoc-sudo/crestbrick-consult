// /api/track-click — receives WA click beacons (I85). Writes to a JSON log.
// Vercel Serverless Function (Node.js).
// Privacy: never logs PII; only event + label + path + ts.

import { promises as fs } from 'node:fs';
import path from 'node:path';
import os from 'node:os';

export const config = { runtime: 'nodejs' };

export default async function handler(req, res) {
  // CORS preflight
  if (req.method === 'OPTIONS') {
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
    return res.status(204).end();
  }
  if (req.method !== 'POST') return res.status(405).json({ error: 'method_not_allowed' });

  try {
    let body = req.body;
    if (typeof body === 'string') body = JSON.parse(body);
    if (!body || typeof body !== 'object') body = {};

    const event = String(body.event || 'click').slice(0, 32);
    const label = String(body.label || '').slice(0, 120);
    const src = String(body.src || '').slice(0, 200);
    const ua = (req.headers['user-agent'] || '').slice(0, 200);
    const ts = Number(body.ts) || Date.now();

    const line = JSON.stringify({ event, label, src, ua, ts, ip_hash: hashIp(req) }) + '\n';
    // In Vercel serverless, /tmp is the only writable path; rotates on cold start.
    const tmp = path.join(os.tmpdir(), 'wa-clicks.log');
    await fs.appendFile(tmp, line);

    // Best-effort fan-out to Telegram if env present (skip if missing).
    if (process.env.TELEGRAM_BOT_TOKEN && process.env.TELEGRAM_WINFRED_CHAT_ID && event === 'wa_click') {
      // Don't await — fire-and-forget
      const text = `📞 WA click · "${label}" · ${src}`;
      fetch(`https://api.telegram.org/bot${process.env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: process.env.TELEGRAM_WINFRED_CHAT_ID, text }),
      }).catch(() => {});
    }

    res.setHeader('Access-Control-Allow-Origin', '*');
    return res.status(204).end();
  } catch (e) {
    return res.status(204).end();  // Never break beacons on a server hiccup
  }
}

function hashIp(req) {
  const ip = req.headers['x-forwarded-for']?.split(',')[0] || req.socket?.remoteAddress || '';
  if (!ip) return '';
  // Tiny non-crypto hash; we only need to bucket repeat clicks, not identify
  let h = 0;
  for (let i = 0; i < ip.length; i++) h = (h * 31 + ip.charCodeAt(i)) | 0;
  return Math.abs(h).toString(36);
}
