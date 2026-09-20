const { chromium } = require(process.env.BOOKANALYST_PLAYWRIGHT || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');

(async () => {
  const root = path.resolve(__dirname, '..');
  fs.mkdirSync(path.join(root, '.scratch'), { recursive: true });
  const state = fs.mkdtempSync(path.join(root, '.scratch', 'providers-browser-'));
  const python = path.join(root, '.venv', 'Scripts', 'python.exe');
  const server = spawn(python, ['-u', '-c',
    'import sys, uvicorn; from bookanalyst.app import create_app; uvicorn.run(create_app(sys.argv[1], sys.argv[2]), host="127.0.0.1", port=0)', root, state],
    { cwd: root, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  let browser;
  try {
    const base = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Error('Server did not start')), 15000);
      let logs = '';
      server.stderr.on('data', chunk => {
        logs += chunk;
        const match = logs.match(/http:\/\/127\.0\.0\.1:\d+/);
        if (match) { clearTimeout(timer); resolve(match[0]); }
      });
      server.on('error', reject);
      server.on('exit', code => { clearTimeout(timer); reject(Error(`Server exited ${code}: ${logs}`)); });
    });
    browser = await chromium.launch({ channel: 'msedge', headless: true });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/connections/openai_subscription/status', route => route.fulfill({ json: { status: 'READY', models: [] } }));
    await page.route('**/api/connections/grok_subscription/status', route => route.fulfill({ json: { status: 'AUTH_REQUIRED', models: [] } }));
    await page.goto(base);
    await page.locator('nav [data-nav="settings"]').click();
    await page.locator('#settings-form').waitFor();
    assert.equal(await page.locator('[data-connection-field="max_in_flight"]').count(), 0);
    await page.locator('[name="workflow_concurrency"]').fill('4');
    // Adding cards must preserve edits and secrets already entered in other cards.
    await page.locator('[data-provider-select="custom_api"]').click();
    await page.locator('[data-connection-id="custom_api"] [data-connection-field="api_key"]').fill('test-original-key');
    const ids = [];
    for (let i = 0; i < 3; i++) {
      await page.getByRole('button', { name: '＋ 添加自定义供应商', exact: true }).click();
      const card = page.locator('[data-connection-id]').last();
      ids.push(await card.getAttribute('data-connection-id'));
      await card.getByLabel('供应商名称', { exact: true }).fill(`供应商 ${i}`);
      await card.getByLabel('API 地址', { exact: true }).fill(`http://provider-${i}.test/v1`);
      await card.getByLabel('默认模型', { exact: true }).fill(`model-${i}`);
      await card.locator('[data-connection-field="api_key"]').fill(`test-provider-key-${i}`);
      assert.equal(await card.getByRole('button', { name: '检查连接' }).isDisabled(), false);
    }
    assert.equal(await page.locator('[data-connection-id="custom_api"] [data-connection-field="api_key"]').inputValue(), 'test-original-key');
    const stages = page.locator('[data-stage-connection]');
    await stages.first().selectOption(ids[0]);
    await stages.nth(1).selectOption(ids[1]);
    assert.equal(await page.locator('[data-stage-model]').first().inputValue(), 'model-0');
    const save = async () => {
      const pending = page.waitForResponse(r => r.url() === base + '/api/settings' && r.request().method() === 'PUT');
      await page.getByRole('button', { name: '保存设置', exact: true }).click();
      const response = await pending;
      assert.equal(response.status(), 200, await response.text());
      await page.waitForFunction(() => !document.querySelector('#settings-form [type="submit"]').disabled);
      return response.json();
    };
    let saved = await save();
    assert.equal(Object.keys(saved.connections).length, 6);
    assert.equal(saved.llm_concurrency, 4);
    assert.equal(Object.values(saved.connections).some(conn => 'max_in_flight' in conn), false);
    assert.equal(saved.stage_models.setup.connection_id, ids[0]);
    for (const id of ids) assert.equal(saved.connections[id].api_key_configured, true);
    const first = page.locator(`[data-connection-id="${ids[0]}"]`);
    await page.locator(`[data-provider-select="${ids[0]}"]`).click();
    await first.getByLabel('供应商名称', { exact: true }).fill('主力 <API> & 中文');
    assert.equal(await stages.first().locator('option:checked').textContent(), '主力 <API> & 中文');
    saved = await save();
    assert.equal(saved.connections[ids[0]].name, '主力 <API> & 中文');
    assert.equal(saved.connections[ids[0]].api_key_configured, true);
    assert.equal(saved.stage_models.setup.connection_id, ids[0]);
    await page.reload();
    await page.locator('nav [data-nav="settings"]').click();
    await page.locator('#settings-form').waitFor();
    assert.equal(await first.getByLabel('供应商名称', { exact: true }).inputValue(), '主力 <API> & 中文');
    assert.equal(await stages.first().inputValue(), ids[0]);
    assert.equal(await first.locator('[data-connection-field="api_key"]').inputValue(), 'test-provider-key-0');
    await page.route('**/api/providers/probe', route => route.fulfill({ json: { status: 'READY', message: 'probe-model', models: [{ id: 'probe-model' }] } }));
    const second = page.locator(`[data-connection-id="${ids[1]}"]`);
    await page.locator(`[data-provider-select="${ids[1]}"]`).click();
    await second.getByRole('button', { name: '检查连接' }).click();
    await second.locator('[data-connection-status]').filter({ hasText: 'probe-model' }).waitFor();
    assert.equal((await first.locator('[data-connection-status]').textContent()).includes('probe-model'), false);
    await second.locator('[data-connection-field="api_key"]').fill('');
    saved = await save();
    assert.equal(saved.connections[ids[1]].api_key_configured, false);
    assert.equal(saved.connections[ids[0]].api_key_configured, true);
    await page.screenshot({ path: path.join(state, 'settings-desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: path.join(state, 'settings-mobile.png'), fullPage: true });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    // Run-page edits update only this task, survive polling and use the existing button.
    const bootstrap = await (await page.request.get(base + '/api/bootstrap')).json();
    const created = await page.request.post(base + '/api/runs', {
      headers: { 'x-bookanalyst-token': bootstrap.token },
      data: { book_id: bootstrap.books[0].id, end_page: 3, llm_concurrency: 2 },
    });
    assert.equal(created.status(), 200);
    const createdRun = await created.json();
    let polls = 0;
    await page.route(`**/api/runs/${createdRun.id}/status`, async route => {
      const actual = await (await route.fetch()).json();
      polls++;
      await route.fulfill({ json: { ...actual, state: 'RUNNING', updated_at: polls, tasks: {} } });
    });
    await page.goto(base + '/?run=' + createdRun.id);
    const concurrency = page.locator('#run-concurrency');
    await concurrency.waitFor();
    assert.equal(await concurrency.inputValue(), '2');
    await concurrency.fill('6');
    const polled = page.waitForResponse(r => r.url() === `${base}/api/runs/${createdRun.id}/status`);
    await page.locator('#run-model-live').click();
    await polled;
    assert.equal(await concurrency.inputValue(), '6');
    let releaseSave;
    const release = new Promise(resolve => releaseSave = resolve);
    let submitted;
    await page.route(`**/api/runs/${createdRun.id}/model`, async route => {
      submitted = route.request().postDataJSON();
      await release;
      await route.continue();
    });
    const requested = page.waitForRequest(r => r.url().endsWith(`/runs/${createdRun.id}/model`));
    const savedRun = page.waitForResponse(r => r.url().endsWith(`/runs/${createdRun.id}/model`));
    await page.getByRole('button', { name: '更新配置', exact: true }).click();
    await requested;
    await concurrency.fill('8');
    releaseSave();
    const updated = await savedRun;
    assert.equal(updated.status(), 200);
    assert.equal(submitted.llm_concurrency, 6);
    assert.equal((await updated.json()).pending_llm_concurrency, 6);
    await page.waitForFunction(() => !document.querySelector('#update-model-config').disabled);
    assert.equal(await concurrency.inputValue(), '8');
    await page.unroute(`**/api/runs/${createdRun.id}/model`);
    const secondSave = page.waitForResponse(r => r.url().endsWith(`/runs/${createdRun.id}/model`));
    await page.getByRole('button', { name: '更新配置', exact: true }).click();
    assert.equal((await (await secondSave).json()).pending_llm_concurrency, 8);
    await page.unroute(`**/api/runs/${createdRun.id}/status`);
    await page.reload();
    await concurrency.waitFor();
    assert.equal(await concurrency.inputValue(), '8');
    assert.equal((await (await page.request.get(base + '/api/settings')).json()).llm_concurrency, 4);
    let invalidSent = false;
    const trackInvalid = request => { if (request.url().endsWith(`/runs/${createdRun.id}/model`)) invalidSent = true; };
    page.on('request', trackInvalid);
    await concurrency.fill('0');
    await page.getByRole('button', { name: '更新配置', exact: true }).click();
    assert.equal(await concurrency.evaluate(input => input.validity.valid), false);
    assert.equal(invalidSent, false);
    page.off('request', trackInvalid);
    await concurrency.fill('8');
    await page.screenshot({ path: path.join(state, 'run-concurrency-mobile.png'), fullPage: true });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.screenshot({ path: path.join(state, 'run-concurrency-desktop.png'), fullPage: true });
    console.log('Run concurrency UI passed: draft survives polling and save, same button submits override, reload preserves pending value, global default unchanged, invalid value rejected.');
    assert.deepEqual(errors, []);
    console.log('Provider UI passed: add multiple, preserve drafts, bind stages, save, rename, reload, test selected provider, clear independent key, mobile layout.');
    console.log(`Screenshots: ${state}`);
  } finally {
    if (browser) await browser.close();
    server.kill();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
