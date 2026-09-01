// Matchmaker service worker: cache first for "/", network fallback.
// This app is a single file artifact (index.html at "/") behind Basic Auth —
// nothing else on this origin needs caching, so the fetch handler ignores
// every other path.
//
// CACHE_VERSION: 3 — bump this number (only this number, and the CACHE_NAME
// string below to match) whenever this file's caching logic changes, so
// returning visitors evict the old cache instead of running stale logic
// forever. build.py never touches this file, so the version is hand rolled.
// v3: the background revalidate now tells open pages when it stored a NEWER
// build (build_id compared, not mere bytes) — the visible "up to date as of"
// stamp made the stale first paint look like a failed refresh (21 Aug 2026).
//
// PRIVACY NOTE: the cached response IS the PII artifact. deploy/vercel.json
// sets Cache-Control: no-store on "/", but the Cache Storage API deliberately
// ignores response cache headers — putting "/" in a cache persists tenant and
// landlord data to that device's disk regardless of what no-store says. That
// is inherent to installing this as an offline app (item 52) rather than a bug
// in either file, and the compensating controls are the auth wall in front of
// it plus the app's own masking and idle lock. Anyone reading vercel.json and
// concluding "this response is never written to disk" would be wrong.
// v4 (31 Aug 2026): the dashboard map moved from a hand-drawn SVG to a real
// Leaflet/OSM map with Leaflet INLINED into the shell. Stale PWA clients were
// still serving the pre-map build (no pins). Bumping forces install() to re-run,
// evict the v3 cache, and fetch the new shell — the clean way to push a shell
// change to installed clients rather than waiting on stale-while-revalidate.
// v5 (1 Sep 2026): added an offline fallback (offline.html, a static page with
// no PII) for the case where this device has never loaded "/" successfully —
// previously a cold install with no signal just surfaced the browser's own
// offline error. Bumped so installed clients pick up the new install() logic
// that caches the fallback page.
const CACHE_NAME = "matchmaker-cache-v5";
const APP_URL = "/";
const OFFLINE_URL = "/offline.html";

self.addEventListener("install", (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) =>
      Promise.all([
        cache.add(APP_URL).catch(() => {}),
        cache.add(OFFLINE_URL).catch(() => {}),
      ])
    )
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
        const cachedCopy = cached ? cached.clone() : null;
        const fresh = fetch(req).then((res) => {
          if (res && res.ok) {
            const resCopy = res.clone();
            cache.put(req, res.clone());
            // The page that just loaded is showing the CACHED build. If the copy
            // we just stored is a genuinely newer build (build_id differs — a
            // plain byte compare would false-alarm never, but a notify on every
            // revalidate would false-alarm always), tell every open page so it
            // can offer a one-tap reload instead of silently looking stale.
            if (cachedCopy) {
              Promise.all([cachedCopy.text(), resCopy.text()]).then(([oldT, newT]) => {
                const idOf = (t) => (t.match(/"build_id": *"([^"]+)"/) || [])[1];
                const a = idOf(oldT), b = idOf(newT);
                if (a && b && a !== b) {
                  self.clients.matchAll().then((cs) =>
                    cs.forEach((c) => c.postMessage({ type: "fresh-build", build_id: b })));
                }
              }).catch(() => {});
            }
          }
          return res;
        }).catch((e) => {
          if (cached) return cached;   // offline with a cached copy is a success
          // First-ever load on this device with no signal: nothing cached yet
          // to fall back to. Show the static offline page instead of letting
          // the browser's own connection-error page take over the tab.
          return caches.match(OFFLINE_URL).then((offline) => offline || Promise.reject(e));
        });
        return cached || fresh;
      })
    )
  );
});
