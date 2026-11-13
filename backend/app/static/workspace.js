import { escape, hours, number, time, unit } from "./operations-labs.js";

const $ = (selector) => document.querySelector(selector);
const parts = location.pathname.split("/").filter(Boolean);
const page = parts[1] || "overview";
const detail = page === "shipments" && parts.length === 3;
const content = $("#workspace-content");
const titles = {
  overview: ["Обзор перевозок", "Риски, документы и ближайшие действия по всему коридору."],
  shipments: ["Перевозки", "Найдите груз, откройте его паспорт или добавьте новую перевозку."],
  map: ["Карта коридора", "Выберите перевозку и посмотрите её принятый маршрут."],
  risks: ["Риски и задачи", "Разберите расчётные предупреждения и назначьте действия."],
  documents: ["Документы", "Откройте комплект перевозки, чтобы проверить замечания или добавить файл."],
  analytics: ["Аналитика", "Сравните оставшиеся время, расходы и выбросы по перевозкам."],
};
const statusNames = { at_node: "В узле", in_transit: "В пути", delivered: "Доставлен" };
const documentNames = {
  ready_for_review: "Готово к проверке", needs_documents: "Нужны документы", missing: "Нужны документы",
  review_required: "Нужна ручная проверка", mismatch: "Есть расхождения",
};
const taskStatuses = { open: "Открыта", in_progress: "В работе", done: "Выполнена" };
const priorities = { normal: "Обычный", high: "Высокий", critical: "Критический" };
const riskNames = { normal: "По плану", elevated: "Есть риск", critical: "Высокий риск", complete: "Доставлен" };

let workspace, loading, map, mapRequest, editingTask, draftKey;
let taskSession = 0;
let token = "";
try { token = sessionStorage.getItem("caspian-operator-token") || ""; } catch {}
const filters = { search: "", risk: "all", sort: "risk", documents: "all", tasks: "active" };
const shipmentLink = (id, tab = "") => `/app/shipments/${encodeURIComponent(id)}${tab ? "?tab=" + tab : ""}`;
const chip = (label, warning = false) => `<span class="workspace-chip ${warning ? "is-warning" : ""}">${escape(label)}</span>`;
const empty = (text) => `<p class="workspace-empty">${escape(text)}</p>`;
const taskLabel = (value) => taskStatuses[value] || value;
const isOverdue = (task) => task.status !== "done" && Date.parse(task.due_at) < Date.now();
const taskDeadline = (task) => task.due_at ? time(task.due_at) : "Срок не назначен";
const money = (value) => unit(value, "USD");
const opts = (pairs, current) => pairs.map(([key, label]) =>
  `<option value="${escape(key)}"${key === current ? " selected" : ""}>${escape(label)}</option>`).join("");

async function api(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new Error("Сервер не ответил вовремя. Повторите запрос.")), 30000);
  const signal = options.signal ? AbortSignal.any([controller.signal, options.signal]) : controller.signal;
  try {
    const response = await fetch(path, {
      ...options, signal,
      headers: {
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(token ? { "X-Write-Token": token } : {}),
        ...options.headers,
      },
    });
    let result;
    try { result = await response.json(); }
    catch { throw new Error("Свежие данные недоступны. Проверьте подключение и повторите запрос."); }
    if (!response.ok) {
      const message = response.status === 401 ? "Укажите ключ в разделе «Ключ доступа»."
        : response.status === 409 ? "Данные изменились. Откройте актуальную запись и повторите изменение."
        : typeof result.detail === "string" ? result.detail : "Проверьте введённые данные.";
      const error = new Error(message);
      error.status = response.status;
      throw error;
    }
    return result;
  } finally { clearTimeout(timer); }
}

function setHead() {
  const [title, description] = titles[page] || titles.overview;
  $("#workspace-heading").textContent = detail ? "Паспорт перевозки" : title;
  $("#workspace-description").textContent = detail ? "Маршрут, экономика, документы и события." : description;
  if (page !== "shipments" || detail)
    $(".workspace-head-actions [data-open=create-dialog]").classList.remove("primary");
  document.title = `${detail ? "Паспорт перевозки" : title} · Caspian`;
  document.querySelector(`[data-workspace-nav="${page}"]`)?.setAttribute("aria-current", "page");
  if (detail) {
    const breadcrumb = $("#workspace-breadcrumb");
    breadcrumb.innerHTML = '<a href="/app/shipments">← Все перевозки</a>';
    breadcrumb.hidden = false;
    content.hidden = true;
    $("#start-demo").hidden = false;
  } else $("#workspace-refresh").hidden = false;
}

