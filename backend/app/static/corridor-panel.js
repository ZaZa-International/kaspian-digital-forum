import { escape, showError, time } from "./operations-labs.js";

const decisions = {
  ready_for_review: "Готово к рассмотрению",
  needs_documents: "Нужны документы",
  review_required: "Нужна ручная проверка",
  mismatch: "Есть расхождения",
};
const rules = {
  pass: "Пройдено", missing: "Нет документа", mismatch: "Расхождение", review_required: "Ручная проверка",
};
const sources = { demo: "демо-данные", checklist: "правила маршрута", text: "из текста документа" };
const verificationNames = {
  valid: "Подпись верна · квитанция актуальна",
  stale: "Подпись верна · сведения устарели",
  invalid: "Проверка не пройдена",
};

function render(data, demo) {
  const { assessment, issuer, receipts = [], chain_valid: chainValid } = data;
  return `<div class="lab-assessment ${assessment.decision === "ready_for_review" && chainValid !== false ? "" : "lab-assessment-warning"}">
      <div><strong>${escape(decisions[assessment.decision] || assessment.decision)}</strong>
        <p class="lab-meta">План v${escape(assessment.plan_version)} · правила ${escape(assessment.rule_version)}${assessment.demo ? " · демонстрационный груз" : ""}</p>
        <p class="lab-hash" title="${escape(assessment.evidence_hash)}">Отпечаток проверки <code>${escape(assessment.evidence_hash?.slice(0, 16))}…</code></p></div>
      ${chainValid === false
        ? '<p class="lab-meta">Журнал не прошёл проверку целостности. Выпуск приостановлен.</p>'
        : `<button type="button" class="lab-primary" data-corridor-issue ${demo ? "disabled" : ""}>Подписать квитанцию</button>`}
    </div>
    <ul class="lab-rules">${(assessment.rules || []).map((rule) => `
      <li><div><strong>${escape(rule.label || rule.key)}</strong>
        <p>${escape(rule.reason || "")}</p>
        ${rule.source ? `<small>Источник · ${escape(sources[rule.source] || rule.source)}</small>` : ""}
        ${(rule.mismatches || []).map((value) => `<p class="lab-mismatch">${escape(value.message || value)}</p>`).join("")}
        </div><span class="lab-pill ${rule.status === "pass" ? "" : "lab-warn"}">${escape(rules[rule.status] || rule.status)}</span></li>`).join("")}</ul>
    <section class="lab-receipts" aria-labelledby="lab-receipts-title">
      <div class="lab-heading"><div><h5 id="lab-receipts-title">Подписанные квитанции</h5>
        <p>${issuer ? `Ключ издателя · ${escape(issuer.algorithm)} · <code title="${escape(issuer.key_id)}">${escape(issuer.key_id?.slice(0, 16))}…</code>` : "Ключ издателя появится при выпуске первой квитанции."}</p>
      ${issuer ? '<p>Для офлайн-проверки передавайте открытый ключ отдельно по доверенному каналу.</p>' : ""}</div>
      ${issuer ? '<button type="button" class="lab-secondary" data-corridor-issuer>Скачать открытый ключ</button>' : ""}</div>
      ${receipts.length ? `<ul>${receipts.map((receipt) => `
        <li><div><strong>${escape(verificationNames[receipt.verification?.status] || "Ожидает проверки")}</strong>
          <p class="lab-meta">${time(receipt.issued_at)} · план v${escape(receipt.plan_version)} · ${escape(decisions[receipt.decision] || receipt.decision)}</p>
          <p class="lab-hash"><code title="${escape(receipt.hash)}">${escape(receipt.hash?.slice(0, 16))}…</code></p></div>
          <div class="lab-actions">
            <button type="button" class="lab-secondary" data-corridor-verify="${escape(receipt.id)}">Проверить</button>
            <button type="button" class="lab-secondary" data-corridor-tamper="${escape(receipt.id)}">Проверить изменённую копию</button>
            <a class="lab-secondary" href="/api/operations/${encodeURIComponent(assessment.shipment_id)}/corridor/receipts/${encodeURIComponent(receipt.id)}?download=true">Скачать JSON</a>
          </div></li>`).join("")}</ul>` : '<p class="lab-meta">Квитанций пока нет. Можно зафиксировать текущую проверку вместе с найденными замечаниями.</p>'}
    </section>`;
}

