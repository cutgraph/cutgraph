import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';
import { base, browserSpec, layout } from '../browser.mjs';

await browserSpec(async (page, width) => {
  const absent = (name) => ({ name, state: 'not installed', available: false, tier: null,
    installedBytes: 0, sourceInstallation: false, restartRequired: false, jobId: null });
  let downloads = 0;
  await page.route('**/api/addons/**', (route) => {
    downloads += 1;
    return route.abort();
  });
  await page.route('**/api/health', async (route) => {
    const health = await (await route.fetch()).json();
    Object.assign(health.capabilities, { packaged: true, localInference: false,
      transcription: absent('transcription'), assistant: { ...absent('assistant'), keySource: 'none', verification: 'not_connected' } });
    await route.fulfill({ json: health });
  });
  await page.route('**/api/addons', async (route) => {
    const catalog = await (await route.fetch()).json();
    catalog.recommendedTier = 'cpu';
    catalog.gpus = [];
    for (const addon of catalog.addons) Object.assign(addon, absent(addon.name), { job: null });
    await route.fulfill({ json: catalog });
  });
  const screenshot = async (name) => {
    if (!process.env.E2E_SCREENSHOT_DIR) return;
    await mkdir(process.env.E2E_SCREENSHOT_DIR, { recursive: true });
    await page.screenshot({ path: path.join(process.env.E2E_SCREENSHOT_DIR, `${name}-${width}.png`), fullPage: true });
  };
  const installedCopy = async () => {
    assert.doesNotMatch(await page.locator('.space').innerText(), /\bpip\b|\bvenv\b|requirements-[\w-]+\.txt|\bterminal\b|Python environment/i);
  };
  await page.goto(`${base}/editor/`);
  await page.getByRole('heading', { name: 'Welcome to Cutgraph', exact: true }).waitFor();
  const transcription = page.getByRole('article', { name: 'Transcription', exact: true });
  await transcription.getByText(/CPU · detected tier/).waitFor();
  await layout(page);
  assert.ok(await page.locator('.first-workspace .card-text b').evaluate((label) => label.scrollWidth <= label.clientWidth),
    'The first Workspace action is fully readable');
  await installedCopy();
  await screenshot('welcome');
  const dismiss = page.getByRole('button', { name: 'Dismiss Transcription setup' });
  await dismiss.focus();
  await page.keyboard.press('Enter');
  assert.equal(await transcription.count(), 0);
  await page.reload();
  await page.getByRole('article', { name: 'Assistant', exact: true }).waitFor();
  assert.equal(await transcription.count(), 0, 'Dismissal survives a new page load');
  await page.getByRole('button', { name: /Create your first Workspace/ }).click();
  await page.getByLabel('Workspace name', { exact: true }).fill('My first Workspace');
  const created = page.waitForResponse((response) => response.url() === `${base}/api/workspaces` && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Create', exact: true }).click();
  const workspace = await (await created).json();
  await page.getByRole('heading', { name: 'My first Workspace', exact: true }).waitFor();
  await page.getByRole('link', { name: 'Home', exact: true }).first().click();
  await page.getByRole('heading', { name: 'What are we making?', exact: true }).waitFor();
  assert.equal(await page.getByRole('heading', { name: 'Welcome to Cutgraph', exact: true }).count(), 0);
  await page.getByRole('link', { name: 'Settings', exact: true }).click();
  await page.getByRole('heading', { name: 'Add-ons', exact: true }).waitFor();
  for (const name of ['Transcription', 'Assistant']) {
    await page.getByRole('article', { name, exact: true }).getByText('Not installed', { exact: true }).waitFor();
  }
  await page.getByRole('heading', { name: 'Add-ons', exact: true }).scrollIntoViewIfNeeded();
  await page.locator('.toast.show').waitFor({ state: 'hidden' });
  await layout(page);
  await installedCopy();
  await screenshot('addons');
  await page.getByRole('article', { name: 'Assistant', exact: true }).scrollIntoViewIfNeeded();
  await screenshot('assistant');
  assert.equal(downloads, 0, 'No add-on mutation or download was requested');
  const removed = await page.evaluate(async (id) => {
    const response = await fetch(`/api/workspaces/${encodeURIComponent(id)}`, { method: 'DELETE' });
    return response.status;
  }, workspace.id);
  assert.equal(removed, 200, 'Remove the empty fixture Workspace before the next viewport');
});
