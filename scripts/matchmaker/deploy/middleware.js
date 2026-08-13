// Basic auth wall for the PII matchmaker app. Same logic as the canonical
// ~/crestbrick-matchmaker-private/middleware.js — this copy exists so that a
// deploy run from THIS folder can never ship without it (see deploy.sh's hard
// precondition). MM_USER/MM_PASS are read from the Vercel project's own env
// vars at request time; no credentials of any kind live in this file.
//
// favicon.ico/manifest.json/sw.js/icon.svg are excluded from the auth wall:
// they carry no PII and must be reachable before a Basic Auth prompt (a PWA
// install banner and the service worker's own fetch of sw.js both happen
// pre auth in most browsers).
export const config = { matcher: ['/((?!favicon.ico|manifest.json|sw.js|icon.svg).*)'] };
export default function middleware(req) {
  const user = process.env.MM_USER, pass = process.env.MM_PASS;
  // Fail closed when the project's env vars are missing. Without this, template
  // interpolation of two undefined values makes the expected header the fixed,
  // publicly derivable Basic btoa('undefined:undefined') — the wall would still
  // answer 401 to a plain visitor (so deploy.sh's 401 check would pass and
  // report the app as private) while anyone sending that one known header got
  // straight in to real tenant and landlord data.
  if (!user || !pass) {
    return new Response('Auth is not configured on this deployment — set MM_USER and MM_PASS on the Vercel project.', {
      status: 503, headers: { 'Cache-Control': 'no-store' }
    });
  }
  const auth = req.headers.get('authorization') || '';
  const expected = 'Basic ' + btoa(`${user}:${pass}`);
  if (auth === expected) return;
  return new Response('Authentication required', {
    status: 401,
    headers: { 'WWW-Authenticate': 'Basic realm="Crestbrick Matchmaker"' }
  });
}
