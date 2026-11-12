/* Offline cache for static assets only (never API responses).
   Map tiles still need internet; tables, twin and forecasts work
   offline because the app + Leaflet are served locally. */
const CACHE = "sct-static-v1";
const ASSETS = ["/", "/static/styles.css", "/static/app.js",
                "/static/manifest.json",
                "/static/vendor/leaflet.js", "/static/vendor/leaflet.css"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS)));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith("/api/")) return; // never cache API
  e.respondWith(
    caches.match(e.request).then((hit) => hit || fetch(e.request))
  );
});
