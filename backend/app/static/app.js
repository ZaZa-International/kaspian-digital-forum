/* Smart Caspian Transit frontend: map + compare + shipment twin + clearance.
   Design contract: docs/DESIGN_CONTRACT.md («Caspian Ops»).
   All dynamic HTML is escaped via esc(); colors come from CSS tokens. */
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const $ = (s) => document.querySelector(s);
const icon = (name) => `<svg class="ic" aria-hidden="true"><use href="#i-${name}"></use></svg>`;
let storm = false;
let scenario = { close: "", extra_customs_h: 0, wave_m: null, wind_ms: null };
let map, polylines = {};
let searchLayer = null, lastPaths = [], nodeCache = {};
let shipments = [];
let currentShipment = null;
const TOKENS = {};
const MODE_RU = { rail: `${icon("train")} ж/д`, sea: `${icon("ship")} море`, road: `${icon("truck")} авто` };
let ROUTE_COLORS = { R1: "#2563EB", R2: "#0F8A5F", R3: "#EA580C" };
const fmtDT = (iso) => String(iso || "").slice(0, 16).replace("T", " ");

function readTokens() {
  const cs = getComputedStyle(document.documentElement);
  ["--color-primary", "--color-signal", "--color-danger", "--color-ok", "--color-ink",
   "--color-ink-2", "--color-ink-3", "--color-line-soft",
   "--route-r1", "--route-r2", "--route-r3"].forEach((k) => { TOKENS[k] = cs.getPropertyValue(k).trim(); });
  ROUTE_COLORS = { R1: TOKENS["--route-r1"], R2: TOKENS["--route-r2"], R3: TOKENS["--route-r3"] };
}

function setBanner(text) {
  const b = $("#offline-banner");
  if (text) { b.textContent = text; b.hidden = false; } else { b.hidden = true; }
}
function updateOfflineBanner() {
  setBanner(navigator.onLine ? "" : "Офлайн: API недоступен — данные не обновляются, карта без тайлов.");
}
window.addEventListener("online", () => { updateOfflineBanner(); refreshAll().catch(() => {}); });
window.addEventListener("offline", updateOfflineBanner);

async function getJSON(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(url + " -> " + r.status);
  return r.json();
}

/* Busy state: block double submits, reserve layout, show spinner (CSS). */
async function withBusy(btn, fn) {
  if (!btn || btn.getAttribute("aria-busy") === "true") return;
  btn.setAttribute("aria-busy", "true");
  btn.disabled = true;
  try { await fn(); } finally { btn.removeAttribute("aria-busy"); btn.disabled = false; }
}

function riskPill(p) {
  const cls = p < 0.3 ? "ok" : p < 0.6 ? "warn" : "danger";
  const txt = p < 0.3 ? "низкий" : p < 0.6 ? "средний" : "высокий";
  return `<span class="chip chip-${cls}"><span class="dot"></span>${txt} ${Math.round(p * 100)}%</span>`;
}

/* ---- inline-SVG charts (no dependencies, offline-friendly) ---- */
const FEAT_RU = { max_wave_m: "Волна", max_wind_ms: "Ветер", max_congestion_pct: "Загруженность порта",
  total_customs_h: "Таможня", sea_km: "Морской путь", rail_km: "Ж/д путь", weight_t: "Вес", sea_legs: "Морских плеч" };
const hexToRgb = (h) => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
const co2Color = (v, lo, hi) => {
  const t = hi > lo ? (v - lo) / (hi - lo) : 0;
  const a = hexToRgb(TOKENS["--route-r2"] || "#0F8A5F"), b = hexToRgb(TOKENS["--color-danger"] || "#DC2626");
  const c = a.map((x, i) => Math.round(x + (b[i] - x) * t));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
};

