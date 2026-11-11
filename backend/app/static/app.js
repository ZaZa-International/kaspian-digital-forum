/* Caspian Twin frontend: map + chart + scenario simulator. No framework. */
const $ = (s) => document.querySelector(s);

async function getJSON(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(url + " -> " + r.status);
  return r.json();
}

let map, chart;
let simYear = 2040, simScenario = "baseline";

const STATUS_RU = { ok: "В норме", warning: "Риск", critical: "Критично" };

async function initMap() {
  map = L.map("map").setView([43.5, 50.5], 5);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 12, attribution: '&copy; OpenStreetMap',
  }).addTo(map);
  const data = await getJSON("/api/ports");
  L.polygon(data.sea_polygon, { color: "#0e7aa8", weight: 2, fillOpacity: 0.18 }).addTo(map)
    .bindPopup("Каспийское море — крупнейший замкнутый водоём планеты");
  data.ports.forEach((p) => {
    L.circleMarker([p.lat, p.lon], { radius: 7, color: "#0e7aa8", fillColor: "#0e7aa8", fillOpacity: 0.9 })
      .addTo(map)
      .bindPopup(`<b>${p.name}</b> (${p.country})<br>Глубина: ${p.depth_m} м<br>${p.cargo}`);
  });
  const st = await getJSON("/api/stations");
  st.stations.forEach((s) => {
    const col = s.status === "warning" ? "#b7791f" : "#1e7d46";
    L.circleMarker([s.lat, s.lon], { radius: 6, color: col, fillColor: col, fillOpacity: 0.9 })
      .addTo(map)
      .bindPopup(`<b>${s.name}</b><br>Температура: ${s.temp_c} °C<br>Индекс загрязнения: ${s.pollution_index}/100`);
  });
  st.protected_areas.forEach((a) => {
    L.circleMarker([a.lat, a.lon], { radius: 8, color: "#d4a017", fillColor: "#f5d76e", fillOpacity: 0.8 })
      .addTo(map).bindPopup(`<b>${a.name}</b><br>${a.note}`);
  });
}

async function initChart() {
  const hist = await getJSON("/api/level/history");
  const fc = await getJSON("/api/level/forecast?scenario=baseline&year=2050");
  const ctx = $("#chart").getContext("2d");
  chart = new Chart(ctx, {
    type: "line",
    data: {
      labels: hist.series.map((p) => p.year).concat(fc.series.map((p) => p.year)),
      datasets: [
        { label: "История (демо-ряд)", data: hist.series.map((p) => p.level_m), borderColor: "#0e7aa8", tension: 0.2 },
        { label: "Прогноз, базовый", data: hist.series.map(() => null).concat(fc.series.map((p) => p.level_m)), borderColor: "#c0392b", borderDash: [6, 4], tension: 0.2 },
      ],
    },
    options: { responsive: true, plugins: { legend: { position: "bottom" } },
      scales: { y: { title: { display: true, text: "Уровень, м" } } } },
  });
  $("#kpi-level").textContent = hist.current.level_m + " м";
}

async function runSim() {
  const res = await getJSON("/api/scenarios/simulate", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ year: simYear, scenario: simScenario }),
  });
  $("#sim-verdict").textContent = `${res.year} · ${res.scenario.name} сценарий · уровень ${res.level_m} м (падение ${res.drop_m} м с 2026). ${res.verdict}`;
  $("#kpi-risk").textContent = `${res.ports_at_risk} из ${res.ports.length}`;
  $("#ports-table tbody").innerHTML = res.ports.map((p) => `
    <tr><td><b>${p.name}</b></td><td>${p.country}</td><td>${p.depth_m} м</td>
    <td>${p.remaining_depth_m} м</td>
    <td><span class="pill ${p.status}">${STATUS_RU[p.status]}</span></td></tr>`).join("");
}

async function initAlerts() {
  const data = await getJSON("/api/alerts");
  $("#alerts").innerHTML = data.alerts.map((a) => `
    <li><span class="tag ${a.level}">${a.level === "warning" ? "⚠ внимание" : a.level === "ok" ? "✓ норма" : "ℹ инфо"}</span>
    <b>${a.title}</b><br><span class="hint">${a.text} — ${a.place}, ${a.time}</span></li>`).join("");
}

document.addEventListener("DOMContentLoaded", async () => {
  try {
    await Promise.all([initMap(), initChart(), runSim(), initAlerts()]);
  } catch (e) {
    console.error(e);
    $("#sim-verdict").textContent = "Не удалось загрузить данные API. Проверьте, что бэкенд запущен (./run.sh).";
  }
  $("#sim-year").addEventListener("input", (e) => {
    simYear = +e.target.value;
    $("#sim-year-label").textContent = simYear;
    runSim();
  });
  document.querySelectorAll("#sim-scenarios button").forEach((b) => {
    b.addEventListener("click", () => {
      document.querySelectorAll("#sim-scenarios button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      simScenario = b.dataset.scenario;
      runSim();
    });
  });
});
