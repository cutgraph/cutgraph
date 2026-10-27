import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { test } from 'node:test';
import { CHARACTERS, PACK_RIGS, characterOf, registerPack, selectPackManifests, type Character } from '../src/index.ts';
import { loadPacks, packArgs } from '../scripts/packs.ts';

const rig: Character = {
  id: 'synthetic-pack', name: 'Synthetic', owner: 'client', palettes: ['plain'],
  parts: { eyes: ['calm', 'wide'] }, expressions: { neutral: { eyes: 'calm' }, alert: { eyes: 'wide' } },
  poses: ['rest', 'wave'], moves: ['idle', 'hop'], draw() {},
};
const entry = { ...rig, expressions: ['alert', 'neutral'], draw: undefined,
  labels: { titles: { eyes: 'Eyes' }, options: { eyes: { calm: 'Calm' } } },
  head: { rows: 40, foot: 40, span: 20, cols: 22, centre: 12 },
};

test('a pack registers its characters and composer metadata', () => {
  assert.deepEqual(registerPack({ characters: [entry] }, [rig]), [entry]);
  assert.equal(characterOf(rig.id), rig);
  assert.equal(PACK_RIGS[rig.id], entry);
});

test('clashes, mismatches and malformed packs warn once and register nothing', (t) => {
  const warnings: unknown[][] = [];
  t.mock.method(console, 'warn', (...args: unknown[]) => { warnings.push(args); });
  const fresh = { ...entry, id: 'never-registered' };
  const character = { ...rig, id: fresh.id };
  const failures: [unknown, unknown][] = [
    [{ characters: [fresh, { ...entry, id: 'lens' }] }, [character, { ...rig, id: 'lens' }]],
    [{ characters: [entry] }, [rig]],
    [{ characters: [fresh, fresh] }, [character, character]],
    [{ characters: [fresh] }, []],
    [{ characters: [fresh] }, [character, character]],
    [{ characters: [fresh] }, [{ ...character, name: 'Different' }]],
    [{ characters: [fresh] }, [{ ...character, owner: 'brand' }]],
    [{ characters: [fresh] }, [{ ...character, palettes: ['other'] }]],
    [{ characters: [fresh] }, [{ ...character, parts: { eyes: ['wide', 'calm'] } }]],
    [{ characters: [fresh] }, [{ ...character, poses: ['wave', 'rest'] }]],
    [{ characters: [fresh] }, [{ ...character, moves: ['hop', 'idle'] }]],
    [{ characters: [fresh] }, [{ ...character, expressions: { neutral: { eyes: 'calm' } } }]],
    [{ characters: [fresh] }, [{ ...character, draw: null }]],
    [null, null], [{ characters: [null] }, [character]],
    [{ characters: [{ ...fresh, id: 'Bad_id' }] }, [character]],
    [{ characters: [{ ...fresh, id: 'rig\n' }] }, [character]],
    [{ characters: [{ ...fresh, palettes: [] }] }, [character]],
    [{ characters: [{ ...fresh, expressions: [] }] }, [{ ...character, expressions: {} }]],
    [{ characters: [fresh] }, [{ ...character, expressions: {} }]],
    [{ characters: [{ ...fresh, parts: { eyes: [] } }] }, [character]],
    [{ characters: [{ ...fresh, labels: { titles: [], options: {} } }] }, [character]],
    [{ characters: [{ ...fresh, head: { ...fresh.head, rows: 0 } }] }, [character]],
  ];
  for (const [manifest, characters] of failures) {
    assert.equal(registerPack(manifest, characters), null);
    assert.equal(CHARACTERS[fresh.id], undefined);
    assert.equal(PACK_RIGS[fresh.id], undefined);
  }
  assert.equal(warnings.length, failures.length);
  assert.ok(warnings.every(([message]) => String(message).startsWith('Skipping character pack:')));
});

test('shared manifest parity cases decide clashes before module registration', async (t) => {
  t.mock.method(console, 'warn', () => {});
  const cases = JSON.parse(await readFile(new URL('./pack-cases.json', import.meta.url), 'utf8')) as {
    name: string; packs: { id: string; manifest: unknown; complete?: boolean }[]; accepted: string[];
  }[];
  for (const scenario of cases) {
    const complete = scenario.packs.filter((pack) => pack.complete !== false);
    for (const entries of [complete, [...complete].reverse()]) {
      assert.deepEqual(selectPackManifests(entries).flatMap((pack) => pack.rigs.map((rig) => rig.id)).sort(), scenario.accepted, scenario.name);
    }
  }
});

