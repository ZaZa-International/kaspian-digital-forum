const featureNames = {
  max_wave_m: "Высота волн",
  max_wind_ms: "Скорость ветра",
  max_congestion_pct: "Загруженность порта",
  total_customs_h: "Время на таможне",
  sea_km: "Длина морских плеч",
  rail_km: "Длина железнодорожных плеч",
  weight_t: "Масса груза",
  sea_legs: "Число морских плеч",
};
const modelNames = {
  ensemble_rf_hgb: "Ансамбль деревьев",
  random_forest: "Случайный лес",
  hist_gradient_boosting: "Градиентный бустинг",
  linear_regression: "Линейная регрессия",
  median: "Медиана задержки",
};
const reliabilityNames = {
  synthetic_only: "Демонстрационная оценка",
  out_of_distribution: "Условия вне обучающей выборки",
  unverified_history: "История требует проверки",
  reference_eligible: "ML доступен для сравнения",
  scoring_only: "Доступен расчёт по правилам",
};
const deadlineNames = {
  before_interval: "Срок раньше всего интервала",
  within_interval: "Срок попадает в интервал",
  after_interval: "Срок позже всего интервала",
};

const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (character) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
const formatter = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });
const isNumber = (value) => typeof value === "number" && Number.isFinite(value);
const number = (value) => isNumber(value) ? formatter.format(value) : "—";
const hours = (value) => isNumber(value) ? `${number(value)} ч` : "—";
const percent = (value) => isNumber(value) ? `${number(value * 100)}%` : "—";
const signed = (value) => isNumber(value) ? `${value > 0 ? "+" : ""}${hours(value)}` : "—";
const date = (value) => {
  if (!value) return "—";
  const time = new Date(value);
  return Number.isNaN(time.valueOf()) ? "—" : time.toLocaleString("ru-RU", {
    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
  });
};

function getInterval(prediction) {
  const range = prediction?.interval;
  return isNumber(range?.lower_h) && isNumber(range?.upper_h) ? range : null;
}

function renderEstimate(data, comparison) {
  const prediction = data.prediction || {};
  const interval = getInterval(prediction);
  const score = prediction.scoring_delay_h ?? data.baseline?.scoring_delay_h;
  const synthetic = prediction.dataset_kind === "synthetic";
  const estimate = prediction.ml_delay_h;
  const marker = interval && isNumber(estimate) && interval.upper_h > interval.lower_h
    ? Math.max(0, Math.min(100, (estimate - interval.lower_h) / (interval.upper_h - interval.lower_h) * 100))
    : 50;
  const delta = isNumber(estimate) && isNumber(comparison) ? estimate - comparison : null;
  return `<section class="intelligence-estimate" aria-labelledby="intelligence-estimate-heading">
    <div class="intelligence-section-heading">
      <h5 id="intelligence-estimate-heading">Оценка ML</h5>
      ${synthetic ? '<span class="intelligence-pill is-warning">Обучение на синтетике</span>' : ""}
    </div>
    <strong class="intelligence-number">${hours(estimate)}</strong>
    ${data.status === "unavailable" ? '<p class="intelligence-meta">ML недоступна. Ниже показан расчёт по правилам.</p>' : ""}
    ${isNumber(delta) ? `<p class="intelligence-meta">Изменение к условиям пульта: <strong>${signed(delta)}</strong></p>` : ""}
    ${interval ? `<div class="intelligence-range">
      <p class="intelligence-meta">Интервал ${percent(interval.level ?? 0.9)} · ${synthetic ? "по синтетическим данным" : interval.usable ? "по калибровочной выборке" : "справочно"}</p>
      <div class="intelligence-range-track" style="--intel-position:${marker}%" aria-hidden="true"></div>
      <div class="intelligence-range-limits"><span>${hours(interval.lower_h)}</span><span>${hours(interval.upper_h)}</span></div>
    </div>` : '<p class="intelligence-meta">Интервал недоступен.</p>'}
    <div class="intelligence-score">Расчёт по правилам <strong>${hours(score)}</strong></div>
    ${data.baseline?.eta ? `<p class="intelligence-meta">Расчётное прибытие по правилам · ${date(data.baseline.eta)}</p>` : ""}
  </section>`;
}

