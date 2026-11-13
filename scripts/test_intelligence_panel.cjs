const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const { once } = require("node:events");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { chromium } = require("playwright");

const root = path.resolve(__dirname, "..");
const port = Number(process.env.ML_UI_TEST_PORT || 8773);
const externalBase = process.env.SCT_TEST_URL;
const base = externalBase || `http://127.0.0.1:${port}`;
const output = process.env.ML_UI_SCREENSHOTS || path.join(root, "artifacts/qa");
let dataDir, server;

async function startServer() {
  if (externalBase) return;
  dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "caspian-ml-ui-"));
  let log = "";
  server = spawn(process.env.PYTHON || path.join(root, ".venv/bin/python"), [
    "-m", "uvicorn", "app.main:app", "--app-dir", "backend",
    "--host", "127.0.0.1", "--port", String(port),
  ], {
    cwd: root,
    env: {
      ...process.env, SCT_DATA_DIR: dataDir, SCT_WRITE_RATE_LIMIT: "1000",
      SCT_ML_HISTORY_CSV: "", SCT_WRITE_TOKEN: "",
    },
    stdio: ["ignore", "ignore", "pipe"],
  });
  server.on("error", (error) => { log += error.message; });
  server.stderr.on("data", (chunk) => { log = (log + chunk).slice(-4000); });
  for (let i = 0; i < 200; i++) {
    if (server.exitCode !== null || !server.pid) break;
    try {
      if ((await fetch(base + "/api/health")).ok) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Test server unavailable: ${log}`);
}

async function openPanel(page) {
  await page.goto(base + "/app/shipments/SHP-1042");
  await page.getByRole("tab", { name: "ML-прогноз" }).click();
  await page.locator(".intelligence-number").waitFor();
}

(async () => {
  await startServer();
  const browser = await chromium.launch({
    ...(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {}),
  });
  try {
    const page = await browser.newPage({
      viewport: { width: 1440, height: 900 },
      reducedMotion: "reduce",
      serviceWorkers: "block",
    });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const writes = [];
    page.on("request", (request) => {
      if (request.url().includes("/api/") && request.method() !== "GET") writes.push(request);
    });

    await openPanel(page);
    assert.match(await page.locator("#intelligence-context").textContent(), /SHP-1042 · план v1/);
    assert.match(await page.locator(".intelligence-range").textContent(), /90%.*синтетическ/);
    assert.match(await page.locator(".intelligence-score").textContent(), /Расчёт по правилам/);
    await page.getByText("Качество на отложенной выборке", { exact: true }).click();
    await page.getByText("Медиана задержки", { exact: true }).waitFor();
    await page.getByRole("heading", { name: "Другие маршруты" }).waitFor();
    if (output) {
      fs.mkdirSync(output, { recursive: true });
      await page.locator("#view-intelligence").screenshot({ path: path.join(output, "ml-desktop.png") });
    }
    console.log("PASS ML estimate, interval, separate rules, route comparisons and test metrics");

    await page.getByRole("spinbutton", { name: "Высота волн, м" }).fill("8");
    await page.getByRole("spinbutton", { name: "Доп. время на таможне, ч" }).fill("12");
    const preview = page.waitForResponse((response) => response.url().includes("wave_m=8") && response.ok());
    await page.getByRole("button", { name: "Сравнить сценарий" }).click();
    await preview;
    await page.getByText("Предпросмотр · план перевозки не изменён").waitFor();
    await page.getByText("Условия вне обучающей выборки", { exact: true }).waitFor();
    assert.match(await page.locator("#intelligence-context").textContent(), /план v1/);
    assert.deepEqual(writes, []);
    await page.getByRole("button", { name: "Сбросить", exact: true }).click();
    await page.locator(".intelligence-number").waitFor();
    assert.equal(await page.getByRole("spinbutton", { name: "Высота волн, м" }).inputValue(), "");
    console.log("PASS what-if changes estimate and does not write");

    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const hold = async (route) => { await gate; await route.continue().catch(() => {}); };
    const heldUrl = /\/api\/operations\/SHP-1042\/intelligence\?[^\s]*wave_m=9/;
    await page.route(heldUrl, hold);
    await page.getByRole("spinbutton", { name: "Высота волн, м" }).fill("9");
    const requested = page.waitForRequest(heldUrl);
    await page.getByRole("button", { name: "Сравнить сценарий" }).click();
    await requested;
    await page.goto(base + "/app/shipments/SHP-1043?tab=intelligence");
    await page.locator(".intelligence-number").waitFor();
    release();
    assert.match(await page.locator("#intelligence-context").textContent(), /SHP-1043/);
    assert.equal(await page.getByRole("spinbutton", { name: "Высота волн, м" }).inputValue(), "");
    assert.doesNotMatch(await page.locator("#intelligence-status").textContent(), /Предпросмотр/);
    await page.unroute(heldUrl, hold);
    console.log("PASS selecting another shipment cancels stale preview");

    await page.route(/\/api\/operations\/SHP-1043\/intelligence\?/, (route) =>
      route.fulfill({ status: 503, json: { detail: "Проверка ошибки" } }),
      { times: 1 },
    );
    await page.getByRole("tab", { name: "Маршрут", exact: true }).click();
    await page.getByRole("button", { name: "Шторм", exact: true }).click();
    await page.getByRole("tab", { name: "ML-прогноз", exact: true }).click();
    await page.getByRole("alert").getByText("Проверка ошибки", { exact: true }).waitFor();
    await page.getByRole("button", { name: "Повторить", exact: true }).click();
    await page.locator(".intelligence-number").waitFor();
    assert.equal(await page.getByRole("spinbutton", { name: "Высота волн, м" }).isDisabled(), true);
    assert.match(await page.locator("#intelligence-context").textContent(), /шторм в пульте/);
    console.log("PASS storm error, retry and fixed weather");

    if (!externalBase || process.env.SCT_TEST_JOURNAL === "1") {
      await page.getByRole("tab", { name: "Маршрут", exact: true }).click();
      await page.getByRole("button", { name: "Обычная погода", exact: true }).click();
      await page.getByRole("tab", { name: "ML-прогноз", exact: true }).click();
      await page.locator(".intelligence-number").waitFor();
      await page.getByText("Журнал проверки на рейсах", { exact: true }).click();
      await page.getByRole("button", { name: "Сохранить прогноз", exact: true }).click();
      await page.getByText("Прогноз сохранён.", { exact: true }).waitFor();
      await page.getByText("Исключён из метрик", { exact: true }).waitFor();
      assert.equal(writes.length, 1);
      assert.match(writes[0].url(), /\/intelligence\/snapshots$/);
      console.log("PASS explicit journal capture and demo exclusion");
    }

    if (!externalBase) {
      const shipmentResponse = await page.request.post(`${base}/api/shipments`, {
        headers: { "Idempotency-Key": "ml-ui-real-shipment" },
        data: { name: "Проверка журнала", cargo: "Тестовый груз", weight_t: 48, route_id: "R1" },
      });
      assert.ok(shipmentResponse.ok());
      const shipment = await shipmentResponse.json();
      const id = shipment.id || shipment.shipment?.id;
      await page.goto(base + `/app/shipments/${encodeURIComponent(id)}?tab=intelligence`);
      await page.locator(".intelligence-number").waitFor();
      const journal = page.locator("#intelligence-journal");
      if ((await journal.getAttribute("open")) === null) {
        await journal.locator("summary").click();
      }
      await page.getByRole("button", { name: "Сохранить прогноз", exact: true }).click();
      await page.getByText("Ожидает прибытия", { exact: true }).waitFor();
      const saved = await page.request.get(`${base}/api/operations/${id}/intelligence/snapshots`);
      const snapshot = (await saved.json()).snapshots[0];
      for (const legId of snapshot.remaining_leg_ids) {
        const event = await page.request.post(`${base}/api/shipments/${id}/events`, {
          data: { event_type: "arrival", leg_id: legId, source: "operator-manual", occurred_ts: new Date().toISOString() },
        });
        assert.ok(event.ok(), await event.text());
      }
      await page.getByRole("button", { name: "Обновить журнал", exact: true }).click();
      await page.getByText("Сверен", { exact: true }).waitFor();
      const link = page.getByRole("link", { name: "Скачать CSV" });
      await link.waitFor();
      const csv = await page.request.get(new URL(await link.getAttribute("href"), base).href);
      assert.ok(csv.ok());
      assert.match(await csv.text(), new RegExp(id));
      console.log("PASS operator arrivals match the forecast and enable CSV");
    }

    const mobile = await browser.newPage({
      viewport: { width: 390, height: 844 },
      isMobile: true,
      hasTouch: true,
      reducedMotion: "reduce",
      serviceWorkers: "block",
    });
    mobile.on("pageerror", (error) => errors.push(error.message));
    await openPanel(mobile);
    if (output) await mobile.locator("#view-intelligence").screenshot({ path: path.join(output, "ml-mobile.png") });
    assert.equal(await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await mobile.getByRole("tab", { name: "ML-прогноз" }).press("Home");
    assert.equal(await mobile.getByRole("tab", { name: "Маршрут" }).getAttribute("aria-selected"), "true");
    assert.deepEqual(errors, []);
    console.log("PASS mobile width and keyboard tab navigation");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
}).finally(async () => {
  if (server?.pid && server.exitCode === null) {
    const closed = once(server, "close");
    server.kill("SIGTERM");
    await closed;
  }
  if (dataDir) fs.rmSync(dataDir, { recursive: true, force: true });
});
