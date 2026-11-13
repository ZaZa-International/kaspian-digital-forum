import { escape, hours, number, showError, time, unit } from "./operations-labs.js";

const profiles = {
  balanced: "Баланс и устойчивость", time: "Быстрее", cost: "Дешевле", green: "Меньше CO₂",
};
const profileNotes = {
  balanced: "Ищет маршрут, который меньше теряет при ухудшении условий.",
  time: "Выбирает самое короткое расчётное время до доставки.",
  cost: "Выбирает наименьшие расчётные расходы.",
  green: "Выбирает наименьшие расчётные выбросы CO₂e.",
};
const routeMetrics = [
  ["Время", "remaining_h", "ч"],
  ["Стоимость", "total_usd", "USD"],
  ["CO₂e", "co2_kg", "кг"],
];
const connectionStates = {
  delivered: "Перевозка завершена",
  land_only: "Оставшийся путь без парома",
  already_sailed: "Груз уже на морском плече",
  blocked: "Стыковка недоступна",
};
const weatherSources = {
  "Open-Meteo forecast": "прогноз Open-Meteo",
  "demo fallback": "резервные демо-данные",
  "storm scenario": "сценарий шторма",
  "scenario override": "параметры сценария",
};
const miss = (value) => Number.isFinite(value) ? `${number(value * 100)}%` : "—";

function connectionTimeline(connection) {
  const { schedule, baseline, intervention } = connection;
  const points = [
    ["Груз готов", baseline.ready_h, "ready"],
    ["Погрузка закрывается", schedule.loading_closes_in_h, "cutoff"],
    ["Первый паром", schedule.departure_in_h, "ferry"],
    ["После сокращения очереди", intervention.ready_h, "intervention"],
  ];
  const maximum = Math.max(1, ...points.map(([, value]) => value || 0));
  return `<figure class="lab-timeline">
    <figcaption><strong>${escape(connection.port_name || "Паромная стыковка")}</strong><span>Часы от расчёта</span></figcaption>
    <div class="lab-timeline-track" aria-hidden="true">${points.map(([label, value, kind]) =>
      Number.isFinite(value) ? `<i class="timeline-${kind}" style="--lab-point:${Math.max(0, value) / maximum * 100}%" title="${label}"></i>` : "").join("")}</div>
    <dl>${points.map(([label, value, kind]) => `<div class="timeline-${kind}"><dt>${label}</dt><dd>${hours(value)}</dd></div>`).join("")}</dl>
    <p class="lab-meta">Первый рейс ${time(schedule.first_departure_at)} · закрытие погрузки ${time(schedule.loading_closes_at)}. Интервал отправлений ${hours(schedule.interval_h)}.</p>
  </figure>`;
}

function runSummary(title, run) {
  return `<section class="lab-run">
    <h5>${title}</h5>
    <strong class="lab-big">${hours(run.catch_in_h)}</strong>
    <p>${run.misses_first ? "Следующее доступное окно" : "Первое окно"} · ${time(run.catch_at)}</p>
    <dl class="lab-list">
      <div><dt>Запас до закрытия</dt><dd>${hours(run.margin_h)}</dd></div>
      <div><dt>Первый рейс пропущен в симуляциях</dt><dd>${miss(run.p_miss)}</dd></div>
      <div><dt>Ожидание парома · P50 / P90</dt><dd>${hours(run.wait_p50_h)} / ${hours(run.wait_p90_h)}</dd></div>
      <div><dt>До доставки · P50 / P90</dt><dd>${hours(run.arrival_p50_h)} / ${hours(run.arrival_p90_h)}</dd></div>
    </dl></section>`;
}

