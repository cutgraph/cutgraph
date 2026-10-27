import assert from 'node:assert/strict';
import path from 'node:path';
import { mkdir } from 'node:fs/promises';
import { base, browserSpec, layout } from '../browser.mjs';

await browserSpec(async (page, width) => {
  let state = 'available';
  let automatic = true;
  let installs = 0;
  await page.route('**/api/updates**', async (route) => {
    const request = route.request();
    if (request.url().endsWith('/preferences')) {
      automatic = request.postDataJSON().automatic;
      state = automatic ? 'available' : 'disabled';
    }
    if (request.url().endsWith('/install')) { installs += 1; state = 'downloading'; }
    await route.fulfill({ json: { state, automatic, reason: 'Verified release metadata.', currentVersion: '0.2.0',
      version: '0.3.0', releaseNotesUrl: 'https://example.org/releases', canInstall: automatic && state === 'available', lastCheck: 1 } });
  });
  await page.goto(`${base}/editor/`);
  await page.getByText('Update available: 0.3.0', { exact: true }).waitFor();
  await page.getByRole('link', { name: 'Release notes and download' }).waitFor();
  await page.getByRole('link', { name: 'Settings', exact: true }).click();
  const updates = page.getByRole('region', { name: 'Studio updates' });
  await updates.getByRole('checkbox', { name: 'Check for updates automatically' }).uncheck();
  await updates.getByText('Update checks are off', { exact: true }).waitFor();
  assert.equal(await updates.getByRole('button', { name: 'Check for updates', exact: true }).isDisabled(), true);
  await updates.getByRole('checkbox', { name: 'Check for updates automatically' }).check();
  if (process.env.E2E_SCREENSHOT_DIR) {
    await mkdir(process.env.E2E_SCREENSHOT_DIR, { recursive: true });
    await updates.scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(process.env.E2E_SCREENSHOT_DIR, `updates-${width}.png`), fullPage: true });
  }
  await updates.getByRole('button', { name: 'Update to 0.3.0', exact: true }).click();
  await updates.getByText('Downloading update…', { exact: true }).waitFor();
  state = 'verifying';
  await updates.getByText('Verifying update…', { exact: true }).waitFor();
  state = 'failed';
  await updates.getByText('Update failed', { exact: true }).waitFor();
  await updates.getByText(/Verified release metadata/).waitFor();
  await updates.scrollIntoViewIfNeeded();
  await layout(page);
  assert.equal(installs, 1, 'The browser only requested the mocked update once');
});