function renderEtaDist(d) {
  const W = 720, H = 260, L = 48, R = 16, T = 30, B = 36, pw = W - L - R, ph = H - T - B;
  const lo = d.edges[0], hi = d.edges[d.edges.length - 1];
  const X = h => L + (h - lo) / (hi - lo) * pw;
  const yMax = Math.max(...d.calm.counts, ...d.storm.counts, 1);
  const Y = c => T + ph - (c / yMax) * ph;
  let bars = "";
  for (let i = 0; i < d.bins; i++) {
    const x0 = X(d.edges[i]), w = Math.max(1, X(d.edges[i + 1]) - x0 - 1);
    bars += `<rect x="${x0.toFixed(1)}" y="${Y(d.calm.counts[i]).toFixed(1)}" width="${w.toFixed(1)}" height="${(T + ph - Y(d.calm.counts[i])).toFixed(1)}" fill="var(--color-primary)" opacity="0.45"/>`;
    bars += `<rect x="${x0.toFixed(1)}" y="${Y(d.storm.counts[i]).toFixed(1)}" width="${w.toFixed(1)}" height="${(T + ph - Y(d.storm.counts[i])).toFixed(1)}" fill="var(--color-signal)" opacity="0.5"/>`;
  }
  const pc = X(d.calm.p90), ps = X(d.storm.p90);
  let xt = ""; for (let k = 0; k <= 4; k++) { const h = lo + (hi - lo) * k / 4; xt += `<text x="${X(h).toFixed(1)}" y="${H - 12}" font-size="11" fill="var(--color-ink-3)" text-anchor="middle">${Math.round(h)}</text>`; }
  let yt = ""; for (let k = 0; k <= 3; k++) { const c = yMax * k / 3; yt += `<line x1="${L}" y1="${Y(c).toFixed(1)}" x2="${W - R}" y2="${Y(c).toFixed(1)}" stroke="var(--color-line-soft)"/><text x="${L - 6}" y="${(Y(c) + 3).toFixed(1)}" font-size="11" fill="var(--color-ink-3)" text-anchor="end">${Math.round(c)}</text>`; }
  const svg = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Распределение ETA: спокойно и шторм">${yt}${bars}
    <line x1="${pc.toFixed(1)}" y1="${T}" x2="${pc.toFixed(1)}" y2="${T + ph}" stroke="var(--color-primary)" stroke-width="2" stroke-dasharray="5 4"/>
    <line x1="${ps.toFixed(1)}" y1="${T}" x2="${ps.toFixed(1)}" y2="${T + ph}" stroke="var(--color-signal)" stroke-width="2" stroke-dasharray="5 4"/>
    <text x="${(pc - 6).toFixed(1)}" y="${T - 6}" font-size="11" fill="var(--color-primary)" text-anchor="end">90%: ${d.calm.p90} ч</text>
    <text x="${(ps + 6).toFixed(1)}" y="${T - 6}" font-size="11" fill="var(--color-signal-ink)" text-anchor="start">90%: ${d.storm.p90} ч</text>
    <line x1="${L}" y1="${T + ph}" x2="${W - R}" y2="${T + ph}" stroke="var(--color-ink-3)"/>${xt}
    <text x="${L + pw / 2}" y="${H - 2}" font-size="11" fill="var(--color-ink-3)" text-anchor="middle">Время доставки, часы</text></svg>`;
  $("#eta-dist").innerHTML = `<div class="chart-head"><b>${esc(d.route_id)}</b> · шторм добавляет ~<b>${d.storm_price_h} ч</b>
    <span class="legend"><i style="background:var(--color-primary)"></i>обычная погода <i style="background:var(--color-signal)"></i>шторм</span></div>${svg}
    <div class="explain">
      <p><b>Что показано.</b> Мы прогнали ${d.n} вариантов погоды и загрузки порта. Каждый столбик — как часто груз приезжает за это время: синие — обычная погода, оранжевые — шторм.</p>
      <p><b>Что это значит для вас.</b> В шторм вся картинка сдвигается вправо — груз едет дольше. Пунктир — срок, в который вы успеваете в <b>9 случаях из 10</b>. Разница между синим и оранжевым пунктиром (~${d.storm_price_h} ч) — это «цена шторма»: столько часов он добавляет к доставке этим маршрутом.</p>
      <p class="tech">Метод: Monte Carlo на реальном архиве погоды Open-Meteo; «цена шторма» — расстояние Вассерштейна-1 между распределениями. Симуляция, не реальные рейсы.</p>
    </div>`;
}

function renderPareto(points) {
  if (!points.length) { $("#pareto-chart").innerHTML = '<p class="hint">Нет кандидатов — все пути заблокированы сценарием.</p>'; return; }
  const W = 720, H = 320, L = 56, R = 18, T = 20, B = 42, pw = W - L - R, ph = H - T - B;
  const ts = points.map(p => p.time_h), cs = points.map(p => p.cost_usd), co = points.map(p => p.co2_kg);
  const tlo = Math.min(...ts), thi = Math.max(...ts), clo = Math.min(...cs), chi = Math.max(...cs);
  const colo = Math.min(...co), cohi = Math.max(...co);
  const pad = (a, b) => (b - a) * 0.08 || 1;
  const X = t => L + (t - tlo + pad(tlo, thi)) / (thi - tlo + 2 * pad(tlo, thi)) * pw;
  const Y = c => T + ph - (c - clo + pad(clo, chi)) / (chi - clo + 2 * pad(clo, chi)) * ph;
  const front = points.filter(p => p.pareto).sort((a, b) => a.time_h - b.time_h);
  const poly = front.map(p => `${X(p.time_h).toFixed(1)},${Y(p.cost_usd).toFixed(1)}`).join(" ");
  let dots = ""; points.forEach(p => {
    dots += `<circle cx="${X(p.time_h).toFixed(1)}" cy="${Y(p.cost_usd).toFixed(1)}" r="${p.pareto ? 7 : 5}" fill="${co2Color(p.co2_kg, colo, cohi)}" stroke="${p.pareto ? 'var(--color-ink)' : 'none'}" stroke-width="2"><title>${esc(p.node_names.join(' → '))}\n${p.time_h} ч · $${p.cost_usd} · CO₂ ${(p.co2_kg / 1000).toFixed(1)} т · риск срыва ${Math.round(p.risk * 100)}%${p.pareto ? ' · лучший компромисс' : ''}</title></circle>`;
  });
  let xt = ""; for (let k = 0; k <= 4; k++) { const t = tlo + (thi - tlo) * k / 4; xt += `<text x="${X(t).toFixed(1)}" y="${H - 14}" font-size="11" fill="var(--color-ink-3)" text-anchor="middle">${Math.round(t)}</text>`; }
  let yt = ""; for (let k = 0; k <= 3; k++) { const c = clo + (chi - clo) * k / 3; yt += `<line x1="${L}" y1="${Y(c).toFixed(1)}" x2="${W - R}" y2="${Y(c).toFixed(1)}" stroke="var(--color-line-soft)"/><text x="${L - 6}" y="${(Y(c) + 3).toFixed(1)}" font-size="11" fill="var(--color-ink-3)" text-anchor="end">$${Math.round(c)}</text>`; }
  const svg = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Парето-фронтир: время, стоимость, CO2">${yt}
    <polyline points="${poly}" fill="none" stroke="var(--color-ink)" stroke-width="1.5" stroke-dasharray="6 4" opacity="0.6"/>${dots}
    <line x1="${L}" y1="${T + ph}" x2="${W - R}" y2="${T + ph}" stroke="var(--color-ink-3)"/>${xt}
    <text x="${L + pw / 2}" y="${H - 2}" font-size="11" fill="var(--color-ink-3)" text-anchor="middle">Время доставки, часы →</text>
    <text x="14" y="${T + ph / 2}" font-size="11" fill="var(--color-ink-3)" text-anchor="middle" transform="rotate(-90 14 ${T + ph / 2})">Стоимость, $ →</text></svg>`;
  $("#pareto-chart").innerHTML = `<div class="chart-head"><b>${points.length}</b> вариантов маршрута · <b>${front.length}</b> лучших компромисса
    <span class="legend"><i style="background:var(--route-r2)"></i>чище <i style="background:var(--color-danger)"></i>грязнее (CO₂) · обводка = лучший компромисс</span></div>${svg}
    <div class="explain">
      <p><b>Что показано.</b> Каждый кружок — отдельный маршрут. Левее — быстрее, ниже — дешевле, цвет — выбросы CO₂ (зелёный чище, красный грязнее).</p>
      <p><b>Что это значит для вас.</b> Обведённые кружки — лучшие компромиссы: среди них нельзя сделать быстрее, не сделав дороже или грязнее. Выбирайте по тому, что важнее — срок, бюджет или экология. <b>Наведите на кружок</b> — увидите маршрут и цифры.</p>
      <p class="tech">Метод: фронт Парето среди маршрутов, найденных по целям время/цена/CO₂ (не полный перебор всех путей).</p>
    </div>`;
}

