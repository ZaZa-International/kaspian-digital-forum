import { CASPIAN_NODES, normalizeCaspianRoute } from "./caspian-scene.js";

let leafletPromise;
const LATLNG = (node) => [Number(node.lat), Number(node.lon)];
const VALID = (node) => node && Number.isFinite(Number(node.lat)) && Number.isFinite(Number(node.lon));
const MODE_COLOR = { rail: "#185c48", sea: "#2855b7", road: "#ad4829" };
const MODE_NAME = { rail: "Ж/д", sea: "Море", road: "Авто" };
const SEA_BOUNDS = [[35.5, 45.6], [47.6, 55.5]];

function loadLeaflet() {
  if (window.L?.map) return Promise.resolve(window.L);
  if (leafletPromise) return leafletPromise;
  leafletPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "/static/vendor/leaflet.js";
    script.onload = () => window.L?.map ? resolve(window.L) : reject(new Error("Leaflet не загружен"));
    script.onerror = () => { script.remove(); leafletPromise = null; reject(new Error("Leaflet недоступен")); };
    document.head.append(script);
  });
  return leafletPromise;
}

function css() {
  if (document.querySelector('link[href="/static/vendor/leaflet.css"]')) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = "/static/vendor/leaflet.css";
  document.head.append(link);
}

function text(value, className = "") {
  const element = document.createElement("span");
  element.textContent = value;
  element.className = className;
  return element;
}

