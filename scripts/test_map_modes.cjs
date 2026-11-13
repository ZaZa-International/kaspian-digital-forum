const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { chromium } = require("playwright");

const root = path.resolve(__dirname, "..");
const port = Number(process.env.MAP_UI_TEST_PORT || 8786);
const base = `http://127.0.0.1:${port}`;
const folder = fs.mkdtempSync(path.join(os.tmpdir(), "sct-map-ui-"));
const output = path.join(root, "artifacts", "qa");
let server, browser;

async function setup() {
  let stderr = "";
  server = spawn(process.env.PYTHON || path.join(root, ".venv/bin/python"), ["-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--host", "127.0.0.1", "--port", String(port)], {
    cwd: root, env: { ...process.env, SCT_DATA_DIR: folder }, stdio: ["ignore", "ignore", "pipe"],
  });
  server.stderr.on("data", (chunk) => { stderr = (stderr + chunk).slice(-3000); });
  for (let i = 0; i < 150; i++) {
    if (server.exitCode !== null) break;
    try { if ((await fetch(base + "/api/health")).ok) return; } catch {}
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("Map test server unavailable: " + stderr);
}

async function mount(page, preference = "2d") {
  // A non-app document avoids executing the dispatcher's own handlers on the fixture DOM.
  await page.goto(base + "/static/manifest.json");
  await page.setContent('<!doctype html><html lang="ru"><head><link rel="stylesheet" href="/static/vendor/fonts.css"><style>body{margin:0;padding:24px;background:#f1f3ea;font-family:"IBM Plex Sans",sans-serif}#map{height:660px;width:100%;max-width:1100px;margin:auto}h1{max-width:1100px;margin:0 auto 20px;font-size:26px;color:#192b2c}@media(max-width:600px){body{padding:12px}#map{height:620px}h1{font-size:22px}}</style></head><body><h1>Карта транспортного коридора</h1><div id="map"></div></body></html>');
  await page.evaluate(async (selected) => {
    localStorage.setItem("sct-map-mode", selected);
    window.selectedPorts = [];
    const { createMapController } = await import("/static/map-controller.js");
    window.mapFixture = await createMapController(document.querySelector("#map"), { mode: "ops", onPortSelect: (id) => window.selectedPorts.push(id) });
    window.mapFixture.setRoute({ id: "test-cargo", legs: [
      { from: "makhachkala", to: "amirabad", mode: "sea" },
      { from: "amirabad", to: "tehran", mode: "rail" },
    ] });
    window.mapFixture.setProgress(0.35);
  }, preference);
}

async function main() {
  await setup();
  browser = await chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  fs.mkdirSync(output, { recursive: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 920 }, reducedMotion: "reduce", serviceWorkers: "block" });
  const errors = [], requests = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => requests.push(request.url()));
  await mount(page);
  await page.locator('[data-renderer="leaflet"] .leaflet-interactive').first().waitFor();
  assert.equal(await page.evaluate(() => window.mapFixture.getMode()), "2d");
  assert.equal(requests.filter((url) => url.includes("three.module.js")).length, 0);
  assert.equal(requests.filter((url) => !url.startsWith(base)).length, 0);
  await page.locator('[data-port="makhachkala"]').click();
  assert.deepEqual(await page.evaluate(() => window.selectedPorts), ["makhachkala"]);
  await page.getByRole("button", { name: "Весь маршрут" }).click();
  await page.screenshot({ path: path.join(output, "map-2d-desktop.png") });
  console.log("PASS local 2D geography, selected route, port callback; no external tiles or Three.js load");

  const before = await page.locator(".flat-port-marker").first().getAttribute("style");
  await page.getByRole("button", { name: "Увеличить карту", exact: true }).click();
  assert.notEqual(await page.locator(".flat-port-marker").first().getAttribute("style"), before);
  await page.locator(".flat-map-canvas").focus();
  await page.keyboard.press("ArrowRight");
  await page.getByRole("button", { name: "Весь маршрут" }).click();
  await page.evaluate(() => window.mapFixture.setScenario({ storm: true, horizon: 24 }));
  await page.getByText("Сценарий шторма · морские плечи под риском", { exact: true }).waitFor();
  console.log("PASS zoom, keyboard pan, route fit and storm state");

  await page.getByRole("button", { name: "3D карта", exact: true }).click();
  await page.locator('[data-renderer="webgl"] canvas').waitFor();
  await page.waitForFunction(() => window.mapFixture.getMode() === "3d");
  assert.equal(await page.locator(".flat-map-wrapper").count(), 0);
  await page.screenshot({ path: path.join(output, "map-3d-desktop.png") });
  await page.getByRole("button", { name: "2D карта", exact: true }).click();
  await page.locator('.flat-map-wrapper[data-progress="0.35"].is-storm').waitFor();
  assert.equal(await page.locator(".caspian-canvas").count(), 0);
  assert.equal(await page.locator(".map-renderer-host").count(), 1);
  assert.equal(await page.evaluate(() => localStorage.getItem("sct-map-mode")), "2d");
  console.log("PASS mode switching keeps route/progress/scenario and disposes inactive renderer");

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Весь маршрут" }).click();
  await page.screenshot({ path: path.join(output, "map-2d-mobile.png") });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.getByRole("button", { name: "3D карта", exact: true }).focus();
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => window.mapFixture.getMode() === "3d");
  await page.screenshot({ path: path.join(output, "map-3d-mobile.png") });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.evaluate(() => window.mapFixture.dispose());
  assert.equal(await page.locator(".map-controller").count(), 0);
  console.log("PASS mobile layout, keyboard mode control and complete disposal");

  const fallback = await browser.newPage({ viewport: { width: 1100, height: 900 }, serviceWorkers: "block" });
  fallback.on("pageerror", (error) => errors.push(error.message));
  await fallback.addInitScript(() => {
    const native = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (kind, ...args) {
      if (String(kind).includes("webgl")) return null;
      return native.call(this, kind, ...args);
    };
  });
  await mount(fallback, "3d");
  await fallback.locator('[data-renderer="svg"]').waitFor();
  await fallback.getByText("3D недоступен · резервная схема", { exact: true }).waitFor();
  await fallback.getByRole("button", { name: "2D карта", exact: true }).click();
  await fallback.locator('[data-renderer="leaflet"]').waitFor();
  assert.deepEqual(errors, []);
  console.log("PASS missing WebGL preserves SVG fallback and working 2D; no uncaught errors");
}

main().catch((error) => { console.error(error); process.exitCode = 1; }).finally(async () => {
  await browser?.close();
  server?.kill("SIGTERM");
  fs.rmSync(folder, { recursive: true, force: true });
});
