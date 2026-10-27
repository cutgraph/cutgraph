import assert from 'node:assert/strict';
import { browserSpec, layout, story } from '../browser.mjs';

await browserSpec(async (page) => {
  await story(page);
  await page.getByRole('button', { name: 'Export', exact: true }).click();
  const sheet = page.getByRole('dialog', { name: 'Export', exact: true });
  const formats = sheet.getByRole('group', { name: 'Format', exact: true });
  assert.equal(await formats.getByRole('button', { pressed: true }).count(), 1);
  assert.equal(await formats.getByRole('button', { name: /Landscape.*16:9/ }).getAttribute('aria-pressed'), 'true');
  await sheet.getByRole('button', { name: 'Burned in', exact: true }).click();
  assert.equal(await sheet.getByRole('button', { name: 'Burned in', exact: true }).getAttribute('aria-pressed'), 'true');
  await layout(page);
  await sheet.getByRole('button', { name: /^Export \d/ }).click();
  await page.waitForFunction(() => document.querySelector('.ex-row.done, .ex-row.error'), null, { timeout: 180000 });
  const row = sheet.locator('.ex-row');
  assert.equal(await row.count(), 1, 'Only the selected format was exported');
  assert.match(await row.locator('.ex-row-head b').innerText(), /16:9/);
  assert.match(await row.getAttribute('class'), /\bdone\b/, await row.innerText());
  await row.getByRole('button', { name: 'Play', exact: true }).click();
  const preview = row.locator('video');
  await preview.waitFor();
  const media = await preview.evaluate(async (video) => {
    await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => finish(new Error('Export metadata timed out')), 15000);
      const finish = (error) => {
        clearTimeout(timeout);
        video.removeEventListener('loadedmetadata', loaded);
        video.removeEventListener('error', failed);
        if (error) reject(error);
        else resolve();
      };
      const loaded = () => finish();
      const failed = () => finish(new Error(`Export media error: ${video.error?.code}`));
      video.addEventListener('loadedmetadata', loaded);
      video.addEventListener('error', failed);
      if (video.error) failed();
      else if (video.readyState >= 1) loaded();
    });
    return { url: video.currentSrc, duration: video.duration, error: video.error?.code ?? null };
  });
  assert.ok(Number.isFinite(media.duration) && media.duration > 0, 'Export has a positive duration');
  assert.equal(media.error, null, 'Export metadata loaded without a media error');
  for (const headers of [{}, { Range: 'bytes=0-1023' }]) {
    const response = await page.request.get(media.url, {
      headers, timeout: 15000,
    });
    try {
      assert.ok([200, 206].includes(response.status()), `GET export: ${response.status()}`);
      assert.match(response.headers()['content-type'] ?? '', /^video\//i);
      assert.ok((await response.body()).length > 0, 'Export serves media bytes');
    } finally {
      await response.dispose();
    }
  }
  await preview.evaluate(async (video) => {
    video.muted = true;
    await video.play();
  });
  await page.waitForFunction(() => {
    const video = document.querySelector('.ex-preview');
    return video.error || video.currentTime > 0;
  });
  assert.equal(await preview.evaluate((video) => video.error?.code ?? null), null, 'Export plays without a media error');
  await preview.evaluate((video) => video.pause());
  await layout(page);

  await formats.getByRole('button', { name: /Landscape.*16:9/ }).click();
  await formats.getByRole('button', { name: /Vertical.*9:16/ }).click();
  assert.equal(await formats.getByRole('button', { pressed: true }).count(), 1);
  const [created] = await Promise.all([
    page.waitForResponse((response) => response.url().endsWith('/api/jobs')
      && response.request().method() === 'POST'),
    sheet.getByRole('button', { name: 'Export again', exact: true }).click(),
  ]);
  assert.equal(created.status(), 200, 'Vertical export job created');
  assert.equal(created.request().postDataJSON().kind, 'render_final');
  assert.match(created.request().postDataJSON().project, /__9x16$/);
  const job = await created.json();
  const jobId = job.jobId ?? job.id;
  assert.ok(jobId, 'Vertical export has a job ID');
  await row.getByRole('button', { name: 'Cancel', exact: true }).waitFor();
  assert.match(await row.locator('.ex-row-head b').innerText(), /9:16/);
  const [cancelled] = await Promise.all([
    page.waitForResponse((response) => response.url().endsWith(`/api/jobs/${jobId}/cancel`)
      && response.request().method() === 'POST'),
    row.getByRole('button', { name: 'Cancel', exact: true }).click(),
  ]);
  assert.equal(cancelled.status(), 200, 'Cancel action succeeded');
  assert.equal((await cancelled.json()).status, 'cancelled', 'Server confirmed cancellation');
  await sheet.locator('.ex-row.cancelled').waitFor();
  assert.equal(await row.getByRole('button', { name: 'Play', exact: true }).count(), 0);
  await layout(page);
});
