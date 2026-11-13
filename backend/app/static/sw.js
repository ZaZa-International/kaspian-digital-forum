/* Cache static presentation only. Cargo, tokens and API responses never enter this cache.
   v15: merge of PR #1 (multi-page logistics workspace) — cache-bust so clients
   drop the old single-page shell and fetch the new workspace assets. */
const CACHE = "sct-static-v15";
const ASSETS = [
  "/",
  "/app",
  "/app/shipments/SHP-1042",
  "/static/home.js",
  "/static/workspace.js",
  "/static/workspace.css",
  "/static/passport.css",
  "/static/map-controller.js",
  "/static/flat-map.js",
  "/static/flat-map.css",
  "/static/laboratory.html",
  "/static/command.css",
  "/static/command.js",
  "/static/route-progress.js",
  "/static/intelligence-panel.js",
  "/static/intelligence-panel.css",
  "/static/operations-labs.css",
  "/static/operations-labs.js",
  "/static/resilience-panel.js",
  "/static/corridor-panel.js",
  "/static/freight-hero.css",
  "/static/freight-hero.js",
  "/static/assets/freight/freight-poster.jpg",
  "/static/assets/freight/freight-720.mp4",
  "/static/assets/freight/oswald.ttf",
  "/static/caspian-scene.js",
  "/static/styles.css",
  "/static/app.js",
  "/static/manifest.json",
  "/static/icon-192.png",
  "/static/icon-512.png",
  "/static/assets/brand/mark.svg",
  "/static/assets/brand/mark-mono.svg",
  "/static/assets/geo/borders.json",
  "/static/assets/geo/caspian.json",
  "/static/vendor/gsap/gsap.min.js",
  "/static/vendor/gsap/ScrollTrigger.min.js",
  "/static/vendor/three/three.module.js",
  "/static/vendor/three/three.core.js",
  "/static/vendor/fonts.css",
  "/static/vendor/fonts/ibm-plex-sans-cyrillic.woff2",
  "/static/vendor/fonts/ibm-plex-sans-latin.woff2",
  "/static/vendor/fonts/jetbrains-mono-cyrillic.woff2",
  "/static/vendor/fonts/jetbrains-mono-latin.woff2",
  "/static/vendor/leaflet.js",
  "/static/vendor/leaflet.css",
  "/static/vendor/images/marker-icon.png",
  "/static/vendor/images/marker-icon-2x.png",
  "/static/vendor/images/marker-shadow.png",
];
self.addEventListener("install", (e) =>
  e.waitUntil(
    caches
      .open(CACHE)
      .then((c) => c.addAll(ASSETS))
      .then(() => self.skipWaiting()),
  ),
);
self.addEventListener("activate", (e) =>
  e.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((k) => k.startsWith("sct-static-") && k !== CACHE)
            .map((k) => caches.delete(k)),
        ),
      )
      .then(() => self.clients.claim()),
  ),
);
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (
    e.request.method !== "GET" ||
    url.origin !== self.location.origin ||
    url.pathname.startsWith("/api/")
  )
    return;
  if (e.request.mode !== "navigate" && !url.pathname.startsWith("/static/"))
    return;
  e.respondWith(
    (async () => {
      try {
        const response = await fetch(e.request);
        // Range responses for video are partial, and Cache.put rejects status 206.
        if (response.status === 200) {
          const copy = response.clone();
          e.waitUntil(caches.open(CACHE).then((c) => c.put(e.request, copy)));
        }
        return response;
      } catch {
        return (
          (await caches.match(e.request)) ||
          (e.request.mode === "navigate" && url.pathname.startsWith("/app")
            ? await caches.match(/^\/app\/shipments\/[^/]+$/.test(url.pathname) ? "/app/shipments/SHP-1042" : "/app")
            : undefined) ||
          new Response("Нет сети. Этот ресурс ещё не сохранён.", {
            status: 503,
            headers: { "Content-Type": "text/plain; charset=utf-8" },
          })
        );
      }
    })(),
  );
});