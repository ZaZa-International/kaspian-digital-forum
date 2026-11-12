/**
 * Caspian spatial instrument. Three.js r180 and Natural Earth data are local.
 * Coordinates and borders: Natural Earth, public domain. Relief is illustrative.
 * Route geometry is a corridor schematic, never a navigational track.
 */
const LOCAL = new URL(".", import.meta.url);
const COAST_FALLBACK = [
  [51.744531, 46.93374],
  [52.085547, 46.8396],
  [52.340332, 46.894775],
  [52.483203, 46.990674],
  [53.03457, 46.89292],
  [53.170215, 46.669043],
  [53.063965, 46.475293],
  [53.041602, 45.967871],
  [52.8375, 45.496729],
  [53.078906, 45.30752],
  [52.531055, 45.398633],
  [51.732617, 45.399463],
  [51.333398, 45.27959],
  [51.153711, 45.040234],
  [51.020703, 44.854004],
  [51.310254, 44.61875],
  [51.49375, 44.577539],
  [51.310742, 44.532422],
  [51.048828, 44.530469],
  [50.409473, 44.624023],
  [50.25293, 44.461523],
  [50.331152, 44.325488],
  [50.782617, 44.228027],
  [51.064844, 43.750146],
  [51.274121, 43.53291],
  [51.313379, 43.355664],
  [51.347852, 43.167383],
  [51.700391, 43.104053],
  [51.844141, 42.910449],
  [52.018555, 42.860547],
  [52.273047, 42.799805],
  [52.493945, 42.820264],
  [52.638477, 42.555664],
  [52.517188, 42.237158],
  [52.467578, 41.885889],
  [52.747266, 41.36543],
  [52.861816, 41.210059],
  [52.883496, 41.652539],
  [52.97002, 41.976221],
  [53.284961, 42.081836],
  [53.752344, 42.129395],
  [53.953809, 41.868457],
  [54.094824, 41.519385],
  [54.592188, 41.193555],
  [54.717969, 41.012988],
  [54.685059, 40.873047],
  [54.374414, 40.871387],
  [54.377344, 40.693262],
  [54.192969, 40.72041],
  [53.76377, 40.665674],
  [53.520313, 40.831055],
  [53.191992, 40.809473],
  [52.997656, 40.959863],
  [52.849902, 40.685645],
  [52.744434, 40.219775],
  [52.964844, 39.833887],
  [52.9875, 39.987598],
  [53.404199, 39.960352],
  [53.458301, 39.831201],
  [53.533301, 39.641748],
  [53.497363, 39.533301],
  [53.235645, 39.608545],
  [53.156641, 39.26499],
  [53.336328, 39.34082],
  [53.617578, 39.215967],
  [53.724121, 39.103076],
  [53.885352, 38.864062],
  [53.840039, 38.514941],
  [53.825195, 38.046924],
  [53.897852, 37.413574],
  [54.017187, 36.95249],
  [53.970117, 36.818311],
  [53.679492, 36.853125],
  [53.767676, 36.930322],
  [51.762012, 36.614502],
  [50.533203, 37.013672],
  [50.17627, 37.380518],
  [49.726953, 37.480518],
  [49.171191, 37.600586],
  [48.959961, 37.890137],
  [48.870703, 38.392529],
  [48.854492, 38.838818],
  [49.013477, 39.133984],
  [49.120996, 39.003906],
  [49.269336, 39.285156],
  [49.367383, 39.398389],
  [49.415137, 39.839844],
  [49.669043, 40.249023],
  [50.143164, 40.323242],
  [50.248047, 40.461768],
  [49.990625, 40.576807],
  [49.718359, 40.608105],
  [49.226465, 41.026221],
  [49.106641, 41.301709],
  [48.664648, 41.786621],
  [48.426367, 41.923975],
  [48.228613, 42.180957],
  [47.769727, 42.644775],
  [47.634863, 42.903467],
  [47.463184, 43.035059],
  [47.489844, 43.381689],
  [47.627832, 43.805957],
  [47.462793, 43.555029],
  [47.307031, 44.103125],
  [47.023633, 44.343262],
  [46.720898, 44.45166],
  [46.755273, 44.656543],
  [46.983691, 44.825586],
  [47.083789, 44.816992],
  [47.221484, 45.024268],
  [47.391113, 45.294775],
  [47.488672, 45.455078],
  [47.524219, 45.601709],
  [47.508398, 45.67417],
  [47.649805, 45.656738],
  [47.830176, 45.663037],
  [48.257617, 45.777783],
  [48.589063, 45.934863],
  [48.72959, 45.896826],
  [48.687305, 46.02876],
  [48.809961, 46.100488],
  [49.125488, 46.281738],
  [49.205664, 46.385693],
  [49.344238, 46.485547],
  [49.584375, 46.545215],
  [49.886328, 46.595654],
  [50.30625, 46.794922],
  [50.528418, 46.873291],
  [50.732715, 46.95166],
  [51.29082, 47.097314],
  [51.744531, 46.93374],
];
const PORTS = [
  { id: "astrakhan", name: "Астрахань", lat: 46.35, lon: 48.04, side: "left" },
  { id: "makhachkala", name: "Махачкала", lat: 42.98, lon: 47.5, side: "left" },
  { id: "baku", name: "Баку", lat: 40.37, lon: 49.85, side: "left" },
  { id: "aktau", name: "Актау / Курык", lat: 43.65, lon: 51.15, side: "right" },
  {
    id: "turkmenbashi",
    name: "Туркменбаши",
    lat: 40.02,
    lon: 52.97,
    side: "right",
  },
  { id: "enzeli", name: "Энзели", lat: 37.47, lon: 49.46, side: "left" },
  { id: "amirabad", name: "Амирабад", lat: 36.85, lon: 53.35, side: "right" },
  { id: "astara", name: "Астара", lat: 38.45, lon: 48.87, side: "left" },
  { id: "tehran", name: "Тегеран", lat: 35.69, lon: 51.39, side: "right" },
];
const NODES = Object.fromEntries(
  [
    ...PORTS,
    { id: "moscow", name: "Москва", lat: 55.76, lon: 37.62 },
    { id: "bandar_abbas", name: "Бендер-Аббас", lat: 27.19, lon: 56.27 },
  ].map((p) => [p.id, p]),
);
const ROUTES = {
  R1: {
    id: "R1",
    name: "Через Махачкалу",
    color: "#8be3c3",
    legs: [
      ["moscow", "makhachkala", "rail"],
      ["makhachkala", "amirabad", "sea"],
      ["amirabad", "bandar_abbas", "rail"],
    ],
  },
  R2: {
    id: "R2",
    name: "Через Актау",
    color: "#b6d9e7",
    legs: [
      ["moscow", "astrakhan", "rail"],
      ["astrakhan", "aktau", "sea"],
      ["aktau", "bandar_abbas", "rail"],
    ],
  },
  R3: {
    id: "R3",
    name: "Через Баку",
    color: "#ffae78",
    legs: [
      ["moscow", "baku", "rail"],
      ["baku", "astara", "road"],
      ["astara", "bandar_abbas", "rail"],
    ],
  },
};
const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
// Equirectangular projection corrected at central latitude 42° N.
const project = (p) => [(p.lon - 50.6) * 0.743, 41.8 - p.lat];
const inSea = (x, z, ring) => {
  let c = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const a = ring[i],
      b = ring[j];
    if (
      a[1] > z !== b[1] > z &&
      x < ((b[0] - a[0]) * (z - a[1])) / (b[1] - a[1]) + a[0]
    )
      c = !c;
  }
  return c;
};
const svgEl = (name, attrs = {}) => {
  const e = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
};
let styleInstalled = false;
function installStyle() {
  if (styleInstalled) return;
  styleInstalled = true;
  const s = document.createElement("style");
  s.textContent = `
  .caspian-canvas{position:relative;width:100%;height:100%;min-height:340px;overflow:hidden;isolation:isolate;background:radial-gradient(ellipse at 58% 45%,#15343d 0,#0b202b 40%,#07131b 75%);border-radius:inherit;color:#e7ede8;font-family:var(--font-ui,system-ui,sans-serif)}
  .caspian-canvas canvas,.caspian-fallback{position:absolute;inset:0;width:100%;height:100%;display:block;touch-action:pan-y;outline:none}
  .caspian-canvas canvas:focus-visible{outline:2px solid #8be3c3;outline-offset:-3px}
  .caspian-fallback [role=button]:focus-visible{outline:2px solid #ff784f;outline-offset:4px}.caspian-fallback [role=button]:focus-visible text{fill:#07131b;font-weight:700;text-decoration:underline}.caspian-fallback [role=button]:focus-visible circle{stroke:#07131b;stroke-width:.07}
  .caspian-canvas.is-dragging canvas{cursor:grabbing}.caspian-canvas canvas{cursor:grab}
  .caspian-label-layer{position:absolute;inset:0;pointer-events:none;overflow:hidden}
  .caspian-port{position:absolute;top:0;left:0;pointer-events:auto;border:0;background:rgba(7,19,27,.72);color:#dbe7e3;padding:5px 8px;font-family:inherit;font-size:10px;font-weight:500;line-height:1.1;letter-spacing:.02em;white-space:nowrap;border-radius:3px;cursor:pointer;backdrop-filter:blur(5px);box-shadow:0 0 0 1px #c1e6d816}
  .caspian-port:hover,.caspian-port:focus-visible,.caspian-port.selected{background:#8be3c3;color:#07131b;outline:none;z-index:5}
  .caspian-port::before{content:'';position:absolute;width:14px;height:1px;background:#c6e8dc66;top:50%;right:100%}.caspian-port.left::before{left:100%;right:auto}
  .caspian-country{position:absolute;left:0;top:0;color:#07131ba3;font-size:9px;letter-spacing:.18em;font-weight:600;text-transform:uppercase;white-space:nowrap;text-shadow:0 1px 0 #ffffff25}
  .caspian-water-label{position:absolute;left:0;top:0;color:#a8d3d15c;font-size:11px;font-weight:500;letter-spacing:.38em;text-transform:uppercase;white-space:nowrap}
  .caspian-controls{position:absolute;right:18px;bottom:42px;display:flex;gap:5px;z-index:4}.caspian-controls button{width:34px;height:34px;border:1px solid #dcece14b;background:#07131bf2;border-radius:5px;color:#e7ede8;font:17px/1 sans-serif;cursor:pointer}.caspian-controls button:hover,.caspian-controls button:focus-visible{background:#23454b;outline:1px solid #8be3c3}
  .caspian-source{position:absolute;bottom:12px;left:19px;right:14px;width:fit-content;max-width:calc(100% - 38px);padding:2px 4px;border-radius:2px;background:#07131be8;font:10px/1.4 monospace;letter-spacing:.01em;color:#bdcfc7;pointer-events:none}
  .caspian-north{position:absolute;right:24px;top:25px;display:grid;gap:4px;justify-items:center;font:9px/1 monospace;color:#d4e6de9c;pointer-events:none}.caspian-north i{width:1px;height:26px;background:linear-gradient(#d5eee6,#d5eee600);position:relative;display:block}.caspian-north i:before{content:'';position:absolute;top:0;left:-3px;border-left:3px solid transparent;border-right:3px solid transparent;border-bottom:7px solid #d5eee6}
  .caspian-state{position:absolute;left:20px;top:23px;display:flex;align-items:center;gap:7px;font:9px/1.3 monospace;letter-spacing:.09em;color:#b7cec6;pointer-events:none}.caspian-state:before{content:'';width:5px;height:5px;border-radius:50%;background:#8be3c3;box-shadow:0 0 12px #8be3c380}.caspian-state.storm:before{background:#ff784f;box-shadow:0 0 14px #ff784f80}
  .caspian-route-key{position:absolute;left:20px;bottom:42px;display:flex;gap:6px;z-index:4}.caspian-route-key button{min-height:34px;min-width:34px;border:1px solid #dcece14b;border-radius:4px;padding:7px 10px;background:#07131bf2;color:#c5d8cf;font:12px/1 monospace;cursor:pointer}.caspian-route-key button[aria-pressed=true]{border-color:#8be3c3;color:#07131b;background:#8be3c3}.caspian-route-key button:focus-visible{outline:2px solid #8be3c3}
  .caspian-port.on-route{box-shadow:0 0 0 1px #c9eee580;background:#0b252cef;color:#eef6f0}.caspian-port.selected{background:#8be3c3;color:#07131b}
  .caspian-cargo-label{position:absolute;left:0;top:0;padding:5px 7px;border:1px solid #8be3c3;border-radius:3px;background:#07131bf5;color:#e7ede8;font-size:12px;font-weight:600;line-height:1;letter-spacing:.02em;white-space:nowrap;pointer-events:none;z-index:6}.caspian-cargo-label::before{content:'';display:inline-block;width:5px;height:5px;background:#8be3c3;border-radius:50%;margin-right:6px;vertical-align:1px}.caspian-canvas.is-storm .caspian-cargo-label{border-color:#ff784f;color:#ffe9da}.caspian-canvas.is-storm .caspian-cargo-label::before{background:#ff784f}
  .caspian-country,.caspian-water-label,.caspian-state,.caspian-source,.caspian-north,.caspian-route-key button{font-family:var(--mono,var(--font-data,ui-monospace,monospace))}
  @media(max-width:600px){.caspian-port{font-size:9px;padding:4px 6px}.caspian-country{font-size:7px;letter-spacing:.11em}.caspian-source{font-size:9px}.caspian-canvas{min-height:360px}.caspian-water-label{font-size:9px;letter-spacing:.2em}.caspian-controls button{width:36px;height:36px}.caspian-route-key{bottom:85px}.caspian-route-key button{min-height:36px;min-width:36px}.caspian-controls{bottom:43px}}
  @media(prefers-reduced-motion:reduce){.caspian-port{transition:none}}
  `;
  document.head.append(s);
}

