import { createIntelligencePanel } from "./intelligence-panel.js";

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const num = (v, digits = 0) =>
  v != null && v !== "" && Number.isFinite(Number(v))
    ? new Intl.NumberFormat("ru-RU", { maximumFractionDigits: digits }).format(
        Number(v),
      )
    : "—";
const money = (v) => (v == null ? "—" : "$" + num(v));
const when = (v) => {
  if (!v) return "—";
  const d = new Date(v);
  return Number.isNaN(d.valueOf())
    ? "—"
    : d.toLocaleString("ru-RU", {
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      });
};
const localInput = (v) => {
  if (!v) return "";
  const d = new Date(v);
  if (Number.isNaN(d.valueOf())) return "";
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
};
const modes = {
  rail: "Железная дорога",
  sea: "Морское плечо",
  road: "Автодорога",
};
const eventNames = {
  departure: "Отправление",
  arrival: "Прибытие",
  gate_in: "Вход на терминал",
  gate_out: "Выход с терминала",
  load: "Погрузка",
  discharge: "Разгрузка",
  customs_start: "Проверка документов",
  customs_done: "Документы проверены",
  delay: "Задержка",
  note: "Заметка",
  plan_accepted: "План принят",
  route_plan_accepted: "План принят",
};
const docNames = {
  ready_for_review: "Готово к проверке",
  review_required: "Нужна проверка",
  missing: "Нет документа",
  mismatch: "Есть расхождения",
  matched: "Поля совпадают",
  demo_verified: "Демо-проверка",
  verified: "Проверен",
  submitted: "Подан",
  rejected: "Отклонён",
  expired: "Истёк",
};
const state = {
  storm: false,
  horizon: 0,
  selected: null,
  filter: "all",
  data: null,
  detail: null,
  proposal: null,
  preview: null,
  tab: "plan",
  token: "",
  nodes: {},
  hero: null,
  map: null,
  generation: 0,
  detailGeneration: 0,
  detailStorm: null,
  review: null,
  demo: null,
};
let toastTimer, viewAbort, refreshAbort;
const intelligence = createIntelligencePanel($("#view-intelligence"), api);
const query = (storm) =>
  new URLSearchParams({ storm: String(storm) }).toString();
const canAnimate = () =>
  !matchMedia("(prefers-reduced-motion: reduce)").matches;
