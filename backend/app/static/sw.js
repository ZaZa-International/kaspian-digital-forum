/* Offline cache for static assets only (never API responses).
   v7: cache-bust for the visible-features deploy (animated map, radar, live
   twin, recommendations, alerts). Network-first for the app shell (HTML,
   app.js, styles.css) so fresh deployments are never hidden by a stale cache;
   cache-first for vendor assets incl. self-hosted fonts; offline fallback. */
const CACHE = "sct-static-v7";
const ASSETS = ["/", "/static/styles.css", "/static/app.js",
                "/static/manifest.json", "/static/icon-192.png", "/static/icon-512.png",
                "/static/vendor/fonts.css",
                "/static/vendor/fonts/ibm-plex-sans-cyrillic.woff2",
                "/static/vendor/fonts/ibm-plex-sans-latin.woff2",
                "/static/vendor/fonts/jetbrains-mono-cyrillic.woff2",
                "/static/vendor/fonts/jetbrains-mono-latin.woff2",
                "/static/vendor/leaflet.js", "/static/vendor/leaflet.css",
                "/static/vendor/images/marker-icon.png",
                "/static/vendor/images/marker-icon-2x.png",
                "/static/vendor/images/marker-shadow.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith("/api/")) return; // never cache API
  if (e.request.mode === "navigate") {
    // network-first: fresh HTML when online, cached shell when offline
    e.respondWith(
      fetch(e.request).then((resp) => {
        const copy = resp.clone();
        caches.open(CACHE).then((c) => c.put("/", copy));
        return resp;
      }).catch(() => caches.match("/"))
    );
    return;
  }
  if (url.pathname === "/static/app.js" || url.pathname === "/static/styles.css") {
    // network-first: fresh app code when online, cached when offline
    e.respondWith(
      fetch(e.request).then((resp) => {
        const copy = resp.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy));
        return resp;
      }).catch(() => caches.match(e.request))
    );
    return;
  }
  e.respondWith(
    caches.match(e.request).then((hit) => hit || fetch(e.request).then((resp) => {
      if (resp.ok && url.origin === self.location.origin) {
        const copy = resp.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy));
      }
      return resp;
    }))
  );
});