function routeDefinition(input) {
  if (typeof input === "string") return ROUTES[input] || ROUTES.R1;
  if (!input) return ROUTES.R1;
  const lookup = { ...NODES };
  if (input.nodes)
    for (const n of input.nodes) {
      if (
        typeof n === "object" &&
        Number.isFinite(+n.lat) &&
        Number.isFinite(+n.lon)
      )
        lookup[n.id] = { ...n, lat: +n.lat, lon: +n.lon };
    }
  let legs = input.legs?.map((l) =>
    Array.isArray(l)
      ? l
      : [l.from?.id || l.from, l.to?.id || l.to, l.mode || "sea"],
  );
  if (!legs && input.nodes)
    legs = input.nodes
      .slice(1)
      .map((n, i) => [
        input.nodes[i].id || input.nodes[i],
        n.id || n,
        n.mode || "sea",
      ]);
  return {
    id: input.id || "custom",
    name: input.name || "Выбранный маршрут",
    color: input.color || "#8be3c3",
    legs: legs || ROUTES.R1.legs,
    lookup,
  };
}

// Off-region hubs terminate on the diagram edge, with no invented coordinates.
function clippedLeg(a, b) {
  const p = project(a),
    q = project(b),
    dx = q[0] - p[0],
    dz = q[1] - p[1];
  let t0 = 0,
    t1 = 1;
  for (const [d, n] of [
    [-dx, p[0] + 5.25],
    [dx, 5.25 - p[0]],
    [-dz, p[1] + 6.25],
    [dz, 6.25 - p[1]],
  ]) {
    if (d === 0 && n < 0) return null;
    if (d === 0) continue;
    const t = n / d;
    if (d < 0) t0 = Math.max(t0, t);
    else t1 = Math.min(t1, t);
    if (t0 > t1) return null;
  }
  return [
    [p[0] + dx * t0, p[1] + dz * t0],
    [p[0] + dx * t1, p[1] + dz * t1],
  ];
}

