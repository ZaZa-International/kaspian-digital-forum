/* End-to-end checks use their own temporary database and never reset a running demo. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawn } = require("node:child_process");
const { chromium } = require("playwright");
const root = path.resolve(__dirname, "..");
const output = path.join(root, "artifacts/qa");
const data = fs.mkdtempSync(path.join(os.tmpdir(), "caspian-ui-"));
const port = Number(process.env.UI_TEST_PORT || 8772);
const base = `http://127.0.0.1:${port}`;
const python = process.env.PYTHON || path.join(root, ".venv/bin/python");
const checks = [],
  errors = [];
let server, browser;
async function until(fn, label, timeout = 20000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    if (await fn()) return;
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error(`Timeout: ${label}`);
}
function pass(name, evidence) {
  checks.push({ name, status: "PASS", evidence });
  console.log(`PASS ${name}`);
}
async function main() {
  fs.mkdirSync(output, { recursive: true });
  server = spawn(
    python,
    [
      "-m",
      "uvicorn",
      "app.main:app",
      "--app-dir",
      "backend",
      "--host",
      "127.0.0.1",
      "--port",
      String(port),
    ],
    {
      cwd: root,
      env: { ...process.env, SCT_DATA_DIR: data, SCT_WRITE_RATE_LIMIT: "1000" },
      stdio: ["ignore", "ignore", "pipe"],
    },
  );
  let log = "";
  server.stderr.on("data", (v) => (log = (log + v).slice(-4000)));
  await until(async () => {
    try {
      return (await fetch(base + "/api/health")).ok;
    } catch {
      return false;
    }
  }, "server ready");
  browser = await chromium.launch({
    headless: true,
    ...(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {}),
  });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 960 },
    deviceScaleFactor: 1,
    timezoneId: "Europe/Moscow",
  });
  const page = await context.newPage();
  page.on("pageerror", (e) => errors.push(e.message));
  const get = async (id) =>
    (await page.request.get(`${base}/api/operations/${id}`)).json();
  await page.goto(base, { waitUntil: "networkidle" });
  await page.locator("#hero-map canvas").waitFor();
  assert.equal(await page.locator("#operations").count(), 0);
  pass("home keeps the geographic atlas and separates the workbench");
  await page.screenshot({ path: path.join(output, "desktop-hero.png") });
  await page.evaluate(() => scrollTo({ top: 600, behavior: "instant" }));
  await until(
    () =>
      page
        .locator("#freight-video")
        .evaluate((video) => video.currentTime > 0.2),
    "freight video follows scroll",
  );
  assert.equal(
    await page.locator("#freight-video").evaluate((video) => video.paused),
    true,
  );
  await page.screenshot({
    path: path.join(output, "desktop-freight-handling.png"),
  });
  await page.evaluate(() => scrollTo({ top: 0, behavior: "instant" }));
  await until(
    () =>
      page
        .locator("#freight-video")
        .evaluate((video) => video.currentTime < 0.05),
    "freight video reverses",
  );
  pass("freight video scrubs forward and backward without autoplay");
  await page.goto(base + "/app/shipments", { waitUntil: "networkidle" });
  await until(() => page.locator(".workspace-shipments > li").count().then((n) => n === 3), "three seed shipments in registry");
  await page.locator('.workspace-shipments a[href="/app/shipments/SHP-1042"]').first().click();
  await page.locator("#workspace-heading").filter({ hasText: "Электроника" }).waitFor();
  assert.match(await page.locator("#workspace-description").innerText(), /SHP-1042/);
  await page.locator("#review-plan").waitFor();
  await page.getByRole("button", { name: "2D карта", exact: true }).click();
  await page.locator("#ops-map [data-renderer=leaflet]").waitFor();
  pass("shipment page loads the dispatcher and interactive map without the hero");
  await page
    .locator("#operations")
    .evaluate((el) =>
      el.scrollIntoView({ behavior: "instant", block: "start" }),
    );
  await page.waitForTimeout(500);
  await page.screenshot({ path: path.join(output, "desktop-ops.png") });
  const calm = await get("SHP-1042");
  await page.locator("#storm").click();
  await until(() => page.locator("#refresh").isEnabled(), "storm refresh");
  const storm = await (
    await page.request.get(base + "/api/operations/SHP-1042?storm=true")
  ).json();
  assert.ok(storm.baseline.remaining_h > calm.baseline.remaining_h);
  assert.ok(storm.proposals.some((p) => p.legs.every((l) => l.mode !== "sea")));
  assert.match(await page.locator(".delta-grid").innerText(), /к времени в пути/);
  pass("storm proposes a ground route from current node", {
    calm_h: calm.baseline.remaining_h,
    storm_h: storm.baseline.remaining_h,
  });
  await page.locator("details.passport-simulation > summary").click();
  await page.locator("#horizon").fill("48");
  await page.locator("#horizon").dispatchEvent("input");
  assert.equal(await page.locator("#horizon-value").textContent(), "+48 часов");
  pass("72-hour horizon control");
  await page.locator("#review-plan").click();
  await page.locator("#accept-confirm").click();
  await until(
    () =>
      page
        .locator("#accept-dialog")
        .isVisible()
        .then((v) => !v),
    "plan accepted",
  );
  const accepted = await get("SHP-1042");
  assert.equal(accepted.plan.version, 2);
  assert.equal(accepted.plan.leg_ids[0], "R1-L1");
  assert.equal(accepted.decisions.length, 1);
  await page.reload({ waitUntil: "networkidle" });
  await page.locator("#review-plan").waitFor();
  assert.match(await page.locator("#workspace-description").innerText(), /План v2/);
  pass("acceptance persists after reload and retains completed leg");
  await page.locator("[data-tab=economics]").click();
  await page.locator("[name=idle_cost_per_day]").fill("880");
  await page.locator("#economics-form button").click();
  await until(
    async () => (await get("SHP-1042")).economics.idle_cost_per_day === 880,
    "economics persisted",
  );
  pass("economic inputs persist");
  await page.locator("[data-tab=documents]").click();
  await page.locator("#upload-form [name=kind]").selectOption("invoice");
  await page.locator("#upload-form [name=file]").setInputFiles({
    name: "mismatched-invoice.txt",
    mimeType: "text/plain",
    buffer: Buffer.from(
      "Shipment: SHP-1042\nWeight: 999 t\nDestination: Bandar Abbas\nCargo: Электроника\n",
    ),
  });
  await page.locator("#upload-form button").click();
  await until(
    async () => (await get("SHP-1042")).documents.status === "mismatch",
    "document mismatch",
  );
  await until(
    () =>
      page
        .locator(".mismatch")
        .count()
        .then((n) => n > 0),
    "mismatch visible",
  );
  pass("document upload extracts fields and reports weight mismatch");
  const documentPage = await context.newPage();
  await documentPage.setContent(
    "<style>body{margin:45px;font:22px Arial;background:white;color:black;line-height:2}</style><pre>Shipment: SHP-1042\nWeight: 48 t\nDestination: Bandar Abbas\nCargo: Электроника</pre>",
  );
  const pdf = await documentPage.pdf({ format: "A4" });
  const scan = await documentPage.screenshot({ type: "png" });
  await documentPage.close();
  for (const [name, mimeType, buffer, expected] of [
    ["invoice.pdf", "application/pdf", pdf, "text"],
    ["invoice.png", "image/png", scan, "ocr"],
  ]) {
    await page
      .locator("#upload-form [name=file]")
      .setInputFiles({ name, mimeType, buffer });
    await page.locator("#upload-form button").click();
    await until(
      async () =>
        (await get("SHP-1042")).documents.files.some(
          (f) => f.filename === name,
        ),
      name + " stored",
    );
    const file = (await get("SHP-1042")).documents.files.find(
      (f) => f.filename === name,
    );
    if (file.extraction_status === "unavailable" && expected === "ocr") {
      checks.push({
        name: "local image OCR",
        status: "NOT_TESTED",
        evidence: file.extraction_source,
      });
    } else {
      assert.equal(file.extraction_status, expected);
      assert.equal(file.fields.weight_t, 48);
      pass(
        expected === "ocr"
          ? "local image OCR reads real PNG"
          : "PDF text extraction reads real PDF",
      );
    }
  }
  await page.goto(base + "/app/shipments", { waitUntil: "networkidle" });
  await page.locator("[data-open=create-dialog]").click();
  await page.locator("#create-form [name=name]").fill("UI проверка перевозки");
  await page.locator("#create-form [name=cargo]").fill("Оборудование");
  await page.locator("#create-form button[type=submit]").click();
  await until(
    () =>
      page
        .locator("#create-dialog")
        .isVisible()
        .then((v) => !v),
    "created",
  );
  await until(
    () =>
      page
        .locator("#workspace-heading")
        .textContent()
        .then((s) => s.includes("UI проверка")),
    "new cargo selected",
  );
  const id = decodeURIComponent(new URL(page.url()).pathname.split("/").pop());
  assert.match(await page.locator("#workspace-description").innerText(), new RegExp(id));
  assert.equal((await get(id)).shipment.current_node, "moscow");
  pass("create shipment through UI", id);
  await page.locator("[data-tab=events]").click();
  await page.locator("#event-form [name=event_type]").selectOption("departure");
  await page.locator("#event-form [name=leg_id]").selectOption("R1-L1");
  await page.locator("#event-form button").click();
  await until(
    async () => (await get(id)).shipment.progress_status === "in_transit",
    "departure",
  );
  await until(() => page.locator("#event-form").isEnabled(), "event form");
  await page.locator("#event-form [name=event_type]").selectOption("arrival");
  await page.locator("#event-form [name=leg_id]").selectOption("R1-L1");
  await page.locator("#event-form button").click();
  await until(
    async () => (await get(id)).shipment.current_node === "makhachkala",
    "arrival",
  );
  pass("actual events advance position and enable rerouting");
  await page.goto(base + "/app/shipments", { waitUntil: "networkidle" });
  await page.locator("#registry-search").fill("несуществующий груз");
  await page.locator("#registry-result .workspace-empty").waitFor();
  assert.equal(await page.locator("#registry-result .workspace-shipments > li").count(), 0);
  await page.locator("#registry-search").fill("");
  await page.goto(base + "/app/shipments/SHP-1042", { waitUntil: "networkidle" });
  pass("search empty state");
  await page.evaluate(() => scrollTo({ top: 0, behavior: "instant" }));
  await page.locator("#start-demo").click();
  await until(() => page.locator("#demo-bar").isVisible(), "demo");
  await page.locator("#demo-pause").click();
  assert.equal(await page.locator("#demo-pause").textContent(), "Продолжить");
  await page.locator("#demo-next").click();
  await page.locator("#demo-close").click();
  await until(
    () =>
      page
        .locator("#demo-bar")
        .isVisible()
        .then((v) => !v),
    "demo closes",
  );
  pass("demo start/pause/next/exit");
  for (const width of [375, 390, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: 844 });
    await page.evaluate(() => scrollTo({ top: 0, behavior: "instant" }));
    await page.waitForTimeout(350);
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
      false,
      `overflow at ${width}`,
    );
    if (width === 390)
      await page.screenshot({ path: path.join(output, "mobile-hero.png") });
  }
  pass("responsive widths 375,390,768,1024,1440: no horizontal overflow");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.reload({ waitUntil: "networkidle" });
  await page.getByRole("button", { name: "2D карта", exact: true }).click();
  await page.locator("#ops-map [data-renderer=leaflet]").waitFor();
  assert.equal(
    await page.evaluate(
      () => getComputedStyle(document.documentElement).scrollBehavior,
    ),
    "auto",
  );
  pass("reduced motion preserves the functional dispatcher map");
  await page.locator("[data-open=sources-dialog]").first().click();
  assert.equal(await page.locator("#sources-dialog").isVisible(), true);
  await page.keyboard.press("Escape");
  assert.equal(await page.locator("#sources-dialog").isVisible(), false);
  pass("native dialog keyboard dismissal");
  const fallback = await context.newPage();
  await fallback.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (type, ...args) {
      return /webgl/.test(type) ? null : original.call(this, type, ...args);
    };
  });
  fallback.on("pageerror", (e) => errors.push(e.message));
  await fallback.goto(base + "/app/shipments/SHP-1042", { waitUntil: "networkidle" });
  await fallback.getByRole("button", { name: "3D карта", exact: true }).click();
  await fallback.locator("[data-renderer=svg]").waitFor();
  assert.equal(await fallback.locator("[data-renderer=svg]").count(), 1);
  await fallback
    .locator('#ops-map [role=button][aria-label="Махачкала"]')
    .click();
  assert.ok(
    (await fallback.locator("#port-info").innerText()).includes("Махачкала"),
  );
  pass("WebGL unavailable: interactive SVG fallback");
  await fallback.close();
  await page.evaluate(() => navigator.serviceWorker.ready);
  await page.reload({ waitUntil: "networkidle" });
  await context.setOffline(true);
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.locator("#operations").waitFor();
  await until(() => page.locator("#api-error").isVisible(), "offline status");
  pass("offline shell opens and does not pretend cargo data is fresh");
  await context.setOffline(false);
  assert.deepEqual(errors, []);
  pass("no uncaught JavaScript errors across flows");
  fs.writeFileSync(
    path.join(output, "ui-results.json"),
    JSON.stringify({ checks, errors }, null, 2),
  );
}
main()
  .catch((e) => {
    console.error(e);
    checks.push({ name: "suite", status: "FAIL", evidence: e.stack });
    process.exitCode = 1;
  })
  .finally(async () => {
    fs.mkdirSync(output, { recursive: true });
    fs.writeFileSync(
      path.join(output, "ui-results.json"),
      JSON.stringify({ checks, errors }, null, 2),
    );
    if (browser) await browser.close();
    if (server) server.kill("SIGTERM");
    fs.rmSync(data, { recursive: true, force: true });
  });