function panel(title, link, body) {
  return `<section class="workspace-panel"><header><h2>${title}</h2>${link || ""}</header>${body}</section>`;
}
function listAlerts(alerts) {
  return alerts.length ? `<ul class="workspace-records">${alerts.map((alert) => `
    <li><div><a href="${shipmentLink(alert.shipment_id, alert.target === "documents" ? "documents" : "")}"><strong>${escape(alert.title)}</strong></a>
    <p>${escape(alert.shipment_id)} · ${escape(alert.shipment_name)} · ${escape(alert.description)}</p>
    </div><div class="workspace-record-actions">${chip(alert.severity === "critical" ? "Критично" : alert.severity === "high" ? "Важно" : "Проверить", alert.severity !== "normal")}
    ${page === "risks" ? `<a class="button compact" href="${shipmentLink(alert.shipment_id, alert.target === "documents" ? "documents" : "")}">Открыть ↗</a><button type="button" class="button compact" data-task-alert="${escape(alert.id)}">Создать задачу</button>` : ""}</div>
    </li>`).join("")}</ul>` : empty("Предупреждений по перевозкам нет.");
}
function overview() {
  const { summary, alerts, activity, tasks } = workspace;
  const metrics = [
    ["Перевозки в работе", summary.active_count, "/app/shipments"],
    ["С риском или опозданием", summary.at_risk_count, "/app/risks"],
    ["Требуют проверки документов", summary.documents_blocked_count, "/app/documents"],
    ["Открытые задачи", summary.active_tasks_count, "/app/risks#tasks"],
  ];
  const next = tasks.filter((task) => task.status !== "done")
    .sort((a, b) => (a.due_at || "~").localeCompare(b.due_at || "~")).slice(0, 5);
  return `<div class="workspace-metrics">${metrics.map(([label, value, href]) =>
    `<a href="${href}"><span>${label}</span><strong>${number(value)}</strong></a>`).join("")}</div>
    <div class="workspace-two">${panel("Требуют внимания", '<a href="/app/risks">Все риски ↗</a>', listAlerts(alerts.slice(0, 5)))}
    ${panel("Ближайшие задачи", '<a href="/app/risks#tasks">К задачам ↗</a>', next.length ? `<ul class="workspace-records">${next.map((task) => `
      <li><div><strong>${escape(task.title)}</strong><p>${escape(task.shipment_id)} · ${escape(task.owner || "Без ответственного")} · ${taskDeadline(task)}</p></div>${chip(isOverdue(task) ? "Просрочена" : taskLabel(task.status), isOverdue(task))}</li>`).join("")}</ul>` : empty("Активных задач нет."))}</div>
    ${panel("Недавние события", "", activity.length ? `<ul class="workspace-records">${activity.slice(0, 6).map((event) => `
      <li><div><a href="${shipmentLink(event.shipment_id, "events")}"><strong>${escape(event.title)}</strong></a>
        <p>${escape(event.shipment_id)} · ${escape(event.shipment_name)}${event.demo ? " · демо" : ""}</p></div><time>${time(event.at)}</time></li>`).join("")}</ul>` : empty("События появятся после изменений в перевозках."))}
    <p class="workspace-caption">Сводка по доступным перевозкам · ${time(workspace.generated_at)}. Демо-грузы отмечены в реестре.</p>`;
}

