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
const output = path.join(root, 'artifacts/qa/product');
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
  await page.getByRole('heading', { name: 'Обзор перевозок', exact: true }).waitFor();
  assert.equal(await page.locator('#freight-video').count(), 0);
  assert.equal(await page.locator('.workspace-metrics strong').first().textContent(), String((await get()).summary.active_count));
  await page.screenshot({ path: path.join(output, 'overview-desktop.png'), fullPage: true });
  pass('overview reads portfolio totals from the API without a landing sequence');

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
  await page.locator('#detail-title').filter({ hasText: 'SHP-1042' }).waitFor();
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
  await page.locator('#detail-title').filter({ hasText: 'Многостраничная перевозка' }).waitFor();
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
  await page.locator('#detail-title').filter({ hasText: 'Вторая перевозка после возврата' }).waitFor();
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
  assert.equal(await page.getByRole('button', { name: '2D карта', exact: true }).getAttribute('aria-pressed'), 'true');
  await page.locator('#workspace-map-shipment').selectOption('SHP-1044');
  await until(async () => (await page.locator('#workspace-map-passport').getAttribute('href')).endsWith('SHP-1044'), 'selected route link');
  await page.screenshot({ path: path.join(output, 'map-2d-desktop.png'), fullPage: true });
  await page.reload();
  await page.locator('#workspace-map .leaflet-container').waitFor();
  assert.equal(await page.locator('#workspace-map-shipment').inputValue(), 'SHP-1044');
  await page.getByRole('button', { name: '3D карта', exact: true }).click();
  await page.locator('#workspace-map canvas').waitFor();
  await page.screenshot({ path: path.join(output, 'map-3d-desktop.png'), fullPage: true });
  pass('2D and 3D keep selected shipment, reload state and working route links');

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

  await page.setViewportSize({ width: 390, height: 844 });
  for (const route of ['/app', '/app/shipments', '/app/risks', '/app/documents', '/app/analytics', '/app/map']) {
    await go(route);
    if (route === '/app/map') await page.locator('#workspace-map .map-mode-switch').waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, route);
    await page.screenshot({ path: path.join(output, route.split('/').at(-1) + '-mobile.png'), fullPage: true });
  }
  pass('all six product pages fit a 390px viewport with real data');
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
