/* Smart Caspian Transit frontend: map + compare + shipment twin + clearance. */
const $ = (s) => document.querySelector(s);
let storm = false;
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

async function loadCompare() {
  const data = await getJSON(`/api/compare?weight_t=48&storm=${storm}`);
  $("#compare-table tbody").innerHTML = data.routes.map((r) => `
    <tr><td><b>${r.route_id}</b> ${r.route_name}<br><span class="hint">${r.via}</span></td>
    <td>${r.total_predicted_h} ч</td><td>+${r.delay_h} ч</td><td>${riskPill(r.delay_probability)}</td>
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
  $("#ml-box").innerHTML =
    `🤖 <b>ML-прогноз</b> (RandomForest, обучена на 800 синтетических рейсах): ` +
    `задержка <b>+${mlp.ml_delay_h} ч</b> против скоринга +${mlp.scoring_delay_h} ч ` +
    `(расхождение ${mlp.difference_h} ч). Главные факторы: ${top}. ` +
    `<span class="hint">Модель честно обучена на синтетике — реальных исторических данных в кейсе не было.</span>`;
}

async function loadLedger() {
  const data = await getJSON("/api/clearance/ledger");
  $("#ledger").innerHTML = data.entries.length ? data.entries.map((e) => `
    <li>#${e.seq} · ${e.shipment_id} · <b>${e.decision === "pre-cleared" ? "✅ pre-cleared" : "⚠ нужны документы: " + e.missing.join(", ")}</b><br>
    <span class="hash">hash ${e.hash.slice(0, 16)}… ← prev ${e.prev_hash.slice(0, 12)}…</span></li>`).join("")
    : `<li class="hint">Пока пусто — отправьте груз на pre-clearance.</li>`;
}

document.addEventListener("DOMContentLoaded", async () => {
  try {
    await Promise.all([initMap(), loadCompare(), loadShipments()]);
  } catch (e) {
    console.error(e);
    $("#verdict").textContent = "Не удалось загрузить данные API. Проверьте, что бэкенд запущен (./run.sh).";
  }
  $("#storm-btn").addEventListener("click", async () => {
    storm = !storm;
    $("#storm-btn").textContent = storm ? "🌩 Шторм на Каспии: ВКЛ" : "🌩 Шторм на Каспии: выкл";
    $("#storm-btn").classList.toggle("on", storm);
    $("#storm-note").textContent = storm
      ? "Шторм: ветер 19 м/с, волна 3,4 м — паромы не выходят по расписанию, морские плечи +24 ч."
      : "Обычная погода: волна 0,9–1,2 м, ветер 9–11 м/с.";
    await Promise.all([loadCompare(), loadShipment()]);
  });
  $("#shipment-select").addEventListener("change", (e) => {
    currentShipment = e.target.value;
    loadShipment();
  });
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
