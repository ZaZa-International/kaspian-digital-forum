/* Full user journey on an isolated database; no writes to the running demo. */
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'caspian-product-'));
const base = `http://127.0.0.1:${process.env.PRODUCT_UI_TEST_PORT || 8778}`;
const output = path.join(root, 'artifacts/qa/platform-v2');
const token = 'isolated-product-test';
const checks = [], errors = [];
let server, browser;

async function until(fn, label) {
  const end = Date.now() + 20000;
  while (Date.now() < end) {
    if (await fn()) return;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error(`Timeout: ${label}`);
}
function pass(name) { checks.push({ name, status: 'PASS' }); console.log('PASS ' + name); }
async function start() {
  let log = '';
  server = spawn(process.env.PYTHON || path.join(root, '.venv/bin/python'), [
    '-m', 'uvicorn', 'app.main:app', '--app-dir', 'backend', '--host', '127.0.0.1', '--port', new URL(base).port,
  ], { cwd: root, env: { ...process.env, SCT_DATA_DIR: temporary, SCT_WRITE_TOKEN: token, SCT_WRITE_RATE_LIMIT: '1000' }, stdio: ['ignore','ignore','pipe'] });
  server.stderr.on('data', chunk => { log = (log + chunk).slice(-2500); });
  await until(async () => {
    if (server.exitCode !== null) throw new Error(log);
    try { return (await fetch(base + '/api/health')).ok; } catch { return false; }
  }, 'server');
}
async function main() {
  await start();
  fs.mkdirSync(output, { recursive: true });
  browser = await chromium.launch({ ...(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {}) });
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 }, timezoneId: 'Europe/Moscow', reducedMotion: 'reduce', serviceWorkers: 'block' });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  const get = async () => (await page.request.get(base + '/api/workspace')).json();
  const go = async (route) => {
    await page.goto(base + route);
    if (!/^\/app\/shipments\//.test(route)) await page.locator('#workspace-content[aria-busy="false"]').waitFor();
  };

  await go('/app');
  await page.getByRole('heading', { name: 'Карта перевозок', exact: true }).waitFor();
  await page.locator('#workspace-map .map-mode-switch').waitFor();
  await page.locator('.route-steps button').first().waitFor();
  const fleet = await get();
  assert.equal(await page.locator('.fleet-card').count(), fleet.shipments.length);
  assert.equal(await page.locator('.workbench-summary strong').first().textContent(), String(fleet.shipments.length));
  assert.equal(await page.locator('#freight-video').count(), 0);
  await page.locator('#fleet-search').fill('несуществующая перевозка');
  await page.getByText('Грузы не найдены.', { exact: true }).waitFor();
  await page.locator('#fleet-search').fill('');
  assert.equal(await page.locator('.fleet-card').count(), fleet.shipments.length);
  pass('map workbench reads real fleet and search empty state resets');

  // A delayed older response must not replace the latest selected cargo.
  let releaseRoute;
  const routeGate = new Promise(resolve => { releaseRoute = resolve; });
  const holdRoute = async route => {
    if (route.request().url().includes('/SHP-1043')) await routeGate;
    try { await route.continue(); } catch { /* Selection aborts the obsolete request. */ }
  };
  await page.route('**/api/operations/SHP-1043', holdRoute);
  const firstRoute = page.waitForRequest(request => request.url().includes('/api/operations/SHP-1043'));
  await page.locator('[data-select-cargo="SHP-1043"]').click();
  await firstRoute;
  await page.locator('[data-select-cargo="SHP-1044"]').click();
  await until(async () => (await page.locator('#workspace-map-status').textContent()).includes('SHP-1044'), 'latest cargo route');
  releaseRoute();
  await page.unroute('**/api/operations/SHP-1043', holdRoute);
  assert.equal(new URL(page.url()).searchParams.get('shipment'), 'SHP-1044');
  assert.equal(await page.locator('[data-select-cargo="SHP-1044"]').getAttribute('aria-pressed'), 'true');
  assert.ok((await page.locator('#workspace-map-passport').getAttribute('href')).endsWith('SHP-1044'));
  assert.ok((await page.locator('#cargo-inspector').textContent()).includes('SHP-1044'));
  await page.reload();
  await page.locator('[data-select-cargo="SHP-1044"][aria-pressed="true"]').waitFor();
  await until(async () => (await page.locator('#workspace-map-status').textContent()).includes('SHP-1044'), 'selected route after reload');
  pass('latest cargo survives delayed response and reload with matching URL, card, route and passport');

  await page.getByRole('navigation', { name: 'Платформа' }).getByRole('link', { name: 'Перевозки', exact: true }).click();
  await page.locator('#registry-search').waitFor();
  assert.equal(new URL(page.url()).pathname, '/app/shipments');
  await page.locator('#registry-search').fill('несуществующая перевозка');
  await page.getByText('По заданным условиям перевозок нет.', { exact: true }).waitFor();
  await page.locator('#registry-search').fill('SHP-1042');
  assert.equal(await page.locator('.workspace-shipments > li').count(), 1);
  await page.locator('#registry-sort').selectOption('eta');
  await page.screenshot({ path: path.join(output, 'shipments-desktop.png'), fullPage: true });
  await page.getByRole('link', { name: 'Открыть SHP-1042', exact: true }).click();
  await page.locator('#workspace-description').filter({ hasText: 'SHP-1042' }).waitFor();
  const beforePreview = await (await page.request.get(base + '/api/operations/SHP-1042')).json();
  assert.ok(beforePreview.proposals.length > 0, 'fixture must contain alternatives');
  const previewPosts = [];
  const observeWrites = request => {
    if (request.method() === 'POST' && request.url().includes('/api/operations/SHP-1042')) previewPosts.push(request.url());
  };
  page.on('request', observeWrites);
  for (const [priority, field] of [['time', 'remaining_h'], ['cost', 'total_usd'], ['green', 'co2_kg']]) {
    await page.locator('#proposal-priority').selectOption(priority);
    const first = page.locator('[data-proposal]').first();
    const selectedId = await first.getAttribute('data-proposal');
    const selected = beforePreview.proposals.find(item => item.id === selectedId);
    assert.ok(selected, `${priority} selects an API proposal`);
    const minimum = Math.min(...beforePreview.proposals.map(item => item[field] ?? Infinity));
    assert.equal(selected[field] ?? Infinity, minimum, `${priority} should sort by ${field}`);
    assert.equal(await first.getAttribute('aria-pressed'), 'true');
    await first.click();
    assert.equal(await page.locator('[data-proposal]').first().getAttribute('aria-pressed'), 'true');
  }
  assert.equal(await page.locator('.plan-caption > [data-proposal]').count(), Math.min(3, beforePreview.proposals.length));
  if (beforePreview.proposals.length > 3) {
    await page.locator('.proposal-more summary').click();
    assert.equal(await page.locator('.proposal-more [data-proposal]').count(), beforePreview.proposals.length - 3);
  }
  const afterPreview = await (await page.request.get(base + '/api/operations/SHP-1042')).json();
  assert.equal(afterPreview.plan.version, beforePreview.plan.version);
  assert.deepEqual(previewPosts, []);
  page.off('request', observeWrites);
  pass('proposal priorities rank API alternatives and preview without changing accepted plan');
  await page.getByRole('tab', { name: 'Документы', exact: true }).click();
  assert.equal(new URL(page.url()).searchParams.get('tab'), 'documents');
  await page.reload();
  await page.locator('#upload-form').waitFor();
  await page.getByRole('tab', { name: 'Стыковки', exact: true }).click();
  await page.locator('.lab-cliff').waitFor();
  await page.goBack();
  await page.locator('#upload-form').waitFor();
  pass('registry search, sorting, direct passport tabs, refresh and browser Back');

  await go('/app/risks');
  const firstAlert = (await get()).alerts[0];
  await page.locator(`[data-task-alert="${firstAlert.id}"]`).click();
  assert.equal(await page.locator('#task-form [name=title]').inputValue(), firstAlert.title);
  assert.equal(await page.locator('#task-form [name=shipment_id]').inputValue(), firstAlert.shipment_id);
  await page.keyboard.press('Escape');
  pass('risk creates a task draft with the correct shipment and reason');
  await page.getByRole('button', { name: 'Новая задача', exact: true }).click();
  let form = page.locator('#task-form');
  await form.locator('[name=shipment_id]').selectOption('SHP-1042');
  await form.locator('[name=title]').fill('Согласовать окно погрузки');
  await form.getByRole('button', { name: 'Сохранить задачу' }).click();
  await form.getByRole('alert').filter({ hasText: 'Ключ доступа' }).waitFor();
  assert.equal(await form.locator('[name=title]').inputValue(), 'Согласовать окно погрузки');
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'Ключ доступа', exact: true }).click();
  await page.locator('#access-form [name=token]').fill(token);
  await page.locator('#access-form').getByRole('button', { name: 'Применить ключ' }).click();
  await page.getByRole('button', { name: 'Новая задача', exact: true }).click();
  await form.locator('[name=shipment_id]').selectOption('SHP-1042');
  await form.locator('[name=title]').fill('Согласовать окно погрузки');
  await form.locator('[name=priority]').selectOption('high');
  await form.locator('[name=note]').fill('Уточнить готовность перед закрытием погрузки.');
  await form.getByRole('button', { name: 'Сохранить задачу' }).click();
  await page.locator('#task-dialog').waitFor({ state: 'hidden' });
  const task = (await get()).tasks.find(item => item.title === 'Согласовать окно погрузки');
  assert.ok(task);
  assert.equal(task.owner, '');
  await page.reload();
  await page.locator(`[data-task-edit="${task.id}"]`).waitFor();
  pass('guarded task creation preserves failed input and supports unassigned persistent tasks');

  await page.locator(`[data-task-edit="${task.id}"]`).click();
  await form.locator('[name=owner]').fill('Диспетчер смены');
  await form.locator('[name=due_at]').fill('2026-11-12T15:00');
  await form.locator('[name=status]').selectOption('in_progress');
  await form.getByRole('button', { name: 'Сохранить задачу' }).click();
  await page.locator('#task-dialog').waitFor({ state: 'hidden' });
  let current = (await get()).tasks.find(item => item.id === task.id);
  assert.equal(current.version, 2);
  assert.equal(current.owner, 'Диспетчер смены');
  assert.equal(current.status, 'in_progress');
  const alertCount = (await get()).alerts.length;
  await page.locator(`[data-task-edit="${task.id}"]`).click();
  await form.locator('[name=status]').selectOption('done');
  await form.getByRole('button', { name: 'Сохранить задачу' }).click();
  await page.locator('#task-dialog').waitFor({ state: 'hidden' });
  assert.equal((await get()).alerts.length, alertCount);
  await page.locator('#tasks-filter').selectOption('done');
  await page.locator(`[data-task-edit="${task.id}"]`).waitFor();
  await page.screenshot({ path: path.join(output, 'risks-desktop.png'), fullPage: true });
  pass('task assignment, due date, progress and completion do not erase cargo risks');

  await page.locator(`[data-task-edit="${task.id}"]`).click();
  current = (await get()).tasks.find(item => item.id === task.id);
  const updated = await page.request.patch(base + '/api/workspace/tasks/' + task.id, { headers: { 'X-Write-Token': token }, data: { expected_version: current.version, owner: 'Другая смена' } });
  assert.ok(updated.ok());
  await form.locator('[name=note]').fill('Моя несохранённая заметка');
  await form.getByRole('button', { name: 'Сохранить задачу' }).click();
  await form.getByRole('alert').filter({ hasText: 'Данные изменились' }).waitFor();
  assert.equal(await form.locator('[name=note]').inputValue(), 'Моя несохранённая заметка');
  assert.equal((await get()).tasks.find(item => item.id === task.id).owner, 'Другая смена');
  await page.keyboard.press('Escape');
  pass('concurrent edit conflict keeps the draft and preserves the other operator change');

  let releaseSave;
  const saveGate = new Promise(resolve => { releaseSave = resolve; });
  const holdSave = async route => {
    if (route.request().method() === 'POST') await saveGate;
    await route.continue();
  };
  await page.route('**/api/workspace/tasks', holdSave);
  await page.getByRole('button', { name: 'Новая задача', exact: true }).click();
  await form.locator('[name=title]').fill('Долгое сохранение');
  const startedSave = page.waitForRequest(request => request.url() === base + '/api/workspace/tasks' && request.method() === 'POST');
  await form.getByRole('button', { name: 'Сохранить задачу' }).click();
  await startedSave;
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'Новая задача', exact: true }).click();
  await form.locator('[name=title]').fill('Новый несохранённый черновик');
  const refreshedAfterSave = page.waitForResponse(response => response.url() === base + '/api/workspace' && response.ok());
  releaseSave();
  await refreshedAfterSave;
  await page.locator('#workspace-content[aria-busy="false"]').waitFor();
  await until(async () => (await get()).tasks.some(item => item.title === 'Долгое сохранение'), 'delayed task saved');
  assert.equal(await page.locator('#task-dialog').isVisible(), true);
  assert.equal(await form.locator('[name=title]').inputValue(), 'Новый несохранённый черновик');
  assert.equal(await form.getByRole('button', { name: 'Сохранить задачу' }).isEnabled(), true);
  await page.keyboard.press('Escape');
  await page.unroute('**/api/workspace/tasks', holdSave);
  pass('late task response cannot close or overwrite a newer draft');

  await go('/app/shipments');
  await page.locator('[data-open=create-dialog]').first().click();
  await page.locator('#create-form [name=name]').fill('Многостраничная перевозка');
  await page.locator('#create-form [name=cargo]').fill('Оборудование');
  await page.locator('#create-form button[type=submit]').click();
  await page.locator('#workspace-heading').filter({ hasText: 'Многостраничная перевозка' }).waitFor();
  const newId = new URL(page.url()).pathname.split('/').at(-1);
  assert.ok(newId.startsWith('SHP-'));
  await page.getByRole('tab', { name: 'События', exact: true }).click();
  await page.locator('#event-form [name=note]').fill('Контакт с перевозчиком подтверждён');
  await page.locator('#event-form').getByRole('button', { name: 'Записать событие' }).click();
  await until(async () => (await (await page.request.get(base + '/api/operations/' + newId)).json()).events.some(item => item.note === 'Контакт с перевозчиком подтверждён'), 'event saved with cross-page token');
  pass('creation opens the new passport and write access survives page navigation');

  await page.goBack(); // events -> initial plan tab
  await page.goBack(); // created passport -> registry (BFCache when browser permits)
  await page.locator('#registry-search').waitFor();
  assert.equal(await page.locator('#create-dialog').isVisible(), false);
  assert.equal(await page.locator('#create-form').getAttribute('data-key'), null);
  await page.locator('[data-open=create-dialog]').first().click();
  assert.equal(await page.locator('#create-form [name=name]').inputValue(), '');
  await page.locator('#create-form [name=name]').fill('Вторая перевозка после возврата');
  await page.locator('#create-form [name=cargo]').fill('Материалы');
  await page.locator('#create-form button[type=submit]').click();
  await page.locator('#workspace-heading').filter({ hasText: 'Вторая перевозка после возврата' }).waitFor();
  assert.notEqual(new URL(page.url()).pathname.split('/').at(-1), newId);
  pass('Back after a successful creation starts a fresh shipment with a new request key');

  await go('/app/documents');
  await page.getByRole('link', { name: 'Многостраничная перевозка', exact: true }).click();
  await page.locator('#upload-form').waitFor();
  assert.equal(new URL(page.url()).searchParams.get('tab'), 'documents');
  assert.ok(new URL(page.url()).pathname.endsWith(newId));
  pass('fleet document queue opens the exact shipment document tab');

  await go('/app/map?shipment=SHP-1042');
  await page.getByRole('button', { name: '2D карта', exact: true }).click();
  await page.locator('#workspace-map .leaflet-container').waitFor();
  await until(async () => (await page.getByRole('button', { name: '2D карта', exact: true }).getAttribute('aria-pressed')) === 'true', '2D selected');
  await page.locator('#workspace-map [data-port="makhachkala"]').click();
  await page.locator('#workspace-map-status').filter({ hasText: 'Махачкала' }).waitFor();
  await page.getByRole('region', { name: 'Узел Махачкала' }).waitFor();
  assert.match(await page.locator('#map-port-card').textContent(), /Точка|На узле/);
  await page.getByRole('button', { name: 'Закрыть сведения об узле' }).click();
  assert.ok((await page.locator('#workspace-map-status').textContent()).includes('SHP-1042'));
  assert.ok((await page.locator('#workspace-map-passport').getAttribute('href')).endsWith('SHP-1042'));
  await page.locator('[data-select-cargo="SHP-1044"]').click();
  await until(async () => (await page.locator('#workspace-map-status').textContent()).includes('SHP-1044'), 'selected route');
  await page.reload();
  await page.locator('[data-select-cargo="SHP-1044"][aria-pressed="true"]').waitFor();
  await page.getByRole('button', { name: '3D карта', exact: true }).click();
  await page.locator('#workspace-map canvas').waitFor();
  await until(async () => (await page.getByRole('button', { name: '3D карта', exact: true }).getAttribute('aria-pressed')) === 'true', '3D selected');
  assert.ok((await page.locator('#workspace-map-passport').getAttribute('href')).endsWith('SHP-1044'));
  await page.locator('#cargo-inspector a[href$="?tab=documents"]').click();
  await page.locator('#upload-form').waitFor();
  assert.equal(new URL(page.url()).searchParams.get('tab'), 'documents');
  assert.ok(new URL(page.url()).pathname.endsWith('SHP-1044'));
  pass('2D and 3D retain cargo, port context is visible, document action opens exact passport');

  await go('/app/analytics');
  await page.getByRole('heading', { name: 'Сравнение по маршрутам', exact: true }).waitFor();
  const totals = (await get()).analytics;
  assert.equal(await page.locator('.workspace-metrics strong').nth(2).textContent(), String(totals.projected_shipments_count));
  await page.screenshot({ path: path.join(output, 'analytics-desktop.png'), fullPage: true });
  pass('analytics uses current portfolio aggregates and explicit unprojected count');

  await page.route('**/api/workspace', route => route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"Проверка сбоя соединения"}' }));
  await page.locator('#workspace-refresh').click();
  await page.getByRole('heading', { name: 'Данные недоступны', exact: true }).waitFor();
  await page.unroute('**/api/workspace');
  await page.getByRole('button', { name: 'Повторить', exact: true }).click();
  await page.getByRole('heading', { name: 'Сравнение по маршрутам', exact: true }).waitFor();
  pass('API failure has a visible retry that restores the page');

  // Capture every product route in both viewport sizes using the same isolated data.
  const routes = [['workbench', '/app'], ['map', '/app/map'], ['shipments', '/app/shipments'],
    ['risks', '/app/risks'], ['documents', '/app/documents'], ['analytics', '/app/analytics']];
  for (const [label, width, height] of [['desktop', 1440, 960], ['mobile', 390, 844]]) {
    await page.setViewportSize({ width, height });
    for (const [name, route] of routes) {
      await go(route);
      if (name === 'workbench' || name === 'map') {
        await page.locator('#workspace-map .map-mode-switch').waitFor();
        await page.getByRole('button', { name: '2D карта', exact: true }).click();
        await page.locator('#workspace-map .leaflet-container').waitFor();
        await page.locator('#workspace-map .flat-coast').waitFor();
        await page.locator('.route-steps summary').waitFor();
        if (label === 'mobile') {
          const box = await page.locator('#workspace-map').boundingBox();
          assert.ok(box.y <= 130 && box.height >= 300, `mobile map placement ${JSON.stringify(box)}`);
          const action = await page.locator('#workspace-next-action').boundingBox();
          assert.ok(action.y + action.height <= height, `action must be on first mobile screen: ${JSON.stringify(action)}`);
          await page.locator('#map-cargo-select').selectOption('SHP-1043');
          await until(async () => (await page.locator('#workspace-map-status').textContent()).includes('SHP-1043'), 'mobile cargo switch');
          assert.match(await page.locator('#workspace-map-passport').getAttribute('href'), /SHP-1043$/);
          await page.locator('#map-cargo-select').selectOption('SHP-1042');
          await until(async () => (await page.locator('#workspace-map-status').textContent()).includes('SHP-1042'), 'restore selected cargo');
        }
      }
      if (label === 'mobile') {
        await page.getByRole('button', { name: 'Разделы', exact: true }).click();
        assert.ok(await page.getByRole('navigation', { name: 'Платформа' }).getByRole('link', { name: 'Документы', exact: true }).isVisible());
        assert.ok(await page.getByRole('navigation', { name: 'Платформа' }).getByRole('link', { name: 'Аналитика', exact: true }).isVisible());
        await page.keyboard.press('Escape');
        assert.equal(await page.getByRole('button', { name: 'Разделы', exact: true }).getAttribute('aria-expanded'), 'false');
      }
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `${route} ${label}`);
      await page.screenshot({ path: path.join(output, `${name}-${label}.png`), fullPage: true });
    }
    for (const tab of ['plan', 'documents', 'intelligence']) {
      await go('/app/shipments/SHP-1042?tab=' + tab);
      await page.locator('#workspace-heading').filter({ hasText: 'Электроника' }).waitFor();
      await page.locator('#view-' + tab).waitFor();
      if (tab === 'plan') await page.locator('#ops-map .flat-coast').waitFor();
      if (tab === 'documents') await page.locator('#upload-form').waitFor();
      if (tab === 'intelligence') await page.locator('#intelligence-result .intelligence-estimate').waitFor();
      await page.screenshot({ path: path.join(output, `passport-${tab}-${label}.png`), fullPage: true });
    }
  }
  pass('all product routes fit desktop and mobile; mobile map starts within 130px and is usable');
  assert.deepEqual(errors, []);
  pass('no uncaught browser errors across the product journey');
}
main().catch(error => {
  checks.push({ name: 'suite', status: 'FAIL', evidence: error.stack });
  console.error(error);
  process.exitCode = 1;
}).finally(async () => {
  fs.mkdirSync(output, { recursive: true });
  fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify({ checks, errors }, null, 2));
  await browser?.close();
  server?.kill('SIGTERM');
  fs.rmSync(temporary, { recursive: true, force: true });
});