function renderReliability(data) {
  const { prediction, arrival } = data;
  const reliability = prediction?.reliability || {};
  const outliers = reliability.range_violations || [];
  const preferred = reliability.preferred_source;
  const scope = reliability.scope_limitations || [];
  const limitations = {
    road_distance_not_modelled: "Модель отдельно не учитывает расстояние по автодороге.",
    in_transit_phase_not_a_feature: "Стадия движения по текущему плечу не входит в признаки ML.",
  };
  const reason = reliability.reason || data.notice;
  const deadline = arrival?.usable ? deadlineNames[arrival.deadline_position] : null;
  return `<section class="intelligence-reliability" aria-labelledby="intelligence-reliability-heading">
    <div class="intelligence-section-heading">
      <h5 id="intelligence-reliability-heading">Как читать прогноз</h5>
    </div>
    <span class="intelligence-pill ${preferred === "ml" ? "" : "is-warning"}">${escape(reliabilityNames[reliability.status] || "Надёжность не оценена")}</span>
    ${preferred ? `<p class="intelligence-meta">Для решения используйте ${preferred === "ml" ? "оценку ML с интервалом" : "расчёт по правилам"}.</p>` : ""}
    ${reason ? `<p class="intelligence-meta">${escape(reason)}</p>` : ""}
    ${outliers.length ? `<ul class="intelligence-reasons">${outliers.map((item) => `
      <li>${escape(featureNames[item.feature] || item.feature)} · ${number(item.value)}, диапазон обучения ${number(item.train_min)}–${number(item.train_max)}</li>
    `).join("")}</ul>` : ""}
    ${scope.map((key) => limitations[key] ? `<p class="intelligence-meta">${limitations[key]}</p>` : "").join("")}
    ${arrival?.estimate && arrival?.usable ? `<p class="intelligence-meta"><strong>Прибытие по ML, справочно</strong> · ${date(arrival.estimate)}</p>` : ""}
    ${arrival?.usable && arrival.earliest && arrival.latest ? `<p class="intelligence-meta">${date(arrival.earliest)} — ${date(arrival.latest)}</p>` : ""}
    ${deadline ? `<p class="intelligence-meta">${deadline}${isNumber(arrival.delay_budget_h) ? ` · запас задержки ${hours(arrival.delay_budget_h)}` : ""}</p>` : ""}
    ${arrival?.usable && arrival.note ? `<p class="intelligence-meta">${escape(arrival.note)}</p>` : ""}
  </section>`;
}

function renderFactors(prediction) {
  const factors = (prediction?.local_attribution || [])
    .filter((item) => isNumber(item.contribution_h))
    .sort((a, b) => Math.abs(b.contribution_h) - Math.abs(a.contribution_h))
    .slice(0, 4);
  if (!factors.length) return "";
  return `<section class="intelligence-block" aria-labelledby="intelligence-factors-heading">
    <h5 id="intelligence-factors-heading">Что повлияло на эту оценку</h5>
    <ul class="intelligence-factors">${factors.map((item) => `
      <li><span>${escape(featureNames[item.feature] || item.feature)}</span><strong>${signed(item.contribution_h)}</strong></li>
    `).join("")}</ul>
    <p class="intelligence-meta">Изменение оценки модели относительно типичных значений признаков. Эти эффекты не складываются в итоговую задержку.</p>
  </section>`;
}

function renderAlternatives(alternatives, hasSea) {
  if (!alternatives?.length) return `<section class="intelligence-block">
    <h5>Другие маршруты</h5>
    <p class="intelligence-meta">Для текущего положения доступных альтернатив нет.</p>
  </section>`;
  return `<section class="intelligence-block" aria-labelledby="intelligence-routes-heading">
    <div class="intelligence-section-heading">
      <h5 id="intelligence-routes-heading">Другие маршруты</h5>
      <span class="intelligence-meta">Оставшийся путь</span>
    </div>
    <table class="intelligence-comparison" aria-labelledby="intelligence-routes-heading">
      <thead><tr><th scope="col">Маршрут</th><th scope="col">Правила · задержка</th><th scope="col">ML · задержка</th></tr></thead>
      <tbody>${alternatives.map((route) => {
        const interval = getInterval(route.prediction);
        const note = interval && !interval.usable ? " · справочно" : "";
        return `<tr><td>${escape(route.node_names?.join(" → ") || "Альтернативный маршрут")}
          ${route.conditional ? "<small>Есть условные плечи</small>" : ""}</td>
          <td>${hours(route.prediction?.scoring_delay_h)}</td>
          <td>${hours(route.prediction?.ml_delay_h)}${interval ? `<small>${hours(interval.lower_h)} – ${hours(interval.upper_h)}${note}</small>` : ""}</td></tr>`;
      }).join("")}</tbody>
    </table>
    <p class="intelligence-meta">${hasSea ? "Погодные параметры относятся к морским плечам." : "У маршрутов без морских плеч погодные параметры не влияют на расчёт."}</p>
  </section>`;
}