export function createCorridorPanel(root, api, isDemo) {
  const status = root.querySelector("[data-corridor-status]");
  const result = root.querySelector("[data-corridor-result]");

  const reload = root.querySelector("[data-corridor-reload]");
  reload.disabled = true;
  const issueKeys = new Map();
  let context, data, request;
  let active = false;
  let writing = false;

  function abort() {
    request?.abort();
    request = null;
    writing = false;
    result.setAttribute("aria-busy", "false");
  }

  function endpoint() {
    return `/api/operations/${encodeURIComponent(context.id)}/corridor`;
  }

  async function load(message = "") {
    if (!context || !active || writing) return;
    abort();
    const controller = new AbortController();
    request = controller;
    const snapshot = context;
    status.textContent = "Проверяем документы…";
    result.setAttribute("aria-busy", "true");
    try {
      const next = await api(endpoint(), { signal: controller.signal });
      if (request !== controller || context !== snapshot) return;
      if (next.assessment.shipment_id !== snapshot.id || next.assessment.plan_version !== snapshot.version) {
        throw new Error("Версия плана изменилась. Обновите пульт перед проверкой.");
      }
      data = next;
      result.innerHTML = render(data, isDemo());
      status.textContent = message;
    } catch (error) {
      if (request === controller && error.name !== "AbortError") {
        data = null;
        result.replaceChildren();
        showError(status, error, load);
      }
    } finally {
      if (request === controller) abort();
    }
  }

  async function issue(button) {
    if (!context || !active || writing || request || !data?.assessment || data.chain_valid === false || isDemo()) return;
    const { assessment } = data;
    const controller = new AbortController();
    const key = `${assessment.shipment_id}:${assessment.plan_version}:${assessment.evidence_hash}`;
    const issueEndpoint = endpoint() + "/issue";
    if (!issueKeys.has(key)) issueKeys.set(key, crypto.randomUUID());
    request = controller;
    writing = true;
    result.setAttribute("aria-busy", "true");
    button.disabled = true;
    status.textContent = "Подписываем квитанцию…";
    try {
      await api(issueEndpoint, {
        method: "POST", signal: controller.signal, headers: { "Idempotency-Key": issueKeys.get(key) },
        body: JSON.stringify({ plan_version: assessment.plan_version, evidence_hash: assessment.evidence_hash }),
      });
      issueKeys.delete(key);
      if (request !== controller) return;
      abort();
      await load("Квитанция подписана.");
    } catch (error) {
      if (request !== controller || error.name === "AbortError") return;
      if (error.status === 409) {
        issueKeys.delete(key);
        data = null;
        result.replaceChildren();
        showError(status, new Error("Комплект документов или план изменился. Обновите проверку перед выпуском."), load);
      } else showError(status, error);
    } finally {
      if (request === controller) {
        abort();
        button.disabled = false;
      }
    }
  }

  async function verify(receiptId, tamper, button) {
    if (!context || !active || request) return;
    const base = endpoint();
    const controller = new AbortController();
    request = controller;
    button.disabled = true;
    result.setAttribute("aria-busy", "true");
    status.textContent = "Проверяем подпись…";
    try {
      const envelope = await api(`${base}/receipts/${encodeURIComponent(receiptId)}`, { signal: controller.signal });
      const receipt = envelope.receipt;
      if (tamper) {
        receipt.payload.decision = receipt.payload.decision === "ready_for_review" ? "needs_documents" : "ready_for_review";
      }
      const checked = await api(base + "/verify", {
        method: "POST", signal: controller.signal, body: JSON.stringify(receipt),
      });
      if (request !== controller) return;
      status.textContent = tamper && checked.status === "invalid"
        ? "Изменённая копия отклонена. Исходная квитанция сохранена."
        : verificationNames[checked.status] || "Проверка завершена.";
    } catch (error) {
      if (request === controller && error.name !== "AbortError") showError(status, error);
    } finally {
      if (request === controller) {
        abort();
        button.disabled = false;
      }
    }
  }

  async function downloadIssuer(button) {
    if (!active || request || !data?.issuer) return;
    const controller = new AbortController();
    request = controller;
    button.disabled = true;
    status.textContent = "Получаем открытый ключ…";
    try {
      const key = await api("/api/operations/corridor/issuer", { signal: controller.signal });
      if (request !== controller) return;
      if (!key.issuer?.public_key) throw new Error("Открытый ключ недоступен.");
      const url = URL.createObjectURL(new Blob([JSON.stringify(key, null, 2)], { type: "application/json" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = "corridor-issuer.json";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      status.textContent = "";
    } catch (error) {
      if (request === controller && error.name !== "AbortError") showError(status, error);
    } finally {
      if (request === controller) {
        abort();
        button.disabled = false;
      }
    }
  }

  reload.addEventListener("click", () => load());
  result.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.hasAttribute("data-corridor-issue")) issue(button);
    if (button.hasAttribute("data-corridor-issuer")) downloadIssuer(button);
    if (button.dataset.corridorVerify) verify(button.dataset.corridorVerify, false, button);
    if (button.dataset.corridorTamper) verify(button.dataset.corridorTamper, true, button);
  });
  return {
    setContext(next) {
      context = next;
      reload.disabled = false;
      if (active) load();
    },
    suspend(id) {
      abort();
      context = null;
      data = null;
      result.replaceChildren();
      reload.disabled = true;
      status.textContent = id ? "Обновляем паспорт…" : "Выберите перевозку.";
    },
    setActive(next) {
      if (active === next) return;
      active = next;
      if (active) load();
      else {
        abort();
        data = null;
        result.replaceChildren();
      }
    },
  };
}
