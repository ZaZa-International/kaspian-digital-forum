/* Smart Caspian Transit frontend: map + compare + shipment twin + clearance. */
const $ = (s) => document.querySelector(s);
let storm = false;
let scenario = { close: "", extra_customs_h: 0 };
let map, polylines = {};
let shipments = [];
let currentShipment = null;
const MODE_RU = { rail: "🚂 ж/д", sea: "⛴ море", road: "🚛 авто" };
const ROUTE_COLORS = { R1: "#0e7aa8", R2: "#1e7d46", R3: "#c96a12" };

async function getJSON(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(url + " -> " + r.status);
  return r.json();
}

function riskPill(p) {
  const cls = p < 0.3 ? "ok" : p < 0.6 ? "medium" : "high";
  const txt = p < 0.3 ? "низкий" : p < 0.6 ? "средний" : "высокий";
  return `<span class="pill ${cls}">${txt} (${Math.round(p * 100)}%)</span>`;
}

async function initMap() {
  map = L.map("map").setView([43.5, 50.0], 4);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 12, attribution: "&copy; OpenStreetMap",
  }).addTo(map);
  const routes = await getJSON("/api/routes");
  routes.routes.forEach((r) => {
    const pts = r.nodes.map((n) => [n.lat, n.lon]);
    polylines[r.id] = L.polyline(pts, { color: ROUTE_COLORS[r.id], weight: 4, opacity: 0.85 })
      .addTo(map).bindPopup(`<b>${r.id} · ${r.name}</b><br>${r.via}`);
  });
  const nodes = await getJSON("/api/nodes");
  nodes.nodes.forEach((n) => {
    L.circleMarker([n.lat, n.lon], { radius: 6, color: "#06283d", fillColor: "#fff", fillOpacity: 1, weight: 2 })
      .addTo(map).bindPopup(`<b>${n.name}</b> (${n.country})<br>${n.kind}`);
  });
}

function scenarioQuery() {
  const q = [];
  if (scenario.close) q.push(`close=${scenario.close}`);
  if (scenario.extra_customs_h) q.push(`extra_customs_h=${scenario.extra_customs_h}`);
  return q.length ? "&" + q.join("&") : "";
}

async function loadCompare() {
  const data = await getJSON(`/api/compare?weight_t=48&storm=${storm}${scenarioQuery()}`);
  $("#compare-table tbody").innerHTML = data.routes.map((r) => `
    <tr><td><b>${r.route_id}</b> ${r.route_name}<br><span class="hint">${r.via}</span></td>
    <td>${r.total_predicted_h} ч</td><td>${r.eta_p90_h} ч</td><td>+${r.delay_h} ч</td><td>${riskPill(r.delay_probability)}</td>
    <td>${(r.co2_kg / 1000).toFixed(1)} т</td><td>$${r.cost_usd.toLocaleString("ru-RU")}</td>
    <td>${r.route_id === data.best.time ? "⏱ быстрее" : ""} ${r.route_id === data.best.co2 ? "🌱 чище" : ""} ${r.route_id === data.best.risk ? "🛡 надёжнее" : ""}</td></tr>`).join("");
  const best = data.routes.find((r) => r.route_id === data.best.co2);
  $("#kpi-co2").textContent = `${best.route_id}: ${(best.co2_kg / 1000).toFixed(1)} т CO₂`;
  const ports = await getJSON("/api/ports");
  const worst = ports.ports.reduce((a, b) => (a.congestion_pct > b.congestion_pct ? a : b));
  $("#kpi-port").textContent = `${worst.name}: ${worst.congestion_pct}%`;
}

