import assert from 'node:assert/strict';
import { test } from 'node:test';
import { CHARACTERS, isSpecOf } from '../src/characters.ts';
import type { CharacterState } from '../src/types.ts';
import { LENS } from '../src/lens.ts';
import { nodeEnv } from '../scripts/node-env.ts';
import { ink, render } from './helpers.ts';
import { DEFAULT_CHARACTER, BUBBLES, FORMATS, INTERACTIONS, drawCharacter, drawSceneFrame, resolveShot, shotTiming } from '../src/index.ts';
import type { Format, ProjectShot } from '../src/index.ts';
import { loadGolden } from './goldens.ts';
import { LENS_FRAMES, lensFrameHash } from './lens-frames.ts';

for (const rig of [LENS]) {
  const env = nodeEnv();
  const frame = (state: CharacterState, fresh = false) => render(400, 240, (ctx) => rig.draw(ctx, 260, 220, 150, state, fresh ? nodeEnv() : env));

  test(`${rig.id}: full vocabulary, distinct expressions and poses, animated moves`, () => {
    assert.equal(CHARACTERS.lens, LENS);
    for (const reference of Object.values(CHARACTERS)) {
      for (const id of Object.keys(reference.expressions)) assert.ok(id in rig.expressions, id);
      for (const id of reference.poses) assert.ok(rig.poses.includes(id), id);
      for (const id of reference.moves) assert.ok(rig.moves.includes(id), id);
    }
    for (const [part, options] of Object.entries(rig.parts)) {
      assert.equal(new Set(options).size, options.length, part);
    }
    for (const [key, ids] of [['expression', Object.keys(rig.expressions)], ['pose', rig.poses]] as const) {
      const hashes = new Set<string>();
      for (const id of ids) {
        const f = frame({ [key]: id, t: 0.6 });
        assert.ok(ink(f) > 2000, id);
        hashes.add(f.hash);
      }
      assert.equal(hashes.size, ids.length, key);
    }
    for (const move of rig.moves) {
      const a = frame({ move, t: 0.1 }), b = frame({ move, t: 0.6 });
      assert.ok(ink(a) > 2000 && ink(b) > 2000, move);
      assert.notEqual(a.hash, b.hash, move);
    }
  });

  test(`${rig.id}: specs, safe defaults and deterministic reuse across environments`, () => {
    const states: CharacterState[] = [];
    for (const [expression, spec] of Object.entries(rig.expressions)) {
      assert.ok(isSpecOf(rig, spec));
      assert.equal(frame({ expression: 'custom', custom: spec, t: 0.6 }).hash, frame({ expression, t: 0.6 }).hash);
      for (const pose of rig.poses) for (const move of rig.moves) states.push({ expression, pose, move, t: 0.6 });
    }
    for (const state of states) assert.equal(frame(state).hash, frame(state, true).hash, JSON.stringify(state));
    assert.equal(frame({ expression: 'unknown', pose: 'unknown', move: 'unknown', palette: 'unknown', t: 0.6 }).hash, frame({ t: 0.6 }).hash);
    assert.equal(frame({ expression: 'toString', pose: 'toString', move: 'toString', t: 0.6 }).hash, frame({ t: 0.6 }).hash);
    assert.equal(frame({ expression: 'unknown', custom: { eyes: 'invalid', mouth: 'smile' }, t: 0.6 }).hash, frame({ t: 0.6 }).hash);
    assert.equal(frame({ lift: 0, t: 0.6 }).hash, frame({ t: 0.6 }).hash);
    assert.notEqual(frame({ lift: 20, t: 0.6 }).hash, frame({ t: 0.6 }).hash);
  });

  test(`${rig.id}: hand anchors meet the art and the crown clears its head`, () => {
    for (const [pose, [fwd, up]] of Object.entries(rig.hands!)) {
      const g = 4, cx = 200, by = 300;
      const f = render(400, 320, (ctx) => rig.draw(ctx, cx, by, rig.rows! * g, { pose, t: 0 }, env));
      const alpha = (x: number, dy: number) => f.ctx.getImageData(Math.round(x), Math.round(by - (up + dy) * g), 1, 1).data[3];
      for (const dy of [-1, 0, 1]) assert.equal(alpha(cx + fwd * g - g / 2, dy), 255, `${pose}: behind anchor`);
      assert.ok([-2, -1, 0, 1, 2].some((dy) => alpha(cx + fwd * g + g / 2, dy) === 0), `${pose}: front edge`);
      assert.ok([-1, 0, 1].every((dy) => alpha(cx + (fwd + 4) * g, dy) === 0), `${pose}: clear in front`);
    }
    const f = render(120, 100, (ctx) => rig.draw(ctx, 60, 80, rig.rows!, { t: 0.6 }, env));
    const data = f.ctx.getImageData(0, 0, 120, 100).data;
    const top = Array.from({ length: 80 }, (_, y) => y).find((y) => Array.from({ length: 120 }, (_, x) => x).some((x) => data[(y * 120 + x) * 4 + 3] === 255));
    assert.ok(top !== undefined && 80 - top <= rig.crown!, 'crown clears the art');
  });
}

