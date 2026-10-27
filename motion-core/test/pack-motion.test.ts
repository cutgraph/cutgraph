import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  cameraAt, characterOf, drawCharacter, drawSceneFrame, registerPack, resolvePoster, resolveShot,
} from '../src/index.ts';
import type { Character, CharacterState, Ctx, MotionEnv } from '../src/index.ts';
import { rigDraw } from '../src/rig.ts';
import { nodeEnv, surface } from '../scripts/node-env.ts';

function pack(id: string, overrides: Partial<Character> = {}): Character {
  const rig: Character = {
    id, name: 'Sample', owner: 'client', palettes: ['plain'],
    parts: { eyes: ['calm', 'wide'] },
    expressions: { neutral: { eyes: 'calm' }, happy: { eyes: 'wide' } },
    poses: ['rest', 'point', 'wave'], moves: ['idle', 'hop'], rows: 72, crown: 68,
    draw() {}, ...overrides,
  };
  const manifest = { characters: [{ ...rig, expressions: Object.keys(rig.expressions) }] };
  assert.ok(registerPack(manifest, [rig], id));
  assert.equal(characterOf(id), rig);
  return rig;
}

test('registered pack rigs retain independent diagrams, cameras and neutral staging', () => {
  const rig = pack('motion-layouts');
  for (const diagram of ['grid', 'chain', 'radial', 'tree', 'list']) {
    for (const camera of ['push-in', 'pull-out']) {
      const shot = resolveShot({
        character: rig.id, diagram, camera, bubble: 'exclaims', boardLabels: ['One', '', 'Three'],
        badges: [{ text: 'A', tone: 'paper' }, { text: 'B', tone: 'unknown' }],
        costar: { character: rig.id }, ensemble: [{ character: rig.id }],
      }, 'Beat', 'Sample');
      assert.equal(shot.character, rig.id);
      assert.equal(shot.diagram, diagram);
      assert.equal(shot.camera, camera);
      assert.equal(shot.bubble, 'exclaims');
      assert.equal(shot.backdrop, 'work');
      assert.deepEqual(shot.boardLabels, ['One', '', 'Three']);
      assert.deepEqual(shot.badges, [{ text: 'A', tone: 'paper' }, { text: 'B', tone: 'ink' }]);
      for (const member of [shot.costar!, ...shot.ensemble!]) {
        assert.equal(member.character, rig.id);
        assert.equal(member.palette, 'plain');
      }
      assert.ok(cameraAt(shot.camera, 0.4, 1).k >= 1);
      assert.ok(cameraAt(shot.camera, 0.4, 1).k <= 1.18);
    }
  }
});

test('a registered pack rig paints through a camera and independent diagram in preview and export', () => {
  let painted = 0;
  const rig = pack('motion-scene', {
    rows: 50, crown: 46,
    draw: rigDraw(['#2bd3ad', '#2bd3ad', '#2bd3ad', '#2bd3ad'], (ctx, m) => {
      painted++;
      ctx.fillStyle = '#2bd3ad';
      ctx.fillRect(10, 5 + m.chest, 25, 35);
      ctx.fillStyle = '#a62bd3';
      ctx.fillRect(12, 12 + m.chest, 4, 4);
      return 20 + m.chest;
    }),
  });
  const env = nodeEnv();
  const shot = resolveShot({ mode: 'animation', character: rig.id, camera: 'push-in',
    diagram: 'grid', boardLabels: ['One', 'Two', 'Three'], hideHeadline: true, backdrop: 'black',
  }, 'Beat', '');
  const render = (scale: number, diagram = shot.diagram, camera = shot.camera) => {
    const { ctx } = surface(1920, 1080);
    ctx.scale(1 / scale, 1 / scale);
    drawSceneFrame(ctx, 'animation', { ...shot, diagram, camera }, 1.95, { endT: 2 },
      1920 * scale, 1080 * scale, env);
    return ctx.getImageData(0, 0, 1920, 1080).data;
  };
  const exported = render(1), preview = render(2);
  let ink = 0;
  for (let i = 0; i < exported.length; i += 4) {
    if (exported[i] === 43 && exported[i + 1] === 211 && exported[i + 2] === 173 && exported[i + 3] === 255) ink++;
  }
  assert.ok(ink > 1000, 'the registered rig contributes its own opaque colour');
  assert.deepEqual(preview, exported, 'a scaled 4K preview matches the 1080p export on the same canvas');
  assert.deepEqual(render(1), exported, 'repeated frames are deterministic');
  assert.notDeepEqual(render(1, 'none'), exported, 'the independent diagram paints too');
  assert.notDeepEqual(render(1, shot.diagram, 'pull-out'), exported, 'the camera changes scene pixels');
  assert.equal(painted, 5, 'every frame invokes the registered drawing code');
});

