/* Smart Caspian Transit frontend: map + compare + shipment twin + clearance. */
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const $ = (s) => document.querySelector(s);
let storm = false;
let scenario = { close: "", extra_customs_h: 0, wave_m: null, wind_ms: null };
let map, polylines = {};
let searchLayer = null, lastPaths = [], nodeCache = {};
let shipments = [];
let currentShipment = null;
const MODE_RU = { rail: "🚂 ж/д", sea: "⛴ море", road: "🚛 авто" };
const ROUTE_COLORS = { R1: "#0e7aa8", R2: "#1e7d46", R3: "#c96a12" };
const fmtDT = (iso) => String(iso || "").slice(0, 16).replace("T", " ");

function setBanner(text) {
  const b = $("#offline-banner");
  if (text) { b.textContent = text; b.hidden = false; } else { b.hidden = true; }
}
function updateOfflineBanner() {
  setBanner(navigator.onLine ? "" : "⚠ Офлайн: API недоступен — данные не обновляются, карта без тайлов.");
}
window.addEventListener("online", () => { updateOfflineBanner(); refreshAll().catch(() => {}); });
window.addEventListener("offline", updateOfflineBanner);

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
      .addTo(map).bindPopup(`<b>${esc(r.id)} · ${esc(r.name)}</b><br>${esc(r.via)}`);
  });
  const nodes = await getJSON("/api/nodes");
  nodes.nodes.forEach((n) => {
    nodeCache[n.id] = n;
    L.circleMarker([n.lat, n.lon], { radius: 6, color: "#06283d", fillColor: "#fff", fillOpacity: 1, weight: 2 })
      .addTo(map).bindPopup(`<b>${esc(n.name)}</b> (${esc(n.country)})<br>${esc(n.kind)}${n.note ? "<br><span class='hint'>" + esc(n.note) + "</span>" : ""}`);
  });
  searchLayer = L.layerGroup().addTo(map);
}

function drawSearchPath(idx) {
  if (!searchLayer || !lastPaths[idx]) return;
  searchLayer.clearLayers();
  const p = lastPaths[idx];
  const pts = p.nodes.map((id) => [nodeCache[id].lat, nodeCache[id].lon]);
  L.polyline(pts, { color: "#c0392b", weight: 5, opacity: 0.9, dashArray: "8 6" })
    .addTo(searchLayer)
    .bindPopup(`<b>Выбранный маршрут (схема)</b><br>${esc(p.node_names.join(" → "))}<br>${p.total_predicted_h} ч · CO₂ ${(p.co2_kg / 1000).toFixed(1)} т`);
  pts.forEach((pt) => L.circleMarker(pt, { radius: 5, color: "#c0392b", fillColor: "#fff", fillOpacity: 1, weight: 2 }).addTo(searchLayer));
  map.fitBounds(L.latLngBounds(pts).pad(0.2));
}

function scenarioQuery() {
  const q = [];
  if (scenario.close) q.push(`close=${encodeURIComponent(scenario.close)}`);
  if (scenario.extra_customs_h) q.push(`extra_customs_h=${scenario.extra_customs_h}`);
  if (scenario.wave_m != null) q.push(`wave_m=${scenario.wave_m}`);
  if (scenario.wind_ms != null) q.push(`wind_ms=${scenario.wind_ms}`);
  return q.length ? "&" + q.join("&") : "";
}