function renderConnection(connection) {
  if (connection.status !== "eligible") {
    return `<section class="lab-callout"><h5>${escape(connectionStates[connection.status] || "Стыковка не рассчитана")}</h5>
      <p>${escape(connection.reason || "")}</p></section>`;
  }
  const { baseline, intervention, cliff, weather, pivotal_cause: cause } = connection;
  return `${connectionTimeline(connection)}
    ${weather ? `<p class="lab-meta lab-weather">Волна ${unit(weather.wave_m, "м")} · ветер ${unit(weather.wind_ms, "м/с")} · ${escape(weatherSources[weather.source] || weather.source || "источник не указан")}.${weather.closed_until_h > 0 ? ` Модель исключает отправления в первые ${hours(weather.closed_until_h)}.` : ""}</p>` : ""}
    <div class="lab-two-col">${runSummary("Без ускорения", baseline)}${runSummary("После сокращения очереди", intervention)}</div>
    <p class="lab-meta">Доля пропусков получена в симуляциях${connection.simulation?.n ? ` (${number(connection.simulation.n)} прогонов)` : ""}. P50 и P90 — медиана и 90-й перцентиль расчётного времени.</p>
    ${connection.readiness_components?.origin_customs_h ? `<p class="lab-meta">Очередь перед погрузкой в порту · ${hours(connection.readiness_components.origin_customs_h)} · входное допущение модели.</p>` : ""}
    <section class="lab-callout lab-cliff">
      <h5>Что меняет один час</h5>
      ${cliff?.example ? `<p>Готовность к закрытию погрузки даёт паром через <strong>${hours(cliff.example.on_cutoff_catch_in_h)}</strong>. Готовность на час позже — через <strong>${hours(cliff.example.one_hour_late_catch_in_h)}</strong>.</p>` : ""}
      ${cause?.label ? `<p><strong>${escape(cause.label)}.</strong>${cause.detail ? ` ${escape(cause.detail)}` : ""}</p>` : ""}
    </section>`;
}

function renderDecision(decision) {
  if (!decision?.options?.length) return "";
  const scenarios = decision.scenarios || [];
  const routes = decision.options.map((option) => ({
    name: escape(option.node_names?.join(" → ") || "Текущий маршрут"),
    selected: option.id === decision.selected_id,
    tags: [
      option.id === decision.selected_id && "Подходит приоритету",
      option.pareto && "Парето", option.conditional && "Условные плечи", option.blocked && "Недоступен",
    ].filter(Boolean).join(" · "),
    values: routeMetrics.map(([, key, suffix]) => unit(option[key], suffix)),
  }));
  return `<section class="lab-decision" aria-labelledby="lab-options-title">
    <div class="lab-heading"><div><h5 id="lab-options-title">Сравнение маршрутов</h5>
      <p>${escape(profileNotes[decision.profile] || "Оставшийся путь")}</p></div>
      <span class="lab-pill">${escape(profiles[decision.profile] || decision.profile)}</span></div>
    <table class="lab-table" aria-labelledby="lab-options-title"><thead>
      <tr><th scope="col">Маршрут</th>${routeMetrics.map(([label]) => `<th scope="col">${label}</th>`).join("")}</tr></thead><tbody>
      ${routes.map((route) => `<tr class="${route.selected ? "lab-selected" : ""}">
        <td>${route.name}${route.tags ? `<small>${route.tags}</small>` : ""}</td>
        ${route.values.map((value) => `<td>${value}</td>`).join("")}</tr>`).join("")}
      </tbody></table>
    <ul class="lab-route-list" aria-labelledby="lab-options-title">${routes.map((route) => `
      <li class="${route.selected ? "lab-selected" : ""}">
        <strong>${route.name}</strong>${route.tags ? `<p>${route.tags}</p>` : ""}
        <dl>${routeMetrics.map(([label], index) => `<div><dt>${label}</dt><dd>${route.values[index]}</dd></div>`).join("")}</dl>
      </li>`).join("")}</ul>
    ${decision.policy ? `<details class="lab-details"><summary>Как выбирается маршрут</summary>
      <p class="lab-meta">На границе Парето находятся маршруты, которые нельзя улучшить по одному критерию без ухудшения другого.</p>
      <p class="lab-meta">${escape(decision.policy)}</p></details>` : ""}
    ${scenarios.length ? `<details class="lab-details"><summary>Проверка в сложных условиях</summary>
      <div class="lab-stress">${scenarios.map((scenario) => `<section><h6>${escape(scenario.label)}</h6>
        ${decision.options.map((option) => {
          const stress = option.stress?.find((item) => item.id === scenario.id);
          if (!stress) return "";
          return `<div><span>${escape(option.node_names?.join(" → ") || "Текущий маршрут")}</span>
            <strong>${stress.blocked ? "Недоступен" : `${hours(stress.remaining_h)} · ${unit(stress.total_usd, "USD")} · ${unit(stress.co2_kg, "кг CO₂e")}`}</strong></div>`;
        }).join("")}</section>`).join("")}</div></details>` : ""}
  </section>`;
}

