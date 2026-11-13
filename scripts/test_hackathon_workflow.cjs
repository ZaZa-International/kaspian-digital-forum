const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const { once } = require("node:events");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { chromium } = require("playwright");

const root = path.resolve(__dirname, "..");
const port = Number(process.env.HACKATHON_UI_TEST_PORT || 8775);
const base = `http://127.0.0.1:${port}`;
const output = process.env.HACKATHON_UI_SCREENSHOTS || path.join(root, "artifacts", "qa");
const data = fs.mkdtempSync(path.join(os.tmpdir(), "caspian-hackathon-ui-"));
const token = "local-playwright-corridor";
let server, browser;

async function setup() {
  let stderr = "";
  server = spawn(process.env.PYTHON || path.join(root, ".venv/bin/python"), [
    "-m", "uvicorn", "app.main:app", "--app-dir", "backend",
    "--host", "127.0.0.1", "--port", String(port),
  ], {
    cwd: root,
    env: { ...process.env, SCT_DATA_DIR: data, SCT_WRITE_TOKEN: token, SCT_WRITE_RATE_LIMIT: "1000" },
    stdio: ["ignore", "ignore", "pipe"],
  });
  server.on("error", (error) => { stderr = error.message; });
  server.stderr.on("data", (chunk) => { stderr = (stderr + chunk).slice(-3000); });
  for (let i = 0; i < 200; i++) {
    if (!server.pid || server.exitCode !== null) break;
    try { if ((await fetch(base + "/api/health")).ok) return; } catch {}
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("Isolated app did not start. " + stderr);
}

const shipment = "/api/operations/SHP-1042";
const headers = { "X-Write-Token": token };
const waitText = (scope, value) => scope.getByText(value, { exact: true }).waitFor();
const post = (page, route, options) => page.request.post(base + route, { headers, ...options });

async function main() {
  await setup();
  browser = await chromium.launch({
    ...(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {}),
  });
  fs.mkdirSync(output, { recursive: true });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 920 },
    reducedMotion: "reduce", serviceWorkers: "block", acceptDownloads: true,
  });
  const errors = [], writes = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    if (request.url().includes("/api/") && request.method() !== "GET") writes.push(request);
  });
  await page.goto(base);
  await page.getByRole("tab", { name: "Стыковки" }).click();
  const lab = page.locator("#view-resilience");
  await lab.locator(".lab-cliff").waitFor();
  const example = page.waitForResponse((response) =>
    response.url().includes("feeder_delay_h=13") && response.ok());
  await lab.getByRole("button", { name: "Пример порога" }).click();
  const modeled = await (await example).json();
  assert.equal(modeled.connection.cliff.example.jump_h, 72);
  assert.equal(modeled.connection.baseline.catch_in_h, 96);
  assert.equal(modeled.connection.intervention.catch_in_h, 24);
  await waitText(lab, "Предпросмотр · принятый план не изменён");
  assert.equal(writes.length, 0);
  const unchanged = await (await page.request.get(base + shipment)).json();
  assert.equal(unchanged.plan.version, modeled.plan_version);
  await lab.getByText("Проверка в сложных условиях", { exact: true }).click();
  await lab.screenshot({ path: path.join(output, "resilience-desktop.png") });
  console.log("PASS scenario crosses the 72h sailing threshold without writes");

  const profile = lab.getByRole("combobox", { name: "Приоритет маршрута" });
  for (const value of ["time", "cost", "green", "balanced"]) {
    await profile.selectOption(value);
    const requested = page.waitForResponse((response) =>
      response.url().includes(`profile=${value}`) && response.ok());
    await lab.getByRole("button", { name: "Рассчитать" }).click();
    assert.equal((await (await requested).json()).decision.profile, value);
    await waitText(lab, "Предпросмотр · принятый план не изменён");
  }
  await page.locator("[data-shipment=SHP-1044]").click();
  await waitText(lab, "Оставшийся путь без парома");
  await page.locator("[data-shipment=SHP-1042]").click();
  await lab.locator(".lab-cliff").waitFor();
  const newShipment = await post(page, "/api/shipments", {
    headers: { ...headers, "Idempotency-Key": "hackathon-transit-fixture" },
    data: { name: "Проверка морского плеча", cargo: "Контейнер", route_id: "R1", weight_t: 48 },
  });
  assert.ok(newShipment.ok());
  const newCargo = await newShipment.json();
  const id = newCargo.id || newCargo.shipment?.id;
  for (const [type, leg] of [["arrival", "R1-L1"], ["departure", "R1-L2"]]) {
    const event = await post(page, `/api/shipments/${id}/events`, {
      data: { event_type: type, leg_id: leg, source: "operator-manual", occurred_ts: new Date().toISOString() },
    });
    assert.ok(event.ok(), await event.text());
  }
  await page.locator("#refresh").click();
  await page.locator(`[data-shipment="${id}"]`).click();
  await waitText(lab, "Груз уже на морском плече");
  await page.locator("[data-shipment=SHP-1042]").click();
  await lab.locator(".lab-cliff").waitFor();
  console.log("PASS all route profiles, land-only and already-sailed states");

  await page.getByRole("tab", { name: "Документы" }).click();
  const corridor = page.locator("#corridor-panel");
  const issue = corridor.getByRole("button", { name: "Подписать квитанцию" });
  await issue.waitFor();
  await issue.click();
  await waitText(corridor, "Нужен ключ оператора. Откройте «Ключ доступа».");
  await page.getByRole("button", { name: "Ключ доступа", exact: true }).click();
  await page.getByRole("textbox", { name: "Ключ оператора" }).fill(token);
  await page.getByRole("button", { name: "Применить ключ" }).click();
  await issue.click();
  await waitText(corridor, "Квитанция подписана.");
  await waitText(corridor, "Подпись верна · квитанция актуальна");
  await corridor.getByRole("button", { name: "Проверить", exact: true }).click();
  await corridor.locator("[data-corridor-status]").getByText("Подпись верна · квитанция актуальна").waitFor();
  await corridor.getByRole("button", { name: "Проверить изменённую копию" }).click();
  await waitText(corridor, "Изменённая копия отклонена. Исходная квитанция сохранена.");
  await page.locator("#toast").waitFor({ state: "hidden" });
  await corridor.screenshot({ path: path.join(output, "corridor-desktop.png") });
  const download = page.waitForEvent("download");
  await corridor.getByRole("link", { name: "Скачать JSON" }).click();
  const downloaded = await download;
  const receipt = JSON.parse(fs.readFileSync(await downloaded.path(), "utf8"));
  assert.equal(receipt.payload.shipment_id, "SHP-1042");
  assert.ok(receipt.signature);
  const issuerDownload = page.waitForEvent("download");
  const issuerResponse = page.waitForResponse((response) =>
    response.url().endsWith("/api/operations/corridor/issuer") && response.ok());
  await corridor.getByRole("button", { name: "Скачать открытый ключ" }).click();
  const issuerFile = await issuerDownload;
  assert.equal(issuerFile.suggestedFilename(), "corridor-issuer.json");
  const issuer = JSON.parse(fs.readFileSync(await issuerFile.path(), "utf8")).issuer;
  assert.equal(issuer.algorithm, "Ed25519");
  assert.equal(issuer.key_id, receipt.key_id);
  assert.match(issuer.public_key, /^[0-9a-f]{64}$/);
  assert.equal(issuer.public_key, (await (await issuerResponse).json()).issuer.public_key);
  const original = await post(page, shipment + "/corridor/verify", { data: receipt });
  assert.equal((await original.json()).status, "valid");
  console.log("PASS guarded issue, separate trusted-key download, signed download, original verification and modified-copy rejection");

  const upload = await post(page, shipment + "/documents", {
    multipart: {
      kind: "invoice",
      file: { name: "invoice.txt", mimeType: "text/plain", buffer: Buffer.from("Shipment ID: SHP-1042\nWeight: 49 t") },
    },
  });
  assert.equal(upload.status(), 201);
  await corridor.getByRole("button", { name: "Обновить проверку" }).click();
  await waitText(corridor, "Подпись верна · сведения устарели");
  console.log("PASS updated documents mark signed assessment as stale");

  const beforeDemo = writes.length;
  await page.locator("#start-demo").click();
  await page.locator("#demo-title").getByText(/Перевозка под наблюдением/).waitFor();
  for (const title of [/Шторм/, /Есть другой путь/, /Граница погрузки/, /ML и расчёт/, /Цифровой коридор/]) {
    await page.getByRole("button", { name: "Следующий шаг" }).click();
    await page.locator("#demo-title").getByText(title).waitFor();
  }
  await issue.waitFor();
  assert.equal(await issue.isDisabled(), true);
  assert.equal(writes.length, beforeDemo);
  await page.getByRole("button", { name: "Закончить демонстрацию" }).click();
  console.log("PASS guided tour demonstrates both features without issuing");

  const mobile = await browser.newPage({
    viewport: { width: 390, height: 844 }, isMobile: true,
    hasTouch: true, reducedMotion: "reduce", serviceWorkers: "block",
  });
  mobile.on("pageerror", (error) => errors.push(error.message));
  await mobile.goto(base);
  await mobile.getByRole("tab", { name: "Стыковки" }).click();
  await mobile.locator("#view-resilience .lab-cliff").waitFor();
  await mobile.locator(".lab-route-list > li").first().waitFor();
  assert.equal(await mobile.locator(".lab-table").isVisible(), false);
  assert.equal(await mobile.locator(".lab-route-list dd").first().evaluate((metric) => getComputedStyle(metric).fontSize), "13px");
  await mobile.locator("#view-resilience").screenshot({ path: path.join(output, "resilience-mobile.png") });
  await mobile.getByRole("tab", { name: "Документы" }).click();
  await mobile.locator("#corridor-panel .lab-assessment").waitFor();
  await mobile.locator("#corridor-panel").screenshot({ path: path.join(output, "corridor-mobile.png") });
  assert.equal(await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  assert.deepEqual(errors, []);
  console.log("PASS mobile layouts and no uncaught JavaScript errors");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
}).finally(async () => {
  if (browser) await browser.close();
  if (server?.pid && server.exitCode === null) {
    const closed = once(server, "close");
    server.kill("SIGTERM");
    await closed;
  }
  fs.rmSync(data, { recursive: true, force: true });
});
