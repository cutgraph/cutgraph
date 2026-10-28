import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { browserSpec, fixture, layout, state, story } from '../browser.mjs';

const preview = `data:video/mp4;base64,${(await readFile(path.join(state, fixture.stockPreview))).toString('base64')}`;
const item = {
  id: 'pexels:video:wide', provider: 'pexels', kind: 'video', thumbUrl: null, previewUrl: preview,
  downloadUrl: preview, width: 2560, height: 320, durationS: 1, author: 'Synthetic Author',
  authorUrl: 'https://www.pexels.com/@synthetic', sourceUrl: 'https://www.pexels.com/video/wide/',
  license: 'Pexels License',
};

function inside(inner, outer, label) {
  assert.ok(inner.x >= outer.x - 0.5 && inner.y >= outer.y - 0.5
    && inner.x + inner.width <= outer.x + outer.width + 0.5
    && inner.y + inner.height <= outer.y + outer.height + 0.5,
  `${label}: ${JSON.stringify({ inner, outer })}`);
}

await browserSpec(async (page, width) => {
  await page.route('**/api/stock/providers', (route) => route.fulfill({
    json: { pexels: { configured: true }, pixabay: { configured: false } },
  }));
  await page.route('**/api/stock/search**', (route) => route.fulfill({
    json: { results: [item], page: 1, hasMore: false, errors: {} },
  }));

  await story(page);
  await page.getByRole('button', { name: 'Search stock', exact: true }).click();
  await page.getByRole('textbox', { name: 'Search stock' }).fill('wide');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await page.getByRole('button', { name: 'Preview by Synthetic Author' }).click();

  const dialog = page.getByRole('dialog').filter({ has: page.locator('.stock-lightbox-body') });
  const video = dialog.locator('video');
  await page.waitForFunction(() => document.querySelector('.stock-lightbox video')?.videoWidth > 0);
  assert.ok(await video.evaluate((element) => element.videoWidth) > width, 'The preview is wider than the viewport');
  await layout(page);

  const viewport = { x: 0, y: 0, ...page.viewportSize() };
  const body = await dialog.locator('.stock-lightbox-body').boundingBox();
  inside(body, viewport, `The lightbox fits the viewport at ${width}px`);
  inside(await video.boundingBox(), body, `The preview fits the lightbox at ${width}px`);
  const close = dialog.getByRole('button', { name: 'Close', exact: true });
  const box = await close.boundingBox();
  inside(box, viewport, `Close is on screen at ${width}px`);
  const hit = await page.evaluate(({ x, y }) => document.elementFromPoint(x, y)?.textContent, {
    x: box.x + box.width / 2, y: box.y + box.height / 2,
  });
  assert.equal(hit, 'Close', `Nothing covers Close at ${width}px`);

  await close.click();
  await dialog.waitFor({ state: 'detached' });
  await layout(page);
});
