import assert from 'node:assert/strict';
import { browserSpec, cut, document, fixture, layout, story, undo } from '../browser.mjs';

await browserSpec(async (page) => {
  await story(page);
  const before = (await document(page)).project.tracks;
  await page.locator('[data-word-idx="5"]').click();
  await page.waitForFunction(() => Number(document.querySelector('[aria-label="Seek"]').value) > 0);
  assert.ok(await page.locator('[data-word-idx="5"][data-now]').count(), 'Clicked word is at the playhead');
  await cut(page);
  assert.notDeepEqual((await document(page)).project.tracks, before, 'Cut changed the saved timeline');
  await undo(page);
  assert.deepEqual((await document(page)).project.tracks, before, 'Undo restored the timeline');
  await layout(page);
  await story(page, fixture.script);
  assert.ok(await page.locator('.recipe-line').count(), 'Script video has an animation recipe');
});
