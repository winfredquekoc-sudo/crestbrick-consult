// J96 — vercel.ts (typed config) replacing vercel.json.
// Per Vercel 2026 platform guidance, vercel.ts is the recommended way.
// To activate: install @vercel/config, then `rm vercel.json`.
//   pnpm add -D @vercel/config   (or npm i -D @vercel/config)
//
// While vercel.json still exists, Vercel prefers it. Keep both during the cutover,
// then delete vercel.json after one production deploy validates vercel.ts.

import { routes, type VercelConfig } from '@vercel/config/v1';

const securityHeaders = [
  routes.header('Strict-Transport-Security', 'max-age=63072000; includeSubDomains; preload'),
  routes.header('X-Content-Type-Options', 'nosniff'),
  routes.header('X-Frame-Options', 'SAMEORIGIN'),
  routes.header('Referrer-Policy', 'strict-origin-when-cross-origin'),
  routes.header('Permissions-Policy', 'camera=(), microphone=(), geolocation=()'),
];

// Helper to keep redirect declarations terse
const r = (from: string, to: string, permanent = true) =>
  routes.redirect(from, to, { permanent });

export const config: VercelConfig = {
  cleanUrls: true,
  trailingSlash: false,

  headers: [
    routes.headers('/(.*)', securityHeaders),
    routes.cacheControl('/img/(.*)', {
      public: true,
      maxAge: '1 hour',
      sMaxAge: '1 day',
      staleWhileRevalidate: '1 week',
    }),
    routes.cacheControl('/(.*).(css|js|woff2)', {
      public: true,
      maxAge: '1 year',
      immutable: true,
    }),
    routes.cacheControl('/(.*).html', {
      public: true,
      maxAge: 0,
      sMaxAge: '10 minutes',
      staleWhileRevalidate: '1 day',
    }),
  ],

  redirects: [
    r('/links', '/links.html', false),
    r('/:path*', 'https://winfredquek.com/:path*', true), // canonical host
    r('/about.html', '/about'),
    r('/services.html', '/services'),
    r('/pricing.html', '/pricing'),
    r('/hdb-towns', '/districts#hdb-towns', false),
    r('/contact.html', '/contact'),
    r('/listings.html', '/listings'),
    r('/insights.html', '/insights'),
    r('/testimonials.html', '/testimonials'),
    r('/track-record.html', '/track-record'),
    r('/buyers-guide.html', '/buyers-guide'),
    r('/sellers-guide.html', '/sellers-guide'),
    r('/faq.html', '/faq'),
    r('/new-launches.html', '/new-launches'),
    r('/districts.html', '/districts'),
    r('/privacy.html', '/privacy'),
    r('/tools/decoupling', '/tools/restructuring'),
    r('/tools/decoupling.html', '/tools/restructuring'),
    r('/insights/decoupling-breakeven', '/insights/restructuring-breakeven'),
    r('/insights/decoupling-breakeven.html', '/insights/restructuring-breakeven'),
    r('/blog', '/insights'),
    r('/blog/index.html', '/insights'),
    r('/blog/hdb-upgrade-timeline', '/insights/hdb-mop-upgrade-timeline'),
    r('/blog/hdb-upgrade-timeline.html', '/insights/hdb-mop-upgrade-timeline'),
    r('/blog/absd-decoupling-singapore', '/insights/ownership-restructuring-math'),
    r('/blog/absd-decoupling-singapore.html', '/insights/ownership-restructuring-math'),
    r('/blog/absd-second-property-guide', '/insights/absd-singapore-2026'),
    r('/blog/absd-second-property-guide.html', '/insights/absd-singapore-2026'),
    r('/blog/hdb-bto-vs-resale', '/insights/new-launch-vs-resale'),
    r('/blog/hdb-bto-vs-resale.html', '/insights/new-launch-vs-resale'),
  ],
};
