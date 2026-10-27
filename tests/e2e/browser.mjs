import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';
import path from 'node:path';

const require = createRequire(new URL('../../ui/package.json', import.meta.url));
const { chromium } = require('playwright');
export const base = process.env.E2E_BASE_URL;
assert.ok(base, 'Run specs through tests/e2e/run.py');
export const state = process.env.CUTGRAPH_STATE_HOME;
export const fixture = JSON.parse(await readFile(path.join(state, 'fixture.json'), 'utf8'));

export async function browserSpec(check) {
  const browser = await chromium.launch({ channel: 'chrome' });
  try {
    for (const width of [1440, 393]) {
      const context = await browser.newContext({ viewport: { width, height: 900 } });
      const errors = [];
      const violations = [];
      await context.exposeBinding('recordPolicyViolation', (_source, directive, blockedURI) => {
        violations.push({ directive, blockedURI });
      });
      await context.addInitScript(() => {
        window.addEventListener('securitypolicyviolation', (event) => {
          void window.recordPolicyViolation(event.violatedDirective, event.blockedURI);
        });
      });
      context.on('page', (page) => page.on('pageerror', (error) => errors.push(error.message)));
      try {
        const page = await context.newPage();
        page.setDefaultTimeout(15000);
        await check(page, width);
        assert.deepEqual(violations, [], `No security policy violations at ${width}px`);
        assert.deepEqual(errors, [], `No page errors at ${width}px`);
        console.log(`${width}px: zero security policy violations`);
      } finally {
        await context.close();
      }
    }
  } finally {
    await browser.close();
  }
}

export async function layout(page) {
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const size = await page.evaluate(() => ({
    content: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth),
    viewport: document.documentElement.clientWidth,
  }));
  assert.ok(size.content <= size.viewport, `No horizontal overflow: ${JSON.stringify(size)}`);
}

export async function story(page, video = fixture.footage) {
  await page.goto(`${base}/editor/?view=story&video=${video}`);
  await page.locator('.story[data-lens="script"] [data-word-idx="5"]')
    .waitFor({ state: 'visible', timeout: 60000 });
  await layout(page);
}

export async function document(page, video = fixture.footage) {
  return page.evaluate(async (video) => {
    const response = await fetch(`/api/project/${video}`);
    if (!response.ok) throw new Error(`Document read: ${response.status}`);
    return response.json();
  }, video);
}

export async function cut(page) {
  await page.locator('[data-word-idx="5"]').click();
  const [saved] = await Promise.all([
    page.waitForResponse((r) => r.url() === `${base}/api/project/${fixture.footage}`
      && r.request().method() === 'PUT'),
    page.keyboard.press('Delete'),
  ]);
  assert.equal(saved.status(), 200, 'Cut saved');
  await page.locator('[data-word-idx="5"].cut').waitFor();
  await layout(page);
}

export async function undo(page) {
  const [saved] = await Promise.all([
    page.waitForResponse((r) => r.url() === `${base}/api/project/${fixture.footage}`
      && r.request().method() === 'PUT'),
    page.locator('button[aria-label="Undo"]').click(),
  ]);
  assert.equal(saved.status(), 200, 'Undo saved');
  await page.locator('[data-word-idx="5"]:not(.cut)').waitFor();
}
