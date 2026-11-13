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
  const visiblePort = async (id) => page.locator(`.flat-port-marker[data-port="${id}"]`).evaluate((marker) => {
    const p = marker.getBoundingClientRect(), c = marker.closest(".flat-map-canvas").getBoundingClientRect();
    return p.left >= c.left && p.right <= c.right && p.top >= c.top && p.bottom <= c.bottom;
  });
  assert.ok(await visiblePort("enzeli"), "Caspian overview must include Enzeli");
  assert.ok(await visiblePort("amirabad"), "Caspian overview must include Amirabad");
  await page.locator('[data-port="makhachkala"]').click();
  assert.deepEqual(await page.evaluate(() => window.selectedPorts), ["makhachkala"]);
  await page.getByRole("button", { name: "Весь маршрут" }).click();
  for (const id of ["makhachkala", "amirabad", "tehran"]) assert.ok(await visiblePort(id), `route fit clipped ${id}`);
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
  await page.waitForTimeout(400);
  const mobileCanvas = page.locator('[data-renderer="webgl"] canvas');
  for (const id of ["enzeli", "amirabad"]) {
    const port = page.locator(`.caspian-port[data-port="${id}"]`);
    assert.equal(await port.evaluate((el) => getComputedStyle(el).visibility), "visible", `3D overview hides ${id}`);
  }
  await page.getByRole("button", { name: "Показать весь маршрут" }).click();
  await page.waitForTimeout(200);
  for (const id of ["makhachkala", "amirabad", "tehran"]) {
    assert.ok(await page.locator(`.caspian-port[data-port="${id}"]`).evaluate((el) => {
      const p = el.getBoundingClientRect(), c = el.closest(".caspian-canvas").getBoundingClientRect();
      return p.left >= c.left && p.right <= c.right && p.top >= c.top && p.bottom <= c.bottom;
    }), `3D route fit clipped ${id}`);
  }
  await page.getByRole("button", { name: "Показать Каспий" }).click();
  await page.waitForTimeout(200);
  const beforeDrag = await mobileCanvas.screenshot();
  await page.screenshot({ path: path.join(output, "map-3d-mobile.png") });
  const box = await mobileCanvas.boundingBox();
  await page.mouse.move(box.x + box.width * .5, box.y + box.height * .45);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * .72, box.y + box.height * .55, { steps: 6 });
  await page.mouse.up();
  await page.waitForTimeout(300);
  const afterDrag = await mobileCanvas.screenshot();
  assert.notDeepEqual(beforeDrag, afterDrag, "3D drag must visibly change the camera");
  fs.writeFileSync(path.join(output, "map-3d-mobile-after-drag.png"), afterDrag);
  console.log("PASS 3D drag changes rendered camera view");
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.evaluate(() => { const map = document.querySelector("#map"); map.style.width = "353px"; map.style.height = "330px"; window.mapFixture.resize(); window.mapFixture.resetView(); });
  await page.waitForTimeout(300);
  await page.screenshot({ path: path.join(output, "map-3d-compact.png") });
  for (const id of ["enzeli", "amirabad"]) {
    assert.equal(await page.locator(`.caspian-port[data-port="${id}"]`).evaluate((el) => getComputedStyle(el).visibility), "visible", `compact 3D overview hides ${id}`);
  }
  await page.evaluate(() => window.mapFixture.dispose());
  assert.equal(await page.locator(".map-controller").count(), 0);
  console.log("PASS mobile layout, keyboard mode control and complete disposal");

  // A stalled Three.js response must never lock the map in loading state.
  const delayed = await browser.newPage({ viewport: { width: 1100, height: 900 }, serviceWorkers: "block" });
  await delayed.route("**/vendor/three/three.module.js", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 2500));
    await route.abort().catch(() => {});
  });
  await mount(delayed, "3d");
  assert.equal(await delayed.getByRole("button", { name: "2D карта", exact: true }).isEnabled(), true);
  await delayed.getByRole("button", { name: "2D карта", exact: true }).click();
  await delayed.waitForFunction(() => window.mapFixture.getMode() === "2d");
  await delayed.waitForTimeout(2800);
  assert.equal(await delayed.evaluate(() => window.mapFixture.getMode()), "2d");
  assert.equal(await delayed.locator(".map-renderer-host").count(), 1);
  await delayed.close();
  console.log("PASS stalled 3D can be interrupted by 2D without a stale renderer");

  // Coastline may arrive after the route: it must never cover the sea leg.
  const lateGeo = await browser.newPage({ viewport: { width: 1100, height: 900 }, serviceWorkers: "block" });
  await lateGeo.route("**/static/assets/geo/**", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1200));
    await route.continue();
  });
  await mount(lateGeo, "2d");
  await lateGeo.locator(".flat-route-sea").waitFor();
  assert.equal(await lateGeo.locator(".flat-coast").count(), 0, "route must be drawn before delayed coast");
  await lateGeo.locator(".flat-coast").waitFor();
  const layers = await lateGeo.evaluate(() => {
    const coast = document.querySelector(".flat-coast");
    const sea = document.querySelector(".flat-route-sea");
    const pane = (element) => element.closest(".leaflet-pane");
    return { coastZ: Number(getComputedStyle(pane(coast)).zIndex),
      routeZ: Number(getComputedStyle(pane(sea)).zIndex),
      seaStroke: getComputedStyle(sea).stroke, seaDash: sea.getAttribute("stroke-dasharray"),
      seaPath: sea.getAttribute("d") };
  });
  assert.ok(layers.routeZ > layers.coastZ, `route pane ${layers.routeZ} is not above coast ${layers.coastZ}`);
  assert.ok(layers.seaDash && layers.seaPath && layers.seaStroke !== "none");
  await lateGeo.screenshot({ path: path.join(output, "map-2d-delayed-geography.png") });
  await lateGeo.close();
  console.log("PASS delayed coastline stays below visible dashed sea route");

  const hidden = await browser.newPage({ viewport: { width: 390, height: 844 }, serviceWorkers: "block" });
  await hidden.goto(base + "/static/manifest.json");
  await hidden.setContent('<div id="map" style="display:none;width:353px;height:483px"></div>');
  await hidden.evaluate(async () => {
    localStorage.setItem("sct-map-mode", "2d");
    const { createMapController } = await import("/static/map-controller.js");
    window.mapFixture = await createMapController(document.querySelector("#map"), { mode: "ops" });
  });
  await hidden.waitForFunction(() => window.mapFixture.getMode() === "2d");
  await hidden.evaluate(() => {
    document.querySelector("#map").style.display = "block";
    window.mapFixture.resize();
    window.mapFixture.resetView();
  });
  for (const id of ["enzeli", "amirabad"]) {
    assert.ok(await hidden.locator(`.flat-port-marker[data-port="${id}"]`).evaluate((marker) => {
      const p = marker.getBoundingClientRect(), c = marker.closest(".flat-map-canvas").getBoundingClientRect();
      return p.left >= c.left && p.right <= c.right && p.top >= c.top && p.bottom <= c.bottom;
    }), `hidden-to-visible map clipped ${id}`);
  }
  await hidden.close();
  console.log("PASS hidden tab opens with correctly framed Caspian overview");

  const timeoutPage = await browser.newPage({ viewport: { width: 1100, height: 900 }, serviceWorkers: "block" });
  await timeoutPage.route("**/vendor/three/three.module.js", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 6500));
    await route.abort().catch(() => {});
  });
  await mount(timeoutPage, "3d");
  await timeoutPage.waitForFunction(() => window.mapFixture.getMode() === "2d", null, { timeout: 6200 });
  await timeoutPage.close();
  console.log("PASS stalled 3D automatically switches to 2D after five seconds");

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