function selectedRows() {
  const rank = { critical: 0, elevated: 1, normal: 2, complete: 3 };
  return workspace.shipments.filter((row) =>
    (filters.risk === "all" || row.risk === filters.risk) &&
    [row.id, row.name, row.cargo, row.route_name].join(" ").toLocaleLowerCase("ru").includes(filters.search))
    .sort((a, b) => filters.sort === "name" ? a.name.localeCompare(b.name, "ru")
      : filters.sort === "eta" ? (a.eta || "~").localeCompare(b.eta || "~")
        : (rank[a.risk] ?? 4) - (rank[b.risk] ?? 4));
}
function shipmentRows() {
  const rows = selectedRows();
  return rows.length ? `<ul class="workspace-shipments">${rows.map((row) => `
    <li><div><a href="${shipmentLink(row.id)}"><strong>${escape(row.name)}</strong></a>
        <p>${escape(row.id)} · ${escape(row.cargo)}${row.demo ? " · демо" : ""}</p></div>
      <div><span>Маршрут</span><strong>${escape(row.route_name || "—")}</strong></div>
      <div><span>Расчётное прибытие</span><strong>${time(row.eta)}</strong></div>
      <div>${chip(riskNames[row.risk] || statusNames[row.progress_status] || row.status, ["critical","elevated"].includes(row.risk))}</div>
      <a class="workspace-row-link" href="${shipmentLink(row.id)}" aria-label="Открыть ${escape(row.id)}">↗</a></li>`).join("")}</ul>` : empty("По заданным условиям перевозок нет.");
}
function registry() {
  return `<section class="workspace-panel"><div class="workspace-filters">
    <label>Поиск<input id="registry-search" type="search" placeholder="Номер, груз или маршрут" value="${escape(filters.search)}" /></label>
    <label>Риск<select id="registry-risk">${opts([["all","Все"], ...Object.entries(riskNames)], filters.risk)}</select></label>
    <label>Сортировка<select id="registry-sort">${opts([["risk","По риску"],["eta","По прибытию"],["name","По названию"]], filters.sort)}</select></label>
    <button type="button" class="button compact" data-reset-registry>Сбросить</button>
    </div><div id="registry-result">${shipmentRows()}</div></section>`;
}

function documents() {
  const docs = workspace.documents.filter((entry) => filters.documents === "all" ||
    (filters.documents === "attention" ? entry.status !== "ready_for_review" : entry.status === "ready_for_review"));
  return `<section class="workspace-panel"><div class="workspace-filters"><label>Комплект документов<select id="documents-filter">${opts([
    ["all","Все перевозки"],["attention","Требуют внимания"],["ready","Готовые к проверке"],
  ], filters.documents)}</select></label></div>
  ${docs.length ? `<ul class="workspace-records">${docs.map((entry) => `
    <li><div><a href="${shipmentLink(entry.shipment_id, "documents")}"><strong>${escape(entry.shipment_name)}</strong></a>
    <p>${escape(entry.shipment_id)} · файлов ${number(entry.files?.length ?? 0)}</p>
    <p>${escape((entry.requirements || []).filter((rule) => ["missing", "mismatch", "review_required"].includes(rule.status)).map((rule) => rule.label).slice(0, 4).join(", "))}</p></div>
    ${chip(documentNames[entry.status] || entry.status, entry.status !== "ready_for_review")}</li>`).join("")}</ul>` : empty("В этой очереди перевозок нет.")}</section>`;
}

function analytics() {
  const data = workspace.analytics;
  return `<div class="workspace-metrics">
    <div><span>Оставшиеся расходы</span><strong>${number(data.remaining_cost_usd)} <small>USD</small></strong></div>
    <div><span>Оставшиеся выбросы</span><strong>${number(data.remaining_co2_kg)} <small>кг CO₂e</small></strong></div>
    <div><span>Перевозки с расчётом</span><strong>${number(data.projected_shipments_count)}</strong></div>
    <div><span>Перевозки без расчёта</span><strong>${number(data.unprojected_shipments_count)}</strong></div></div>
    ${panel("Сравнение по маршрутам", "", data.by_route?.length ? `<ul class="workspace-analytics">${data.by_route.map((row) => `
      <li><strong>${escape(row.label)}</strong><dl>
        <div><dt>Перевозки</dt><dd>${number(row.count)}</dd></div>
        <div><dt>Остаток расходов</dt><dd>${money(row.remaining_cost_usd)}</dd></div>
        <div><dt>Остаток выбросов</dt><dd>${unit(row.remaining_co2_kg, "кг CO₂e")}</dd></div>
      </dl></li>`).join("")}</ul>` : empty("Маршрутов для сравнения пока нет."))}
    ${panel("По состоянию", "", `<ul class="workspace-records">${(data.by_status || []).map((row) => `<li>${escape(row.label)}<strong>${number(row.count)}</strong></li>`).join("")}</ul>`)}
    <p class="workspace-caption">${escape(data.notice || workspace.notice || "")} Демо-перевозок в сводке: ${number(data.demo_shipments_count)}.</p>`;
}