async function loadShipments() {
  const data = await getJSON("/api/shipments");
  shipments = data.shipments;
  $("#kpi-ship").textContent = shipments.length;
  const avg = shipments.reduce((s, x) => s + x.delay_h, 0) / shipments.length;
  $("#kpi-delay").textContent = `+${avg.toFixed(1)} ч`;
  $("#shipment-select").innerHTML = shipments.map((s) =>
    `<option value="${s.id}">${s.id} — ${s.name} (${s.route_name})</option>`).join("");
  currentShipment = shipments[0].id;
  await loadShipment();
}

async function loadShipment() {
  const s = await getJSON(`/api/shipments/${currentShipment}?storm=${storm}`);
  $("#shipment-info").innerHTML =
    `<b>${s.id} · ${s.name}</b> — ${s.cargo}, ${s.weight_t} т · ${s.status} · ETA ${s.eta}`;
  const nameOf = {};
  (await getJSON("/api/nodes")).nodes.forEach((n) => (nameOf[n.id] = n.name));
  $("#timeline").innerHTML = s.forecast.legs.map((l) => `
    <li class="${l.id === s.forecast.bottleneck_leg_id ? "bottleneck" : ""}">
      <b>${MODE_RU[l.mode]} ${nameOf[l.from]} → ${nameOf[l.to]}</b>
      ${l.id === s.forecast.bottleneck_leg_id ? " ⚠ узкое место" : ""}<br>
      план ${l.base_h} ч → прогноз ${l.predicted_h} ч (+${l.delay_h} ч)<br>
      <span class="why">${l.reasons.join("; ")}</span>
    </li>`).join("");
  $("#verdict").textContent =
    `Прогноз: ${s.forecast.total_predicted_h} ч вместо ${s.forecast.total_base_h} ч ` +
    `(+${s.forecast.delay_h} ч). ${s.forecast.recommendation} CO₂ маршрута: ${(s.forecast.co2_kg / 1000).toFixed(1)} т.`;
  const docLabel = { verified: "проверен", pending: "на проверке", missing: "нет документа" };
  $("#docs-list").innerHTML = Object.entries(s.docs).map(([k, v]) => `
    <li><span class="dot ${v}"></span><b>${s.doc_types[k] || k}</b> — ${docLabel[v] || v}</li>`).join("");
  $("#clearance-result").textContent = "";
  await loadLedger();
  const mlp = await getJSON(`/api/ml/shipment/${currentShipment}?storm=${storm}`);
  const top = mlp.top_factors.map((f) => `${f.feature} (${f.importance})`).join(", ");
  const attr = mlp.local_attribution.map((a) =>
    `${a.feature}: ${a.contribution_h >= 0 ? "+" : ""}${a.contribution_h} ч`).join("; ");
  $("#ml-box").innerHTML =
    `🤖 <b>ML-прогноз</b> (ансамбль RandomForest + HistGradientBoosting): ` +
    `задержка <b>+${mlp.ml_delay_h} ч</b> против скоринга +${mlp.scoring_delay_h} ч ` +
    `(расхождение ${mlp.difference_h} ч). Разбор прогноза: ${attr}. ` +
    `Главные факторы глобально: ${top}. ` +
    `<span class="hint">Обучена на реальных распределениях погоды (архив 90 дней); метки задержек — по правилам коридора.</span>`;
  const tl = await getJSON(`/api/shipments/${currentShipment}/timeline?storm=${storm}`);
  const statusIcon = { done: "✅", in_progress: "🔄", planned: "⏳" };
  $("#events-timeline").innerHTML = tl.events.map((e) => `
    <li>${statusIcon[e.status] || ""} <b>${e.title}</b><br>
    <span class="hint">${e.time.slice(0, 16).replace("T", " ")}${e.predicted_time ? " → прогноз " + e.predicted_time.slice(0, 16).replace("T", " ") : ""}</span><br>
    <span class="why">${e.detail || ""}</span></li>`).join("");
}