export function createResiliencePanel(root, api) {
  const form = root.querySelector("form");
  const status = root.querySelector("[data-resilience-status]");
  const result = root.querySelector("[data-resilience-result]");
  const label = root.querySelector("[data-resilience-context]");
  const controls = Array.from(form.elements);
  controls.forEach((control) => { control.disabled = true; });
  let active = false;
  let context;
  let request;
  let loaded = false;
  let lastId;

  function abort() {
    request?.abort();
    request = null;
    result.setAttribute("aria-busy", "false");
  }

  async function load() {
    if (!context || !active || !form.reportValidity()) return;
    if (Number(form.elements.cutoff_h.value) > Number(form.elements.departure_in_h.value)) {
      showError(status, new Error("Закрытие погрузки должно приходиться на время от расчёта до отправления."));
      return;
    }
    abort();
    const controller = new AbortController();
    request = controller;
    const snapshot = context;
    const parameters = new URLSearchParams(new FormData(form));
    parameters.set("plan_version", snapshot.version);
    parameters.set("storm", snapshot.storm);
    result.replaceChildren();
    result.setAttribute("aria-busy", "true");
    status.textContent = "Рассчитываем стыковку и варианты…";
    loaded = false;
    try {
      const data = await api(`/api/operations/${encodeURIComponent(snapshot.id)}/resilience?${parameters}`, { signal: controller.signal });
      if (request !== controller || context !== snapshot) return;
      if (data.shipment_id !== snapshot.id || data.plan_version !== snapshot.version) {
        throw new Error("Версия плана изменилась. Обновите пульт и повторите расчёт.");
      }
      result.innerHTML = renderConnection(data.connection) + renderDecision(data.decision);
      status.textContent = "Предпросмотр · принятый план не изменён";
      loaded = true;
    } catch (error) {
      if (request === controller && error.name !== "AbortError") showError(status, error, load);
    } finally {
      if (request === controller) {
        request = null;
        result.setAttribute("aria-busy", "false");
      }
    }
  }

  function example() {
    if (!context) return;
    form.elements.departure_in_h.value = "24";
    form.elements.cutoff_h.value = "6";
    form.elements.feeder_delay_h.value = "13";
    form.elements.customs_reduction_pct.value = "25";
    load();
  }

  form.addEventListener("submit", (event) => { event.preventDefault(); load(); });
  root.querySelector("[data-resilience-example]").addEventListener("click", example);
  return {
    setContext(next) {
      context = next;
      label.textContent = `${next.id} · план v${next.version}${next.storm ? " · шторм" : ""}`;
      controls.forEach((control) => { control.disabled = false; });
      if (active) load();
    },
    suspend(id) {
      abort();
      if (id !== lastId) form.reset();
      lastId = id;
      context = null;
      loaded = false;
      controls.forEach((control) => { control.disabled = true; });
      result.replaceChildren();
      label.textContent = "";
      status.textContent = id ? "Обновляем паспорт…" : "Выберите перевозку.";
    },
    setActive(next) {
      active = next;
      if (active && !loaded && !request) load();
      if (!active) abort();
    },
    example,
  };
}