function renderAttribution(attr) {
  if (!attr || !attr.length) return "";
  const mx = Math.max(...attr.map(a => Math.abs(a.contribution_h)), 1);
  const rows = attr.map(a => {
    const w = Math.abs(a.contribution_h) / mx * 100, col = a.contribution_h >= 0 ? 'var(--color-danger)' : 'var(--color-ok)', sign = a.contribution_h >= 0 ? '+' : '';
    return `<div class="attr-row"><span class="attr-lbl">${esc(FEAT_RU[a.feature] || a.feature)}</span><span class="attr-track"><i style="width:${w.toFixed(0)}%;background:${col}"></i></span><span class="attr-val">${sign}${a.contribution_h} ч</span></div>`;
  }).join("");
  return `<div class="attr"><div class="attr-title">Из чего складывается задержка (по часам):</div>${rows}
    <div class="attr-note">Красные полоски добавляют часы к задержке, зелёные — уменьшают. Длина полоски = сколько часов добавил фактор.</div></div>`;
}

function renderGauge(ex) {
  if (!ex || ex.l1_to_median == null) return "";
  const scaleMax = Math.max(ex.train_max, ex.l1_to_median) * 1.08 || 1;
  const thr = ex.train_p95 / scaleMax * 100, pos = Math.min(100, ex.l1_to_median / scaleMax * 100);
  const chip = ex.is_extrapolating
    ? `<span class="chip chip-danger">${icon("alert")}редкая ситуация — осторожно</span>`
    : `<span class="chip chip-ok">${icon("check")}модель уверена</span>`;
  const note = ex.is_extrapolating
    ? 'Маркер в красной зоне: шторм — редкая ситуация, модель такого почти не видела при обучении. Верьте объяснимому расчёту по правилам, а не ML-цифре.'
    : 'Маркер в зелёной зоне: такая погода и загрузка часто встречались при обучении — прогнозу модели можно верить.';
  return `<div class="gauge-wrap"><div class="gauge-title">Насколько ситуация знакома модели ${chip}</div>
    <div class="gauge"><i class="gauge-ok" style="width:${thr.toFixed(1)}%"></i><i class="gauge-bad" style="width:${(100 - thr).toFixed(1)}%"></i><span class="gauge-thr" style="left:${thr.toFixed(1)}%"></span><span class="gauge-marker" style="left:${pos.toFixed(1)}%"></span></div>
    <div class="gauge-scale"><span>знакомо</span><span>граница опыта</span><span>сейчас</span></div>
    <div class="gauge-note">${note}</div></div>`;
}

async function loadEtaDist() {
  const rid = $("#eta-route") ? $("#eta-route").value : "R1";
  renderEtaDist(await getJSON(`/api/routes/${rid}/eta-distribution?weight_t=48${scenarioQuery()}`));
}

async function loadPareto() {
  const from = $("#search-from").value || "moscow", to = $("#search-to").value || "bandar_abbas";
  const d = await getJSON(`/api/routes/pareto?from_node=${from}&to_node=${to}&weight_t=48&storm=${storm}${scenarioQuery()}`);
  renderPareto(d.points);
}

async function initMap() {
  map = L.map("map", { scrollWheelZoom: false }).setView([43.5, 50.0], 4);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 12, attribution: "&copy; OpenStreetMap",
  }).addTo(map);
  const routes = await getJSON("/api/routes");
  routes.routes.forEach((r) => {
    const pts = r.nodes.map((n) => [n.lat, n.lon]);
    polylines[r.id] = L.polyline(pts, { color: ROUTE_COLORS[r.id], weight: 3.5, opacity: 0.9 })
      .addTo(map).bindPopup(`<b>${esc(r.id)} · ${esc(r.name)}</b><br>${esc(r.via)}`);
  });
  const nodes = await getJSON("/api/nodes");
  nodes.nodes.forEach((n) => {
    nodeCache[n.id] = n;
    L.circleMarker([n.lat, n.lon], { radius: 5, color: TOKENS["--color-ink"], fillColor: "#fff", fillOpacity: 1, weight: 2 })
      .addTo(map).bindPopup(`<b>${esc(n.name)}</b> (${esc(n.country)})<br>${esc(n.kind)}${n.note ? "<br><span class='hint'>" + esc(n.note) + "</span>" : ""}`);
  });
  searchLayer = L.layerGroup().addTo(map);
  // Fit the whole corridor on first load instead of a fixed viewport.
  const all = routes.routes.flatMap((r) => r.nodes.map((n) => [n.lat, n.lon]));
  if (all.length) map.fitBounds(L.latLngBounds(all).pad(0.06));
}

/* Focus a route on the map + sync legend/table highlight (product interaction). */
function focusRoute(id) {
  if (!map || !polylines[id]) return;
  Object.entries(polylines).forEach(([rid, pl]) => pl.setStyle({ weight: rid === id ? 6 : 3, opacity: rid === id ? 1 : 0.4 }));
  map.fitBounds(polylines[id].getBounds().pad(0.2));
  document.querySelectorAll(".legend-item").forEach((el) => el.classList.toggle("focused", el.dataset.route === id));
  document.querySelectorAll("#compare-table tbody tr").forEach((tr) => tr.classList.toggle("focused", tr.dataset.route === id));
}

function drawSearchPath(idx) {
  if (!searchLayer || !lastPaths[idx]) return;
  searchLayer.clearLayers();
  const p = lastPaths[idx];
  const pts = p.nodes.map((id) => [nodeCache[id].lat, nodeCache[id].lon]);
  L.polyline(pts, { color: TOKENS["--color-ink"], weight: 4.5, opacity: 0.9, dashArray: "8 6" })
    .addTo(searchLayer)
    .bindPopup(`<b>Выбранный маршрут (схема)</b><br>${esc(p.node_names.join(" → "))}<br>${p.total_predicted_h} ч · CO₂ ${(p.co2_kg / 1000).toFixed(1)} т`);
  pts.forEach((pt) => L.circleMarker(pt, { radius: 5, color: TOKENS["--color-ink"], fillColor: "#fff", fillOpacity: 1, weight: 2 }).addTo(searchLayer));
  map.fitBounds(L.latLngBounds(pts).pad(0.06));
}

function scenarioQuery() {
  const q = [];
  if (scenario.close) q.push(`close=${encodeURIComponent(scenario.close)}`);
  if (scenario.extra_customs_h) q.push(`extra_customs_h=${scenario.extra_customs_h}`);
  if (scenario.wave_m != null) q.push(`wave_m=${scenario.wave_m}`);
  if (scenario.wind_ms != null) q.push(`wind_ms=${scenario.wind_ms}`);
  return q.length ? "&" + q.join("&") : "";
}