function risks() {
  const tasks = workspace.tasks.filter((task) =>
    filters.tasks === "all" || (filters.tasks === "done" ? task.status === "done" : task.status !== "done"));
  return `${panel("Предупреждения по перевозкам", "", listAlerts(workspace.alerts))}
    <section class="workspace-panel" id="tasks"><header><h2>Задачи</h2><button type="button" class="button primary compact" data-task-create>Новая задача</button></header>
      <div class="workspace-filters"><label>Статус<select id="tasks-filter">${opts([["active","Активные"],["done","Выполненные"],["all","Все задачи"]], filters.tasks)}</select></label></div>
      ${tasks.length ? `<ul class="workspace-records">${tasks.map((task) => `<li><div>
        <strong>${escape(task.title)}</strong>
        <p><a href="${shipmentLink(task.shipment_id)}">${escape(task.shipment_id)} · ${escape(task.shipment_name)}</a> · ${escape(task.owner || "Без ответственного")} · ${taskDeadline(task)}${isOverdue(task) ? ' · <strong class="workspace-overdue">Просрочена</strong>' : ""}</p>
        ${task.note ? `<p>${escape(task.note)}</p>` : ""}</div>
        <div class="workspace-task-actions">${chip(priorities[task.priority] || "Обычный", task.priority !== "normal")}${chip(taskLabel(task.status))}
          <button type="button" class="button compact" data-task-edit="${escape(task.id)}">Изменить</button></div></li>`).join("")}</ul>` : empty("В этом списке задач нет.")}
      <p class="workspace-caption">Задача фиксирует действие оператора. Предупреждения исчезают после изменения условий перевозки.</p>
    </section>`;
}

function render() {
  const views = { overview, shipments: registry, documents, risks, analytics };
  if (page === "map") { renderMap(); return; }
  content.innerHTML = (views[page] || overview)();
}
async function refresh() {
  loading?.abort();
  const request = new AbortController();
  loading = request;
  const refreshButton = $("#workspace-refresh");
  refreshButton.disabled = true;
  content.setAttribute("aria-busy", "true");
  try {
    const next = await api("/api/workspace", { signal: request.signal });
    if (loading !== request) return;
    workspace = next;
    render();
    if (location.hash === "#tasks") $("#tasks")?.scrollIntoView();
  } catch (error) {
    if (loading !== request || error.name === "AbortError") return;
    content.innerHTML = `<div class="workspace-panel"><h2>Данные недоступны</h2><p role="alert">${escape(error.message)}</p>
      <button class="button compact" type="button" data-workspace-retry>Повторить</button></div>`;
  } finally {
    if (loading === request) {
      refreshButton.disabled = false;
      content.setAttribute("aria-busy", "false");
    }
  }
}