async function loadExceptions() {
  const data = await getJSON(`/api/exceptions?storm=${storm}`);
  $("#exceptions-list").innerHTML = data.exceptions.length ? data.exceptions.map((e) => `
    <li class="exc ${e.severity}"><b>${e.severity === "high" ? "🔴" : "🟡"} ${e.title}</b><br>
    ${e.detail}<br><span class="action">→ ${e.action}</span></li>`).join("")
    : `<li class="hint">Исключений нет — все грузы в графике.</li>`;
}

async function initSearch() {
  const nodes = await getJSON("/api/nodes");
  const opts = nodes.nodes.map((n) => `<option value="${n.id}">${n.name}</option>`).join("");
  $("#search-from").innerHTML = opts;
  $("#search-to").innerHTML = opts;
  $("#search-from").value = "moscow";
  $("#search-to").value = "bandar_abbas";
}

async function runSearch() {
  const from = $("#search-from").value, to = $("#search-to").value;
  const obj = $("#search-objective").value;
  const data = await getJSON(`/api/routes/search?from_node=${from}&to_node=${to}&weight_t=48&storm=${storm}&objective=${obj}${scenarioQuery()}`);
  $("#search-results").innerHTML = data.paths.length ? data.paths.map((p, i) => `
    <div class="path-card"><b>${i + 1}. ${p.node_names.join(" → ")}</b>
    ${p.pareto_optimal ? " <span class='pill ok'>Парето-оптимален</span>" : ""}<br>
    <span class="hint">${p.modes.map((m) => MODE_RU[m]).join(" · ")}</span><br>
    ${p.total_predicted_h} ч (P90 ${p.eta_p90_h || "—"} ч) · +${p.delay_h} ч · риск ${Math.round(p.delay_probability * 100)}% · CO₂ ${(p.co2_kg / 1000).toFixed(1)} т · $${p.cost_usd.toLocaleString("ru-RU")}</div>`).join("")
    : `<p class="hint">Маршрутов нет — все пути заблокированы сценарием.</p>`;
}

async function loadLedger() {
  const data = await getJSON("/api/clearance/ledger");
  $("#ledger").innerHTML = data.entries.length ? data.entries.map((e) => `
    <li>#${e.seq} · ${e.shipment_id} · <b>${e.decision === "pre-cleared" ? "✅ pre-cleared" : "⚠ нужны документы: " + e.missing.join(", ")}</b><br>
    <span class="hash">hash ${e.hash.slice(0, 16)}… ← prev ${e.prev_hash.slice(0, 12)}…</span></li>`).join("")
    : `<li class="hint">Пока пусто — отправьте груз на pre-clearance.</li>`;
}

async function loadCorridor() {
  const data = await getJSON("/api/corridor");
  $("#corridor-stats").innerHTML = data.stats.map((s) =>
    `<div class="stat"><b>${s.value}</b><span>${s.label}</span></div>`).join("");
}

async function loadPorts() {
  const data = await getJSON("/api/ports");
  $("#ports-grid").innerHTML = data.ports.map((p) => {
    const cls = p.congestion_pct >= 60 ? "hot" : p.congestion_pct >= 45 ? "warm" : "";
    return `<div class="port-card"><b>${p.name}</b><br>
      <span class="hint">загруженность ${p.congestion_pct}% · таможня ~${p.customs_queue_h} ч</span>
      <div class="bar"><i class="${cls}" style="width:${p.congestion_pct}%"></i></div></div>`;
  }).join("");
}

async function loadLiveWeather() {
  try {
    const data = await getJSON("/api/weather/live");
    const parts = Object.values(data.segments).map((s) =>
      `${s.segment}: волна ${s.wave_m} м, ветер ${s.wind_ms} м/с`).join(" · ");
    $("#live-badge").textContent = data.live
      ? `🌊 LIVE Open-Meteo: ${parts}`
      : `🌊 Погода: демо (live недоступен): ${parts}`;
  } catch (e) {
    $("#live-badge").textContent = "🌊 Погода: демо";
  }
}