async function api(path, options = {}) {
  const headers = {
    ...(options.body && !(options.body instanceof FormData)
      ? { "Content-Type": "application/json" }
      : {}),
    ...(state.token ? { "X-Write-Token": state.token } : {}),
    ...options.headers,
  };
  const controller = new AbortController();
  const timeout = setTimeout(
    () =>
      controller.abort(
        new Error("Сервер не ответил вовремя. Попробуйте ещё раз."),
      ),
    35000,
  );
  const cancel = () => controller.abort(options.signal.reason);
  if (options.signal?.aborted) cancel();
  else options.signal?.addEventListener("abort", cancel, { once: true });
  try {
    const res = await fetch(path, {
      ...options,
      headers,
      signal: controller.signal,
    });
    let data;
    try {
      data = await res.json();
    } catch {
      if (controller.signal.aborted) throw controller.signal.reason;
      throw new Error("Сервер вернул некорректный ответ.");
    }
    if (!res.ok) {
      const msg =
        typeof data.detail === "string"
          ? data.detail
          : Array.isArray(data.detail)
            ? data.detail.map((x) => x.msg).join("; ")
            : "Не удалось выполнить запрос";
      const e = new Error(
        res.status === 401
          ? "Нужен ключ оператора. Откройте «Ключ доступа»."
          : msg,
      );
      e.status = res.status;
      throw e;
    }
    return data;
  } catch (error) {
    if (error instanceof TypeError && !controller.signal.aborted) {
      throw new Error(
        "Нет связи с сервером. Проверьте подключение и нажмите «Обновить».",
      );
    }
    throw error;
  } finally {
    clearTimeout(timeout);
    options.signal?.removeEventListener("abort", cancel);
  }
}
function notify(message) {
  clearTimeout(toastTimer);
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  toastTimer = setTimeout(() => ($("#toast").hidden = true), 5500);
}
function report(error) {
  if (error.name === "AbortError") return;
  $("#api-error").textContent =
    error.message || "Не удалось связаться с сервером.";
  $("#api-error").hidden = false;
  $("#connection-status").textContent = "Нет свежих данных";
  $("#connection-status").classList.add("error");
}
function nodeName(id) {
  if (!id) return "В пути";
  return state.nodes[id]?.name || id;
}
function routeName(legs = []) {
  if (!legs.length) return "Маршрут завершён";
  return [nodeName(legs[0].from), ...legs.map((l) => nodeName(l.to))].join(
    " → ",
  );
}
function svgArrow() {
  return "↗";
}
function docChip(s) {
  return `<span class="chip ${["ready_for_review", "matched", "demo_verified", "verified"].includes(s) ? "mint" : "orange"}">${esc(docNames[s] || s || "Не проверен")}</span>`;
}
function setBusy(form, busy) {
  form.classList.toggle("saving", busy);
  form.setAttribute("aria-busy", String(busy));
  Array.from(form.elements)
    .filter((control) => control.type === "submit")
    .forEach((control) => (control.disabled = busy));
}
function setDetailPending(pending) {
  $("#decision-panel").setAttribute("aria-busy", String(pending));
  [$("#decision-content"), ...$$(".tab-view")].forEach(
    (view) => (view.inert = pending),
  );
}
function detailIsCurrent() {
  return (
    state.detail?.shipment.id === state.selected &&
    state.detailStorm === state.storm
  );
}
function activeProposal() {
  return (
    state.detail?.proposals?.find((p) => p.id === state.proposal) ||
    state.detail?.proposals?.[0]
  );
}
function knownDeparture(legId, observedAt) {
  const events = state.detail?.events || [];
  const replaced = new Set(
    events.map((event) => event.correction_of).filter(Boolean),
  );
  const departures = events.filter(
    (event) =>
      event.leg_id === legId &&
      event.event_type === "departure" &&
      event.source !== "demo-simulation" &&
      !replaced.has(event.id),
  );
  return departures.reduce((latest, event) => {
    const time = Date.parse(
      event.actual_ts ||
        event.occurred_ts ||
        (!event.planned_ts && event.received_ts),
    );
    return time <= observedAt && time > latest ? time : latest;
  }, -Infinity);
}
function routeProgress(plan, projection, isCurrentPlan) {
  const legs = plan.legs || [];
  if (!legs.length) return 0;
  const completed = new Set(plan.completed_leg_ids || []);
  const timings = new Map((projection?.legs || []).map((leg) => [leg.id, leg]));
  const shipment = state.detail?.shipment;
  const observedAt = Date.parse(state.detail?.horizon?.as_of);
  const departure = isCurrentPlan
    ? knownDeparture(shipment?.current_leg_id, observedAt)
    : NaN;
  let horizon = state.horizon;
  for (let index = 0; index < legs.length; index++) {
    const leg = legs[index];
    if (completed.has(leg.id)) continue;
    const timing = timings.get(leg.id);
    if (timing?.remaining_h == null) return index / legs.length;
    const remaining = Math.max(0, timing.remaining_h);
    const waiting = timing.in_transit
      ? 0
      : Math.min(
          remaining,
          (timing.wait_h || 0) + (timing.weather?.wave_m >= 2 ? 48 : 0),
        );
    if (horizon < remaining || (horizon === 0 && timing.in_transit)) {
      const elapsed =
        timing.in_transit &&
        Number.isFinite(departure) &&
        Number.isFinite(observedAt)
          ? Math.max(0, (observedAt - departure) / 3600000)
          : 0;
      const moving = Math.max(0, horizon - waiting) + elapsed;
      const duration = remaining - waiting + elapsed;
      const fraction = duration > 0 ? Math.min(moving / duration, 1) : 0;
      return (index + fraction) / legs.length;
    }
    horizon -= remaining;
  }
  return 1;
}
function sceneRoute(plan) {
  if (!state.map || !plan) return;
  const legs = plan.legs || [];
  let nodes = plan.nodes;
  if (!nodes?.length)
    nodes = legs.length ? [legs[0].from, ...legs.map((l) => l.to)] : [];
  nodes = nodes
    .map((n) => (typeof n === "string" ? state.nodes[n] : n))
    .filter(Boolean);
  state.map.setRoute({ nodes, legs, id: "selected" });
  updateMapProgress(plan);
}
function updateMapProgress(plan = state.detail?.plan) {
  if (!state.map || !plan) return;
  const isCurrentPlan = plan === state.detail?.plan;
  state.map.setProgress(
    routeProgress(
      plan,
      isCurrentPlan ? state.detail?.baseline : plan,
      isCurrentPlan,
    ),
  );
}
async function refresh({ keepSelection = true } = {}) {
  const generation = ++state.generation;
  const storm = state.storm;
  refreshAbort?.abort();
  const request = new AbortController();
  refreshAbort = request;
  $("#refresh").disabled = true;
  $("#connection-status").textContent = "Обновляем…";
  $("#api-error").hidden = true;
  try {
    const data = await api("/api/operations?" + query(storm), {
      signal: request.signal,
    });
    if (generation !== state.generation) return false;
    state.data = data;
    const rows = data.shipments || [];
    if (!keepSelection || !rows.some((s) => s.id === state.selected))
      state.selected = rows[0]?.id;
    $("#metric-count").textContent = num(
      data.summary?.shipment_count ?? rows.length,
    );
    $("#metric-late").textContent = num(data.summary?.late_count);
    $("#metric-risk").textContent = money(data.summary?.financial_risk_usd);
    $("#metric-docs").textContent = num(data.summary?.documents_blocked_count);
    $("#cargo-count").textContent = String(rows.length).padStart(2, "0");
    $("#connection-status").textContent =
      "Обновлено " +
      new Date().toLocaleTimeString("ru-RU", {
        hour: "2-digit",
        minute: "2-digit",
      });
    $("#connection-status").classList.remove("error");
    const weather = data.horizon?.weather;
    const sources = {
      available: "Open-Meteo",
      partial: "Open-Meteo · часть прогноза недоступна",
      loading: "источник загружается",
      unavailable: "прогноз недоступен",
    };
    $("#weather-source").textContent =
      sources[weather?.status] ||
      weather?.source ||
      "источник указан в прогнозе";
    renderList();
    if (state.selected) return await loadDetail(state.selected);
    state.detail = null;
    setDetailPending(false);
    $("#decision-content").innerHTML =
      '<p class="empty-state">Создайте первую перевозку.</p>';
    return true;
  } catch (e) {
    if (generation === state.generation) {
      report(e);
      if (!detailIsCurrent()) clearDetail();
    }
    return false;
  } finally {
    if (generation === state.generation) $("#refresh").disabled = false;
  }
}
function renderList() {
  const search = $("#cargo-search").value.trim().toLocaleLowerCase();
  const rows = (state.data?.shipments || []).filter(
    (s) =>
      (!search ||
        (s.id + " " + s.name + " " + s.cargo)
          .toLocaleLowerCase()
          .includes(search)) &&
      (state.filter !== "risk" ||
        s.blocked ||
        s.late_h > 0 ||
        s.financial_risk_usd > 0) &&
      (state.filter !== "docs" || s.document_status !== "ready_for_review"),
  );
  $("#cargo-list").innerHTML =
    rows
      .map(
        (s) =>
          `<button class="cargo-item ${s.id === state.selected ? "selected" : ""}" data-shipment="${esc(s.id)}" aria-pressed="${s.id === state.selected}"><div class="row"><span class="id">${esc(s.id)}</span><span class="chip ${s.blocked || s.late_h > 0 ? "orange" : "mint"}">${s.blocked ? "Ограничение" : s.late_h > 0 ? "+" + num(s.late_h) + " ч" : s.progress_status === "delivered" ? "Доставлен" : "В работе"}</span></div><strong>${esc(s.name)}</strong><p class="cargo-route">${esc(s.current_node ? nodeName(s.current_node) : "На маршруте")} · ${num(s.weight_t)} т</p><div class="cargo-meta"><span>${when(s.eta)}</span><span class="${s.blocked || s.late_h > 0 ? "late" : ""}">${s.blocked || s.late_h > 0 ? "Есть риск" : "План v" + (s.plan_version ?? 1)}</span></div></button>`,
      )
      .join("") || '<p class="empty-state">По этому фильтру перевозок нет.</p>';
}
function clearDetail(
  message = "Данные выбранного груза недоступны. Нажмите «Обновить».",
) {
  state.detail = null;
  state.proposal = null;
  state.preview = null;
  $("#decision-content").innerHTML =
    `<p class="empty-state">${esc(message)}</p>`;
  state.map?.setRoute({ id: "selected", nodes: [], legs: [] });
  updateHorizon();
  $("#detail-title").textContent = "Паспорт перевозки";
  $$(".tab-view").filter((view) => view.id !== "view-intelligence").forEach((view) => view.replaceChildren());
  intelligence.clear();
  setDetailPending(false);
}
async function loadDetail(id) {
  const seq = ++state.detailGeneration;
  const storm = state.storm;
  const previousProposal =
    detailIsCurrent() && id === state.selected ? state.proposal : null;
  state.selected = id;
  intelligence.suspend(id);
  renderList();
  viewAbort?.abort();
  const request = new AbortController();
  viewAbort = request;
  setDetailPending(true);
  try {
    const detail = await api(
      `/api/operations/${encodeURIComponent(id)}?${query(storm)}`,
      { signal: request.signal },
    );
    if (
      seq !== state.detailGeneration ||
      storm !== state.storm ||
      id !== state.selected
    )
      return false;
    state.detail = detail;
    state.detailStorm = storm;
    state.preview = null;
    state.proposal =
      detail.proposals?.find((p) => p.id === previousProposal)?.id ??
      detail.proposals?.[0]?.id;
    renderDetail();
    intelligence.setContext({
      id, storm,
      status: detail.shipment.progress_status,
      version: detail.plan.version,
      sea: detail.baseline?.legs?.some((leg) => leg.mode === "sea"),
    });
    return true;
  } catch (e) {
    if (e.name !== "AbortError" && seq === state.detailGeneration) {
      report(e);
      clearDetail();
    }
    return false;
  } finally {
    if (seq === state.detailGeneration) setDetailPending(false);
  }
}
function deltaText(value, formatter) {
  if (value == null) return "—";
  const sign = value === 0 ? "" : value < 0 ? "−" : "+";
  return sign + formatter(Math.abs(value));
}
function renderDetail() {
  const d = state.detail,
    s = d.shipment,
    b = d.baseline || {},
    p = activeProposal();
  $("#detail-title").textContent = `${s.id} · ${s.name}`;
  const forecastNote = b.blocked
    ? "Движение по текущему плану ограничено"
    : b.remaining_h == null
      ? "Прогноз недоступен"
      : `${num(b.remaining_h, 1)} ч осталось · ${b.late_h > 0 ? "опоздание " + num(b.late_h, 1) + " ч" : "в пределах заданного срока"}`;
  const position =
    s.progress_status === "delivered"
      ? "Перевозка завершена"
      : s.progress_status === "in_transit"
        ? "Груз в движении"
        : `На узле: ${nodeName(s.current_node)}`;
  $("#decision-content").innerHTML =
    `<div><div class="cargo-id">${esc(s.id)} / ПЛАН V${esc(d.plan.version)}</div><h4>${esc(s.name)}</h4><p class="route-name">${esc(s.cargo)} · ${num(s.weight_t)} тонн</p><div class="decision-status"><i class="dot ${b.late_h > 0 ? "orange" : "mint"}"></i>${esc(position)}</div><div class="decision-eta"><span>Текущий прогноз прибытия</span><strong>${when(b.eta || s.eta)}</strong><small>${forecastNote}</small><button type="button" class="intelligence-link" data-open-intelligence>ML-прогноз <span aria-hidden="true">↗</span></button></div></div><div class="recommendation"><div class="eyebrow">${p ? "АЛЬТЕРНАТИВНЫЙ ПЛАН" : "ТЕКУЩИЙ ПЛАН"}</div><h4>${p ? esc(p.node_names?.join(" → ") || routeName(p.legs)) : "Продолжить по маршруту"}</h4><p>${p ? (p.conditional ? "Включает условные плечи. Подтвердите доступность перевозки." : "Пересчёт от доступного узла с сохранением пройденного пути.") : "Доступной альтернативы в текущем графе нет. Можно уточнить условия или добавить фактическое событие."}</p>${p ? `<div class="delta-grid"><div><strong>${deltaText(p.delta_h, (v) => num(v, 1) + " ч")}</strong><span>${p.delta_h == null ? "сравнение недоступно" : p.delta_h < 0 ? "экономия времени" : p.delta_h > 0 ? "дольше текущего" : "время без изменений"}</span></div><div><strong>${deltaText(p.delta_usd, money)}</strong><span>${p.delta_usd == null ? "сравнение недоступно" : p.delta_usd < 0 ? "расчётная экономия" : p.delta_usd > 0 ? "дополнительные расходы" : "стоимость без изменений"}</span></div></div><button class="button primary" id="review-plan">Сравнить и принять ${svgArrow()}</button>` : ""}<p class="decision-footnote">Оценка по модели и заданным ставкам. Решение сохраняется только после вашего подтверждения.</p></div>`;
  renderPlan();
  renderEconomics();
  renderDocuments();
  renderEvents();
  sceneRoute(d.plan);
  updateHorizon();
  if (window.gsap && canAnimate())
    gsap.fromTo(
      "#decision-content",
      { opacity: 0.55, x: 8 },
      { opacity: 1, x: 0, duration: 0.24, overwrite: true },
    );
}
function renderPlan() {
  const d = state.detail,
    completed = d.plan.completed_leg_ids || [],
    s = d.shipment;
  $("#view-plan").innerHTML =
    `<div class="plan-layout"><div class="plan-legs">${(d.plan.legs || []).map((l, i) => `<div class="plan-leg ${completed.includes(l.id) ? "done" : l.id === s.current_leg_id ? "current" : ""}"><span class="leg-index">${completed.includes(l.id) ? "✓" : i + 1}</span><div><strong>${esc(nodeName(l.from))} → ${esc(nodeName(l.to))}</strong><p>${esc(modes[l.mode] || l.mode)} · ${completed.includes(l.id) ? "Пройдено" : l.id === s.current_leg_id ? "Текущее плечо" : "В плане"}${l.conditional ? " · условное плечо" : ""}</p></div><span class="leg-hours">${num(l.predicted_h ?? l.base_h, 1)} ч</span></div>`).join("")}<p class="form-note">План / факты / прогноз. Завершённые плечи сохраняются при смене маршрута.</p></div><div class="plan-caption"><h4>Варианты от текущего положения</h4>${(d.proposals || []).map((p, i) => `<button class="proposal-option button ${state.proposal === p.id ? "primary" : ""}" data-proposal="${esc(p.id)}" style="width:100%;justify-content:space-between;margin-bottom:9px;text-align:left"><span>${i + 1}. ${esc(p.node_names?.slice(0, 3).join(" → ") || routeName(p.legs))}<small style="display:block;font-size:10px;margin-top:3px">${num(p.remaining_h, 1)} ч · ${money(p.total_usd)}</small></span><span>↗</span></button>`).join("") || "<p>Доступных альтернатив сейчас нет.</p>"}<h4 style="margin-top:25px">История решений</h4><ul class="version-list">${
      (d.decisions || [])
        .slice()
        .reverse()
        .map(
          (x) =>
            `<li>Принят план v${esc(x.version ?? x.plan_version ?? "—")}<small>${when(x.created_at || x.accepted_at || x.ts)} · ${esc(x.source || "Решение оператора")}</small></li>`,
        )
        .join("") || "<li>Исходный план. Новых решений пока нет.</li>"
    }</ul></div></div>`;
}
function renderEconomics() {
  const e = state.detail.economics || {},
    b = state.detail.baseline || {};
  $("#view-economics").innerHTML =
    `<div class="form-layout"><form id="economics-form"><div class="form-grid"><label>Доставить до<input name="deadline" type="datetime-local" value="${esc(localInput(e.deadline))}" required></label><label>Простой, USD / сутки<input type="number" name="idle_cost_per_day" min="0" max="10000000" step="0.01" value="${esc(e.idle_cost_per_day ?? 0)}" required></label><label>Штраф за сутки опоздания, USD<input type="number" name="late_penalty_per_day" min="0" max="10000000" step="0.01" value="${esc(e.late_penalty_per_day ?? 0)}" required></label><label>Перегрузка при смене плана, USD<input type="number" name="reroute_transfer_cost" min="0" max="10000000" step="0.01" value="${esc(e.reroute_transfer_cost ?? 0)}" required></label></div><p class="form-note">Все суммы в USD для сопоставимости с тарифной моделью. Начальные ставки — допущения; задайте условия своей перевозки. Дата — в вашем часовом поясе.</p><p class="form-error" role="alert"></p><button class="button primary">Сохранить и пересчитать ↗</button></form><div><h4 style="font-weight:500;margin-bottom:12px">Стоимость оставшегося пути</h4>${costList(b)}<p class="form-note">Учитываются оставшиеся плечи и расходы текущего сценария. Это расчётная оценка, не коммерческое предложение.</p></div></div>`;
}
function costList(b) {
  return `<ul class="cost-list"><li>Перевозка<strong>${money(b.transport_usd)}</strong></li><li>Простой<strong>${money(b.idle_usd)}</strong></li><li>Опоздание<strong>${money(b.late_usd)}</strong></li><li>Перегрузка<strong>${money(b.transfer_usd)}</strong></li><li class="total">Итого<strong>${money(b.total_usd)}</strong></li></ul>`;
}
function renderDocuments() {
  const docs = state.detail.documents || {},
    req = docs.requirements || [];
  $("#view-documents").innerHTML =
    `<div class="document-grid"><div><div class="document-head" style="margin-bottom:20px"><h4 style="font-weight:500">Комплект перевозки</h4>${docChip(docs.status)}</div>${
      req
        .map(
          (r) =>
            `<article class="document-item"><div class="document-head"><strong>${esc(r.label || r.key)}</strong>${docChip(r.status)}</div><p>${esc(r.reason || "Проверяется по составу груза и маршруту.")}</p>${(r.mismatches || []).map((m) => `<p class="mismatch">${esc(typeof m === "string" ? m : m.message || JSON.stringify(m))}</p>`).join("")}${
              r.fields
                ? `<p>${esc(
                    Object.entries(r.fields)
                      .map(([k, v]) => `${k}: ${v}`)
                      .join(" · "),
                  )}</p>`
                : ""
            }</article>`,
        )
        .join("") ||
      '<p class="empty-state">Требования пока не сформированы.</p>'
    }<p class="form-note">Проверка комплектности и расхождений. Загрузка файла не означает разрешение таможни.</p></div><div><form id="upload-form" class="upload-box"><h4>Добавить документ</h4><label>Тип документа<select name="kind">${req.map((r) => `<option value="${esc(r.key)}">${esc(r.label || r.key)}</option>`).join("")}</select></label><label style="margin-top:15px">Файл<input type="file" name="file" accept=".txt,.pdf,.png,.jpg,.jpeg" required></label><p class="form-note">PDF, TXT, PNG или JPG · до 2 МБ. Доступность распознавания отображается после загрузки.</p><p class="form-error" role="alert"></p><button class="button primary">Загрузить и проверить ↗</button></form><div class="files-list">${(docs.files || []).map((f) => `<p><strong>${esc(f.filename || f.name)}</strong><br>${esc(f.extraction_status || f.status || "Файл сохранён")} · ${when(f.created_at || f.uploaded_at)}${f.extraction_source ? "<br>" + esc(f.extraction_source) : ""}${f.extraction_note ? "<br>" + esc(f.extraction_note) : ""}</p>`).join("")}</div></div></div>`;
}
function renderEvents() {
  const d = state.detail,
    active = (d.plan.legs || []).filter(
      (l) => !(d.plan.completed_leg_ids || []).includes(l.id),
    );
  $("#view-events").innerHTML =
    `<div class="form-layout"><div><h4 style="font-weight:500;margin-bottom:14px">Факты перевозки</h4><ul class="event-list">${
      (d.events || [])
        .slice()
        .reverse()
        .map(
          (e) =>
            `<li><strong>${esc(eventNames[e.event_type] || e.event_type)}</strong><small>${when(e.actual_ts || e.occurred_ts || e.planned_ts || e.received_ts)} · ${esc(e.source || "Не указан")}</small>${e.note ? "<p>" + esc(e.note) + "</p>" : ""}${e.delay_h ? "<p>Дополнительная задержка: " + num(e.delay_h, 1) + " ч</p>" : ""}</li>`,
        )
        .join("") || '<li class="muted">Фактических событий пока нет.</li>'
    }</ul></div><form id="event-form"><div class="form-grid"><label>Событие<select name="event_type"><option value="note">Заметка</option><option value="departure">Отправление</option><option value="arrival">Прибытие</option><option value="delay">Задержка</option><option value="load">Погрузка</option><option value="discharge">Разгрузка</option><option value="customs_start">Проверка документов</option><option value="customs_done">Документы проверены</option></select></label><label>Плечо<select name="leg_id"><option value="">— без привязки —</option>${active.map((l) => `<option value="${esc(l.id)}">${esc(nodeName(l.from))} → ${esc(nodeName(l.to))}</option>`).join("")}</select></label><label>Время факта<input name="occurred_ts" type="datetime-local"></label><label>Задержка, часов<input name="delay_h" type="number" min="0" max="720" step="0.5" value="0"></label></div><label>Комментарий<textarea name="note" maxlength="300" placeholder="Что произошло на маршруте"></textarea></label><p class="form-note">Прибытие и отправление обновляют положение груза. Для них выберите соответствующее плечо. Пустое время означает «сейчас».</p><p class="form-error" role="alert"></p><button class="button primary">Записать событие ↗</button></form></div>`;
}
function setTab(tab) {
  state.tab = tab;
  $$("[data-tab]").forEach((b) => {
    const active = b.dataset.tab === tab;
    b.setAttribute("aria-selected", String(active));
    b.tabIndex = active ? 0 : -1;
  });
  $$(".tab-view").forEach((v) => (v.hidden = v.id !== `view-${tab}`));
  intelligence.setActive(tab === "intelligence");
}
async function setStorm(value) {
  if (state.storm === value) return detailIsCurrent();
  state.storm = value;
  state.detailGeneration++;
  intelligence.suspend(state.selected);
  viewAbort?.abort();
  setDetailPending(true);
  state.preview = null;
  $("#storm").classList.toggle("active", value);
  $("#storm").setAttribute("aria-pressed", String(value));
  $("#calm").classList.toggle("active", !value);
  $("#calm").setAttribute("aria-pressed", String(!value));
  $("#scenario-banner").hidden = !value;
  state.map?.setScenario({ storm: value, horizon: state.horizon });
  return refresh();
}
function updateHorizon(plan = state.preview || state.detail?.plan) {
  const h = state.horizon;
  $("#horizon-value").textContent = h ? `+${h} часов` : "Сейчас";
  updateMapProgress(plan);
  state.map?.setScenario({ storm: state.storm, horizon: h });
  const from = Date.parse(state.detail?.horizon?.as_of);
  const impact =
    detailIsCurrent() &&
    state.detail.horizon?.impacts?.find(
      (item) =>
        item.shipment_id === state.selected &&
        Date.parse(item.at) <= from + h * 3600000,
    );
  const names = {
    sea_disruption: "штормовое ограничение",
    late: "опоздание",
    reported_delay: "задержка по событию",
    blocked: "плечо недоступно",
  };
  $("#horizon-note").textContent = h
    ? `${impact ? `На горизонте +${h} ч: ${names[impact.type] || "ограничение"}. ` : ""}Положение через ${h} ч по модели. Факты и принятый план не меняются.`
    : "Положение груза по модели. Факты не изменяются.";
}
function showPlanReview() {
  const d = state.detail,
    p = activeProposal(),
    dialog = $("#accept-dialog");
  if (
    !p ||
    !detailIsCurrent() ||
    dialog.open ||
    dialog.getAttribute("aria-busy") === "true" ||
    $("#decision-content").inert
  )
    return;
  state.review = {
    shipmentId: d.shipment.id,
    proposalId: p.id,
    version: d.plan.version,
    storm: state.storm,
    key: crypto.randomUUID(),
    stale: false,
  };
  $("#accept-confirm").disabled = false;
  $("#accept-dialog .form-error").textContent = "";
  $("#accept-summary").innerHTML =
    `<p class="form-note">${esc(d.shipment.id)} · исходная версия ${esc(d.plan.version)}. Пройденные плечи останутся в истории.</p><h3 style="font-size:18px;margin-bottom:16px">${esc(p.node_names?.join(" → ") || routeName(p.legs))}</h3><div class="form-grid"><div><span class="eyebrow muted">ТЕКУЩИЙ ПЛАН</span><p style="font-size:23px;margin:7px 0">${money(d.baseline.total_usd)}</p><p class="form-note">${when(d.baseline.eta)}<br>${d.baseline.remaining_h == null ? "Прогноз недоступен" : num(d.baseline.remaining_h, 1) + " ч осталось"}</p></div><div><span class="eyebrow" style="color:var(--mint)">НОВЫЙ ПЛАН</span><p style="font-size:23px;color:var(--mint);margin:7px 0">${money(p.total_usd)}</p><p class="form-note">${when(p.eta)}<br>${num(p.remaining_h, 1)} ч осталось</p></div></div>${p.conditional ? '<p class="error-banner">В маршруте есть условные плечи. Их доступность нужно подтвердить с перевозчиком.</p>' : ""}<p class="form-note">${esc(p.document_note || "После изменения плана требования к документам будут проверены заново.")}</p>`;
  dialog.showModal();
}
async function acceptPlan() {
  const review = state.review;
  const button = $("#accept-confirm");
  const dialog = $("#accept-dialog");
  if (!review || review.stale || button.disabled || !dialog.open) return;
  button.disabled = true;
  $("[data-close]", dialog).disabled = true;
  $(".form-error", dialog).textContent = "";
  dialog.setAttribute("aria-busy", "true");
  try {
    await api(
      `/api/operations/${encodeURIComponent(review.shipmentId)}/accept?${query(review.storm)}`,
      {
        method: "POST",
        headers: { "Idempotency-Key": review.key },
        body: JSON.stringify({
          proposal_id: review.proposalId,
          version: review.version,
        }),
      },
    );
    dialog.close();
    notify("Новый план принят. Решение сохранено в истории перевозки.");
    await refresh();
  } catch (e) {
    review.stale = e.status === 409;
    $(".form-error", dialog).textContent = review.stale
      ? "Это предложение устарело. Закройте окно и заново сравните обновлённые варианты перед подтверждением."
      : e.message;
    if (review.stale) await refresh();
  } finally {
    if (state.review === review) {
      button.disabled = review.stale;
      $("[data-close]", dialog).disabled = false;
      dialog.setAttribute("aria-busy", "false");
    }
  }
}
async function submitForm(form) {
  if (form.getAttribute("aria-busy") === "true" || !form.reportValidity())
    return;
  const editing = ["economics-form", "upload-form", "event-form"].includes(
    form.id,
  );
  if (editing && (!detailIsCurrent() || form.closest("[inert]"))) return;
  const shipmentId = state.selected;
  const fd = new FormData(form);
  $(".form-error", form) && ($(".form-error", form).textContent = "");
  setBusy(form, true);
  try {
    if (form.id === "economics-form") {
      await api(`/api/operations/${encodeURIComponent(shipmentId)}/economics`, {
        method: "PUT",
        body: JSON.stringify({
          deadline: new Date(fd.get("deadline")).toISOString(),
          idle_cost_per_day: Number(fd.get("idle_cost_per_day")),
          late_penalty_per_day: Number(fd.get("late_penalty_per_day")),
          reroute_transfer_cost: Number(fd.get("reroute_transfer_cost")),
        }),
      });
      notify("Условия сохранены. Варианты пересчитаны.");
      await refresh();
    }
    if (form.id === "upload-form") {
      const f = fd.get("file");
      if (f.size > 2 * 1024 * 1024)
        throw new Error("Максимальный размер файла — 2 МБ.");
      await api(`/api/operations/${encodeURIComponent(shipmentId)}/documents`, {
        method: "POST",
        body: fd,
      });
      notify("Документ сохранён. Результат проверки — в паспорте.");
      await refresh();
    }
    if (form.id === "event-form") {
      const type = fd.get("event_type"),
        leg = fd.get("leg_id");
      if (["arrival", "departure", "delay"].includes(type) && !leg)
        throw new Error("Для этого события выберите плечо маршрута.");
      const body = {
        event_type: type,
        source: "operator-manual",
        note: fd.get("note") || null,
        leg_id: leg || null,
        occurred_ts: fd.get("occurred_ts")
          ? new Date(fd.get("occurred_ts")).toISOString()
          : type === "arrival" ? new Date().toISOString() : null,
      };
      if (type === "delay") body.delay_h = Number(fd.get("delay_h"));
      await api(`/api/shipments/${encodeURIComponent(shipmentId)}/events`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      notify("Событие записано. Прогноз обновлён.");
      await refresh();
    }
    if (form.id === "create-form") {
      const s = await api("/api/shipments", {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify({
          name: fd.get("name"),
          cargo: fd.get("cargo"),
          weight_t: Number(fd.get("weight_t")),
          route_id: fd.get("route_id"),
        }),
      });
      state.selected = s.id || s.shipment?.id;
      $("#create-dialog").close();
      form.reset();
      notify("Перевозка создана.");
      await refresh();
      $("#operations").scrollIntoView({
        behavior: canAnimate() ? "smooth" : "auto",
      });
    }
    if (form.id === "access-form") {
      state.token = String(fd.get("token") || "");
      form.reset();
      $("#access-dialog").close();
      notify("Ключ применён к этой вкладке.");
    }
  } catch (e) {
    const error = $(".form-error", form);
    if (error) error.textContent = e.message;
    else notify(e.message);
  } finally {
    setBusy(form, false);
  }
}
const demoSteps = [
  [
    "01 / Перевозка под наблюдением",
    "Выберите груз: маршрут, документы и прогноз связаны в одном пульте.",
  ],
  [
    "02 / Шторм на Каспии",
    "Шторм увеличивает ожидание отправления. Система пересчитывает оставшийся путь.",
  ],
  [
    "03 / Есть другой путь",
    "Сравните альтернативу на карте: выигрыш времени и полную стоимость.",
  ],
  [
    "04 / Экономика решения",
    "Стоимость простоя и штрафы задаются вами. Расчёт показывает цену альтернативы.",
  ],
  [
    "05 / Решение остаётся в истории",
    "Примите новый план в карточке груза. Демонстрация сама не меняет маршрут.",
  ],
];
function updateDemoProgress(demo) {
  $("#demo-progress").style.transform =
    `scaleX(${Math.min(1, demo.elapsed / 90)})`;
}
async function demoStep(index) {
  const demo = state.demo;
  if (!demo) return;
  const step = Math.max(0, Math.min(demoSteps.length - 1, index));
  const request = ++demo.request;
  demo.step = step;
  demo.elapsed = step * 18;
  updateDemoProgress(demo);
  const [title, text] = demoSteps[step];
  $("#demo-title").textContent = title;
  $("#demo-description").textContent = text;
  $("#demo-prev").disabled = step === 0;
  $("#demo-next").disabled = step === demoSteps.length - 1;
  const changedStorm = state.storm !== step > 0;
  if (changedStorm) await setStorm(step > 0);
  else if (
    !detailIsCurrent() ||
    $("#decision-panel").getAttribute("aria-busy") === "true"
  )
    await refresh();
  if (state.demo !== demo || demo.request !== request) return;
  if (step === 2 && detailIsCurrent()) {
    const proposal = activeProposal();
    if (proposal) previewRoute(proposal);
  }
  setTab(step === 3 ? "economics" : "plan");
  if (step === 3)
    $(".workspace-detail").scrollIntoView({
      behavior: canAnimate() ? "smooth" : "auto",
      block: "center",
    });
  if (step === 4)
    $("#operations").scrollIntoView({
      behavior: canAnimate() ? "smooth" : "auto",
    });
}
async function startDemo() {
  const start = $("#start-demo");
  if (state.demo || start.disabled) return;
  const previous = {
    storm: state.storm,
    tab: state.tab,
    selected: state.selected,
  };
  start.disabled = true;
  try {
    if (!state.data) await refresh();
    if (!state.data) {
      notify("Для демонстрации нужен доступ к серверу.");
      return;
    }
    const demo = { step: 0, elapsed: 0, paused: false, request: 0, previous };
    state.demo = demo;
    const seed = state.data.shipments.find(
      (shipment) => shipment.id === "SHP-1042",
    );
    if (seed) state.selected = seed.id;
    $("#demo-bar").hidden = false;
    $("#demo-pause").textContent = "Пауза";
    $("#operations").scrollIntoView({
      behavior: canAnimate() ? "smooth" : "auto",
    });
    await demoStep(0);
    if (state.demo !== demo) return;
    demo.timer = setInterval(() => {
      if (state.demo !== demo || demo.paused || document.hidden) return;
      demo.elapsed += 0.25;
      updateDemoProgress(demo);
      const step = Math.min(
        demoSteps.length - 1,
        Math.floor(demo.elapsed / 18),
      );
      if (step !== demo.step) demoStep(step);
      if (demo.elapsed >= 90) {
        demo.paused = true;
        $("#demo-pause").textContent = "Повторить";
      }
    }, 250);
  } finally {
    start.disabled = false;
  }
}
async function closeDemo() {
  const demo = state.demo;
  if (!demo) return;
  clearInterval(demo.timer);
  state.demo = null;
  $("#demo-bar").hidden = true;
  const previous = demo.previous;
  if (previous.selected) state.selected = previous.selected;
  setTab(previous.tab);
  if (state.storm !== previous.storm) await setStorm(previous.storm);
  else await refresh();
}
function storyMotion() {
  // Freight Services owns the scroll narrative; the geographic atlas stays interactive.
  if (document.querySelector(".atlas-section")) return;
  if (!window.gsap || !window.ScrollTrigger) return;
  gsap.registerPlugin(ScrollTrigger);
  const mm = gsap.matchMedia();
  mm.add(
    "(min-width: 801px) and (prefers-reduced-motion: no-preference)",
    () => {
      const hero = $("#hero-copy");
      const story = $("#story-copy");
      const dots = $$(".chapter-dots>span");
      const readout = $("#hero-readout");
      const chapters = [
        { route: "R1", readout: "R1 · Махачкала → Амирабад" },
        {
          route: "R1",
          readout: "R1 · шторм ограничивает морское плечо",
          index: "02 / ПРЕДВИДЕТЬ",
          title: "Шторм меняет<br>весь маршрут.",
          text: "Задержка в порту становится цепочкой сорванных стыковок. Смотрите, какие грузы окажутся под угрозой.",
        },
        {
          route: "R3",
          readout: "R3 · сухопутный обход через Баку",
          index: "03 / ДЕЙСТВОВАТЬ",
          title: "Есть путь.<br>Есть решение.",
          text: "Сравните время, расходы и документы. Сохраните новый план, пока груз ещё можно перенаправить.",
        },
      ];
      let last = -1;
      const progress = { value: 0 };
      function updateStory() {
        const p = progress.value;
        state.hero?.setStoryProgress(p);
        const index = p < 0.24 ? 0 : p < 0.65 ? 1 : 2;
        if (index === last) return;
        last = index;
        const chapter = chapters[index];
        dots.forEach((dot, i) => dot.classList.toggle("active", i === index));
        hero.inert = index !== 0;
        story.hidden = index === 0;
        state.hero?.setScenario({
          storm: index === 1,
          horizon: index === 1 ? 48 : 0,
        });
        state.hero?.setRoute(chapter.route);
        readout.textContent = chapter.readout;
        if (index !== 0) {
          $("#story-index").textContent = chapter.index;
          $("#story-title").innerHTML = chapter.title;
          $("#story-text").textContent = chapter.text;
        }
      }
      gsap.from(
        ".hero-copy .eyebrow, .hero-copy h1, .hero-copy p, .hero-actions",
        {
          y: 18,
          opacity: 0,
          duration: 0.55,
          stagger: 0.06,
          ease: "power3.out",
        },
      );
      const timeline = gsap.timeline({
        onUpdate: updateStory,
        scrollTrigger: {
          trigger: ".journey",
          start: "top top",
          end: "bottom bottom",
          scrub: 0.6,
        },
      });
      timeline.to(progress, { value: 1, duration: 1, ease: "none" }, 0);
      timeline.to(hero, { y: -35, opacity: 0, duration: 0.18 }, 0.05);
      gsap.utils
        .toArray(".value-strip>div,.section-heading,.closing h2")
        .forEach((el) =>
          gsap.from(el, {
            y: 22,
            opacity: 0,
            duration: 0.55,
            ease: "power3.out",
            scrollTrigger: { trigger: el, start: "top 90%", once: true },
          }),
        );
      updateStory();
      return () => {
        hero.inert = false;
        story.hidden = true;
        dots.forEach((dot, i) => dot.classList.toggle("active", i === 0));
        state.hero?.setStoryProgress(0);
        state.hero?.setScenario({ storm: false, horizon: 0 });
        state.hero?.setRoute("R1");
        readout.textContent = chapters[0].readout;
      };
    },
  );
  window.addEventListener("pagehide", () => mm.revert(), { once: true });
}
async function scenes() {
  try {
    const { createCaspianScene } = await import("/static/caspian-scene.js");
    state.hero = await createCaspianScene($("#hero-map"), {
      mode: "hero",
      onPortSelect: (port) => showPort(port, "hero"),
    });
    state.map = await createCaspianScene($("#ops-map"), {
      mode: "ops",
      onPortSelect: (port) => showPort(port, "ops"),
    });
    if (detailIsCurrent()) sceneRoute(state.preview || state.detail.plan);
    state.map?.setScenario({ storm: state.storm, horizon: state.horizon });
  } catch (e) {
    console.warn("Map unavailable:", e);
    if (!state.hero)
      $("#hero-map").innerHTML =
        '<div class="empty-state" style="padding:25% 15%">Карта временно недоступна. Перевозки и расчёты доступны в центре управления.</div>';
    if (!state.map)
      $("#ops-map").innerHTML =
        '<p class="empty-state">Не удалось загрузить карту. Текстовый маршрут доступен в паспорте.</p>';
  }
  storyMotion();
}
function showPort(port, mode) {
  const id = typeof port === "string" ? port : port.id;
  const node = state.nodes[id] || port;
  const label = `${node.name || id} · ${node.country || "Узел коридора"}`;
  if (mode === "hero") {
    $("#hero-readout").textContent = label;
    return;
  }
  const info = $("#port-info");
  info.textContent = `${label} · Нажмите на груз, чтобы вернуться к его маршруту.`;
  info.hidden = false;
}
function previewRoute(proposal) {
  state.preview = proposal;
  sceneRoute(proposal);
  updateHorizon(proposal);
}
document.addEventListener("click", async (e) => {
  const b = e.target.closest("button,a");
  if (!b) return;
  if (b.dataset.open) $("#" + b.dataset.open).showModal();
  if (b.hasAttribute("data-close")) b.closest("dialog").close();
  if (b.dataset.shipment) {
    $("#port-info").hidden = true;
    await loadDetail(b.dataset.shipment);
  }
  if (b.dataset.filter) {
    state.filter = b.dataset.filter;
    $$("[data-filter]").forEach((x) => {
      const active = x === b;
      x.classList.toggle("active", active);
      x.setAttribute("aria-pressed", String(active));
    });
    renderList();
  }
  if (b.dataset.tab) setTab(b.dataset.tab);
  if (b.hasAttribute("data-open-intelligence")) {
    setTab("intelligence");
    $("#tab-intelligence").focus({ preventScroll: true });
    $(".workspace-detail").scrollIntoView({ behavior: canAnimate() ? "smooth" : "auto" });
  }
  if (b.dataset.proposal && detailIsCurrent()) {
    state.proposal = b.dataset.proposal;
    renderDetail();
    previewRoute(activeProposal());
  }
  if (b.id === "review-plan") showPlanReview();
});
document.addEventListener("submit", (e) => {
  if (e.target.matches("form:not(#intelligence-scenario)")) {
    e.preventDefault();
    submitForm(e.target);
  }
});
$$("[role=tab]").forEach((tab) =>
  tab.addEventListener("keydown", (e) => {
    const keys = ["ArrowLeft", "ArrowRight", "Home", "End"];
    if (!keys.includes(e.key)) return;
    e.preventDefault();
    const tabs = $$("[role=tab]"),
      i = tabs.indexOf(tab),
      next =
        e.key === "Home"
          ? 0
          : e.key === "End"
            ? tabs.length - 1
            : (i + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) %
              tabs.length;
    setTab(tabs[next].dataset.tab);
    tabs[next].focus();
  }),
);
$("#cargo-search").addEventListener("input", renderList);
$("#refresh").addEventListener("click", () => refresh());
$("#calm").addEventListener("click", () => setStorm(false));
$("#storm").addEventListener("click", () => setStorm(true));
$("#horizon").addEventListener("input", (e) => {
  state.horizon = Number(e.target.value);
  updateHorizon();
});
$("#accept-confirm").addEventListener("click", acceptPlan);
$("#start-demo").addEventListener("click", startDemo);
$("#demo-close").addEventListener("click", closeDemo);
$("#demo-prev").addEventListener("click", () => {
  if (state.demo) demoStep(state.demo.step - 1);
});
$("#demo-next").addEventListener("click", () => {
  if (state.demo) demoStep(state.demo.step + 1);
});
$("#demo-pause").addEventListener("click", () => {
  if (!state.demo) return;
  if (state.demo.elapsed >= 90) {
    state.demo.paused = false;
    demoStep(0);
  } else state.demo.paused = !state.demo.paused;
  $("#demo-pause").textContent = state.demo.paused ? "Продолжить" : "Пауза";
});
$("#accept-dialog").addEventListener("cancel", (event) => {
  if (event.currentTarget.getAttribute("aria-busy") === "true")
    event.preventDefault();
});
window.addEventListener("offline", () => {
  notify("Нет сети. Изменения станут доступны после подключения.");
  $("#connection-status").textContent = "Нет сети";
});
window.addEventListener("online", () => refresh());
window.addEventListener(
  "pagehide",
  () => {
    state.hero?.dispose();
    state.map?.dispose();
    clearInterval(state.demo?.timer);
    viewAbort?.abort();
    refreshAbort?.abort();
    intelligence.suspend();
  },
  { once: true },
);
async function init() {
  try {
    const d = await api("/api/nodes");
    state.nodes = Object.fromEntries(d.nodes.map((n) => [n.id, n]));
  } catch (e) {
    report(e);
  }
  await Promise.allSettled([scenes(), refresh()]);
  if ("serviceWorker" in navigator)
    navigator.serviceWorker.register("/sw.js").catch(() => {});
}
init();