async function renderMap() {
  mapRequest?.abort();
  map?.dispose();
  map = null;
  const selected = new URLSearchParams(location.search).get("shipment");
  const id = workspace.shipments.some((shipment) => shipment.id === selected) ? selected : workspace.shipments[0]?.id;
  content.innerHTML = `<section class="workspace-panel workspace-map-panel"><div class="workspace-filters">
    <label>Перевозка<select id="workspace-map-shipment">${opts(workspace.shipments.map((row) => [row.id, row.id + " · " + row.name]), id)}</select></label>
    <a id="workspace-map-passport" class="button compact" href="${id ? shipmentLink(id) : "/app/shipments"}">Паспорт ↗</a></div>
    <div id="workspace-map" aria-label="Интерактивная карта маршрутов"></div>
    <p id="workspace-map-status" class="workspace-caption" role="status">Загружаем карту…</p></section>`;
  const target = $("#workspace-map");
  try {
    const { createMapController } = await import("./map-controller.js");
    if (!target.isConnected) return;
    const controller = await createMapController(target, {
      mode: "ops",
      onPortSelect: (port, node) => {
        $("#workspace-map-status").textContent = `${node?.name || port} · ${node?.country || "Узел коридора"}`;
      },
    });
    if (!target.isConnected) { controller.dispose(); return; }
    map = controller;
    const selectedId = $("#workspace-map-shipment").value;
    if (selectedId) await showRoute(selectedId);
    else $("#workspace-map-status").textContent = "В реестре пока нет перевозок.";
  } catch (error) {
    if (target.isConnected) $("#workspace-map-status").textContent = "Карта недоступна. Маршрут можно открыть в паспорте.";
  }
}
async function showRoute(id) {
  if (!id || !map) return;
  mapRequest?.abort();
  const request = new AbortController();
  mapRequest = request;
  const status = $("#workspace-map-status");
  status.textContent = "Загружаем маршрут…";
  $("#workspace-map-passport").href = shipmentLink(id);
  try {
    const data = await api(`/api/operations/${encodeURIComponent(id)}`, { signal: request.signal });
    if (mapRequest !== request || !status.isConnected) return;
    map.setRoute({ id, legs: data.plan.legs || [], nodes: data.plan.nodes || [] });
    const total = data.plan.legs?.length || 0;
    map.setProgress(total ? (data.plan.completed_leg_ids?.length || 0) / total : 0);
    status.textContent = `${id} · план v${data.plan.version} · географическая схема`;
  } catch (error) {
    if (mapRequest === request && status.isConnected) {
      map.setRoute({ id, legs: [] });
      status.textContent = error.message;
    }
  }
}