async function loadCompare() {
  const data = await getJSON(`/api/compare?weight_t=48&storm=${storm}${scenarioQuery()}`);
  $("#compare-table tbody").innerHTML = data.routes.map((r) => `
    <tr><td><b>${esc(r.route_id)}</b> ${esc(r.route_name)}<br><span class="hint">${esc(r.via)}</span></td>
    <td>${r.total_predicted_h} ч${r.total_wait_h ? `<br><span class="hint">вкл. ожидание парома ${r.total_wait_h} ч</span>` : ""}</td><td>${r.eta_p90_h} ч</td><td>+${r.delay_h} ч</td><td>${riskPill(r.delay_probability)}</td>
    <td>${(r.co2_kg / 1000).toFixed(1)} т</td><td>$${esc(r.cost_usd.toLocaleString("ru-RU"))}</td>
    <td>${r.storm_price_h} ч</td>
    <td>${r.route_id === data.best.time ? "⏱ быстрее" : ""} ${r.route_id === data.best.co2 ? "🌱 чище" : ""} ${r.route_id === data.best.risk ? "🛡 надёжнее" : ""}</td></tr>`).join("");
  const best = data.routes.find((r) => r.route_id === data.best.co2);
  $("#kpi-co2").textContent = `${best.route_id}: ${(best.co2_kg / 1000).toFixed(1)} т CO₂`;
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
    `<option value="${esc(s.id)}">${esc(s.id)} — ${esc(s.name)} (${esc(s.route_name)})${s.blocked ? " ⛔ заблокирован" : ""}</option>`).join("");
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
      `<b>${esc(currentShipment)}</b> — ⛔ маршрут заблокирован сценарием (закрыт узел на пути).`;
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
      ${l.id === s.forecast.bottleneck_leg_id ? " ⚠ узкое место" : ""}${l.conditional ? " <span class='pill medium'>условное плечо</span>" : ""}<br>
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
  $("#docs-list").innerHTML = (s.documents || []).map((d) => `
    <li><span class="dot ${esc(d.status)}"></span><b>${esc(s.doc_types[d.doc_type] || d.doc_type)}</b> — ${esc(docLabel[d.status] || d.status)}
    ${d.issuer ? `<br><span class="hint">решение: ${esc(d.issuer)}${d.decided_at ? " · " + esc(fmtDT(d.decided_at)) : ""}</span>` : ""}
    ${d.content_hash ? `<br><span class="hash">hash ${esc(d.content_hash.slice(0, 12))}… · источник: ${esc(d.source || "—")}</span>` : ""}
    ${d.status !== "verified" ? `<br><input type="text" class="doc-ref" data-doc="${esc(d.doc_type)}" placeholder="Ссылка на документ (REJ- → отказ, EXP- → истёк)" value="${esc(d.ref || "")}">` : ""}
    </li>`).join("");
  $("#clearance-result").textContent = "";
  await loadLedger();
  const mlp = await getJSON(`/api/ml/shipment/${currentShipment}?storm=${storm}`);
  const top = mlp.top_factors.map((f) => `${f.feature} (${f.importance})`).join(", ");
  const attr = mlp.local_attribution.map((a) =>
    `${a.feature}: ${a.contribution_h >= 0 ? "+" : ""}${a.contribution_h} ч`).join("; ");
  const ex = mlp.extrapolation || {};
  const exFlag = ex.is_extrapolating
    ? `<span class="pill high">⚠ ЭКСТРАПОЛЯЦИЯ: L1 ${ex.l1_to_median} &gt; p95 опыта ${ex.train_p95} — модель вне своего опыта, доверяйте скорингу</span><br>`
    : `<span class="pill ok">в пределах опыта: L1 ${ex.l1_to_median} ≤ p95 ${ex.train_p95}</span><br>`;
  const analogs = (mlp.analogs || []).map((a) =>
    `волна ${a.max_wave_m} м, ветер ${a.max_wind_ms} м/с, порт ${a.max_congestion_pct}% → +${a.delay_h} ч (L1 ${a.l1})`).join("; ");
  $("#ml-box").innerHTML =
    exFlag +
    `🤖 <b>ML-прогноз (эксперимент)</b> — ансамбль RandomForest + HistGradientBoosting: ` +
    `задержка <b>+${mlp.ml_delay_h} ч</b> против скоринга +${mlp.scoring_delay_h} ч ` +
    `(расхождение ${mlp.difference_h} ч). Разбор прогноза: ${esc(attr)}. ` +
    `Главные факторы глобально: ${esc(top)}. ` +
    `<span class="hint">Метки задержек — синтетические (правила коридора + шум); волна/ветер — из реального архива Open-Meteo. Метрики — synthetic benchmark, не точность на реальных рейсах.</span>` +
    (analogs ? `<br><span class="hint">Похожие сценарии (kNN по манхэттенскому L1, демо-аналоги — иллюстрация, не точный прогноз): ${esc(analogs)}.</span>` : "");
  const tl = await getJSON(`/api/shipments/${currentShipment}/timeline?storm=${storm}${scenarioQuery()}`);
  const statusIcon = { done: "✅", in_progress: "🔄", planned: "⏳" };
  const actualHtml = (tl.actual_events || []).map((e) => `
    <li>📍 <b>${esc(e.title)}</b> <span class="pill ok">факт</span><br>
    <span class="hint">${esc(fmtDT(e.time))} · источник: ${esc(e.source)}${e.location ? " · " + esc(e.location) : ""}</span><br>
    <span class="why">${esc(e.note || "")}</span></li>`).join("");
  $("#events-timeline").innerHTML = actualHtml + tl.events.map((e) => `
    <li>${statusIcon[e.status] || ""} <b>${esc(e.title)}</b> <span class="hint">(модель)</span><br>
    <span class="hint">${esc(fmtDT(e.time))}${e.predicted_time ? " → прогноз " + esc(fmtDT(e.predicted_time)) : ""}</span><br>
    <span class="why">${esc(e.detail || "")}</span></li>`).join("");
}