test('the Node loader sorts packs, ignores incomplete directories and contains import errors', async (t) => {
  const dir = await mkdtemp(join(tmpdir(), 'motion packs-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const warnings: unknown[][] = [];
  t.mock.method(console, 'warn', (...args: unknown[]) => { warnings.push(args); });
  async function pack(name: string, id: string, code?: string): Promise<void> {
    const folder = join(dir, name);
    await mkdir(folder);
    const metadata = { ...entry, id };
    await writeFile(join(folder, 'pack.json'), JSON.stringify({ characters: [metadata] }));
    await writeFile(join(folder, 'index.ts'), code ?? `export const characters = [{ ...${JSON.stringify(metadata)}, expressions: { neutral: {}, alert: {} }, draw() {} }];`);
  }
  await pack('b-second', 'loader-rig', 'throw new Error("clashing module must not import");');
  await pack('a-first', 'loader-rig', 'throw new Error("clashing module must not import");');
  await pack('f-valid', 'accepted-loader-rig');
  await pack('g-builtin', 'lens', 'throw new Error("builtin clash must not import");');
  await pack('h-invalid', 'accepted-loader-rig', 'throw new Error("invalid manifest must not import");');
  await writeFile(join(dir, 'h-invalid', 'pack.json'), JSON.stringify({ characters: [{ ...entry, id: 'accepted-loader-rig', expressions: [] }] }));
  await pack('c-broken', 'broken-rig', 'throw new Error("synthetic import failure");');
  await pack('d-mismatch', 'mismatch-rig', 'export const characters = [];');
  await pack('Bad_id', 'bad-folder-rig');
  await pack('e-malformed', 'malformed-rig');
  await writeFile(join(dir, 'e-malformed', 'pack.json'), '{');
  await mkdir(join(dir, 'incomplete'));
  await writeFile(join(dir, 'incomplete', 'pack.json'), '{');
  await mkdir(join(dir, 'module-only'));
  await writeFile(join(dir, 'module-only', 'index.ts'), 'throw new Error("must not load");');
  await loadPacks(dir);
  assert.equal(CHARACTERS['loader-rig'], undefined);
  assert.equal(characterOf('accepted-loader-rig').id, 'accepted-loader-rig');
  assert.equal(PACK_RIGS['accepted-loader-rig'].name, 'Synthetic');
  assert.equal(warnings.length, 8);
  assert.ok(warnings.some(([message]) => String(message).includes('synthetic import failure')));
  assert.ok(warnings.every(([message]) => !String(message).includes('must not import')));
  assert.equal(CHARACTERS['mismatch-rig'], undefined);
  await loadPacks(join(dir, 'absent'));
  assert.equal(warnings.length, 8);
});

test('pack CLI options preserve the renderers positional arguments', () => {
  assert.deepEqual(packArgs(['-', 'out.mp4', 'encoder']), { args: ['-', 'out.mp4', 'encoder'] });
  assert.deepEqual(packArgs(['-', 'out.mp4', 'encoder', '--packs', 'rigs']), { args: ['-', 'out.mp4', 'encoder'], dir: 'rigs' });
  assert.deepEqual(packArgs(['--packs', 'rigs', '-', 'out.png']), { args: ['-', 'out.png'], dir: 'rigs' });
  assert.throws(() => packArgs(['-', 'out.png', '--packs']), /requires a directory/);
});

test('both renderer CLIs load packs before drawing and survive an invalid neighbor', async (t) => {
  const dir = await mkdtemp(join(tmpdir(), 'pack render-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const packDir = join(dir, 'rigs');
  await mkdir(packDir);
  await mkdir(join(packDir, 'synthetic'));
  const metadata = { ...entry, id: 'render-pack-rig' };
  await writeFile(join(packDir, 'synthetic', 'pack.json'), JSON.stringify({ characters: [metadata] }));
  await writeFile(join(packDir, 'synthetic', 'index.ts'),
    `export const characters = [{ ...${JSON.stringify(metadata)}, expressions: { neutral: {}, alert: {} },
      draw(ctx, cx, by, h) { process.stderr.write('synthetic rig drew\\n'); ctx.fillRect(cx - h / 4, by - h, h / 2, h); } }];`);
  await mkdir(join(packDir, 'invalid'));
  await writeFile(join(packDir, 'invalid', 'pack.json'), '{}');
  await writeFile(join(packDir, 'invalid', 'index.ts'), 'export const characters = [];');
  const ffmpeg = execFileSync(process.platform === 'win32' ? 'where.exe' : 'which', ['ffmpeg'], { encoding: 'utf8' }).trim().split(/\r?\n/)[0];
  const scene = {
    width: 160, height: 90, fps: 30, durF: 1, mode: 'animation',
    shot: { mode: 'animation', character: metadata.id }, role: 'Hook', title: '',
    startF: 0, firstWordF: null, cueWordF: null, alpha: false,
  };
  const poster = { width: 160, height: 90, poster: { character: metadata.id, headline: '' } };
  for (const [name, spec, suffix, extra] of [
    ['scene', scene, 'mp4', [ffmpeg]], ['poster', poster, 'png', []],
  ] as const) {
    const out = join(dir, `out.${suffix}`);
    const result = spawnSync(process.execPath, [resolve(import.meta.dirname, `../bin/render-${name}.ts`), '-', out, ...extra, '--packs', packDir],
      { input: JSON.stringify(spec), encoding: 'utf8' });
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stderr, /synthetic rig drew/);
    assert.match(result.stderr, /Skipping character pack invalid: malformed manifest/);
    assert.equal(JSON.parse(result.stdout.trim().split('\n').at(-1)!).event, 'done');
  }
});