test('Lens is a default rig as the default', () => {
  assert.equal(CHARACTERS.lens, LENS);
  assert.equal(LENS.name, 'Lens');
  assert.equal(LENS.owner, 'brand');
  assert.deepEqual(LENS.palettes, ['periwinkle']);
  assert.equal(DEFAULT_CHARACTER, LENS.id);
  const env = nodeEnv();
  const state = { expression: 'curious', pose: 'wave', move: 'nod', t: 0.6 };
  assert.equal(
    render(400, 240, (ctx) => LENS.draw(ctx, 260, 220, 150, state, env)).hash,
    render(400, 240, (ctx) => drawCharacter(ctx, 260, 220, 150, { ...state, character: 'lens' }, env)).hash,
  );
});

test('Lens acts in scenes, pairs with every rig, and springs in on a cameo cue', () => {
  const env = nodeEnv(), timing = shotTiming(0, 12, 30, 30, 180);
  for (const format of Object.keys(FORMATS) as Format[]) {
    const [W, H] = FORMATS[format];
    const frame = (stored: ProjectShot, t = 1.5) => render(W / 4, H / 4, (ctx) => {
      ctx.scale(0.25, 0.25);
      const shot = resolveShot({ mode: 'animation', character: 'lens', hideHeadline: true, diagram: 'none', ...stored }, 'Beat', '');
      drawSceneFrame(ctx, stored.mode ?? 'animation', shot, t, timing, W, H, env);
    });
    const plain = frame({});
    for (const bubble of BUBBLES) assert.notEqual(frame({ bubble }).hash, plain.hash, `${format} ${bubble}`);
    for (const rig of Object.values(CHARACTERS)) {
      for (const interaction of INTERACTIONS) {
        const duo = { costar: { character: rig.id, move: 'enter', bubble: 'say' }, interaction };
        assert.ok(ink(frame(duo)) > 1000, `${format} ${rig.id} ${interaction}`);
        assert.notEqual(frame(duo, 0.1).hash, frame(duo, 0.6).hash, 'entrance animates');
      }
      const ensemble: ProjectShot = { ensemble: [{ character: rig.id, bubble: 'think' }, { character: 'lens', bubble: 'question' }] };
      assert.notEqual(frame(ensemble).hash, plain.hash);
    }
    assert.equal(ink(frame({ mode: 'cameo', cueWord: 1 }, 0.9)), 0, 'hidden before cue');
    assert.ok(ink(frame({ mode: 'cameo', cueWord: 1 }, 1.5)) > 100, 'visible after cue');
  }
});

const golden = loadGolden<Record<string, string>>('lens-frames');
// Other platforms remain unrecorded until their pixels and fonts are qualified.
test('Lens representative frames match the platform recording', { skip: golden.skip }, () => {
  assert.deepEqual(Object.keys(golden.value!), LENS_FRAMES.map((f) => f.name));
  const env = nodeEnv();
  for (const f of LENS_FRAMES) assert.equal(lensFrameHash(f, env), golden.value![f.name], f.name);
});