function renderStormBtn() {
  const b = $("#storm-btn");
  b.classList.toggle("on", storm);
  b.setAttribute("aria-pressed", String(storm));
  b.innerHTML = `${icon("storm")}Шторм на Каспии<span class="storm-state">${storm ? "вкл" : "выкл"}</span>`;
  document.body.classList.toggle("storm-mode", storm);
  $("#storm-strip").hidden = !storm;
}

async function loadCompare() {
  const data = await getJSON(`/api/compare?weight_t=48&storm=${storm}${scenarioQuery()}`);
  $("#compare-table tbody").innerHTML = data.routes.map((r) => {
    const rid = esc(r.route_id);
    const best = [
      r.route_id === data.best.time ? `<span class="chip chip-primary">${icon("clock")}быстрее</span>` : "",
      r.route_id === data.best.co2 ? `<span class="chip chip-ok">${icon("leaf")}чище</span>` : "",
      r.route_id === data.best.risk ? `<span class="chip chip-primary">${icon("shield")}надёжнее</span>` : "",
    ].join("");
    return `<tr data-route="${rid}">
      <td><span class="route-cell"><span class="route-cell-top"><span class="rc rc-${rid.toLowerCase()}">${rid}</span>${esc(r.route_name)}</span><span class="route-via">${esc(r.via)}</span></span></td>
      <td class="num">${r.total_predicted_h} ч${r.total_wait_h ? `<br><span class="hint">ожидание ${r.total_wait_h} ч</span>` : ""}</td>
      <td class="num">${r.eta_p90_h} ч</td><td class="num">+${r.delay_h} ч</td><td>${riskPill(r.delay_probability)}</td>
      <td class="num">${(r.co2_kg / 1000).toFixed(1)}</td><td class="num">$${esc(r.cost_usd.toLocaleString("ru-RU"))}</td>
      <td class="num">${r.storm_price_h}</td>
      <td><span class="best-badges">${best}</span></td></tr>`;
  }).join("");
  document.querySelectorAll("#compare-table tbody tr").forEach((tr) => {
    tr.addEventListener("click", () => focusRoute(tr.dataset.route));
  });
  $("#route-legend").innerHTML = data.routes.map((r) => `
    <button type="button" class="legend-item" data-route="${esc(r.route_id)}" title="Показать ${esc(r.route_name)} на карте">
      <span class="legend-top"><span class="rc rc-${esc(r.route_id.toLowerCase())}">${esc(r.route_id)}</span><span class="legend-name">${esc(r.route_name)}</span></span>
      <span class="legend-metrics">${r.total_predicted_h} ч · +${r.delay_h} ч · риск ${Math.round(r.delay_probability * 100)}%</span>
    </button>`).join("");
  document.querySelectorAll(".legend-item").forEach((el) => {
    el.addEventListener("click", () => focusRoute(el.dataset.route));
  });
  const best = data.routes.find((r) => r.route_id === data.best.co2);
  $("#kpi-co2").textContent = `${best.route_id}: ${(best.co2_kg / 1000).toFixed(1)} т`;
  const ports = await getJSON("/api/ports");
  const worst = ports.ports.reduce((a, b) => (a.congestion_pct > b.congestion_pct ? a : b));
  $("#kpi-port").textContent = `${worst.name}: ${worst.congestion_pct}%`;
}

async function loadShipments() {
  const data = await getJSON(`/api/shipments?storm=${storm}${scenarioQuery()}`);
  shipments = data.shipments;
  $("#kpi-ship").textContent = shipments.length;
  const withDelay = shipments.filter((s) => typeof s.delay_h === "number");
  const avg = withDelay.length ? withDelay.reduce((s, x) => s + x.delay_h, 0) / withDelay.length : 0;
  $("#kpi-delay").textContent = `+${avg.toFixed(1)} ч`;
  $("#shipment-select").innerHTML = shipments.map((s) =>
    `<option value="${esc(s.id)}">${esc(s.id)} — ${esc(s.name)} (${esc(s.route_name)})${s.blocked ? " — заблокирован" : ""}</option>`).join("");
  if (!shipments.some((s) => s.id === currentShipment)) currentShipment = shipments[0].id;
  $("#shipment-select").value = currentShipment;
  await loadShipment();
}