/** Local geographic base map: no remote tiles, tracking, API token, or network basemap dependency. */
export async function createFlatMap(container, { onPortSelect = () => {} } = {}) {
  css();
  const L = await loadLeaflet();
  const wrapper = document.createElement("div");
  wrapper.className = "flat-map-wrapper";
  wrapper.dataset.renderer = "leaflet";
  const viewport = document.createElement("div");
  viewport.className = "flat-map-canvas";
  viewport.setAttribute("aria-label", "Географическая карта Каспия. Стрелки перемещают карту, плюс и минус изменяют масштаб.");
  const caption = document.createElement("div");
  caption.className = "flat-map-caption";
  caption.append(text("Схема маршрутов · не GPS/AIS"));
  const source = text("Natural Earth · локальная география");
  caption.append(source);
  const tools = document.createElement("div");
  tools.className = "flat-map-tools";
  const seaButton = document.createElement("button");
  seaButton.type = "button";
  seaButton.textContent = "Каспий";
  seaButton.title = "Показать Каспийское море";
  const routeButton = document.createElement("button");
  routeButton.type = "button";
  routeButton.textContent = "Весь маршрут";
  tools.append(seaButton, routeButton);
  const scenario = document.createElement("p");
  scenario.className = "flat-map-scenario";
  scenario.hidden = true;
  const legend = document.createElement("div");
  legend.className = "flat-map-legend";
  for (const [mode, label] of Object.entries(MODE_NAME)) {
    const item = text(label);
    item.dataset.mode = mode;
    legend.append(item);
  }
  wrapper.append(viewport, tools, scenario, legend, caption);
  container.append(wrapper);
  const map = L.map(viewport, {
    zoomControl: false, attributionControl: false, minZoom: 3, maxZoom: 10,
    zoomSnap: 0.25, scrollWheelZoom: true, doubleClickZoom: true,
    keyboard: true, zoomAnimation: false, fadeAnimation: false, markerZoomAnimation: false,
    maxBounds: [[15, 20], [65, 75]], maxBoundsViscosity: 0.8,
  });
  L.control.zoom({ position: "bottomright", zoomInTitle: "Увеличить карту", zoomOutTitle: "Уменьшить карту" }).addTo(map);
  L.control.scale({ imperial: false, position: "bottomleft", maxWidth: 100 }).addTo(map);
  const geography = L.layerGroup().addTo(map);
  const routeLayer = L.layerGroup().addTo(map);
  const portLayer = L.layerGroup().addTo(map);
  const cargoLayer = L.layerGroup().addTo(map);
  const markers = new Map();
  const state = { route: normalizeCaspianRoute("R1"), progress: 0, storm: false, disposed: false, routeSignature: null };
  let routePoints = [], validLegs = [], resizeObserver;

  // Graticule and country labels keep navigation legible without online tiles.
  for (let lon = 25; lon <= 75; lon += 5)
    L.polyline([[15, lon], [65, lon]], { color: "#708d80", weight: 0.6, opacity: 0.22, interactive: false }).addTo(geography);
  for (let lat = 20; lat <= 65; lat += 5)
    L.polyline([[lat, 20], [lat, 75]], { color: "#708d80", weight: 0.6, opacity: 0.22, interactive: false }).addTo(geography);
  for (const [name, position] of [["РОССИЯ", [49.3, 43]], ["КАЗАХСТАН", [47.8, 57]], ["АЗЕРБАЙДЖАН", [40.4, 46.7]], ["ТУРКМЕНИСТАН", [39.7, 56.6]], ["ИРАН", [33.8, 51.8]]]) {
    L.marker(position, { keyboard: false, interactive: false, icon: L.divIcon({ className: "flat-country", html: text(name), iconSize: [140, 20], iconAnchor: [70, 10] }) }).addTo(geography);
  }
  map.fitBounds(SEA_BOUNDS, { animate: false, padding: [32, 45] });

  const geoRequest = new AbortController();
  const geoTimeout = setTimeout(() => geoRequest.abort(), 10000);
  try {
    const [coastResponse, bordersResponse] = await Promise.all([
      fetch("/static/assets/geo/caspian.json", { signal: geoRequest.signal }),
      fetch("/static/assets/geo/borders.json", { signal: geoRequest.signal }),
    ]);
    if (!coastResponse.ok || !bordersResponse.ok) throw new Error("География недоступна");
    const [coast, borders] = await Promise.all([coastResponse.json(), bordersResponse.json()]);
    L.geoJSON(coast, { style: { color: "#4d7e83", fillColor: "#a2c9ca", weight: 1.5, fillOpacity: 1 }, interactive: false }).addTo(geography);
    for (const line of borders.lines || []) {
      L.polyline(line.points.map(([lon, lat]) => [lat, lon]), { color: "#829e8d", weight: 1, opacity: 0.65, interactive: false }).addTo(geography);
    }
    L.marker([41.8, 51.1], { keyboard: false, interactive: false, icon: L.divIcon({ className: "flat-sea-name", html: text("КАСПИЙСКОЕ МОРЕ"), iconSize: [150, 24], iconAnchor: [75, 12] }) }).addTo(geography);
  } catch {
    source.textContent = "Береговая линия недоступна · координатная схема";
  } finally { clearTimeout(geoTimeout); }

  function fitRoute() {
    if (state.disposed) return;
    if (routePoints.length) map.fitBounds(routePoints, { animate: false, paddingTopLeft: [92, 112], paddingBottomRight: [92, 65], maxZoom: 7 });
    else map.fitBounds(SEA_BOUNDS, { animate: false, padding: [32, 45] });
  }

  function selectedPort(id, notify = false) {
    for (const [key, marker] of markers) marker.getElement()?.classList.toggle("selected", key === id);
    const marker = markers.get(id);
    if (marker) { marker.openTooltip(); map.panTo(marker.getLatLng(), { animate: false }); }
    if (notify) onPortSelect(id, (state.route.lookup || {})[id] || CASPIAN_NODES[id]);
  }

  function drawPorts() {
    portLayer.clearLayers();
    markers.clear();
    const lookup = { ...CASPIAN_NODES, ...state.route.lookup };
    const routeIds = new Set(validLegs.flatMap(([from, to]) => [from, to]));
    for (const node of Object.values(lookup).filter(VALID)) {
      const dot = document.createElement("span");
      dot.className = "flat-port-dot";
      const onRoute = routeIds.has(node.id);
      const marker = L.marker(LATLNG(node), {
        keyboard: true, title: node.name || node.id, alt: node.name || node.id,
        icon: L.divIcon({ className: `flat-port-marker${onRoute ? " on-route" : ""}`, html: dot, iconSize: [30, 30], iconAnchor: [15, 15] }),
      }).addTo(portLayer);
      marker.bindTooltip(text(node.name || node.id), { permanent: onRoute, direction: Number(node.lon) < 50 ? "left" : "right", offset: [Number(node.lon) < 50 ? -7 : 7, 0], className: "flat-port-label" });
      marker.on("click", () => selectedPort(node.id, true));
      const element = marker.getElement();
      element?.setAttribute("aria-label", `${node.name || node.id} · открыть узел`);
      if (element) element.dataset.port = node.id;
      markers.set(node.id, marker);
    }
  }

  function drawCargo() {
    cargoLayer.clearLayers();
    wrapper.dataset.progress = String(state.progress);
    if (!validLegs.length) return;
    const lookup = { ...CASPIAN_NODES, ...state.route.lookup };
    const position = Math.min(state.progress * validLegs.length, validLegs.length - Number.EPSILON);
    const index = Math.min(validLegs.length - 1, Math.floor(position));
    const fraction = state.progress >= 1 ? 1 : position - index;
    const [from, to] = validLegs[index];
    const a = lookup[from], b = lookup[to];
    const point = [a.lat + (b.lat - a.lat) * fraction, a.lon + (b.lon - a.lon) * fraction];
    const icon = text("ГРУЗ", "flat-cargo-dot");
    const cargo = L.marker(point, { keyboard: false, interactive: false, zIndexOffset: 1000, icon: L.divIcon({ className: "flat-cargo", html: icon, iconSize: [48, 26], iconAnchor: [24, 34] }) }).addTo(cargoLayer);
    cargo.getElement()?.setAttribute("aria-label", "Расчётное положение груза");
  }

  function drawRoute() {
    routeLayer.clearLayers();
    const lookup = { ...CASPIAN_NODES, ...state.route.lookup };
    validLegs = (state.route.legs || []).filter(([from, to]) => VALID(lookup[from]) && VALID(lookup[to]));
    routePoints = validLegs.flatMap(([from, to]) => [LATLNG(lookup[from]), LATLNG(lookup[to])]);
    for (const [from, to, mode] of validLegs) {
      const color = mode === "sea" && state.storm ? "#b74b2b" : MODE_COLOR[mode] || MODE_COLOR.rail;
      L.polyline([LATLNG(lookup[from]), LATLNG(lookup[to])], { color: "#f7faf4", weight: 7, opacity: 0.9, interactive: false }).addTo(routeLayer);
      L.polyline([LATLNG(lookup[from]), LATLNG(lookup[to])], { color, weight: 3.5, opacity: 1, dashArray: mode === "sea" ? "8 6" : mode === "road" ? "2 6" : null, interactive: false }).addTo(routeLayer);
    }
    drawPorts();
    drawCargo();
  }

  seaButton.addEventListener("click", () => map.fitBounds(SEA_BOUNDS, { animate: false, padding: [32, 45] }));
  routeButton.addEventListener("click", fitRoute);
  const api = {
    setRoute(route) {
      state.route = normalizeCaspianRoute(route);
      const signature = JSON.stringify(state.route.legs);
      const changed = state.routeSignature !== signature;
      state.routeSignature = signature;
      drawRoute();
      if (changed) fitRoute();
    },
    setProgress(progress) { state.progress = Math.max(0, Math.min(1, Number(progress) || 0)); drawCargo(); },
    setScenario({ storm = state.storm } = {}) {
      state.storm = Boolean(storm);
      wrapper.classList.toggle("is-storm", state.storm);
      scenario.hidden = !state.storm;
      scenario.textContent = "Сценарий шторма · морские плечи под риском";
      drawRoute();
    },
    setStoryProgress() {},
    focusPort(id) { selectedPort(id); },
    resetView: fitRoute,
    resize() { if (!state.disposed) map.invalidateSize({ animate: false, pan: false }); },
    dispose() { state.disposed = true; resizeObserver?.disconnect(); geoRequest.abort(); map.remove(); wrapper.remove(); },
  };
  if ("ResizeObserver" in window) {
    resizeObserver = new ResizeObserver(api.resize);
    resizeObserver.observe(viewport);
  }
  return api;
}