function taskDialog() {
  if ($("#task-dialog")) return $("#task-dialog");
  const dialog = document.createElement("dialog");
  dialog.id = "task-dialog";
  dialog.innerHTML = `<form id="task-form"><div class="dialog-head"><h2>Задача</h2><button type="button" data-close class="close-button" aria-label="Закрыть">×</button></div>
    <label>Перевозка<select name="shipment_id" required></select></label>
    <label>Действие<input name="title" maxlength="120" required /></label>
    <div class="form-grid"><label>Ответственный<input name="owner" maxlength="80" /></label>
      <label>Срок<input name="due_at" type="datetime-local" /></label>
      <label>Приоритет<select name="priority">${opts(Object.entries(priorities), "normal")}</select></label>
      <label>Статус<select name="status">${opts(Object.entries(taskStatuses), "open")}</select></label></div>
    <label>Заметка<textarea name="note" maxlength="500"></textarea></label>
    <p class="form-error" role="alert"></p><button class="button primary" type="submit">Сохранить задачу</button></form>`;
  document.body.append(dialog);
  return dialog;
}
function openTask(id, alert) {
  taskSession++;
  editingTask = workspace.tasks.find((task) => task.id === id);
  const dialog = taskDialog();
  const form = dialog.querySelector("form");
  form.reset();
  form.querySelector("button[type=submit]").disabled = false;
  draftKey = crypto.randomUUID();
  form.querySelector("h2").textContent = editingTask ? "Изменить задачу" : "Новая задача";
  form.querySelector(".form-error").textContent = "";
  form.elements.shipment_id.innerHTML = opts(workspace.shipments.map((row) => [row.id, row.id + " · " + row.name]), editingTask?.shipment_id || alert?.shipment_id);
  if (alert) {
    form.elements.title.value = alert.title;
    form.elements.priority.value = priorities[alert.severity] ? alert.severity : "normal";
  }
  form.elements.shipment_id.disabled = Boolean(editingTask);
  for (const key of ["title", "owner", "priority", "status", "note"]) if (editingTask?.[key]) form.elements[key].value = editingTask[key];
  if (editingTask?.due_at) {
    const date = new Date(editingTask.due_at);
    form.elements.due_at.value = new Date(date.valueOf() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  }
  dialog.showModal();
}
async function submitTask(form) {
  const task = editingTask;
  const session = taskSession;
  const payload = Object.fromEntries(new FormData(form));
  payload.due_at = payload.due_at ? new Date(payload.due_at).toISOString() : null;
  payload.owner ||= "";
  payload.note ||= "";
  if (task) payload.expected_version = task.version;
  await api(task ? `/api/workspace/tasks/${encodeURIComponent(task.id)}` : "/api/workspace/tasks", {
    method: task ? "PATCH" : "POST",
    headers: task ? {} : { "Idempotency-Key": draftKey },
    body: JSON.stringify(payload),
  });
  const dialog = $("#task-dialog");
  if (session === taskSession && dialog.open) dialog.close();
  await refresh();
}

setHead();
if (!detail) {
  $("#workspace-refresh").addEventListener("click", refresh);
  document.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.dataset.open) {
      if (button.dataset.open === "access-dialog") $("#access-form [name=token]").value = token;
      $("#" + button.dataset.open)?.showModal();
    }
    if (button.hasAttribute("data-close")) button.closest("dialog")?.close();
    if (button.hasAttribute("data-workspace-retry")) refresh();
    if (button.hasAttribute("data-task-create")) openTask();
    if (button.dataset.taskAlert) {
      const alert = workspace.alerts.find((item) => item.id === button.dataset.taskAlert);
      if (alert) openTask(null, alert);
    }
    if (button.hasAttribute("data-reset-registry")) {
      Object.assign(filters, { search: "", risk: "all", sort: "risk" });
      render();
      $("#registry-search").focus();
    }
    if (button.dataset.taskEdit) openTask(button.dataset.taskEdit);
  });
  document.addEventListener("input", (event) => {
    if (event.target.id === "registry-search") {
      filters.search = event.target.value.trim().toLocaleLowerCase("ru");
      $("#registry-result").innerHTML = shipmentRows();
    }
  });
  document.addEventListener("change", (event) => {
    const { id, value } = event.target;
    if (id === "registry-risk" || id === "registry-sort") {
      filters[id.split("-")[1]] = value;
      $("#registry-result").innerHTML = shipmentRows();
    }
    if (id === "documents-filter") { filters.documents = value; render(); }
    if (id === "tasks-filter") { filters.tasks = value; render(); }
    if (id === "workspace-map-shipment") {
      const url = new URL(location.href);
      url.searchParams.set("shipment", value);
      history.replaceState(null, "", url);
      showRoute(value);
    }
  });
  document.addEventListener("submit", async (event) => {
    const form = event.target;
    if (!["create-form", "access-form", "task-form"].includes(form.id)) return;
    event.preventDefault();
    const button = form.querySelector("button[type=submit]") || Array.from(form.elements).find((control) => control.type === "submit");
    if (button?.disabled || !form.reportValidity()) return;
    if (button) button.disabled = true;
    const error = form.querySelector(".form-error");
    const session = taskSession;
    const isCurrentTask = () => form.id !== "task-form" ||
      (session === taskSession && form.closest("dialog").open);
    if (error) error.textContent = "";
    try {
      if (form.id === "task-form") await submitTask(form);
      if (form.id === "access-form") {
        token = String(new FormData(form).get("token") || "");
        try {
          if (token) sessionStorage.setItem("caspian-operator-token", token);
          else sessionStorage.removeItem("caspian-operator-token");
        } catch {}
        form.reset();
        form.closest("dialog").close();
      }
      if (form.id === "create-form") {
        form.dataset.key ||= crypto.randomUUID();
        const payload = Object.fromEntries(new FormData(form));
        payload.weight_t = Number(payload.weight_t);
        const result = await api("/api/shipments", {
          method: "POST", headers: { "Idempotency-Key": form.dataset.key }, body: JSON.stringify(payload),
        });
        form.closest("dialog").close();
        form.reset();
        delete form.dataset.key;
        location.assign(shipmentLink(result.id || result.shipment?.id));
      }
    } catch (failure) {
      if (!isCurrentTask()) return;
      if (error) error.textContent = failure.message;
      else console.error(failure);
      if (failure.status === 409 && form.id === "task-form") refresh();
    } finally {
      if (button && isCurrentTask()) button.disabled = false;
    }
  });
  window.addEventListener("pagehide", (event) => {
    if (event.persisted) return;
    loading?.abort();
    mapRequest?.abort();
    map?.dispose();
  });
  window.addEventListener("online", refresh);
  if (new URLSearchParams(location.search).has("create")) $("#create-dialog").showModal();
  refresh();
}
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