async function loadExceptions() {
  const data = await getJSON(`/api/exceptions?storm=${storm}${scenarioQuery()}`);
  $("#exceptions-list").innerHTML = data.exceptions.length ? data.exceptions.map((e) => `
    <li class="exc ${esc(e.severity)}"><b>${e.severity === "high" ? "🔴" : "🟡"} ${esc(e.title)}</b><br>
    ${esc(e.detail)}<br><span class="action">→ ${esc(e.action)}</span></li>`).join("")
    : `<li class="hint">Исключений нет — все грузы в графике.</li>`;
}

async function initSearch() {
  const nodes = await getJSON("/api/nodes");
  const opts = nodes.nodes.map((n) => `<option value="${esc(n.id)}">${esc(n.name)}</option>`).join("");
  $("#search-from").innerHTML = opts;
  $("#search-to").innerHTML = opts;
  $("#search-from").value = "moscow";
  $("#search-to").value = "bandar_abbas";
}

async function runSearch() {
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
    <div class="path-card" data-idx="${i}" title="Показать на карте"><b>${i + 1}. ${esc(p.node_names.join(" → "))}</b>
    ${p.pareto_optimal ? " <span class='pill ok'>Парето (среди найденных)</span>" : ""}
    ${p.has_conditional_legs ? " <span class='pill medium'>есть условные плечи</span>" : ""}<br>
    <span class="hint">${p.modes.map((m) => MODE_RU[m]).join(" · ")}</span><br>
    ${p.total_predicted_h} ч${p.total_wait_h ? ` (вкл. ожидание ${p.total_wait_h} ч)` : ""} · P90 симуляции ${p.eta_p90_h || "—"} ч · +${p.delay_h} ч · индекс риска ${Math.round(p.delay_probability * 100)}% · CO₂ ${(p.co2_kg / 1000).toFixed(1)} т · $${esc(p.cost_usd.toLocaleString("ru-RU"))}<br><span class="hint">${esc(p.search_algorithm)} · раскрытий узлов: ${p.expansions_first_search}</span></div>`).join("")
    : `<p class="hint">Маршрутов нет — все пути заблокированы сценарием.</p>`;
  document.querySelectorAll("#search-results .path-card").forEach((card) => {
    card.addEventListener("click", () => {
      document.querySelectorAll("#search-results .path-card").forEach((c) => c.classList.remove("selected"));
      card.classList.add("selected");
      drawSearchPath(+card.dataset.idx);
    });
  });
  if (data.paths.length) {
    drawSearchPath(0);
    const first = document.querySelector("#search-results .path-card");
    if (first) first.classList.add("selected");
  } else if (searchLayer) {
    searchLayer.clearLayers();
  }
}

async function loadLedger() {
  const data = await getJSON("/api/clearance/ledger");
  $("#ledger").innerHTML = data.entries.length ? data.entries.map((e) => `
    <li>#${e.seq} · ${esc(e.shipment_id)} · <b>${esc(e.decision === "pre-cleared" ? "✅ pre-cleared" : "⚠ нужны документы: " + e.missing.join(", "))}</b><br>
    <span class="hint">${esc(e.issuer || "demo")} · правила ${esc(e.rule_version || "v0")} · ${esc(fmtDT(e.ts))}</span><br>
    <span class="hash">hash ${esc(e.hash.slice(0, 16))}… ← prev ${esc(e.prev_hash.slice(0, 12))}…</span></li>`).join("")
    : `<li class="hint">Пока пусто — отправьте груз на pre-clearance.</li>`;
}

async function loadProvenance() {
  try {
    const data = await getJSON("/api/provenance");
    const kindLabel = { live: "live", fallback: "fallback", historical: "исторические",
                        demo: "демо", synthetic: "синтетика", static: "справочник",
                        assumption: "допущение" };
    $("#sources-list").innerHTML = data.sources.map((s) => `
      <li><span class="pill ${s.kind === "live" ? "ok" : s.kind === "demo" || s.kind === "synthetic" ? "medium" : "ok"}">${esc(kindLabel[s.kind] || s.kind)}</span>
      <b>${esc(s.what)}</b><br><span class="hint">${esc(s.source || "")}${s.license ? " · " + esc(s.license) : ""}${s.retrieved ? " · получено: " + esc(String(s.retrieved).slice(0, 16)) : ""}</span>
      ${s.note ? `<br><span class="why">${esc(s.note)}</span>` : ""}</li>`).join("");
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
    return `<div class="port-card"><b>${esc(p.name)}</b><br>
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
    `${fc.route_name}: ${fc.total_predicted_h} ч = ход ${fc.total_base_h} ч` +
    (fc.total_wait_h ? ` + ожидание парома ${fc.total_wait_h} ч` : "") +
    ` + задержки ${fc.delay_h} ч (индекс риска ${Math.round(fc.delay_probability * 100)}%, P90 симуляции ${fc.eta_p90_h} ч). ` +
    `ML-прогноз: +${mlp.ml_delay_h} ч. CO₂ ${(fc.co2_kg / 1000).toFixed(1)} т, ` +
    `стоимость ~$${fc.cost_usd.toLocaleString("ru-RU")}. ${fc.recommendation}`;
}

