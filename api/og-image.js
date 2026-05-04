// Dynamic OG image generator (#265). Returns SVG-as-PNG-equivalent for OG tags.
// Usage: /api/og-image?title=Foo&subtitle=Bar
// Page meta: <meta property="og:image" content="/api/og-image?title=...&subtitle=...">
import { ImageResponse } from '@vercel/og';
export const config = { runtime: 'edge' };

export default async function handler(req) {
  const url = new URL(req.url);
  const title = (url.searchParams.get('title') || 'Winfred Quek').slice(0, 80);
  const subtitle = (url.searchParams.get('subtitle') || 'Singapore property strategist').slice(0, 100);

  return new ImageResponse(
    {
      type: 'div',
      props: {
        style: {
          width: '100%', height: '100%', display: 'flex', flexDirection: 'column',
          background: '#0e0c08', color: '#f0e9d8', padding: '60px',
          fontFamily: 'system-ui'
        },
        children: [
          { type: 'div', props: { style: { color: '#b48c50', fontSize: '20px', letterSpacing: '0.2em', textTransform: 'uppercase', marginBottom: '20px' }, children: 'Crestbrick · Winfred Quek' } },
          { type: 'div', props: { style: { fontSize: '60px', fontWeight: '500', lineHeight: '1.1', marginBottom: '20px' }, children: title } },
          { type: 'div', props: { style: { color: '#a89980', fontSize: '28px', lineHeight: '1.4' }, children: subtitle } },
          { type: 'div', props: { style: { marginTop: 'auto', display: 'flex', alignItems: 'center', gap: '12px', color: '#7a6c54', fontSize: '18px' }, children: [
            { type: 'div', props: { style: { background: '#b48c50', width: '40px', height: '4px' } } },
            { type: 'div', props: { children: 'winfredquek.com · CEA R073319H' } }
          ] } }
        ]
      }
    },
    { width: 1200, height: 630 }
  );
}
