import assert from 'node:assert/strict';
import { browserSpec, cut, document, layout, story } from '../browser.mjs';

await browserSpec(async (page) => {
  await story(page);
  const before = (await document(page)).project.tracks;
  await cut(page);
  const saved = (await document(page)).project.tracks;
  assert.notDeepEqual(saved, before, 'Cut changed the timeline');
  await page.reload();
  await page.locator('[data-word-idx="5"].cut').waitFor();
  assert.deepEqual((await document(page)).project.tracks, saved, 'Reload preserved the saved edit');
  await layout(page);
  // Undo history is session-local; restore through the word's UI after proving persistence.
  await page.locator('[data-word-idx="5"]').click();
  const [restored] = await Promise.all([
    page.waitForResponse((r) => r.url().endsWith('/api/project/synthetic-footage')
      && r.request().method() === 'PUT'),
    page.keyboard.press('Delete'),
  ]);
  assert.equal(restored.status(), 200);
  assert.deepEqual((await document(page)).project.tracks, before, 'Restore returned to the fixture');
  await layout(page);
});
