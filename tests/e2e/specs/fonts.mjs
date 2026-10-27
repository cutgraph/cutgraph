import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import { browserSpec, fixture, story } from '../browser.mjs';

const faces = [
  ['Inter', 400, 'inter/Inter-Regular.ttf'],
  ['Inter', 600, 'inter/Inter-SemiBold.ttf'],
  ['Inter', 700, 'inter/Inter-Bold.ttf'],
  ['Inter', 900, 'inter/Inter-Black.ttf'],
  ['Liberation Sans', 400, 'liberation-sans/LiberationSans-Regular.ttf'],
  ['Liberation Sans', 700, 'liberation-sans/LiberationSans-Bold.ttf'],
  ['Cascadia Code', 400, 'cascadia-code/CascadiaCode-Regular.ttf'],
];
const expected = await Promise.all(faces.map(async ([family, weight, file]) => ({
  family, weight,
  hash: createHash('sha256').update(await readFile(new URL(`../../../assets/fonts/${file}`, import.meta.url))).digest('hex'),
})));

await browserSpec(async (page, width) => {
  await story(page, fixture.script);
  const actual = await page.evaluate(async (faces) => {
    const rules = [...document.styleSheets].flatMap((sheet) => [...sheet.cssRules])
      .filter((rule) => rule instanceof CSSFontFaceRule);
    return Promise.all(faces.map(async ({ family, weight }) => {
      const loaded = await document.fonts.load(`${weight} 16px "${family}"`);
      const rule = rules.find((rule) => rule.style.fontFamily.replaceAll('"', '') === family && Number(rule.style.fontWeight) === weight);
      if (!rule) throw new Error(`Missing face: ${family} ${weight}`);
      const url = rule.style.src.match(/url\(["']?([^"')]+)["']?\)/)?.[1];
      if (!url) throw new Error(`Missing font URL: ${family} ${weight}`);
      const response = await fetch(url);
      if (!response.ok) throw new Error(`Font response: ${response.status}`);
      const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', await response.arrayBuffer()));
      return { family, weight, count: loaded.length, loaded: loaded.every((face) => face.status === 'loaded'),
        hash: [...digest].map((byte) => byte.toString(16).padStart(2, '0')).join('') };
    }));
  }, expected);
  assert.deepEqual(actual.map(({ count, loaded, ...face }) => {
    assert.equal(count, 1, 'One loaded file per face');
    assert.equal(loaded, true, 'The browser has finished loading each face');
    return face;
  }), expected, 'Preview font bytes equal the bundled export files');
  assert.equal(await page.locator('.v3').evaluate((element) => getComputedStyle(element).fontFamily), 'Inter');
  if (width === 1440 && process.env.E2E_SCREENSHOT_DIR) {
    await mkdir(process.env.E2E_SCREENSHOT_DIR, { recursive: true });
    await page.screenshot({ path: path.join(process.env.E2E_SCREENSHOT_DIR, 'story.png') });
  }
});