async function loadShipment() {
  let s;
  try {
    s = await getJSON(`/api/shipments/${currentShipment}?storm=${storm}${scenarioQuery()}`);
  } catch (e) {
    if (String(e.message).includes("404")) {
      // shipment vanished (e.g. demo reset) — reselect an existing one
      await loadShipments();
      return;
    }
    $("#shipment-info").innerHTML =
      `${icon("alert")} <b>${esc(currentShipment)}</b> — маршрут заблокирован сценарием (закрыт узел на пути).`;
    $("#timeline").innerHTML = "";
    $("#events-timeline").innerHTML = "";
    $("#docs-list").innerHTML = "";
    $("#ml-box").textContent = "";
    $("#verdict").textContent = "Прогноз недоступен: сбросьте сценарий или выберите другой маршрут.";
    return;
  }
  $("#shipment-info").innerHTML =
    `<b>${esc(s.id)} · ${esc(s.name)}</b> — ${esc(s.cargo)}, ${s.weight_t} т · ${esc(s.status)} · ETA ${esc(fmtDT(s.eta))}`;
  const nameOf = {};
  (await getJSON("/api/nodes")).nodes.forEach((n) => (nameOf[n.id] = n.name));
  $("#timeline").innerHTML = s.forecast.legs.map((l) => `
    <li class="${l.id === s.forecast.bottleneck_leg_id ? "bottleneck" : ""}">
      <b>${MODE_RU[l.mode]} ${esc(nameOf[l.from])} → ${esc(nameOf[l.to])}</b>
      ${l.id === s.forecast.bottleneck_leg_id ? ` <span class="chip chip-danger">${icon("alert")}узкое место</span>` : ""}${l.conditional ? ` <span class="chip chip-warn">условное плечо</span>` : ""}<br>
      план ${l.base_h} ч${l.wait_h ? ` + ожидание парома ${l.wait_h} ч` : ""} → прогноз ${l.predicted_h} ч (+${l.delay_h} ч задержки)<br>
      <span class="why">${esc(l.reasons.join("; "))}</span>
      ${l.note ? `<br><span class="hint">${esc(l.note)}</span>` : ""}
    </li>`).join("");
  $("#verdict").textContent =
    `Прогноз: ${s.forecast.total_predicted_h} ч = ход ${s.forecast.total_base_h} ч` +
    (s.forecast.total_wait_h ? ` + ожидание парома ${s.forecast.total_wait_h} ч` : "") +
    ` + задержки ${s.forecast.delay_h} ч. ${s.forecast.recommendation} CO₂ маршрута: ${(s.forecast.co2_kg / 1000).toFixed(1)} т.`;
  const docLabel = { verified: "проверен", submitted: "подан, ждёт проверки",
                     missing: "нет документа", rejected: "отказ", expired: "истёк срок",
                     unknown: "неизвестный тип" };
  const docChip = { verified: "chip-ok", submitted: "chip-warn", missing: "chip-danger",
                    rejected: "chip-danger", expired: "chip-neutral", unknown: "chip-neutral" };
  $("#docs-list").innerHTML = (s.documents || []).map((d) => `
    <li><span class="doc-head"><span class="dot ${esc(d.status)}"></span><b>${esc(s.doc_types[d.doc_type] || d.doc_type)}</b><span class="chip ${docChip[d.status] || "chip-neutral"}">${esc(docLabel[d.status] || d.status)}</span></span>
    ${d.issuer ? `<br><span class="hint">решение: ${esc(d.issuer)}${d.decided_at ? " · " + esc(fmtDT(d.decided_at)) : ""}</span>` : ""}
    ${d.content_hash ? `<br><span class="hash">hash ${esc(d.content_hash.slice(0, 12))}… · источник: ${esc(d.source || "—")}</span>` : ""}
    ${d.status !== "verified" ? `<br><input type="text" class="doc-ref" data-doc="${esc(d.doc_type)}" placeholder="Ссылка на документ (REJ- → отказ, EXP- → истёк)" value="${esc(d.ref || "")}" aria-label="Ссылка на документ: ${esc(s.doc_types[d.doc_type] || d.doc_type)}">` : ""}
    </li>`).join("");
  $("#clearance-result").textContent = "";
  await loadLedger();
  const mlp = await getJSON(`/api/ml/shipment/${currentShipment}?storm=${storm}`);
  const ex = mlp.extrapolation || {};
  const analogs = (mlp.analogs || []).map((a) =>
    `волна ${a.max_wave_m} м, ветер ${a.max_wind_ms} м/с, порт ${a.max_congestion_pct}% → задержка +${a.delay_h} ч`).join("; ");
  $("#ml-box").innerHTML =
    `<div class="ml-head">${icon("activity")}ML-прогноз задержки <span class="chip chip-neutral">эксперимент</span></div>` +
    renderGauge(ex) +
    `<p>Модель: <b>+${mlp.ml_delay_h} ч</b>. Объяснимый расчёт по правилам коридора: <b>+${mlp.scoring_delay_h} ч</b>. ` +
    (mlp.difference_h >= 10
      ? `Расхождение ${mlp.difference_h} ч — модель и правила не согласны; в шторм доверяйте правилам: там каждый час подписан причиной.`
      : `Расхождение всего ${mlp.difference_h} ч — модель и правила согласны, прогнозу можно верить.`) + `</p>` +
    renderAttribution(mlp.local_attribution) +
    (analogs ? `<div class="analogs">${icon("history")}<b>Похожие прошлые случаи:</b> при такой же погоде и загрузке грузы задерживались на ${esc(analogs)}. Это ориентир из истории, не точный прогноз.</div>` : "") +
    `<div class="tech">Модель обучена на смоделированных задержках (реальная погода Open-Meteo + правила коридора), не на реальных рейсах — это эксперимент. Ансамбль RandomForest + HistGradientBoosting.</div>`;
  const tl = await getJSON(`/api/shipments/${currentShipment}/timeline?storm=${storm}${scenarioQuery()}`);
  const statusIcon = { done: icon("check-circle"), in_progress: icon("refresh"), planned: icon("clock") };
  const actualHtml = (tl.actual_events || []).map((e) => `
    <li class="fact">${icon("pin")}<b>${esc(e.title)}</b> <span class="chip chip-ok">факт</span><br>
    <span class="hint">${esc(fmtDT(e.time))} · источник: ${esc(e.source)}${e.location ? " · " + esc(e.location) : ""}</span><br>
    <span class="why">${esc(e.note || "")}</span></li>`).join("");
  $("#events-timeline").innerHTML = actualHtml + tl.events.map((e) => `
    <li>${statusIcon[e.status] || ""}<b>${esc(e.title)}</b> <span class="hint">(модель)</span><br>
    <span class="hint">${esc(fmtDT(e.time))}${e.predicted_time ? " → прогноз " + esc(fmtDT(e.predicted_time)) : ""}</span><br>
    <span class="why">${esc(e.detail || "")}</span></li>`).join("");
  const cur = shipments.find((s) => s.id === currentShipment);
  if (cur) loadDocking(cur.route_id).catch(() => {});
}

async function loadExceptions() {
  const data = await getJSON(`/api/exceptions?storm=${storm}${scenarioQuery()}`);
  $("#exceptions-list").innerHTML = data.exceptions.length ? data.exceptions.map((e) => `
    <li class="exc ${esc(e.severity)}">${icon("alert")}<div><b>${esc(e.title)}</b><br>
    ${esc(e.detail)}<br><span class="action">${icon("arrow-right")}${esc(e.action)}</span></div></li>`).join("")
    : `<li class="exceptions-empty">${icon("check-circle")}Исключений нет — все грузы в графике.</li>`;
}

async function initSearch() {
  const nodes = await getJSON("/api/nodes");
  const opts = nodes.nodes.map((n) => `<option value="${esc(n.id)}">${esc(n.name)}</option>`).join("");
  $("#search-from").innerHTML = opts;
  $("#search-to").innerHTML = opts;
  $("#search-from").value = "moscow";
  $("#search-to").value = "bandar_abbas";
}