function renderQuality(model, prediction) {
  const evaluation = model?.evaluation || {};
  const metrics = evaluation.test_metrics || {};
  const selected = model?.selected_model || evaluation.selected_model || prediction?.selected_model;
  const keys = [...new Set([selected, "median", "linear_regression"].filter((key) => metrics[key]))];
  if (!keys.length) return "";
  const synthetic = prediction?.dataset_kind === "synthetic" || model?.dataset?.source_kind === "synthetic";
  return `<details class="intelligence-method">
    <summary>Качество на отложенной выборке</summary>
    <p class="intelligence-meta">${synthetic
      ? "Метрики измерены на отложенной синтетике. Точность на реальных перевозках неизвестна."
      : "Метрики отложенной выборки относятся к данным обучения модели и их происхождению."}
      ${model?.n_test != null ? ` Проверено примеров: ${number(model.n_test)}.` : ""}</p>
    <table class="intelligence-comparison" aria-label="Сравнение моделей на отложенной выборке">
      <thead><tr><th scope="col">Модель</th><th scope="col">Средняя ошибка</th><th scope="col">RMSE</th></tr></thead>
      <tbody>${keys.map((key) => `<tr>
        <td>${escape(modelNames[key] || key)}${key === selected ? "<small>Выбрана на валидации</small>" : ""}</td>
        <td>${hours(metrics[key].mae_h)}</td>
        <td>${hours(metrics[key].rmse_h)}</td></tr>`).join("")}
      </tbody>
    </table>
    ${isNumber(evaluation.interval?.coverage) ? `<p class="intelligence-meta">В интервал попало ${percent(evaluation.interval.coverage)} фактических значений задержки в тестовой выборке · средняя ширина ${hours(evaluation.interval.mean_width_h)}.</p>` : ""}
    ${model?.model_version ? `<p class="intelligence-meta">Версия модели · ${escape(model.model_version)}</p>` : ""}
  </details>`;
}

function renderResult(data, comparison, hasSea) {
  if (data.status === "delivered") {
    return '<p class="intelligence-meta">Перевозка завершена. Прогноз для оставшегося пути не требуется.</p>';
  }
  if (data.status === "blocked") {
    return `<p class="intelligence-meta">Действующий маршрут заблокирован условиями сценария.</p>
      ${renderAlternatives(data.alternatives, hasSea)}`;
  }
  return `<div class="intelligence-summary">
      ${renderEstimate(data, comparison)}${renderReliability(data)}
    </div>
    ${renderFactors(data.prediction)}
    ${renderAlternatives(data.alternatives, hasSea)}
    ${renderQuality(data.model, data.prediction)}`;
}

const journalStatuses = {
  matched: "Сверен",
  pending: "Ожидает прибытия",
  excluded: "Исключён из метрик",
};

function renderJournal(data) {
  const { snapshots = [], counts = {}, metrics = {} } = data;
  return `<p class="intelligence-meta">Сверены ${number(counts.matched)} · ожидают ${number(counts.pending)} · исключены ${number(counts.excluded)}</p>
    ${metrics.n ? `<div class="intelligence-journal-metrics">
      <span>Средняя ошибка ML <strong>${hours(metrics.mae_h)}</strong></span>
      ${metrics.scoring_n ? `<span>Ошибка расчёта по правилам <strong>${hours(metrics.scoring_mae_h)}</strong> · ${number(metrics.scoring_n)} снимков</span>` : ""}
      <span>Покрытие экспериментального интервала <strong>${percent(metrics.interval_coverage)}</strong> · ${number(metrics.interval_n)} снимков</span>
    </div><p class="intelligence-meta">Сопоставлено снимков: ${number(metrics.n)}. Несколько снимков одной перевозки могут относиться к одному прибытию.</p>` : '<p class="intelligence-meta">Метрики появятся после первого сопоставления с записанным прибытием.</p>'}
    ${snapshots.length ? `<table class="intelligence-comparison" aria-label="Сохранённые прогнозы и факты прибытия">
      <thead><tr><th scope="col">Снимок</th><th scope="col">Оценка / факт</th><th scope="col">Проверка</th></tr></thead>
      <tbody>${snapshots.map((item) => `<tr>
        <td>${date(item.forecast_at)}<small>План v${number(item.plan_version)}</small></td>
        <td>${hours(item.prediction?.ml_delay_h)} / ${hours(item.outcome?.delay_h)}</td>
        <td><span>${escape(journalStatuses[item.status] || "—")}</span>${item.reason ? `<small>${escape(item.reason)}</small>` : ""}</td>
      </tr>`).join("")}</tbody>
    </table>` : ""}`;
}