async function refreshAll() {
  await Promise.all([loadCompare(), loadShipments(), loadExceptions()]);
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

document.addEventListener("DOMContentLoaded", async () => {
  try {
    await Promise.all([initMap(), loadCompare(), loadShipments(), loadCorridor(), loadPorts(), loadLiveWeather(), initSearch(), loadExceptions(), loadProvenance()]);
  } catch (e) {
    console.error(e);
    $("#verdict").textContent = "Не удалось загрузить данные API. Проверьте, что бэкенд запущен (./run.sh).";
    setBanner("⚠ API недоступен: проверьте, что бэкенд запущен (./run.sh). Данные не показаны.");
  }
  updateOfflineBanner();
  $("#storm-btn").addEventListener("click", async () => {
    storm = !storm;
    $("#storm-btn").textContent = storm ? "🌩 Шторм на Каспии: ВКЛ" : "🌩 Шторм на Каспии: выкл";
    $("#storm-btn").classList.toggle("on", storm);
    $("#storm-note").textContent = storm
      ? "Шторм: ветер 19 м/с, волна 3,4 м — паромы не выходят, пропуск рейса +48 ч."
      : "Обычная погода: волна 0,9–1,2 м, ветер 9–11 м/с.";
    await refreshAll();
  });
  $("#search-btn").addEventListener("click", runSearch);
  $("#scenario-btn").addEventListener("click", async () => {
    readScenarioInputs();
    await Promise.all([refreshAll(), runQuote()]);
  });
  $("#scenario-reset").addEventListener("click", async () => {
    scenario = { close: "", extra_customs_h: 0, wave_m: null, wind_ms: null };
    $("#scenario-close").value = "";
    $("#scenario-customs").value = 0;
    $("#scenario-wave").value = "";
    $("#scenario-wind").value = "";
    await Promise.all([refreshAll(), runQuote()]);
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
      await loadExceptions();
    } catch (e) {
      $("#create-result").textContent = "Не удалось создать груз: " + e.message;
    }
  });
  $("#quote-btn").addEventListener("click", runQuote);
  $("#event-btn").addEventListener("click", async () => {
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
      $("#event-result").textContent = `✅ Событие ${res.event_type} записано (источник: ${res.source}).`;
      $("#event-note").value = "";
      await loadShipment();
    } catch (e) {
      $("#event-result").textContent = "Ошибка записи события: " + e.message;
    }
  });
  $("#clearance-btn").addEventListener("click", async () => {
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
      $("#clearance-result").textContent = res.decision === "pre-cleared"
        ? `✅ Pre-clearance пройден (симуляция). Запись #${res.seq}, hash ${res.hash.slice(0, 16)}… Решение: ${res.issuer}, правила ${res.rule_version}.`
        : `⚠ Не хватает документов: ${res.missing.join(", ")}. Запись #${res.seq} — «нужны документы». Решение: ${res.issuer}.`;
    } catch (e) {
      $("#clearance-result").textContent = "Ошибка pre-clearance: " + e.message;
    }
  });
});