async function runSearch(focusMap = true) {
  const from = $("#search-from").value, to = $("#search-to").value;
  const obj = $("#search-objective").value;
  let data;
  try {
    data = await getJSON(`/api/routes/search?from_node=${from}&to_node=${to}&weight_t=48&storm=${storm}&objective=${obj}${scenarioQuery()}`);
  } catch (e) {
    $("#search-results").innerHTML = `<p class="hint">Поиск не выполнен: ${esc(e.message)}. Проверьте параметры сценария.</p>`;
    return;
  }
  lastPaths = data.paths;
  $("#search-results").innerHTML = data.paths.length ? data.paths.map((p, i) => `
    <div class="path-card" data-idx="${i}" role="button" tabindex="0" title="Показать на карте">
      <span class="path-title"><span class="path-num">${String(i + 1).padStart(2, "0")}</span>${esc(p.node_names.join(" → "))}
      ${p.pareto_optimal ? `<span class="chip chip-ok">Парето (среди найденных)</span>` : ""}
      ${p.has_conditional_legs ? `<span class="chip chip-warn">есть условные плечи</span>` : ""}</span>
      <span class="hint">${p.modes.map((m) => MODE_RU[m]).join(" · ")}</span>
      <div class="path-metrics">${p.total_predicted_h} ч${p.total_wait_h ? ` (ожидание ${p.total_wait_h} ч)` : ""} · P90 ${p.eta_p90_h || "—"} ч · +${p.delay_h} ч · риск ${Math.round(p.delay_probability * 100)}% · CO₂ ${(p.co2_kg / 1000).toFixed(1)} т · $${esc(p.cost_usd.toLocaleString("ru-RU"))}</div>
      <span class="hint">Кратчайший путь найден умным поиском; просмотрено узлов: ${p.expansions_first_search}.</span>
    </div>`).join("")
    : `<p class="hint">Маршрутов нет — все пути заблокированы сценарием.</p>`;
  const selectCard = (card) => {
    document.querySelectorAll("#search-results .path-card").forEach((c) => c.classList.remove("selected"));
    card.classList.add("selected");
    drawSearchPath(+card.dataset.idx);
  };
  document.querySelectorAll("#search-results .path-card").forEach((card) => {
    card.addEventListener("click", () => selectCard(card));
    card.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectCard(card); } });
  });
  if (data.paths.length) {
    if (focusMap) drawSearchPath(0);
    const first = document.querySelector("#search-results .path-card");
    if (first) first.classList.add("selected");
  } else if (searchLayer) {
    searchLayer.clearLayers();
  }
}

async function loadLedger() {
  const data = await getJSON("/api/clearance/ledger");
  $("#ledger").innerHTML = data.entries.length ? data.entries.map((e) => `
    <li><span class="doc-head"><span class="ledger-meta">#${e.seq} · ${esc(e.shipment_id)}</span>
    ${e.decision === "pre-cleared"
      ? `<span class="chip chip-ok">${icon("check")}pre-cleared</span>`
      : `<span class="chip chip-warn">${icon("alert")}нужны документы: ${esc(e.missing.join(", "))}</span>`}</span>
    <span class="ledger-meta">${esc(e.issuer || "demo")} · правила ${esc(e.rule_version || "v0")} · ${esc(fmtDT(e.ts))}</span><br>
    <span class="hash">hash ${esc(e.hash.slice(0, 16))}… ← prev ${esc(e.prev_hash.slice(0, 12))}…</span></li>`).join("")
    : `<li class="hint">Пока пусто — отправьте груз на pre-clearance.</li>`;
}

async function loadProvenance() {
  try {
    const data = await getJSON("/api/provenance");
    const kindLabel = { live: "live", fallback: "fallback", historical: "исторические",
                        demo: "демо", synthetic: "синтетика", static: "справочник",
                        assumption: "допущение" };
    const kindChip = { live: "chip-ok", fallback: "chip-warn", historical: "chip-primary",
                       demo: "chip-warn", synthetic: "chip-warn", static: "chip-neutral",
                       assumption: "chip-neutral" };
    $("#sources-list").innerHTML = data.sources.map((s) => `
      <li><span><span class="chip ${kindChip[s.kind] || "chip-neutral"}">${esc(kindLabel[s.kind] || s.kind)}</span></span>
      <span><b>${esc(s.what)}</b><br><span class="hint">${esc(s.source || "")}${s.license ? " · " + esc(s.license) : ""}${s.retrieved ? " · получено: " + esc(String(s.retrieved).slice(0, 16)) : ""}</span>
      ${s.note ? `<br><span class="why">${esc(s.note)}</span>` : ""}</span></li>`).join("");
  } catch (e) {
    $("#sources-list").innerHTML = "<li class='hint'>Не удалось загрузить provenance.</li>";
  }
}

async function loadCorridor() {
  const data = await getJSON("/api/corridor");
  $("#corridor-stats").innerHTML = data.stats.map((s) =>
    `<div class="stat"><b>${esc(s.value)}</b><span>${esc(s.label)}</span></div>`).join("");
}

async function loadPorts() {
  const data = await getJSON("/api/ports");
  $("#ports-grid").innerHTML = data.ports.map((p) => {
    const cls = p.congestion_pct >= 60 ? "hot" : p.congestion_pct >= 45 ? "warm" : "";
    const badge = p.live
      ? `<span class="chip chip-ok port-badge">обновлено оператором, ${new Date(p.updated_at).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}</span>`
      : `<span class="chip port-badge port-badge-demo">демо-снимок</span>`;
    return `<div class="port-card" data-port="${esc(p.node)}">
      <div class="port-top"><span class="port-name">${icon("anchor")}${esc(p.name)}</span>${badge}</div>
      <div class="port-meta">загруженность ${p.congestion_pct}% · таможня ~${p.customs_queue_h} ч</div>
      <div class="bar" role="img" aria-label="Загруженность ${p.congestion_pct}%"><i class="${cls}" style="width:${p.congestion_pct}%"></i></div>
      <form class="port-form" data-port="${esc(p.node)}">
        <label class="field">Загрузка, %<input type="number" name="congestion_pct" min="0" max="100" value="${p.congestion_pct}" required inputmode="numeric"></label>
        <label class="field">Таможня, ч<input type="number" name="customs_queue_h" min="0" step="0.5" value="${p.customs_queue_h}" required inputmode="decimal"></label>
        <button class="btn btn-ghost btn-sm" type="submit">Обновить</button>
      </form>
    </div>`;
  }).join("");
}

