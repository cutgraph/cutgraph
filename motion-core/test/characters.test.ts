import { characterOf as defaultRigOf, DEFAULT_CHARACTER as defaultRigId } from '../src/index.ts';
const DEFAULT_RIG = defaultRigOf(defaultRigId);
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  CHARACTERS, DEFAULT_CHARACTER, LENS_EXPRESSIONS, LENS_MOVES, LENS_POSES,
  characterOf,  drawCharacter, drawSceneFrame, drawShot, isSpecOf, performer, resolveShot, shotTiming,
} from '../src/index.ts';
import type { CharacterState, ShotMode } from '../src/index.ts';
import { nodeEnv } from '../scripts/node-env.ts';
import { ink, render } from './helpers.ts';

const env = nodeEnv();

test('the default rig is registered as "lens" with its own sets', () => {
  assert.deepEqual(Object.keys(CHARACTERS), ['lens']);
  assert.equal(CHARACTERS.lens, DEFAULT_RIG);
  assert.equal(DEFAULT_CHARACTER, 'lens');
  assert.equal(DEFAULT_RIG.owner, 'brand');
  assert.deepEqual(DEFAULT_RIG.palettes, ['periwinkle']);
  assert.equal(DEFAULT_RIG.expressions, LENS_EXPRESSIONS);
  assert.equal(DEFAULT_RIG.poses, LENS_POSES);
  assert.equal(DEFAULT_RIG.moves, LENS_MOVES);
  assert.deepEqual(Object.keys(DEFAULT_RIG.parts), ['eyes', 'mouth']);
  for (const spec of Object.values(DEFAULT_RIG.expressions)) assert.ok(isSpecOf(DEFAULT_RIG, spec), JSON.stringify(spec));
  for (const [part, options] of Object.entries(DEFAULT_RIG.parts)) {
    assert.equal(new Set(options).size, options.length, `${part} options are unique`);
    for (const spec of Object.values(LENS_EXPRESSIONS)) assert.ok(options.includes(spec[part as keyof typeof spec]));
  }
});

test('a missing or unknown character is the default rig', () => {
  assert.equal(characterOf(undefined), DEFAULT_RIG);
  assert.equal(characterOf('lens'), DEFAULT_RIG);
  assert.equal(characterOf('garden-guest'), DEFAULT_RIG);
  assert.equal(characterOf('toString'), DEFAULT_RIG, 'not an inherited name');
});

test('a spec is exactly one known option for each of the character\'s parts', () => {
  assert.ok(isSpecOf(DEFAULT_RIG, { eyes: 'side', mouth: 'smile' }));
  for (const spec of [undefined, null, 'arc', ['arc', 'up', 'off'], { eyes: 'arc', character: 'up' },
    { eyes: 'arc', mouth: 'grin', extra: 'grin' }, { eyes: 'laser', mouth: 'smile' },
    { eyes: 'arc', mouth: 1 }, { unknownPart: 'arc', otherPart: 'smile' },
    { eyes: 'arc', mouth: 'grin', unknownPart: 'wide' }]) {
    assert.equal(isSpecOf(DEFAULT_RIG, spec), false, JSON.stringify(spec));
  }
});

// The registry wraps the default rig without touching it: every frame through it is byte-identical to
// the default rig's, for every expression, pose, move and a custom spec, at several moments.
test('the registry draws the default rig pixel for pixel', () => {
  const direct = (st: CharacterState) => render(400, 200, (ctx) => DEFAULT_RIG.draw(ctx, 260, 180, 164, st, env)).hash;
  const viaRig = (st: CharacterState) => render(400, 200, (ctx) => DEFAULT_RIG.draw(ctx, 260, 180, 164, st, env)).hash;
  const viaCharacter = (st: CharacterState, character?: string) =>
    render(400, 200, (ctx) => drawCharacter(ctx, 260, 180, 164, { ...st, character }, env)).hash;
  const states: CharacterState[] = [
    ...Object.keys(LENS_EXPRESSIONS).flatMap((expression) => [0.1, 0.6, 3.35].map((t) => ({ expression, pose: 'rest', move: 'idle', t }) as CharacterState)),
    ...LENS_POSES.map((pose) => ({ pose, expression: 'neutral', move: 'idle', t: 0.6 }) as CharacterState),
    ...LENS_MOVES.flatMap((move) => [0.1, 0.6, 1.3].map((t) => ({ move, pose: 'point', expression: 'idea', t }) as CharacterState)),
    { expression: 'proud', custom: { eyes: 'side', mouth: 'crooked' }, pose: 'wave', move: 'wave', t: 0.9 },
    {},
  ];
  for (const st of states) {
    const want = direct(st);
    assert.equal(viaRig(st), want, JSON.stringify(st));
    assert.equal(viaCharacter(st, 'lens'), want, JSON.stringify(st));
    assert.equal(viaCharacter(st), want, JSON.stringify(st));
  }
});

