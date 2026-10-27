import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { test } from 'node:test';

function loadMissingGolden(platform: string) {
  return spawnSync(process.execPath, ['--input-type=module', '-e',
    "import { loadGolden } from './test/goldens.ts'; " +
    `Object.defineProperty(process, 'platform', { value: ${JSON.stringify(platform)} }); ` +
    "console.log(JSON.stringify(loadGolden('unrecorded')));",
  ], { cwd: new URL('../', import.meta.url), encoding: 'utf8' });
}

test('a missing Windows recording fails with regeneration instructions', () => {
  const result = loadMissingGolden('win32');
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /Missing platform golden: .*unrecorded\.golden\.win32\.json/);
  assert.match(result.stderr, /run node scripts\/update-goldens\.ts/);
});

test('a missing Linux recording provides a visible skip reason', () => {
  const result = loadMissingGolden('linux');
  assert.equal(result.status, 0, result.stderr);
  const golden = JSON.parse(result.stdout);
  assert.equal(golden.value, null);
  assert.match(golden.skip, /Missing platform golden: .*unrecorded\.golden\.linux\.json/);
});

test('missing required fonts fail unless the explicit opt-out is set', () => {
  const empty = mkdtempSync(join(import.meta.dirname, 'font-bundle-'));
  const bundle = join(empty, 'motion-core');
  mkdirSync(join(bundle, 'scripts'), { recursive: true });
  mkdirSync(join(bundle, 'test'));
  copyFileSync(new URL('../scripts/node-env.ts', import.meta.url), join(bundle, 'scripts/node-env.ts'));
  copyFileSync(new URL('./goldens.ts', import.meta.url), join(bundle, 'test/goldens.ts'));
  const recording = `headline-frames.golden.${process.platform}.json`;
  if (existsSync(new URL(recording, import.meta.url))) {
    copyFileSync(new URL(recording, import.meta.url), join(bundle, 'test', recording));
  }
  const run = (optOut: string) => spawnSync(process.execPath, ['--input-type=module', '-e',
    "import { registerFonts } from './scripts/node-env.ts'; import { loadGolden } from './test/goldens.ts'; " +
    "registerFonts(process.env.CUTGRAPH_ALLOW_MISSING_TEST_FONTS !== '1'); console.log(JSON.stringify(loadGolden('headline-frames')));",
  ], { cwd: bundle, encoding: 'utf8',
    env: { ...process.env, CUTGRAPH_ALLOW_MISSING_TEST_FONTS: optOut } });
  try {
    const required = run('');
    assert.notEqual(required.status, 0);
    assert.match(required.stderr, /Required test fonts missing/);
    const allowed = run('1');
    assert.equal(allowed.status, 0, allowed.stderr);
    // A platform without any recording skips before needing fonts; either reason must be visible.
    assert.match(JSON.parse(allowed.stdout).skip, /Required fonts unavailable|Missing platform golden/);
  } finally {
    assert.equal(dirname(empty), import.meta.dirname);
    rmSync(empty, { recursive: true });
  }
});