async function runQuote() {
  const routeId = $("#quote-route").value;
  const weight = Math.max(1, +$("#quote-weight").value || 48);
  const fc = await getJSON(`/api/routes/${routeId}/forecast?weight_t=${weight}&storm=${storm}${scenarioQuery()}`);
  const mlp = await getJSON(`/api/ml/forecast?route_id=${routeId}&weight_t=${weight}&storm=${storm}`);
  $("#quote-result").textContent =
    `${fc.route_name}: ${fc.total_predicted_h} ч вместо ${fc.total_base_h} ч ` +
    `(+${fc.delay_h} ч, риск срыва ${Math.round(fc.delay_probability * 100)}%, P90 ${fc.eta_p90_h} ч). ` +
    `ML-прогноз: +${mlp.ml_delay_h} ч. CO₂ ${(fc.co2_kg / 1000).toFixed(1)} т, ` +
    `стоимость ~$${fc.cost_usd.toLocaleString("ru-RU")}. ${fc.recommendation}`;
}

document.addEventListener("DOMContentLoaded", async () => {
  try {
    await Promise.all([initMap(), loadCompare(), loadShipments(), loadCorridor(), loadPorts(), loadLiveWeather(), initSearch(), loadExceptions()]);
  } catch (e) {
    console.error(e);
    $("#verdict").textContent = "Не удалось загрузить данные API. Проверьте, что бэкенд запущен (./run.sh).";
  }
  $("#storm-btn").addEventListener("click", async () => {
    storm = !storm;
    $("#storm-btn").textContent = storm ? "🌩 Шторм на Каспии: ВКЛ" : "🌩 Шторм на Каспии: выкл";
    $("#storm-btn").classList.toggle("on", storm);
    $("#storm-note").textContent = storm
      ? "Шторм: ветер 19 м/с, волна 3,4 м — паромы не выходят, пропуск рейса +48 ч."
      : "Обычная погода: волна 0,9–1,2 м, ветер 9–11 м/с.";
    await Promise.all([loadCompare(), loadShipment(), loadExceptions()]);
  });
  $("#search-btn").addEventListener("click", runSearch);
  $("#scenario-btn").addEventListener("click", async () => {
    scenario = { close: $("#scenario-close").value,
                 extra_customs_h: Math.max(0, +$("#scenario-customs").value || 0) };
    await Promise.all([loadCompare(), runQuote(), loadExceptions()]);
  });
  $("#scenario-reset").addEventListener("click", async () => {
    scenario = { close: "", extra_customs_h: 0 };
    $("#scenario-close").value = "";
    $("#scenario-customs").value = 0;
    await Promise.all([loadCompare(), runQuote(), loadExceptions()]);
  });
  $("#shipment-select").addEventListener("change", (e) => {
    currentShipment = e.target.value;
    loadShipment();
  });
  $("#create-btn").addEventListener("click", async () => {
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
      $("#create-result").textContent = `✅ Груз ${res.id} создан. ETA ${res.eta.slice(0, 16).replace("T", " ")}.`;
      await loadShipments();
      currentShipment = res.id;
      $("#shipment-select").value = res.id;
      await loadShipment();
    } catch (e) {
      $("#create-result").textContent = "Не удалось создать груз: " + e.message;
    }
  });
  $("#quote-btn").addEventListener("click", runQuote);
  $("#clearance-btn").addEventListener("click", async () => {
    const s = shipments.find((x) => x.id === currentShipment);
    const res = await getJSON("/api/clearance/submit", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ shipment_id: s.id, docs: s.docs }),
    });
    $("#clearance-result").textContent = res.decision === "pre-cleared"
      ? `✅ Pre-clearance пройден. Запись #${res.seq} в реестре, hash ${res.hash.slice(0, 16)}…`
      : `⚠ Не хватает документов: ${res.missing.join(", ")}. Запись #${res.seq} — «нужны документы».`;
    await loadLedger();
  });
});