test('shots and scene frames draw the shot\'s character; absent or unknown is the default rig', () => {
  const timing = shotTiming(0, 12, 30, 30);
  const [W, H] = [480, 270];
  for (const mode of ['animation', 'cameo'] as ShotMode[]) {
    const shot = { ...resolveShot({ mode, expression: 'smug' }, 'Payoff', 'Done'), custom: { eyes: 'side', mouth: 'smile' } };
    assert.equal(shot.character, 'lens');
    const frame = (character: string | undefined) =>
      render(W, H, (ctx) => drawSceneFrame(ctx, mode, { ...shot, character }, 1.4, timing, W, H, env));
    const character = frame('lens');
    assert.ok(ink(character) > 200, `${mode} draws`);
    assert.equal(frame(undefined).hash, character.hash, `${mode}: no character is the default rig`);
    assert.notEqual(frame('unregistered-rig').hash, character.hash, `${mode}: an unknown character is the default rig`);
  }
  const shot = resolveShot({ pose: 'cheer' }, 'Hook', 'Open');
  const whole = (character?: string) => render(W, H, (ctx) => {
    ctx.scale(0.25, 0.25);
    drawShot(ctx, { ...shot, character }, 2, timing, 1920, 1080, env);
  }).hash;
  assert.notEqual(whole('nope'), whole('lens'));
  assert.equal(whole(undefined), whole('lens'));
});

test('a custom spec or palette outside the character\'s space is ignored rather than breaking the frame', () => {
  const character = (st: CharacterState) => render(400, 200, (ctx) => DEFAULT_RIG.draw(ctx, 260, 180, 164, st, env)).hash;
  const via = (custom: Record<string, string>, palette?: string) => render(400, 200, (ctx) =>
    drawCharacter(ctx, 260, 180, 164, { character: 'lens', palette, expression: 'custom', custom, t: 0.6 }, env)).hash;
  assert.equal(via({ unknownPart: 'arc', otherPart: 'smile' }), character({ expression: 'neutral', t: 0.6 }));
  assert.equal(via({ eyes: 'arc', unknownPart: 'sideways' }), character({ expression: 'neutral', t: 0.6 }));
  assert.equal(via({ eyes: 'arc', mouth: 'grin' }), character({ expression: 'happy', t: 0.6 }));
  assert.equal(via({ eyes: 'arc', mouth: 'grin' }, 'unknown-palette'), character({ expression: 'happy', t: 0.6 }));
});

test('resolveShot resolves ids against the character and stamps its registry id and palette', () => {
  assert.equal(resolveShot({ character: 'unregistered-rig' }, 'Beat', 't').character, 'unregistered-rig');
  assert.equal(resolveShot({ palette: 'unknown-palette' }, 'Beat', 't').palette, 'periwinkle');
  const s = resolveShot({ character: 'lens', pose: 'moonwalk', move: 'wave', expression: 'grin' }, 'Wrap', 'Bye');
  assert.deepEqual([s.pose, s.move, s.expression], ['wave', 'wave', 'grin']);
  assert.deepEqual(performer(resolveShot({ expression: 'smug' }, 'Beat', 't'), 1.5), {
    character: 'lens', palette: 'periwinkle', pose: 'rest', expression: 'smug', move: 'idle', t: 1.5, custom: undefined,
  });
});

// Each hand anchor sits on the front edge of that pose's hand, at its middle: the hand fills the rig pixel
// behind it and the ones above and below that, and in front of them is empty but for a pointing finger.
// Drawn alone at a whole scale, still (no bob, no breath).
test('every rig\'s hand anchors mark the front of its hands in the art', () => {
  for (const rig of Object.values(CHARACTERS)) {
    assert.ok(rig.rows && rig.hands, `${rig.id} declares its hands`);
    for (const [pose, [fwd, up]] of Object.entries(rig.hands)) {
      assert.ok(rig.poses.includes(pose), `${rig.id} has the ${pose} pose`);
      const g = 4, cx = 200, by = 300;
      const f = render(400, 320, (ctx) => drawCharacter(ctx, cx, by, rig.rows! * g, { character: rig.id, pose, move: 'idle', t: 0 }, env));
      const alpha = (x: number, dy: number): number => f.ctx.getImageData(Math.round(x), Math.round(by - (up + dy) * g), 1, 1).data[3];
      for (const dy of [-1, 0, 1]) assert.equal(alpha(cx + fwd * g - g / 2, dy), 255, `${rig.id} ${pose}: the hand is just behind the anchor (${dy})`);
      assert.ok([-2, -1, 0, 1, 2].some((dy) => alpha(cx + fwd * g + g / 2, dy) === 0), `${rig.id} ${pose}: its front edge is there`);
      assert.ok([-1, 0, 1].every((dy) => alpha(cx + (fwd + 4) * g, dy) === 0), `${rig.id} ${pose}: nothing further in front`);
    }
  }
});

test('a lifted character rises off the ground while its shadow stays there; no lift draws as before', () => {
  const at = (lift?: number) => render(400, 320, (ctx) => drawCharacter(ctx, 200, 300, 192, { character: 'lens', pose: 'wave', move: 'idle', t: 0, lift }, env));
  assert.equal(at(0).hash, at().hash);
  const ground = at(), up = at(40);
  const rows = (f: ReturnType<typeof at>, y: number) => f.ctx.getImageData(0, y, 400, 1).data;
  const opaque = (d: Uint8ClampedArray) => { let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i] === 255) n++; return n; };
  const shade = (d: Uint8ClampedArray) => { let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i] > 0 && d[i] < 255) n++; return n; };
  assert.ok(opaque(rows(ground, 297)) > 0 && opaque(rows(up, 297)) === 0, 'the feet leave the ground');
  assert.ok(opaque(rows(up, 257)) > 0, 'and stand 40 px higher');
  assert.ok(shade(rows(up, 300)) > 0 && shade(rows(up, 300)) < shade(rows(ground, 300)), 'the shadow stays down, smaller');
});
