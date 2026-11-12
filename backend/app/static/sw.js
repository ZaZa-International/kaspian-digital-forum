/* Offline cache for static assets only (never API responses).
   v4: network-first for navigations (no stale index.html), cache-first
   for versioned static assets, explicit offline fallback. Self-hosted
   fonts + chart icon cached for offline demo. */
const CACHE = "sct-static-v4";
const ASSETS = ["/", "/static/styles.css", "/static/fonts.css", "/static/app.js",
                "/static/manifest.json", "/static/icon.svg",
                "/static/icon-192.png", "/static/icon-512.png",
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