/* П5: discrete-event rail–ferry docking card (twin section). */
async function loadDocking(routeId) {
  const el = $("#docking-card");
  if (!el || !routeId) return;
  try {
    const d = await getJSON(`/api/routes/${routeId}/docking?weight_t=48&storm=${storm}${scenarioQuery()}`);
    if (!d.has_connection) {
      el.innerHTML = `<p class="hint">Нет стыковки ж/д–паром: маршрут сухопутный или без морского плеча.</p>`;
      return;
    }
    const c = d.connections[0], ex = c.example;
    const chip = c.p_miss >= 0.5 ? "chip-danger" : c.p_miss >= 0.2 ? "chip-warn" : "chip-ok";
    el.innerHTML = `
      <div class="docking-stats">
        <div class="docking-stat"><span class="docking-num ${chip}">${Math.round(c.p_miss * 100)}%</span><span class="docking-lbl">шанс пропустить паром</span></div>
        <div class="docking-stat"><span class="docking-num">${c.expected_wait_h} ч</span><span class="docking-lbl">среднее ожидание</span></div>
        <div class="docking-stat"><span class="docking-num">${c.wait_p90_h} ч</span><span class="docking-lbl">ожидание с запасом (90%)</span></div>
      </div>
      <div class="docking-example ${ex.missed ? "is-miss" : "is-catch"}">
        ${icon(ex.missed ? "alert" : "check-circle")}
        <span><b>${esc(c.port_name)}:</b> поезд прибыл ${esc(ex.arrival_clock)}, паром ${esc(ex.departure_clock)} → ${esc(ex.verdict)}.</span>
      </div>
      <p class="hint">${esc(d.schedule_assumption)} Синхронизация настоящая (discrete-event): прибытие поезда сравнивается с расписанием парома и cutoff погрузки.</p>`;
  } catch (e) {
    el.innerHTML = `<p class="hint">Стыковка недоступна: ${esc(e.message)}</p>`;
  }
}

/* П4: real local-EVM DLT anchor card (clearance section). */
async function loadAnchor() {
  const el = $("#dlt-anchor-card");
  if (!el) return;
  try {
    const st = await getJSON("/api/clearance/anchor");
    if (!st.available) {
      el.innerHTML = `<p class="hint">DLT-якорь недоступен в этой сборке (нужны web3/eth-tester). Hash-chain реестр работает независимо.</p>`;
      return;
    }
    const ledger = await getJSON("/api/clearance/ledger");
    const lastHash = ledger.entries.length ? ledger.entries[ledger.entries.length - 1].hash : "0".repeat(64);
    const l = st.latest;
    const verified = l && l.root === lastHash;
    el.innerHTML = `
      <dl class="anchor-grid">
        <div><dt>Контракт</dt><dd class="mono" title="${esc(st.contract_address)}">${esc(st.contract_address.slice(0, 10))}…${esc(st.contract_address.slice(-6))}</dd></div>
        <div><dt>Якорей в цепочке</dt><dd>${st.anchored_count}</dd></div>
        ${l ? `<div><dt>Последний якорь</dt><dd>seq ${l.seq} · root <span class="mono">${esc(l.root.slice(0, 16))}…</span></dd></div>` : ""}
        <div><dt>Сверка с реестром</dt><dd>${l ? (verified ? '<span class="chip chip-ok">✓ verified on-chain</span>' : '<span class="chip chip-warn">якорь устарел — обновите</span>') : '<span class="hint">ещё не якорили</span>'}</dd></div>
      </dl>
      <button id="anchor-btn" class="btn btn-primary" type="button">${icon("ledger")}Якорить в реестр</button>
      <p class="hint">${esc(st.note || "локальный EVM devnet (py-evm, in-memory), не mainnet/production")}. EVM, контракт и транзакции настоящие; корень hash-chain реестра фиксируется on-chain.</p>`;
    $("#anchor-btn").addEventListener("click", () => withBusy($("#anchor-btn"), async () => {
      try {
        await getJSON("/api/clearance/anchor", { method: "POST", headers: { "Content-Type": "application/json" } });
        await loadAnchor();
      } catch (e) {
        el.insertAdjacentHTML("beforeend", `<p class="hint">Не удалось заякорить: ${esc(e.message)}</p>`);
      }
    }));
  } catch (e) {
    el.innerHTML = `<p class="hint">DLT-якорь недоступен: ${esc(e.message)}</p>`;
  }
}

async function loadLiveWeather() {
  try {
    const data = await getJSON("/api/weather/live");
    const parts = Object.values(data.segments).map((s) =>
      `${s.segment}: волна ${s.wave_m} м, ветер ${s.wind_ms} м/с`).join(" · ");
    const badge = $("#live-badge");
    badge.innerHTML = `${icon("waves")}${data.live ? "LIVE" : "ДЕМО"} · ${esc(parts)}`;
    badge.title = `${data.live ? "LIVE Open-Meteo" : "Погода: демо (live недоступен)"} — ${parts}`;
    badge.classList.toggle("is-live", !!data.live);
  } catch (e) {
    const badge = $("#live-badge");
    badge.innerHTML = `${icon("waves")}Погода: демо`;
    badge.classList.remove("is-live");
  }
}

async function runQuote() {
  const routeId = $("#quote-route").value;
  const weight = Math.max(1, +$("#quote-weight").value || 48);
  const fc = await getJSON(`/api/routes/${routeId}/forecast?weight_t=${weight}&storm=${storm}${scenarioQuery()}`);
  const mlp = await getJSON(`/api/ml/forecast?route_id=${routeId}&weight_t=${weight}&storm=${storm}`);
  $("#quote-result").textContent =
    `${fc.route_name}: доставка ~${fc.total_predicted_h} ч (движение ${fc.total_base_h} ч` +
    (fc.total_wait_h ? ` + ожидание парома ${fc.total_wait_h} ч` : "") +
    ` + задержки ${fc.delay_h} ч). Риск срыва срока ${Math.round(fc.delay_probability * 100)}%, срок с запасом (90%) ${fc.eta_p90_h} ч. ` +
    `Прогноз модели: +${mlp.ml_delay_h} ч задержки. CO₂ ${(fc.co2_kg / 1000).toFixed(1)} т, ` +
    `стоимость ~$${fc.cost_usd.toLocaleString("ru-RU")}. ${fc.recommendation}`;
}

async function refreshAll() {
  await Promise.all([loadCompare(), loadShipments(), loadExceptions(), loadEtaDist(), loadPareto()]);
  if ($("#search-results").children.length) await runSearch();
}