function createJournal(root, api, canCapture) {
  const capture = root.querySelector("[data-intelligence-capture]");
  const reload = root.querySelector("[data-intelligence-reload]");
  const exportLink = root.querySelector("[data-intelligence-export]");
  const status = root.querySelector("[data-intelligence-journal-status]");
  const result = root.querySelector("[data-intelligence-journal-result]");
  const note = root.querySelector("#intelligence-capture-note");
  let context;
  let request;
  let saving = false;
  const idempotencyKeys = new Map();

  function refreshControls() {
    const ready = Boolean(context) && !saving;
    const eligibility = canCapture();
    capture.disabled = !ready || !eligibility.allowed;
    reload.disabled = !ready;
    note.textContent = eligibility.message || "";
  }

  function setContext(nextContext) {
    request?.abort();
    request = null;
    context = nextContext;
    saving = false;
    status.textContent = "";
    result.replaceChildren();
    exportLink.hidden = true;
    refreshControls();
    if (root.open) load();
  }

  async function load({ save = false } = {}) {
    if (!context || saving || (save && !canCapture().allowed)) return;
    request?.abort();
    const controller = new AbortController();
    request = controller;
    saving = save;
    refreshControls();
    status.textContent = save ? "Сохраняем прогноз…" : "Загружаем журнал…";
    const id = context.id;
    const endpoint = `/api/operations/${encodeURIComponent(id)}/intelligence/snapshots`;
    try {
      if (save) {
        if (!idempotencyKeys.has(id)) idempotencyKeys.set(id, crypto.randomUUID());
        await api(endpoint, {
          method: "POST", headers: { "Idempotency-Key": idempotencyKeys.get(id) }, signal: controller.signal,
        });
        idempotencyKeys.delete(id);
        if (request !== controller) return;
      }
      const data = await api(endpoint, { signal: controller.signal });
      if (request !== controller) return;
      result.innerHTML = renderJournal(data);
      exportLink.href = endpoint + ".csv";
      exportLink.hidden = !(data.counts?.matched > 0);
      status.textContent = save ? "Прогноз сохранён." : "";
    } catch (error) {
      if (request !== controller || error.name === "AbortError") return;
      status.textContent = error.message || "Журнал временно недоступен.";
    } finally {
      if (request === controller) {
        request = null;
        saving = false;
        refreshControls();
      }
    }
  }

  root.addEventListener("toggle", () => { if (root.open && context) load(); });
  capture.addEventListener("click", () => load({ save: true }));
  reload.addEventListener("click", () => load());
  return { setContext, refreshControls };
}