test('pack sign-offs use neutral defaults without a mark and preserve explicit looks', () => {
  const rig = pack('motion-signoff');
  const shot = resolveShot({ character: rig.id, layout: 'signoff' }, 'Payoff', 'Closing');
  assert.deepEqual([shot.pose, shot.expression, shot.move, shot.mark], ['rest', 'neutral', 'idle', undefined]);
  const explicit = resolveShot({
    character: rig.id, layout: 'signoff', pose: 'wave', expression: 'happy', move: 'hop', mark: 'END',
  }, 'Payoff', 'Closing');
  assert.deepEqual([explicit.pose, explicit.expression, explicit.move, explicit.mark], ['wave', 'happy', 'hop', 'END']);
});

test('poster and sign-off defaults respect a pack with its own look vocabulary', () => {
  const rig = pack('motion-vocabulary', {
    poses: ['sit', 'reach'], moves: ['breathe'], expressions: { calm: { eyes: 'calm' } },
  });
  const shot = resolveShot({ character: rig.id, layout: 'signoff' }, 'Payoff', 'Closing');
  assert.deepEqual([shot.pose, shot.expression, shot.move], ['sit', 'calm', 'breathe']);
  const poster = resolvePoster({ character: rig.id, ensemble: [{ character: rig.id }] });
  for (const look of [poster.lead, ...poster.ensemble]) {
    assert.deepEqual(look, { character: rig.id, palette: 'plain', pose: 'sit', expression: 'calm' });
  }
  const familiar = pack('motion-poster');
  const defaults = resolvePoster({ character: familiar.id, ensemble: [{ character: familiar.id }] });
  assert.equal(defaults.lead.pose, 'rest');
  assert.equal(defaults.ensemble[0].pose, 'point');
  assert.equal(defaults.lead.expression, 'neutral');
  assert.equal(defaults.ensemble[0].expression, 'neutral');
});

test('the shared character drawing path forwards a pack palette and valid custom face', () => {
  const calls: CharacterState[] = [];
  const rig = pack('motion-drawing', { draw(_ctx, _cx, _by, _h, state) { calls.push(state); } });
  const ctx = {} as Ctx;
  const env: MotionEnv = { createCanvas() { throw new Error('This rig draws without an offscreen canvas'); } };
  drawCharacter(ctx, 100, 200, 144, {
    character: rig.id, palette: 'plain', pose: 'wave', expression: 'custom', move: 'hop', t: 0.4,
    custom: { eyes: 'wide' },
  }, env);
  assert.deepEqual(calls[0], {
    palette: 'plain', pose: 'wave', expression: 'custom', move: 'hop', t: 0.4, custom: { eyes: 'wide' },
  });
  drawCharacter(ctx, 100, 200, 144, {
    character: rig.id, palette: 'unknown', pose: 'rest', custom: { eyes: 'unknown' },
  }, env);
  assert.deepEqual(calls[1], { palette: undefined, pose: 'rest', custom: undefined });
});