function readScenarioInputs() {
  const wave = $("#scenario-wave").value, wind = $("#scenario-wind").value;
  scenario = {
    close: $("#scenario-close").value,
    extra_customs_h: Math.max(0, +$("#scenario-customs").value || 0),
    wave_m: wave === "" ? null : Math.max(0, +wave),
    wind_ms: wind === "" ? null : Math.max(0, +wind),
  };
}

/* Scrollspy: highlight the current section in the fixed appbar nav. */
function initScrollSpy() {
  const links = [...document.querySelectorAll(".appnav a")];
  const byId = {};
  links.forEach((a) => {
    const el = document.getElementById(a.getAttribute("href").slice(1));
    if (el) byId[el.id] = a;
  });
  const obs = new IntersectionObserver((entries) => {
    entries.forEach((en) => {
      if (!en.isIntersecting) return;
      links.forEach((a) => a.removeAttribute("aria-current"));
      const a = byId[en.target.id];
      if (a) a.setAttribute("aria-current", "true");
    });
  }, { rootMargin: "-30% 0px -60% 0px" });
  Object.keys(byId).forEach((id) => obs.observe(document.getElementById(id)));
}

document.addEventListener("DOMContentLoaded", async () => {
  readTokens();
  renderStormBtn();
  initScrollSpy();
  try {
    await Promise.all([initMap(), loadCompare(), loadShipments(), loadCorridor(), loadPorts(), loadLiveWeather(), initSearch(), loadExceptions(), loadProvenance()]);
  } catch (e) {
    console.error(e);
    $("#verdict").textContent = "Не удалось загрузить данные API. Проверьте, что бэкенд запущен (./run.sh).";
    setBanner("API недоступен: проверьте, что бэкенд запущен (./run.sh). Данные не показаны.");
  }
  updateOfflineBanner();
  loadEtaDist().catch(() => {});
  loadPareto().catch(() => {});
  runSearch(false).catch(() => {});
  if ($("#eta-route")) $("#eta-route").addEventListener("change", () => loadEtaDist().catch(() => {}));
  $("#storm-btn").addEventListener("click", () => withBusy($("#storm-btn"), async () => {
    storm = !storm;
    renderStormBtn();
    $("#storm-note").textContent = storm
      ? "Шторм: ветер 19 м/с, волна 3,4 м — паромы не выходят, пропуск рейса +48 ч."
      : "Обычная погода: волна 0,9–1,2 м, ветер 9–11 м/с.";
    await refreshAll();
  }));
  $("#search-btn").addEventListener("click", () => withBusy($("#search-btn"), async () => { await runSearch(); loadPareto().catch(() => {}); }));
  $("#scenario-btn").addEventListener("click", () => withBusy($("#scenario-btn"), async () => {
    readScenarioInputs();
    await Promise.all([refreshAll(), runQuote()]);
  }));
  $("#scenario-reset").addEventListener("click", () => withBusy($("#scenario-reset"), async () => {
    scenario = { close: "", extra_customs_h: 0, wave_m: null, wind_ms: null };
    $("#scenario-close").value = "";
    $("#scenario-customs").value = 0;
    $("#scenario-wave").value = "";
    $("#scenario-wind").value = "";
    await Promise.all([refreshAll(), runQuote()]);
  }));
  $("#shipment-select").addEventListener("change", (e) => {
    currentShipment = e.target.value;
    loadShipment();
  });
  $("#create-btn").addEventListener("click", () => withBusy($("#create-btn"), async () => {
    const body = {
      name: $("#new-name").value || "Груз",
      cargo: $("#new-cargo").value || "—",
      weight_t: Math.max(1, +$("#new-weight").value || 48),
      route_id: $("#new-route").value,
    };
    try {
      const res = await getJSON("/api/shipments", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      $("#create-result").textContent = `Груз ${res.id} создан. ETA ${res.eta.slice(0, 16).replace("T", " ")}.`;
      await loadShipments();
      currentShipment = res.id;
      $("#shipment-select").value = res.id;
      await loadShipment();
      await loadExceptions();
    } catch (e) {
      $("#create-result").textContent = "Не удалось создать груз: " + e.message;
    }
  }));
  $("#quote-btn").addEventListener("click", () => withBusy($("#quote-btn"), runQuote));
  $("#event-btn").addEventListener("click", () => withBusy($("#event-btn"), async () => {
    const body = {
      event_type: $("#event-type").value,
      source: $("#event-source").value || "manual (demo)",
      note: $("#event-note").value || null,
      occurred_ts: new Date().toISOString(),
      confidence: "manual entry",
    };
    try {
      const res = await getJSON(`/api/shipments/${currentShipment}/events`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      $("#event-result").textContent = `Событие ${res.event_type} записано (источник: ${res.source}).`;
      $("#event-note").value = "";
      await loadShipment();
    } catch (e) {
      $("#event-result").textContent = "Ошибка записи события: " + e.message;
    }
  }));
  $("#clearance-btn").addEventListener("click", () => withBusy($("#clearance-btn"), async () => {
    const refs = {};
    document.querySelectorAll(".doc-ref").forEach((inp) => {
      if (inp.value.trim()) refs[inp.dataset.doc] = inp.value.trim();
    });
    try {
      const res = await getJSON("/api/clearance/submit", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ shipment_id: currentShipment, docs: refs }),
      });
      await loadShipment();
      await loadExceptions();
      loadAnchor().catch(() => {});
      $("#clearance-result").textContent = res.decision === "pre-cleared"
        ? `Pre-clearance пройден (симуляция). Запись #${res.seq}, hash ${res.hash.slice(0, 16)}… Решение: ${res.issuer}, правила ${res.rule_version}.`
        : `Не хватает документов: ${res.missing.join(", ")}. Запись #${res.seq} — «нужны документы». Решение: ${res.issuer}.`;
    } catch (e) {
      $("#clearance-result").textContent = "Ошибка pre-clearance: " + e.message;
    }
  }));
  $("#ports-grid").addEventListener("submit", (e) => {
    const form = e.target.closest(".port-form");
    if (!form) return;
    e.preventDefault();
    const pid = form.dataset.port;
    withBusy(form.querySelector("button"), async () => {
      try {
        await getJSON(`/api/ports/${pid}`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ congestion_pct: +form.congestion_pct.value, customs_queue_h: +form.customs_queue_h.value }),
        });
        await loadPorts();
        await refreshAll();
      } catch (err) {
        form.title = "Не удалось обновить порт: " + err.message;
      }
    });
  });
  loadAnchor().catch(() => {});
  window.addEventListener("load", () => { if (map) map.invalidateSize(); });
});
