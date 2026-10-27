import assert from 'node:assert/strict';
import { base, browserSpec, layout, story } from '../browser.mjs';

await browserSpec(async (page) => {
  let installed = false;
  let downloaded = false;
  let connection = { connected: false, state: 'not_connected', source: null };
  let finish;
  let started;
  const requestStarted = new Promise((resolve) => { started = resolve; });
  await page.route('**/api/health', async (route) => {
    const response = await route.fetch();
    const health = await response.json();
    Object.assign(health.capabilities.assistant, { sourceInstallation: false, available: installed,
      state: downloaded ? 'standard' : 'not installed', tier: downloaded ? 'standard' : null,
      restartRequired: downloaded && !installed, jobId: null });
    await route.fulfill({ json: health });
  });
  await page.route('**/api/addons', async (route) => {
    const response = await route.fetch();
    const catalog = await response.json();
    const assistant = catalog.addons.find((addon) => addon.name === 'assistant');
    assert.ok(assistant);
    Object.assign(assistant, { sourceInstallation: false, available: installed,
      state: downloaded ? 'standard' : 'not installed', tier: downloaded ? 'standard' : null,
      restartRequired: downloaded && !installed, jobId: null, job: null });
    await route.fulfill({ json: catalog });
  });
  await page.route('**/api/addons/assistant/install', async (route) => {
    assert.equal(route.request().postDataJSON().tier, 'standard');
    assert.ok(route.request().postDataJSON().acceptance);
    downloaded = true;
    await route.fulfill({ status: 202, json: { jobId: 'synthetic-download' } });
  });
  await page.route('**/api/agent/connection**', async (route) => {
    const request = route.request();
    if (!installed && request.method() === 'GET') {
      assert.equal(new URL(request.url()).searchParams.get('verify'), '0');
    }
    if (request.method() === 'POST' && request.url().endsWith('/verify')) {
      assert.equal(connection.state, 'error');
    } else if (request.method() === 'POST') {
      if (connection.state === 'error') {
        assert.deepEqual(request.postDataJSON(), { key: 'replacement-test-secret' });
        connection = { connected: true, state: 'ready', source: 'settings' };
      } else {
        assert.deepEqual(request.postDataJSON(), { key: 'test-api-secret' });
        await new Promise((resolve) => { finish = resolve; started(); });
        connection = { connected: true, state: 'error', source: 'settings', message: 'That API key was rejected.' };
      }
    } else if (request.method() === 'DELETE') {
      connection = { connected: false, state: 'not_connected', source: null };
    }
    await route.fulfill({ json: connection });
  });
  await page.route('**/api/chat/config**', async (route) => {
    const response = await route.fetch();
    const config = await response.json();
    const availability = !installed
      ? { available: false, error: { code: 'assistant_not_installed', message: "Assistant isn't installed. Open Settings, Add-ons." } }
      : connection.state === 'ready' ? { available: true, error: null }
      : { available: false, error: { code: 'authentication_required', message: connection.connected
        ? 'Key not verified. Open Settings to verify your Anthropic API key.' : 'Connect your key in Settings to ask for edits.' } };
    for (const provider of config.providers) {
      provider.availability = availability;
      for (const profile of provider.profiles) profile.availability = availability;
    }
    await route.fulfill({ json: config });
  });

  await story(page);
  await page.getByText("Assistant isn't installed. Open Settings, Add-ons.", { exact: true }).waitFor();
  assert.ok(await page.getByRole('textbox', { name: 'Ask the Assistant' }).isDisabled());
  await page.getByRole('link', { name: 'Open Settings, Add-ons' }).click();
  const card = page.locator('.addon-card').filter({ has: page.getByText('Assistant', { exact: true }) });
  const install = card.getByRole('button', { name: /Install assistant/ });
  await install.waitFor();
  assert.ok(await install.isDisabled());
  assert.equal(await card.getByRole('link', { name: 'Anthropic Commercial Terms' }).getAttribute('href'),
    'https://www.anthropic.com/legal/commercial-terms');
  assert.equal(await card.getByRole('link', { name: 'Anthropic Usage Policy' }).getAttribute('href'),
    'https://www.anthropic.com/legal/aup');
  assert.equal(await page.getByLabel('Anthropic API key').count(), 0);
  await card.getByRole('checkbox').check();
  await install.click();
  await card.getByText('Restart required', { exact: true }).waitFor();
  installed = true;
  await page.reload();
  await page.getByText('Connect your key', { exact: true }).waitFor();
  await story(page);
  await page.getByText('Connect your key in Settings to ask for edits.', { exact: true }).waitFor();
  await page.getByRole('link', { name: 'Open Settings, Add-ons', exact: true }).click();
  await page.getByText('Connect your key', { exact: true }).waitFor();
  await layout(page);
  const key = page.getByLabel('Anthropic API key');
  await key.fill('test-api-secret');
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await page.getByText('Checking…', { exact: true }).waitFor();
  assert.equal(await key.inputValue(), '');
  await requestStarted;
  finish();
  await page.getByText('That API key was rejected.', { exact: true }).waitFor();
  assert.ok(!(await page.locator('body').innerText()).includes('test-api-secret'));
  await story(page);
  await page.getByText('Key not verified. Open Settings to verify your Anthropic API key.', { exact: true }).waitFor();
  await page.getByRole('link', { name: 'Open Settings, Add-ons', exact: true }).click();
  await page.getByRole('button', { name: 'Verify', exact: true }).click();
  await page.getByText('That API key was rejected.', { exact: true }).waitFor();
  await key.fill('replacement-test-secret');
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await page.locator('.conn').filter({ has: key }).getByText('Ready', { exact: true }).waitFor();
  await story(page);
  await page.waitForFunction(() => !document.querySelector('[aria-label="Ask the Assistant"]').disabled);
  await page.goto(`${base}/editor/?view=settings`);
  await page.getByRole('button', { name: 'Disconnect saved key', exact: true }).click();
  await page.getByText('Connect your key', { exact: true }).waitFor();
  await layout(page);
});