function corridorPoints(a, b, mode) {
  let via = [];
  // Schematic inland waypoints keep the rail corridors on the correct shore.
  // They express direction, not verified railway track geometry.
  if (mode !== "sea" && a.id === "aktau" && b.id === "bandar_abbas")
    via = [
      { lat: 43.35, lon: 54.6 },
      { lat: 40.4, lon: 55.4 },
      { lat: 37.7, lon: 55.2 },
    ];
  if (mode !== "sea" && a.id === "astara" && b.id === "bandar_abbas")
    via = [{ lat: 37.25, lon: 49.4 }, NODES.tehran];
  const nodes = [a, ...via, b],
    points = [];
  for (let i = 1; i < nodes.length; i++) {
    const segment = clippedLeg(nodes[i - 1], nodes[i]);
    if (segment) points.push(...(points.length ? segment.slice(1) : segment));
  }
  return points;
}

export async function createCaspianScene(
  container,
  { onPortSelect = () => {}, onRouteSelect = () => {}, mode = "hero" } = {},
) {
  if (!container) throw new Error("Caspian scene requires a container");
  installStyle();
  const wrap = document.createElement("div");
  wrap.className = "caspian-canvas";
  wrap.dataset.mode = mode;
  container.append(wrap);
  const state = {
    storm: false,
    horizon: 72,
    progress: 0,
    manualProgress: false,
    story: 0,
    storyControlled: false,
    paused: false,
    route: routeDefinition("R1"),
    disposed: false,
    visible: true,
    reduced: matchMedia("(prefers-reduced-motion: reduce)").matches,
  };
  let coast = COAST_FALLBACK,
    borders = [];
  try {
    const [g, b] = await Promise.all([
      fetch(new URL("assets/geo/caspian.json", LOCAL)).then((r) =>
        r.ok ? r.json() : Promise.reject(),
      ),
      fetch(new URL("assets/geo/borders.json", LOCAL)).then((r) =>
        r.ok ? r.json() : Promise.reject(),
      ),
    ]);
    const geom = g.features[0].geometry;
    coast =
      geom.type === "MultiPolygon"
        ? geom.coordinates[0][0]
        : geom.coordinates[0];
    borders = b.lines || [];
  } catch {
    /* An embedded, reduced Natural Earth ring keeps the map available offline. */
  }
  const ring = coast.map(([lon, lat]) => project({ lon, lat }));
  const info = document.createElement("div");
  info.className = "caspian-state";
  info.textContent = "КАСПИЙ / ПРОСТРАНСТВЕННАЯ МОДЕЛЬ";
  wrap.append(info);
  const north = document.createElement("div");
  north.className = "caspian-north";
  north.innerHTML = "<span>N</span><i></i>";
  wrap.append(north);
  const source = document.createElement("div");
  source.className = "caspian-source";
  source.textContent = "Natural Earth · Схема коридора, не навигационная карта";
  wrap.append(source);
  const controls = document.createElement("div");
  controls.className = "caspian-controls";
  wrap.append(controls);
  const routeKey = document.createElement("div");
  routeKey.className = "caspian-route-key";
  wrap.append(routeKey);
  const listeners = [];
  const listen = (el, event, fn, options) => {
    el.addEventListener(event, fn, options);
    listeners.push(() => el.removeEventListener(event, fn, options));
  };
  let engine = null;
  const api = {
    setScenario({ storm = state.storm, horizon = state.horizon } = {}) {
      state.storm = !!storm;
      state.horizon = Math.max(1, Number(horizon) || 72);
      wrap.classList.toggle("is-storm", state.storm);
      info.classList.toggle("storm", state.storm);
      info.textContent = state.storm
        ? "ШТОРМОВОЙ СЦЕНАРИЙ / МОДЕЛЬ"
        : "КАСПИЙ / ПРОСТРАНСТВЕННАЯ МОДЕЛЬ";
      engine?.scenario?.();
    },
    setRoute(route) {
      state.route = routeDefinition(route);
      engine?.route();
      for (const b of routeKey.children)
        b.setAttribute(
          "aria-pressed",
          String(b.dataset.route === state.route.id),
        );
    },
    setProgress(p) {
      state.progress = clamp(Number(p) || 0);
      state.manualProgress = true;
      engine?.progress?.();
    },
    setStoryProgress(p) {
      state.story = clamp(Number(p) || 0);
      state.storyControlled = true;
      engine?.story?.();
    },
    focusPort(id) {
      wrap
        .querySelectorAll(".caspian-port")
        .forEach((b) => b.classList.toggle("selected", b.dataset.port === id));
      engine?.focus(id);
    },
    resetView() {
      engine?.reset();
    },
    resize() {
      engine?.resize();
    },
    dispose() {
      state.disposed = true;
      engine?.dispose();
      for (const off of listeners) off();
      observer?.disconnect();
      resizeObserver?.disconnect();
      wrap.remove();
    },
  };
  for (const [title, text, fn] of [
    ["Увеличить карту", "+", () => engine?.zoom(0.86)],
    ["Уменьшить карту", "−", () => engine?.zoom(1.16)],
    ["Исходный ракурс", "↺", () => api.resetView()],
  ]) {
    const b = document.createElement("button");
    b.type = "button";
    b.title = title;
    b.setAttribute("aria-label", title);
    b.textContent = text;
    listen(b, "click", fn);
    controls.append(b);
  }
  const pause = document.createElement("button");
  pause.type = "button";
  pause.textContent = "Ⅱ";
  pause.title = "Приостановить движение";
  pause.setAttribute("aria-label", "Приостановить движение");
  pause.setAttribute("aria-pressed", "false");
  controls.prepend(pause);
  listen(pause, "click", () => {
    state.paused = !state.paused;
    pause.textContent = state.paused ? "▷" : "Ⅱ";
    pause.setAttribute("aria-pressed", String(state.paused));
    pause.setAttribute(
      "aria-label",
      state.paused ? "Продолжить движение" : "Приостановить движение",
    );
    engine?.wake?.();
  });
  for (const r of Object.values(ROUTES)) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = { R1: "Запад", R2: "Восток", R3: "Суша" }[r.id];
    b.dataset.route = r.id;
    b.title = r.id + " — " + r.name;
    b.setAttribute("aria-label", r.id + " — " + r.name);
    b.setAttribute("aria-pressed", String(r.id === "R1"));
    listen(b, "click", () => {
      api.setRoute(r.id);
      onRouteSelect(r.id);
    });
    routeKey.append(b);
  }
  let observer = null,
    resizeObserver = null;
  if ("IntersectionObserver" in window) {
    observer = new IntersectionObserver(
      ([entry]) => {
        state.visible = entry.isIntersecting;
        engine?.wake?.();
      },
      { threshold: 0.01 },
    );
    observer.observe(wrap);
  }
  listen(document, "visibilitychange", () => engine?.wake?.());
  const reduce = matchMedia("(prefers-reduced-motion: reduce)");
  listen(reduce, "change", (e) => {
    state.reduced = e.matches;
    engine?.wake?.();
  });
  try {
    const THREE = await import("./vendor/three/three.module.js");
    engine = createWebGL(THREE);
    wrap.dataset.renderer = "webgl";
  } catch (error) {
    wrap.querySelector("canvas")?.remove();
    wrap.querySelector(".caspian-label-layer")?.remove();
    engine = createFallback();
    wrap.dataset.renderer = "svg";
    info.textContent = "КАСПИЙ / КАРТА КОРИДОРА";
    console.info("Caspian: using accessible SVG map.", error?.message || error);
  }
  if ("ResizeObserver" in window) {
    resizeObserver = new ResizeObserver(() => engine.resize());
    resizeObserver.observe(wrap);
  } else listen(window, "resize", api.resize);
  engine.resize();
  engine.route();
  return api;

  function choosePort(id) {
    wrap
      .querySelectorAll(".caspian-port")
      .forEach((b) => b.classList.toggle("selected", b.dataset.port === id));
    engine?.focus(id);
    onPortSelect(id, NODES[id]);
  }
  function createFallback() {
    const svg = svgEl("svg", {
      viewBox: "-6 -7.3 12 14.6",
      role: "img",
      "aria-label": "Карта Каспийского моря и транспортных маршрутов",
    });
    svg.classList.add("caspian-fallback");
    wrap.prepend(svg);
    const land = svgEl("rect", {
      x: -5.7,
      y: -6.8,
      width: 11.4,
      height: 13.6,
      rx: 0.4,
      fill: "#bfcac0",
    });
    svg.append(land);
    const path = svgEl("path", {
      d: ring.map((p, i) => (i ? "L" : "M") + p.join(" ")).join(" ") + "Z",
      fill: "#103845",
      stroke: "#e7ede8",
      "stroke-width": ".03",
    });
    svg.append(path);
    for (const b of borders) {
      const pts = b.points.map(([lon, lat]) => project({ lon, lat }));
      svg.append(
        svgEl("polyline", {
          points: pts.map((p) => p.join(",")).join(" "),
          fill: "none",
          stroke: "#405b53",
          "stroke-width": ".015",
        }),
      );
    }
    const lines = svgEl("g"),
      dot = svgEl("circle", {
        r: 0.17,
        fill: "#8be3c3",
        stroke: "#f1f7ef",
        "stroke-width": ".07",
      });
    svg.append(lines, dot);
    for (const port of PORTS) {
      const [x, z] = project(port);
      const group = svgEl("g", {
        tabindex: 0,
        role: "button",
        "aria-label": port.name,
      });
      group.style.cursor = "pointer";
      group.append(
        svgEl("circle", { cx: x, cy: z, r: 0.065, fill: "#ff784f" }),
      );
      const t = svgEl("text", {
        x: x + (port.side === "left" ? -0.18 : 0.18),
        y: z - 0.1,
        fill: "#07131b",
        "font-size": ".18",
        "font-family": "sans-serif",
        "text-anchor": port.side === "left" ? "end" : "start",
      });
      t.textContent = port.name;
      group.append(t);
      listen(group, "click", () => choosePort(port.id));
      listen(group, "keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          choosePort(port.id);
        }
      });
      svg.append(group);
    }
    let legPaths = [],
      scale = 1;
    function progress() {
      const count = state.route.legs.length;
      const index = Math.min(count - 1, Math.floor(state.progress * count));
      const leg = legPaths[index];
      dot.style.display = leg ? "" : "none";
      if (!leg) return;
      // Same equal-duration leg indexing as WebGL; distance is normalized within
      // the active leg, independent of its number of inland geometry segments.
      const fraction = clamp(state.progress * count - index);
      let remaining = leg.total * fraction,
        segment = 0;
      while (
        segment < leg.lengths.length - 1 &&
        remaining > leg.lengths[segment]
      )
        remaining -= leg.lengths[segment++];
      const from = leg.points[segment],
        to = leg.points[segment + 1],
        t = leg.lengths[segment] ? clamp(remaining / leg.lengths[segment]) : 0;
      dot.setAttribute("cx", from[0] + (to[0] - from[0]) * t);
      dot.setAttribute("cy", from[1] + (to[1] - from[1]) * t);
    }
    function route() {
      lines.replaceChildren();
      const r = state.route;
      legPaths = new Array(r.legs.length);
      r.legs.forEach(([a, b, mode], index) => {
        const p = (r.lookup || NODES)[a],
          q = (r.lookup || NODES)[b];
        if (!p || !q) return;
        const clip = corridorPoints(p, q, mode);
        if (clip.length < 2) return;
        const points = clip.map((p) => p.join(",")).join(" "),
          dash = mode === "sea" ? "0" : ".2 .09";
        const underlay = svgEl("polyline", {
          points,
          fill: "none",
          stroke: "#07131b",
          "stroke-width": mode === "sea" ? ".17" : ".10",
          "stroke-linecap": "round",
          "stroke-linejoin": "round",
        });
        const line = svgEl("polyline", {
          points,
          fill: "none",
          stroke: state.storm && mode === "sea" ? "#ff784f" : r.color,
          "stroke-width": ".09",
          "stroke-linecap": "round",
          "stroke-linejoin": "round",
          "stroke-dasharray": dash,
        });
        lines.append(underlay, line);
        const lengths = clip
          .slice(1)
          .map((p, i) => Math.hypot(p[0] - clip[i][0], p[1] - clip[i][1]));
        legPaths[index] = {
          points: clip,
          lengths,
          total: lengths.reduce((sum, length) => sum + length, 0),
        };
      });
      progress();
    }
    return {
      route,
      progress,
      resize() {},
      focus() {},
      reset() {
        scale = 1;
        svg.setAttribute("viewBox", "-6 -7.3 12 14.6");
      },
      zoom(f) {
        scale = clamp(scale * f, 0.6, 1.6);
        svg.setAttribute(
          "viewBox",
          `${-6 * scale} ${-7.3 * scale} ${12 * scale} ${14.6 * scale}`,
        );
      },
      scenario() {
        path.setAttribute("fill", state.storm ? "#24414b" : "#103845");
        dot.setAttribute("fill", state.storm ? "#ff784f" : "#8be3c3");
        route();
      },
      dispose() {},
    };
  }

  function createWebGL(T) {
    const scene = new T.Scene();
    const renderer = new T.WebGLRenderer({
      antialias: true,
      alpha: true,
      powerPreference: "low-power",
    });
    renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 1.75));
    renderer.outputColorSpace = T.SRGBColorSpace;
    renderer.toneMapping = T.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.12;
    const canvas = renderer.domElement;
    canvas.tabIndex = 0;
    canvas.setAttribute("role", "img");
    canvas.setAttribute(
      "aria-label",
      "Интерактивная 3D-карта Каспия. Перетаскивайте для вращения. Стрелки вращают карту, плюс и минус меняют масштаб, Home сбрасывает ракурс.",
    );
    wrap.prepend(canvas);
    const camera = new T.PerspectiveCamera(35, 1, 0.1, 150);
    const orbit = {
        theta: -0.22,
        phi: 0.64,
        radius: 25,
        target: new T.Vector3(0, 0, 0.1),
        user: false,
      },
      desired = {
        theta: -0.22,
        phi: 0.64,
        radius: 25,
        target: new T.Vector3(0, 0, 0.1),
      };
    scene.add(new T.HemisphereLight(0xc4e7e2, 0x172e36, 2.5));
    const key = new T.DirectionalLight(0xfff5de, 3.4);
    key.position.set(-7, 14, 6);
    scene.add(key);
    const rim = new T.DirectionalLight(0x82dbcf, 1.6);
    rim.position.set(6, 7, -8);
    scene.add(rim);
    const plate = new T.Group();
    scene.add(plate);
    function shapeFrom(points) {
      const s = new T.Shape();
      points.forEach(([x, z], i) => (i ? s.lineTo(x, -z) : s.moveTo(x, -z)));
      s.closePath();
      return s;
    }
    const landShape = shapeFrom([
      [-5.65, -6.65],
      [5.65, -6.65],
      [5.65, 6.65],
      [-5.65, 6.65],
    ]);
    const hole = new T.Path();
    ring.forEach(([x, z], i) => (i ? hole.lineTo(x, -z) : hole.moveTo(x, -z)));
    hole.closePath();
    landShape.holes.push(hole);
    const landGeo = new T.ExtrudeGeometry(landShape, {
      depth: 0.38,
      bevelEnabled: true,
      bevelSize: 0.025,
      bevelThickness: 0.035,
      bevelSegments: 2,
      curveSegments: 1,
    });
    landGeo.rotateX(-Math.PI / 2);
    const land = new T.Mesh(landGeo, [
      new T.MeshStandardMaterial({
        color: 0xbdc8b9,
        roughness: 0.93,
        metalness: 0.08,
      }),
      new T.MeshStandardMaterial({ color: 0x52685f, roughness: 0.88 }),
    ]);
    plate.add(land);
    // Dark stratified plinth makes coastline elevation legible without claiming real relief.
    const baseGeo = new T.BoxGeometry(11.42, 0.18, 13.42);
    const base = new T.Mesh(
      baseGeo,
      new T.MeshStandardMaterial({
        color: 0x1b3339,
        roughness: 0.8,
        metalness: 0.35,
      }),
    );
    base.position.y = -0.14;
    plate.add(base);
    const baseEdges = new T.LineSegments(
      new T.EdgesGeometry(baseGeo),
      new T.LineBasicMaterial({
        color: 0x70958c,
        transparent: true,
        opacity: 0.24,
      }),
    );
    baseEdges.position.copy(base.position);
    plate.add(baseEdges);
    const seaShape = shapeFrom(ring),
      seaGeo = new T.ShapeGeometry(seaShape);
    seaGeo.rotateX(-Math.PI / 2);
    const waterUniforms = {
      uTime: { value: 0 },
      uStorm: { value: 0 },
      uProgress: { value: 0 },
    };
    const water = new T.Mesh(
      seaGeo,
      new T.ShaderMaterial({
        uniforms: waterUniforms,
        vertexShader: `varying vec3 vP;void main(){vP=position;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);}`,
        fragmentShader: `
      varying vec3 vP;uniform float uTime;uniform float uStorm;uniform float uProgress;
      void main(){vec2 p=vP.xz;float w=sin(p.x*13.+p.y*8.+uTime*.55)*sin(p.y*17.-uTime*.38);float stripe=pow(max(0.,sin(p.y*33.+p.x*12.+w*.65-uTime*(.4+uStorm))),15.);float glow=exp(-length((p-vec2(-1.,-.5))*vec2(.3,.13)));vec3 col=mix(vec3(.025,.13,.17),vec3(.075,.27,.30),glow);col+=vec3(.18,.35,.34)*stripe*(.04+uStorm*.11);col=mix(col,col*.72+vec3(.01,.02,.04),uStorm*.6);gl_FragColor=vec4(col,1.);}
    `,
      }),
    );
    water.position.y = 0.06;
    plate.add(water);
    const shore = new T.LineLoop(
      new T.BufferGeometry().setFromPoints(
        ring.map(([x, z]) => new T.Vector3(x, 0.415, z)),
      ),
      new T.LineBasicMaterial({
        color: 0xe7ede8,
        transparent: true,
        opacity: 0.8,
      }),
    );
    plate.add(shore);
    // Fine halftone land survey points and true country boundaries.
    const dots = [];
    for (let x = -5.4; x < 5.4; x += 0.16)
      for (let z = -6.4; z < 6.4; z += 0.16) {
        if (!inSea(x, z, ring)) dots.push(x, 0.425, z);
      }
    const dotGeo = new T.BufferGeometry();
    dotGeo.setAttribute("position", new T.Float32BufferAttribute(dots, 3));
    plate.add(
      new T.Points(
        dotGeo,
        new T.PointsMaterial({
          color: 0x3d6155,
          size: 0.017,
          transparent: true,
          opacity: 0.18,
          sizeAttenuation: true,
        }),
      ),
    );
    for (const border of borders) {
      const ps = border.points
        .map(([lon, lat]) => project({ lon, lat }))
        .filter(([x, z]) => Math.abs(x) < 5.6 && Math.abs(z) < 6.6);
      if (ps.length < 2) continue;
      const l = new T.Line(
        new T.BufferGeometry().setFromPoints(
          ps.map(([x, z]) => new T.Vector3(x, 0.43, z)),
        ),
        new T.LineDashedMaterial({
          color: 0x4e6b5d,
          dashSize: 0.07,
          gapSize: 0.05,
          transparent: true,
          opacity: 0.4,
        }),
      );
      l.computeLineDistances();
      plate.add(l);
    }
    // Decorative contours describe the raised model, not surveyed elevations.
    for (let level = 0; level < 13; level++) {
      let contour = [];
      const flush = () => {
        if (contour.length > 1)
          plate.add(
            new T.Line(
              new T.BufferGeometry().setFromPoints(contour),
              new T.LineBasicMaterial({
                color: 0x748d7b,
                transparent: true,
                opacity: 0.18,
              }),
            ),
          );
        contour = [];
      };
      for (let z = -6.5; z < 6.5; z += 0.09) {
        const x =
          -4.7 +
          level * 0.15 +
          0.22 * Math.sin(z * 1.3 + level * 0.23) +
          0.45 * Math.cos(z * 0.5);
        if (!inSea(x, z, ring)) contour.push(new T.Vector3(x, 0.435, z));
        else flush();
      }
      flush();
    }
    // Small survey marks around the floating plate, deliberately quiet.
    const marks = [];
    for (let z = -6; z <= 6; z += 0.5) {
      marks.push(
        new T.Vector3(-5.85, -0.12, z),
        new T.Vector3(-5.85 - (Number.isInteger(z) ? 0.16 : 0.07), -0.12, z),
      );
    }
    plate.add(
      new T.LineSegments(
        new T.BufferGeometry().setFromPoints(marks),
        new T.LineBasicMaterial({
          color: 0x7fa79b,
          transparent: true,
          opacity: 0.32,
        }),
      ),
    );
    const labelLayer = document.createElement("div");
    labelLayer.className = "caspian-label-layer";
    wrap.append(labelLayer);
    const anchors = [],
      portMeshes = [];
    for (const p of PORTS) {
      const [x, z] = project(p);
      const pos = new T.Vector3(x, 0.46, z);
      const group = new T.Group();
      group.position.copy(pos);
      const pedestal = new T.Mesh(
        new T.CylinderGeometry(0.095, 0.12, 0.12, 12),
        new T.MeshStandardMaterial({
          color: 0x344d49,
          metalness: 0.5,
          roughness: 0.4,
        }),
      );
      pedestal.position.y = 0.03;
      group.add(pedestal);
      const mast = new T.Mesh(
        new T.CylinderGeometry(0.012, 0.012, 0.35, 5),
        new T.MeshBasicMaterial({ color: 0xf5e6cd }),
      );
      mast.position.y = 0.23;
      group.add(mast);
      const beacon = new T.Mesh(
        new T.SphereGeometry(0.055, 10, 8),
        new T.MeshBasicMaterial({
          color: p.id === "makhachkala" ? 0xff784f : 0x8be3c3,
        }),
      );
      beacon.position.y = 0.42;
      group.add(beacon);
      const halo = new T.Mesh(
        new T.RingGeometry(0.11, 0.125, 36),
        new T.MeshBasicMaterial({
          color: 0x8be3c3,
          side: T.DoubleSide,
          transparent: true,
          opacity: 0.6,
        }),
      );
      halo.rotation.x = -Math.PI / 2;
      halo.position.y = 0.11;
      group.add(halo);
      plate.add(group);
      portMeshes.push({ id: p.id, group, halo, beacon });
      const button = document.createElement("button");
      button.type = "button";
      button.className = "caspian-port " + (p.side === "left" ? "left" : "");
      button.textContent = p.name;
      button.dataset.port = p.id;
      button.setAttribute("aria-label", "Порт " + p.name);
      listen(button, "click", () => choosePort(p.id));
      labelLayer.append(button);
      anchors.push({
        el: button,
        point: new T.Vector3(x, 0.9, z),
        side: p.side,
        port: p.id,
      });
    }
    for (const [name, lon, lat] of [
      ["РОССИЯ", 46.0, 45.8],
      ["КАЗАХСТАН", 54.45, 45.6],
      ["АЗЕРБАЙДЖАН", 46.8, 40.0],
      ["ТУРКМЕНИСТАН", 55.7, 39.4],
      ["ИРАН", 51.5, 35.6],
    ]) {
      const p = project({ lon, lat }),
        el = document.createElement("span");
      el.className = "caspian-country";
      el.textContent = name;
      labelLayer.append(el);
      anchors.push({
        el,
        point: new T.Vector3(p[0], 0.46, p[1]),
        country: true,
      });
    }
    const seaLabel = document.createElement("span");
    seaLabel.className = "caspian-water-label";
    seaLabel.textContent = "Каспийское море";
    labelLayer.append(seaLabel);
    anchors.push({
      el: seaLabel,
      point: new T.Vector3(0.65, 0.1, 1.25),
      country: true,
    });
    const cargoLabel = document.createElement("span");
    cargoLabel.className = "caspian-cargo-label";
    cargoLabel.textContent = "Груз";
    labelLayer.append(cargoLabel);
    const cargoAnchor = {
      el: cargoLabel,
      point: new T.Vector3(),
      side: "right",
      cargo: true,
      hidden: true,
    };
    anchors.push(cargoAnchor);
    let routeGroup = new T.Group();
    plate.add(routeGroup);
    let activeCurves = [],
      vehicles = [],
      routeMaterials = [];
    const ghostGroup = new T.Group();
    plate.add(ghostGroup);
    function curveFor(a, b, mode) {
      const clipped = corridorPoints(a, b, mode);
      if (clipped.length < 2) return null;
      const p = clipped[0],
        q = clipped.at(-1);
      const height = mode === "sea" ? 0.19 : 0.47;
      if (clipped.length > 2)
        return new T.CatmullRomCurve3(
          clipped.map(([x, z]) => new T.Vector3(x, height, z)),
          false,
          "centripetal",
          0.2,
        );
      const bend = mode === "sea" && Math.abs(a.lat - b.lat) > 4 ? 0.65 : 0;
      return new T.QuadraticBezierCurve3(
        new T.Vector3(p[0], height, p[1]),
        new T.Vector3(
          (p[0] + q[0]) / 2 + bend,
          height + (mode === "sea" ? 0.16 : 0),
          (p[1] + q[1]) / 2,
        ),
        new T.Vector3(q[0], height, q[1]),
      );
    }
    for (const r of Object.values(ROUTES))
      for (const [a, b, mode] of r.legs) {
        const curve = curveFor(NODES[a], NODES[b], mode);
        if (!curve) continue;
        const l = new T.Line(
          new T.BufferGeometry().setFromPoints(curve.getPoints(64)),
          new T.LineDashedMaterial({
            color: 0x77968e,
            transparent: true,
            opacity: 0.1,
            dashSize: 0.075,
            gapSize: 0.14,
          }),
        );
        l.computeLineDistances();
        ghostGroup.add(l);
      }
    function cargoShip(color, mode) {
      const g = new T.Group();
      if (mode === "sea") {
        const hull = new T.Mesh(
          new T.CylinderGeometry(0.08, 0.045, 0.43, 4),
          new T.MeshStandardMaterial({
            color: 0xd2e6df,
            roughness: 0.5,
            metalness: 0.25,
          }),
        );
        hull.rotation.x = Math.PI / 2;
        hull.rotation.z = Math.PI / 4;
        g.add(hull);
        const deck = new T.Mesh(
          new T.BoxGeometry(0.115, 0.04, 0.27),
          new T.MeshStandardMaterial({ color: 0x213f43 }),
        );
        deck.position.y = 0.055;
        g.add(deck);
        for (let row = 0; row < 3; row++) {
          const c = new T.Mesh(
            new T.BoxGeometry(0.085, 0.055, 0.06),
            new T.MeshStandardMaterial({
              color: row === 1 ? 0x8be3c3 : 0xff784f,
              roughness: 0.7,
            }),
          );
          c.position.set(0, 0.095, -0.07 + row * 0.075);
          g.add(c);
        }
        const bridge = new T.Mesh(
          new T.BoxGeometry(0.11, 0.11, 0.07),
          new T.MeshStandardMaterial({ color: 0xe7ede8 }),
        );
        bridge.position.set(0, 0.105, 0.15);
        g.add(bridge);
        const wakeGeo = new T.BufferGeometry().setFromPoints([
          new T.Vector3(-0.05, -0.03, 0.25),
          new T.Vector3(-0.19, -0.03, 0.8),
          new T.Vector3(0.05, -0.03, 0.25),
          new T.Vector3(0.19, -0.03, 0.8),
        ]);
        g.add(
          new T.LineSegments(
            wakeGeo,
            new T.LineBasicMaterial({
              color: 0xa5e5d1,
              transparent: true,
              opacity: 0.5,
            }),
          ),
        );
      } else {
        for (let i = 0; i < 3; i++) {
          const wagon = new T.Mesh(
            new T.BoxGeometry(0.07, 0.065, 0.14),
            new T.MeshStandardMaterial({ color: i ? 0xbfcfc6 : 0xff784f }),
          );
          wagon.position.set(0, 0.04, i * 0.18);
          g.add(wagon);
        }
      }
      const disk = new T.Mesh(
        new T.CircleGeometry(0.22, 32),
        new T.MeshBasicMaterial({ color: 0x07131b, depthWrite: false }),
      );
      disk.rotation.x = -Math.PI / 2;
      disk.position.y = -0.03;
      g.add(disk);
      const marker = new T.Mesh(
        new T.RingGeometry(0.2, 0.26, 40),
        new T.MeshBasicMaterial({
          color: state.storm ? 0xff784f : 0x8be3c3,
          side: T.DoubleSide,
          depthWrite: false,
        }),
      );
      marker.rotation.x = -Math.PI / 2;
      marker.position.y = -0.018;
      g.add(marker);
      g.userData.markerMaterial = marker.material;
      const edge = new T.Mesh(
        new T.RingGeometry(0.265, 0.285, 40),
        new T.MeshBasicMaterial({
          color: 0xf4f8ec,
          side: T.DoubleSide,
          depthWrite: false,
        }),
      );
      edge.rotation.x = -Math.PI / 2;
      edge.position.y = -0.018;
      g.add(edge);
      return g;
    }
    function routeTube(curve, radius, dashed) {
      const length = curve.getLength();
      const geometry = new T.TubeGeometry(
        curve,
        Math.max(80, Math.ceil(length / 0.03)),
        radius,
        6,
        false,
      );
      if (dashed) {
        const uv = geometry.attributes.uv,
          index = geometry.index.array,
          visible = [];
        for (let i = 0; i < index.length; i += 3) {
          const along =
            ((uv.getX(index[i]) +
              uv.getX(index[i + 1]) +
              uv.getX(index[i + 2])) /
              3) *
            length;
          if (along % 0.27 < 0.18)
            visible.push(index[i], index[i + 1], index[i + 2]);
        }
        geometry.setIndex(visible);
      }
      return geometry;
    }
    function disposeGroup(group) {
      group.traverse((o) => {
        o.geometry?.dispose();
        if (o.material)
          (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) =>
            m.dispose(),
          );
      });
    }
    function drawRoute() {
      disposeGroup(routeGroup);
      plate.remove(routeGroup);
      routeGroup = new T.Group();
      plate.add(routeGroup);
      activeCurves = [];
      vehicles = [];
      routeMaterials = [];
      const r = state.route,
        lookup = r.lookup || NODES;
      for (const [legIndex, [a, b, mode]] of r.legs.entries()) {
        if (!lookup[a] || !lookup[b]) continue;
        const curve = curveFor(lookup[a], lookup[b], mode);
        if (!curve) continue;
        const underlay = new T.Mesh(
          routeTube(curve, mode === "sea" ? 0.085 : 0.04, false),
          new T.MeshBasicMaterial({ color: 0x07131b, depthWrite: false }),
        );
        underlay.renderOrder = 1;
        routeGroup.add(underlay);
        const material = new T.MeshBasicMaterial({
          color: state.storm && mode === "sea" ? 0xff784f : r.color,
          depthWrite: false,
        });
        const line = new T.Mesh(
          routeTube(curve, mode === "sea" ? 0.045 : 0.032, mode !== "sea"),
          material,
        );
        line.renderOrder = 2;
        routeGroup.add(line);
        routeMaterials.push({ material, mode, color: r.color });
        const ship = cargoShip(r.color, mode);
        ship.scale.setScalar(mode === "sea" ? 1.7 : 1.35);
        routeGroup.add(ship);
        activeCurves.push(curve);
        vehicles.push({
          ship,
          curve,
          mode,
          legIndex,
          point: new T.Vector3(),
          tangent: new T.Vector3(),
          phase: vehicles.length * 0.3,
        });
      }
      const routePorts = new Set(r.legs.flatMap((l) => l.slice(0, 2)));
      for (const p of portMeshes) {
        p.beacon.material.color.set(routePorts.has(p.id) ? 0xff784f : 0x8be3c3);
        p.halo.material.opacity = routePorts.has(p.id) ? 0.8 : 0.3;
      }
      for (const button of labelLayer.querySelectorAll(".caspian-port"))
        button.classList.toggle(
          "on-route",
          routePorts.has(button.dataset.port),
        );
      wake();
    }
    const stormGroup = new T.Group();
    plate.add(stormGroup);
    stormGroup.visible = false;
    const stormCenter = project({ lat: 41.3, lon: 50.6 });
    for (let k = 0; k < 4; k++) {
      const pts = [];
      for (let i = 0; i <= 100; i++) {
        const a = (i / 100) * Math.PI * 2,
          r = 0.4 + k * 0.3;
        pts.push(
          new T.Vector3(
            stormCenter[0] + Math.cos(a) * r,
            0.22,
            stormCenter[1] + Math.sin(a) * r * 0.7,
          ),
        );
      }
      stormGroup.add(
        new T.Line(
          new T.BufferGeometry().setFromPoints(pts),
          new T.LineDashedMaterial({
            color: 0xff784f,
            transparent: true,
            opacity: 0.16 + k * 0.05,
            dashSize: 0.06,
            gapSize: 0.06,
          }),
        ),
      );
      stormGroup.children.at(-1).computeLineDistances();
    }
    const rain = new Float32Array(240 * 6);
    for (let i = 0; i < 240; i++) {
      const x = stormCenter[0] + (Math.sin(i * 127.1) * 0.5 + 0.5) * 2.6 - 1.3,
        z = stormCenter[1] + (Math.sin(i * 311.7) * 0.5 + 0.5) * 2.2 - 1.1,
        y = 0.4 + ((i % 17) / 17) * 1.2;
      rain.set([x, y, z, x - 0.025, y - 0.13, z + 0.02], i * 6);
    }
    const rainGeo = new T.BufferGeometry();
    rainGeo.setAttribute("position", new T.BufferAttribute(rain, 3));
    const rainLines = new T.LineSegments(
      rainGeo,
      new T.LineBasicMaterial({
        color: 0xb6d7db,
        transparent: true,
        opacity: 0.3,
      }),
    );
    stormGroup.add(rainLines);
    let width = 1,
      height = 1,
      frame = 0,
      last = 0,
      time = 0,
      needFrame = true,
      contextLost = false;
    const projected = new T.Vector3();
    function fittedRadius() {
      return width / height < 0.85
        ? Math.max(29, (25 * 0.85) / (width / height))
        : 25;
    }
    function resize() {
      width = wrap.clientWidth || 600;
      height = wrap.clientHeight || 500;
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      if (!orbit.user) {
        desired.radius = fittedRadius();
        orbit.radius = desired.radius;
      }
      wake();
    }
    function positionLabels() {
      const boxes = [];
      // Prioritize selected route endpoints; suppress only colliding secondary labels.
      const important = new Set(state.route.legs.flatMap((l) => l.slice(0, 2)));
      for (const a of [...anchors].sort(
        (a, b) =>
          (b.cargo ? 2 : important.has(b.port) ? 1 : 0) -
          (a.cargo ? 2 : important.has(a.port) ? 1 : 0),
      )) {
        projected.copy(a.point).project(camera);
        const x = (projected.x * 0.5 + 0.5) * width,
          y = (-projected.y * 0.5 + 0.5) * height;
        const ew = a.el.offsetWidth || 80,
          eh = a.el.offsetHeight || 20;
        let px = a.country
            ? x - ew / 2
            : a.side === "left"
              ? x - ew - 15
              : x + 15,
          py = y - eh / 2;
        const outside =
          a.hidden ||
          projected.z > 1 ||
          x < 5 ||
          x > width - 5 ||
          y < 48 ||
          y > height - (width < 520 ? 130 : 86);
        const box = [px, py, px + ew, py + eh];
        const collides =
          !a.country &&
          boxes.some(
            (b) =>
              box[0] < b[2] + 3 &&
              box[2] > b[0] - 3 &&
              box[1] < b[3] + 3 &&
              box[3] > b[1] - 3,
          );
        a.el.style.visibility = outside || collides ? "hidden" : "visible";
        a.el.style.transform = `translate3d(${px}px,${py}px,0)`;
        if (!outside && !collides && !a.country) boxes.push(box);
      }
    }
    function render(now) {
      frame = 0;
      if (state.disposed || contextLost || !state.visible || document.hidden)
        return;
      const dt = Math.min((now - last) / 1000 || 0, 0.05);
      last = now;
      if (!state.reduced && !state.paused) time += dt;
      waterUniforms.uTime.value = time;
      waterUniforms.uStorm.value +=
        (Number(state.storm) - waterUniforms.uStorm.value) * 0.07;
      stormGroup.visible = state.storm;
      if (state.storm && !state.reduced) {
        rainLines.position.y = -((time * 0.65) % 1);
        stormGroup.children
          .slice(0, 4)
          .forEach(
            (l, i) => (l.material.opacity = 0.12 + 0.08 * Math.sin(time + i)),
          );
      }
      const smooth = state.reduced ? 1 : 0.085;
      orbit.theta += (desired.theta - orbit.theta) * smooth;
      orbit.phi += (desired.phi - orbit.phi) * smooth;
      orbit.radius += (desired.radius - orbit.radius) * smooth;
      orbit.target.lerp(desired.target, smooth);
      camera.position.set(
        orbit.target.x +
          Math.sin(orbit.theta) * Math.sin(orbit.phi) * orbit.radius,
        orbit.target.y + Math.cos(orbit.phi) * orbit.radius,
        orbit.target.z +
          Math.cos(orbit.theta) * Math.sin(orbit.phi) * orbit.radius,
      );
      camera.lookAt(orbit.target);
      camera.updateMatrixWorld();
      vehicles.forEach((v, i) => {
        const count = state.route.legs.length;
        const p = state.manualProgress
          ? clamp(state.progress * count - v.legIndex)
          : state.storyControlled
            ? clamp((0.22 + state.story * 0.65) * count - v.legIndex)
            : (time / (state.storm ? 32 : 24) + 0.28 + i * 0.22) % 1;
        v.ship.visible =
          !state.manualProgress ||
          v.legIndex ===
            Math.min(count - 1, Math.floor(state.progress * count));
        const at = v.curve.getPointAt(p, v.point),
          t = v.curve.getTangentAt(p, v.tangent);
        v.ship.position.copy(at);
        v.ship.position.y +=
          v.mode === "sea" && !state.reduced
            ? Math.sin(time * 1.8 + i) * 0.012
            : 0;
        v.ship.rotation.y = Math.atan2(-t.x, -t.z);
        if (v.mode === "sea" && state.storm && !state.reduced)
          v.ship.rotation.z = Math.sin(time * 2.2) * 0.035;
        else v.ship.rotation.z = 0;
      });
      const tracked =
        vehicles.find(
          (v) => v.ship.visible && (state.manualProgress || v.mode === "sea"),
        ) || vehicles.find((v) => v.ship.visible);
      cargoAnchor.hidden = !tracked;
      if (tracked) {
        cargoAnchor.point.copy(tracked.ship.position);
        cargoAnchor.point.y += 0.45;
      }
      for (const p of portMeshes) {
        p.halo.scale.setScalar(
          state.reduced ? 1 : 1 + 0.08 * Math.sin(time * 1.7),
        );
      }
      renderer.render(scene, camera);
      positionLabels();
      const moving =
        Math.abs(orbit.theta - desired.theta) +
          Math.abs(orbit.radius - desired.radius) +
          Math.abs(orbit.phi - desired.phi) +
          orbit.target.distanceTo(desired.target) >
        0.001;
      if ((!state.reduced && !state.paused) || moving || needFrame) {
        needFrame = false;
        frame = requestAnimationFrame(render);
      }
    }
    function wake() {
      needFrame = true;
      if (
        !frame &&
        !contextLost &&
        !state.disposed &&
        state.visible &&
        !document.hidden
      ) {
        last = performance.now();
        frame = requestAnimationFrame(render);
      }
    }
    let pointer = null;
    listen(canvas, "pointerdown", (e) => {
      if (e.pointerType === "touch" && !e.isPrimary) return;
      pointer = {
        x: e.clientX,
        y: e.clientY,
        theta: desired.theta,
        phi: desired.phi,
        moved: false,
      };
      canvas.setPointerCapture(e.pointerId);
      wrap.classList.add("is-dragging");
    });
    listen(canvas, "pointermove", (e) => {
      if (!pointer) return;
      const dx = e.clientX - pointer.x,
        dy = e.clientY - pointer.y;
      if (Math.abs(dx) + Math.abs(dy) > 4) {
        pointer.moved = true;
        orbit.user = true;
      }
      desired.theta = pointer.theta - dx * 0.005;
      desired.phi = clamp(pointer.phi - dy * 0.004, 0.27, 1.12);
      wake();
    });
    const endPointer = () => {
      pointer = null;
      wrap.classList.remove("is-dragging");
    };
    listen(canvas, "pointerup", endPointer);
    listen(canvas, "pointercancel", endPointer);
    // Preserve normal page scrolling. Wheel zoom requires Ctrl / Cmd or desk mode.
    listen(
      canvas,
      "wheel",
      (e) => {
        if (mode === "hero" && !e.ctrlKey && !e.metaKey) return;
        e.preventDefault();
        zoom(Math.exp(e.deltaY * 0.001));
      },
      { passive: false },
    );
    listen(canvas, "keydown", (e) => {
      const step = 0.08;
      let handled = true;
      if (e.key === "ArrowLeft") desired.theta -= step;
      else if (e.key === "ArrowRight") desired.theta += step;
      else if (e.key === "ArrowUp")
        desired.phi = clamp(desired.phi - step, 0.27, 1.12);
      else if (e.key === "ArrowDown")
        desired.phi = clamp(desired.phi + step, 0.27, 1.12);
      else if (e.key === "+" || e.key === "=") zoom(0.9);
      else if (e.key === "-") zoom(1.1);
      else if (e.key === "Home") reset();
      else handled = false;
      if (handled) {
        e.preventDefault();
        orbit.user = true;
        wake();
      }
    });
    function zoom(f) {
      orbit.user = true;
      desired.radius = clamp(desired.radius * f, 10, 42);
      wake();
    }
    function reset() {
      orbit.user = false;
      desired.theta = -0.22;
      desired.phi = 0.64;
      desired.radius = fittedRadius();
      desired.target.set(0, 0, 0.1);
      wake();
    }
    function focus(id) {
      const p = NODES[id];
      if (!p) return;
      const [x, z] = project(p);
      orbit.user = true;
      desired.target.set(x * 0.38, 0, z * 0.38);
      desired.radius = width / height < 0.85 ? 25 : 20;
      wake();
    }
    function story() {
      if (orbit.user || state.reduced) return;
      desired.theta = -0.22 + state.story * 0.32;
      desired.phi = 0.64 - state.story * 0.12;
      desired.target.set(0, 0, 0.1 + state.story * 0.6);
      wake();
    }
    listen(canvas, "webglcontextlost", (e) => {
      e.preventDefault();
      contextLost = true;
      if (frame) cancelAnimationFrame(frame);
      frame = 0;
      info.textContent = "ВОССТАНОВЛЕНИЕ 3D-КАРТЫ";
    });
    listen(canvas, "webglcontextrestored", () => {
      contextLost = false;
      info.textContent = state.storm
        ? "ШТОРМОВОЙ СЦЕНАРИЙ / МОДЕЛЬ"
        : "КАСПИЙ / ПРОСТРАНСТВЕННАЯ МОДЕЛЬ";
      wake();
    });
    function scenario() {
      for (const { material, mode, color } of routeMaterials)
        material.color.set(state.storm && mode === "sea" ? 0xff784f : color);
      for (const { ship } of vehicles)
        ship.userData.markerMaterial.color.set(state.storm ? 0xff784f : 0x8be3c3);
      wake();
    }
    return {
      route: drawRoute,
      resize,
      focus,
      zoom,
      reset,
      story,
      progress: wake,
      scenario,
      wake,
      dispose() {
        if (frame) cancelAnimationFrame(frame);
        disposeGroup(scene);
        renderer.dispose();
        renderer.forceContextLoss();
      },
    };
  }
}
