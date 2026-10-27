import { characterOf as defaultRigOf, DEFAULT_CHARACTER as defaultRigId } from '../src/index.ts';
const DEFAULT_RIG = defaultRigOf(defaultRigId);
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  LENS_EXPRESSIONS, LENS_MOVES, LENS_POSES,  drawShot,
} from '../src/index.ts';
import type { CharacterState, ExpressionId, MoveId } from '../src/index.ts';
import { nodeEnv } from '../scripts/node-env.ts';
import { ink, render } from './helpers.ts';

const env = nodeEnv();
// Wide enough that the 'enter' move, which slides in from 58 rig pixels to the left, stays in frame.
const character = (st: CharacterState) => render(400, 200, (ctx) => DEFAULT_RIG.draw(ctx, 260, 180, 164, st, env));

test('every expression renders, and each one looks different', () => {
  const ids = Object.keys(LENS_EXPRESSIONS) as ExpressionId[];
  assert.equal(ids.length, 11);
  const hashes = new Set<string>();
  for (const expression of ids) {
    const f = character({ expression, pose: 'rest', move: 'idle', t: 0.6 });
    assert.ok(ink(f) > 2000, expression + ' rendered blank');
    hashes.add(f.hash);
  }
  assert.equal(hashes.size, ids.length);
});

test('every pose renders, and each one looks different', () => {
  assert.equal(LENS_POSES.length, 9);
  const hashes = new Set<string>();
  for (const pose of LENS_POSES) {
    const f = character({ pose, expression: 'neutral', move: 'idle', t: 0.6 });
    assert.ok(ink(f) > 2000, pose + ' rendered blank');
    hashes.add(f.hash);
  }
  assert.equal(hashes.size, LENS_POSES.length);
});

test('every move renders and animates: the frame at t differs from t + 0.5', () => {
  assert.equal(LENS_MOVES.length, 9);
  for (const move of LENS_MOVES) {
    const a = character({ move, pose: 'rest', expression: 'neutral', t: 0.1 });
    const b = character({ move, pose: 'rest', expression: 'neutral', t: 0.6 });
    assert.ok(ink(a) > 2000 && ink(b) > 2000, move + ' rendered blank');
    assert.notEqual(a.hash, b.hash, move + ' did not animate');
  }
});

test('a whole shot animates for every move', () => {
  const shot = (move: MoveId, t: number) => render(480, 270, (ctx) => {
    ctx.scale(0.25, 0.25);
    drawShot(ctx, { pose: 'rest', expression: 'happy', move, backdrop: 'route', diagram: 'route', headline: 'Moving' }, t, { cueT: 0.2 }, 1920, 1080, env);
  });
  for (const move of LENS_MOVES) assert.notEqual(shot(move, 1.1).hash, shot(move, 1.6).hash, move);
});

test('a custom expression spec draws like the built-in it matches', () => {
  const custom = character({ expression: 'custom', custom: { eyes: 'arc', mouth: 'grin' }, t: 0.6 });
  assert.equal(custom.hash, character({ expression: 'happy', t: 0.6 }).hash);
  assert.equal(character({ expression: 'unknown', t: 0.6 }).hash, character({ expression: 'neutral', t: 0.6 }).hash);
});
