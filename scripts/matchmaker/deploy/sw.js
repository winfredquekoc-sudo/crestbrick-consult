// Matchmaker service worker: cache first for "/", network fallback.
// This app is a single file artifact (index.html at "/") behind Basic Auth —
// nothing else on this origin needs caching, so the fetch handler ignores
// every other path.
//
// CACHE_VERSION: 2 — bump this number (only this number, and the CACHE_NAME
// string below to match) whenever this file's caching logic changes, so
// returning visitors evict the old cache instead of running stale logic
// forever. build.py never touches this file, so the version is hand rolled.
//
// PRIVACY NOTE: the cached response IS the PII artifact. deploy/vercel.json
// sets Cache-Control: no-store on "/", but the Cache Storage API deliberately
// ignores response cache headers — putting "/" in a cache persists tenant and
// landlord data to that device's disk regardless of what no-store says. That
// is inherent to installing this as an offline app (item 52) rather than a bug
// in either file, and the compensating controls are the auth wall in front of
// it plus the app's own masking and idle lock. Anyone reading vercel.json and
// concluding "this response is never written to disk" would be wrong.
const CACHE_NAME = "matchmaker-cache-v2";
const APP_URL = "/";

self.addEventListener("install", (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.add(APP_URL).catch(() => {}))
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((names) => Promise.all(names.filter((n) => n !== CACHE_NAME).map((n) => caches.delete(n))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.pathname !== APP_URL) return;   // only the app shell is cached

  // Cache first, but ALWAYS revalidate in the background (stale while
  // revalidate). Plain cache first would pin the very first artifact this
  // device ever loaded: deploy.sh ships a rebuilt index.html to the same "/",
  // and since sw.js itself is unchanged by a data-only deploy, nothing would
  // ever re-run install() to refresh the entry — an installed PWA would keep
  // serving that first day's tenant data forever, with clearing site data as
  // the only way out. Refreshing behind the response costs the user nothing
  // (they still get the cached copy instantly, and offline still works) and
  // the next launch picks up the newer build.
  //
  // `res.ok` is what keeps the auth wall from poisoning the cache: an expired
  // Basic Auth session answers 401, and a 401 must never overwrite a good
  // cached artifact.
  event.respondWith(
    caches.open(CACHE_NAME).then((cache) =>
      cache.match(req).then((cached) => {
        const fresh = fetch(req).then((res) => {
          if (res && res.ok) cache.put(req, res.clone());
          return res;
        }).catch((e) => {
          if (cached) return cached;   // offline with a cached copy is a success
          throw e;
        });
        return cached || fresh;
      })
    )
  );
});