export function createIntelligencePanel(panel, api) {
  const form = panel.querySelector("form");
  const status = panel.querySelector("#intelligence-status");
  const scenarioLabel = panel.querySelector("#intelligence-context");
  const scenarioNote = panel.querySelector("#intelligence-scenario-note");
  const error = panel.querySelector("#intelligence-error");
  const result = panel.querySelector("#intelligence-result");
  const inputs = Array.from(form.querySelectorAll("input"));
  const actions = Array.from(form.querySelectorAll("button"));
  let context;
  let active = false;
  let pending;
  let lastRequest = {};
  let baselineDelay = null;
  let loaded = false;
  let forecast;
  let resetting = false;
  const journal = createJournal(panel.querySelector("#intelligence-journal"), api, () => {
    if (context?.storm || Object.keys(lastRequest).length || inputs.some((input) => input.value !== "")) {
      return { allowed: false, message: "Для записи в журнал вернитесь к обычным условиям пульта." };
    }
    if (!forecast || forecast.status !== "ok" || !isNumber(forecast.prediction?.ml_delay_h)) {
      return { allowed: false, message: "Для записи нужен рассчитанный прогноз ML." };
    }
    return {
      allowed: true,
      message: forecast.scope?.shipment_source === "demo"
        ? "Демонстрационный груз не попадёт в метрики и CSV фактических рейсов."
        : "",
    };
  });

  function abort() {
    pending?.abort();
    pending = null;
    result.setAttribute("aria-busy", "false");
    actions.forEach((action) => { action.disabled = !context || context.status === "delivered"; });
    journal.refreshControls();
  }

  function suspend(id) {
    context = null;
    forecast = null;
    lastRequest = {};
    resetting = true;
    form.reset();
    resetting = false;
    abort();
    journal.setContext(null);
    inputs.forEach((input) => { input.disabled = true; });
    loaded = false;
    baselineDelay = null;
    error.hidden = true;
    result.replaceChildren();
    scenarioLabel.textContent = "";
    status.textContent = id ? "Обновляем данные перевозки…" : "Выберите перевозку.";
  }

  async function load(overrides = {}) {
    if (!context || !active) return;
    if (context.status === "delivered") {
      status.textContent = "Перевозка завершена. Прогноз для оставшегося пути не требуется.";
      return;
    }
    abort();
    const request = new AbortController();
    const snapshot = context;
    pending = request;
    lastRequest = overrides;
    forecast = null;
    journal.refreshControls();
    loaded = false;
    const preview = Object.keys(overrides).length > 0;
    error.hidden = true;
    result.replaceChildren();
    result.setAttribute("aria-busy", "true");
    status.textContent = preview ? "Сравниваем сценарий…" : "Рассчитываем прогноз…";
    actions[0].disabled = true;
    const query = new URLSearchParams({ storm: String(snapshot.storm), ...overrides });
    try {
      const data = await api(`/api/operations/${encodeURIComponent(snapshot.id)}/intelligence?${query}`, { signal: request.signal });
      if (pending !== request || context !== snapshot || !active) return;
      if (data.shipment_id !== snapshot.id || data.plan_version !== snapshot.version) {
        throw new Error("План перевозки обновился. Обновите пульт и повторите расчёт.");
      }
      forecast = data;
      result.innerHTML = renderResult(data, preview ? baselineDelay : null, snapshot.sea);
      if (!preview) baselineDelay = data.prediction?.ml_delay_h ?? null;
      status.textContent = preview ? "Предпросмотр · план перевозки не изменён" : "";
      loaded = true;
    } catch (failure) {
      if (pending !== request || failure.name === "AbortError") return;
      status.textContent = "";
      error.querySelector("p").textContent = failure.message || "Прогноз пока недоступен.";
      error.hidden = false;
    } finally {
      if (pending === request) {
        pending = null;
        abort();
      }
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    load(Object.fromEntries(inputs.filter((input) => !input.disabled && input.value !== "")
      .map((input) => [input.name, input.value])));
  });
  form.addEventListener("reset", () => {
    if (!resetting) load();
  });
  form.addEventListener("input", () => journal.refreshControls());
  panel.querySelector("[data-intelligence-retry]").addEventListener("click", () => load(lastRequest));
  return {
    setContext(nextContext) {
      context = nextContext;
      scenarioLabel.textContent = `${context.id} · план v${context.version} · ${context.storm ? "шторм в пульте" : "условия пульта"}`;
      inputs.forEach((input) => {
        const weather = input.name === "wave_m" || input.name === "wind_ms";
        input.disabled = context.status === "delivered" || (weather && (context.storm || !context.sea));
      });
      scenarioNote.textContent = context.storm
        ? "В пульте включён шторм с заданными волной и ветром. Здесь можно изменить время на таможне."
        : !context.sea
          ? "В оставшемся пути нет морских плеч. Здесь можно изменить время на таможне."
          : "Пустое поле использует условия пульта. Предпросмотр не меняет план перевозки.";
      abort();
      journal.setContext(active ? context : null);
      if (active) load();
    },
    setActive(nextActive) {
      if (active === nextActive) return;
      active = nextActive;
      journal.setContext(active ? context : null);
      if (active) {
        if (!loaded && !pending) load(lastRequest);
      } else abort();
    },
    suspend,
    clear() { suspend(); },
  };
}
