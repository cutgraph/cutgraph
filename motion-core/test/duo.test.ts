import { characterOf as defaultRigOf, DEFAULT_CHARACTER as defaultRigId } from '../src/index.ts';
const DEFAULT_RIG = defaultRigOf(defaultRigId);
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  FORMATS, INTERACTIONS, costarTime, drawCharacter, drawSceneFrame, duoBeat, duoLayout, layoutFor, resolveShot, shotTiming,
} from '../src/index.ts';
import type { Character, Ctx, Format, Shot } from '../src/index.ts';
import { diagramRow } from '../src/scene.ts';
import { nodeEnv } from '../scripts/node-env.ts';
import { render } from './helpers.ts';
import { SINGLE_FRAMES, singleFrameHash } from './single-frames.ts';
import { loadGolden } from './goldens.ts';

const env = nodeEnv();
const FORMAT_IDS = Object.keys(FORMATS) as Format[];

const golden = loadGolden<Record<string, string>>('single-frames');

test('every single-character golden frame hash matches', { skip: golden.skip }, () => {
  const GOLDEN = golden.value!;
  assert.equal(SINGLE_FRAMES.length, Object.keys(GOLDEN).length);
  for (const f of SINGLE_FRAMES) assert.equal(singleFrameHash(f, env), GOLDEN[f.name], f.name);
});

const duo = (interaction: string, extra: Record<string, unknown> = {}, costar: Record<string, unknown> = {}): Shot =>
  resolveShot({ mode: 'animation', pose: 'rest', expression: 'neutral', move: 'idle', diagram: 'route', backdrop: 'route',
    costar: { character: 'lens', pose: 'rest', expression: 'neutral', move: 'idle', ...costar }, interaction, ...extra }, 'Build', 'Launch');

test('resolveShot adds a co-star and its interaction only when the shot has one', () => {
  const single = resolveShot({ mode: 'animation', interaction: 'fistbump' }, 'Wrap', 'Bye');
  assert.ok(!('costar' in single) && !('interaction' in single), 'an interaction alone is ignored');
  const s = resolveShot({ mode: 'animation', costar: { character: 'lens' } }, 'Hook', 'Open');
  assert.deepEqual(s.costar, { character: 'lens', palette: 'periwinkle', pose: 'rest', expression: 'happy', move: 'idle' });
  assert.equal(s.interaction, 'none');
  assert.equal(s.expression, 'confused', 'the lead keeps the role look; the co-star does not copy it');
  const custom = { eyes: 'arc', mouth: 'crooked' };
  const t = resolveShot({ costar: { pose: 'moonwalk', move: 'wave', expression: 'grin', custom, palette: 'unknown-palette' }, interaction: 'hug' }, 'Beat', 'x');
  assert.deepEqual(t.costar, { character: 'lens', palette: 'periwinkle', pose: 'rest', expression: 'grin', move: 'wave', custom });
  assert.equal(t.interaction, 'none');
  for (const kind of INTERACTIONS) assert.equal(resolveShot({ costar: {}, interaction: kind }, 'Beat', 'x').interaction, kind);
});

test('a shot without a co-star draws exactly as before; a cameo never draws the co-star', () => {
  const [W, H] = [480, 270], timing = shotTiming(0, 12, 30, 30);
  const frame = (mode: 'animation' | 'cameo', shot: Shot) => render(W, H, (ctx) => {
    ctx.scale(0.25, 0.25);
    drawSceneFrame(ctx, mode, shot, 1.4, timing, 1920, 1080, env);
  }).hash;
  const single = resolveShot({ mode: 'animation' }, 'Build', 'Launch');
  assert.equal(frame('animation', { ...single, interaction: 'fistbump' }), frame('animation', single));
  assert.notEqual(frame('animation', duo('none')), frame('animation', single));
  const cameo = resolveShot({ mode: 'cameo' }, 'Wrap', 'Bye');
  assert.equal(frame('cameo', { ...cameo, costar: duo('face').costar, interaction: 'face' }), frame('cameo', cameo));
});

const box = (L: ReturnType<typeof duoLayout>, slot: 'lead' | 'costar', aspect: number) => {
  const s = L[slot];
  return { left: s.x - (aspect * s.h) / 2, right: s.x + (aspect * s.h) / 2, top: s.by - s.h, bottom: s.by };
};

test('duo layout per format: lead left, co-star right, clear of the headline, diagram and captions', () => {
  for (const format of FORMAT_IDS) {
    const [W, H] = FORMATS[format];
    const L = duoLayout(W, H, DEFAULT_RIG, DEFAULT_RIG, true), single = layoutFor(W, H);
    const lead = box(L, 'lead', 0.53), costar = box(L, 'costar', 0.53), row = diagramRow(L);
    assert.ok(L.center && !L.left, `${format}: headline and diagram centred`);
    assert.ok(lead.left > 0 && lead.right < W / 2 && costar.left > W / 2 && costar.right < W, `${format}: one each side, in frame`);
    assert.equal(L.lead.h, L.costar.h, `${format}: the same rig, the same size`);
    assert.deepEqual([L.lead.face, L.costar.face], [1, -1], `${format}: facing each other`);
    assert.equal(duoLayout(W, H, DEFAULT_RIG, DEFAULT_RIG, false).costar.face, 1);
    assert.ok(lead.top > row.Y + row.bh && lead.top > L.ty + L.fs * 1.12, `${format}: heads below the headline and diagram`);
    assert.ok(Math.abs(row.X + row.total / 2 - W / 2) < 1e-6, `${format}: the diagram sits centred between them`);
    assert.ok(lead.bottom < single.capY - single.capFs, `${format}: feet above the caption line`);
  }
  const [W, H] = FORMATS['16:9'], L = duoLayout(W, H, DEFAULT_RIG, DEFAULT_RIG, true);
  assert.deepEqual([L.lead.x, L.costar.x], [W / 3, (W * 2) / 3], '16:9: the thirds');
  const [pw, ph] = FORMATS['9:16'], P = duoLayout(pw, ph, DEFAULT_RIG, DEFAULT_RIG, true), single = layoutFor(pw, ph);
  assert.ok(P.lead.h < single.characterH && P.lead.h > ph * 0.3, '9:16: side by side, a little smaller than the single Character');
});

test('duo layout scales rigs by their declared height and fits any pair, wide or tall, in every format', () => {
  const big: Character = { ...DEFAULT_RIG, id: 'big', scale: 1.5, aspect: 1.2 };
  for (const format of FORMAT_IDS) {
    const [W, H] = FORMATS[format];
    const characters = duoLayout(W, H, DEFAULT_RIG, DEFAULT_RIG, true);
    for (const [lead, costar] of [[DEFAULT_RIG, big], [big, DEFAULT_RIG]] as const) {
      const L = duoLayout(W, H, lead, costar, true);
      const [small, tall] = lead === big ? [L.costar, L.lead] : [L.lead, L.costar];
      assert.ok(Math.abs(tall.h / small.h - 1.5) < 1e-9, `${format}: the big rig stands 1.5x the default rig`);
      assert.ok(tall.h <= characters.lead.h + 1e-9, `${format}: the taller one never outgrows the format`);
      const a = box(L, 'lead', lead.aspect ?? 0.62), b = box(L, 'costar', costar.aspect ?? 0.62);
      assert.ok(a.left >= W * 0.03 - 1e-6 && b.right <= W * 0.97 + 1e-6 && a.right < b.left, `${format}: both fit, apart`);
    }
  }
});

// Exact rig colours let the tests distinguish performer pixels from the backdrop and blends.
const PAL = ['#9fbae9', '#718cbf', '#d0ddfc', '#adcfd9', '#ffffff', '#eef2f4'].map((c) => [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16)));
const paletteAt = (data: Uint8ClampedArray, i: number): number =>
  PAL.findIndex(([r, g, b]) => data[i] === r && data[i + 1] === g && data[i + 2] === b);

function full(shot: Shot, lt: number, cueT = 2): { data: Uint8ClampedArray; ctx: Ctx } {
  const f = render(1920, 1080, (ctx) => drawSceneFrame(ctx, 'animation', shot, lt, { firstT: 0.4, cueT }, 1920, 1080, env));
  return { data: f.ctx.getImageData(0, 0, 1920, 1080).data, ctx: f.ctx };
}

test('facing mirrors the co-star exactly about its own centre, so the pixel rig stays crisp', () => {
  const L = duoLayout(1920, 1080, DEFAULT_RIG, DEFAULT_RIG, true), X0 = Math.round(L.costar.x), by = Math.round(L.costar.by);

  const front = full(duo('none'), 1.0).data, facing = full(duo('face'), 1.0).data;
  let rig = 0, same = 0;
  for (let y = Math.round(by - L.costar.h); y < by; y++) {
    for (let x = X0 - Math.round(L.costar.h / 2); x < X0 + Math.round(L.costar.h / 2); x++) {
      const i = (y * 1920 + x) * 4;
      if (paletteAt(front, i) < 0) continue;
      rig++;
      if (paletteAt(facing, (y * 1920 + (2 * X0 - 1 - x)) * 4) === paletteAt(front, i)) same++;
      else assert.fail(`a mirrored rig pixel differs at (${x}, ${y})`);
    }
  }
  assert.ok(rig > 5000, `the co-star has ${rig} rig pixels`);
  assert.ok(rig === same, `every other rig pixel lands, unblended, at its mirror image (${rig - same} differ)`);
});

test('fistbump and high five: wind-up, contact exactly on the beat, impact, recoil past rest, settle', () => {
  for (const kind of ['fistbump', 'highfive'] as const) {
    const at = (u: number) => duoBeat(kind, 5 + u, 5);
    assert.deepEqual(at(-1).lead, { approach: 0, squash: 0, posed: false });
    assert.ok(at(-0.35).lead.approach < 0 && at(-0.35).lead.posed, 'anticipation pulls back, arm ready');
    const lunge = [-0.3, -0.2, -0.1, -0.02].map((u) => at(u).lead.approach);
    assert.ok(lunge.every((v, i) => i === 0 || v > lunge[i - 1]), 'the lunge accelerates in');
    assert.equal(at(0).lead.approach, 1, 'hands touch on the beat');
    assert.equal(at(0).lead.squash, 1);
    assert.equal(at(0).burst, 0);
    assert.ok(at(0.2).lead.approach < 0 && at(0.2).lead.squash === 0, 'recoil overshoots past rest');
    assert.equal(at(0.5).burst, null);
    assert.ok(Math.abs(at(0.79).lead.approach) < 0.01 && at(0.9).lead.approach === 0 && !at(0.9).lead.posed, 'and settles');
    assert.deepEqual(at(0.1).lead, at(0.1).costar, 'both lean alike');
  }
  const point = (u: number) => duoBeat('point', 5 + u, 5);
  assert.equal(point(-0.5).pointer, null);
  assert.ok(point(-0.2).costar.posed && !point(-0.2).lead.posed, 'the co-star raises its arm before the beat');
  assert.equal(point(0)?.pointer?.p, 0);
  assert.equal(point(1).pointer?.p, 1);
  assert.equal(point(3.5).pointer, null, 'the pointer fades away');
  for (const kind of ['none', 'face'] as const) assert.deepEqual(duoBeat(kind, 5, 5).costar, { approach: 0, squash: 0, posed: false });
});

// The gap between the lead's and the co-star's bodies (their faces), in pixels.
function bodyGap(data: Uint8ClampedArray): number {
  let right = 0, left = 1920;
  for (let y = 500; y < 910; y++) {
    for (let x = 400; x < 1520; x++) {
      if (paletteAt(data, (y * 1920 + x) * 4) !== 3) continue;
      if (x < 960) right = Math.max(right, x);
      else left = Math.min(left, x);
    }
  }
  return left - right;
}

function flash(data: Uint8ClampedArray, cx: number, cy: number): number {
  let n = 0;
  for (let y = cy - 40; y < cy + 40; y++) for (let x = cx - 40; x < cx + 40; x++) if (paletteAt(data, (y * 1920 + x) * 4) === 4) n++;
  return n;
}

test('in pixels: the pair closes to contact on the cue, with a flash, then recoils and settles', () => {
  const shot = duo('fistbump'), cue = 2;
  const L = duoLayout(1920, 1080, DEFAULT_RIG, DEFAULT_RIG, true), my = Math.round(L.lead.by - 0.31 * L.lead.h);
  const gaps = Object.fromEntries([-1.5, -0.4, -0.1, 0, 0.25, 1.5].map((u) => {
    const { data } = full(shot, cue + u, cue);
    return [u, { gap: bodyGap(data), flash: flash(data, 960, my) }];
  }));
  assert.ok(gaps[-0.4].gap > gaps[-1.5].gap, `wind-up pulls apart ${JSON.stringify(gaps)}`);
  assert.ok(gaps[0].gap < gaps[-0.1].gap && gaps[0].gap < gaps[-1.5].gap / 2, 'closest on the beat');
  assert.ok(gaps[0.25].gap > gaps[-1.5].gap, 'recoil past rest');
  assert.equal(gaps[1.5].gap, gaps[-1.5].gap, 'back at rest');
  assert.ok(gaps[0].flash > 20, 'the impact flash is at the meeting point on the beat');
  for (const u of [-1.5, -0.4, -0.1, 0.25, 1.5]) assert.equal(gaps[u].flash, 0, `no flash at ${u}`);
});

test('the pair never twins: the co-star blinks and bobs on its own clock', () => {
  let bothBlink = 0, sameBob = 0, n = 0;
  for (let f = 0; f < 600; f++, n++) {
    const lt = f / 30, ct = costarTime('idle', lt);
    if (lt % 3.7 < 0.1 && ct % 3.7 < 0.1) bothBlink++;
    if (Math.floor(lt * 2) % 2 === Math.floor(ct * 2) % 2) sameBob++;
  }
  assert.equal(bothBlink, 0);
  assert.ok(sameBob / n < 0.8, `bobs in step ${sameBob} of ${n} frames`);
  assert.ok(costarTime('enter', 1) < 1, 'and enters a beat after the lead');
  const L = duoLayout(1920, 1080, DEFAULT_RIG, DEFAULT_RIG, false), lt = 3.75;
  const eyePixels = (ctx: Ctx, spot: typeof L.lead) => {
    const g = Math.floor(spot.h / DEFAULT_RIG.rows!);
    return [...ctx.getImageData(Math.round(spot.x) - 6 * g, Math.round(spot.by) - 29 * g, 12 * g, 4 * g).data];
  };
  const direct = (spot: typeof L.lead, t: number) => render(1920, 1080, (ctx) =>
    drawCharacter(ctx, spot.x, spot.by, spot.h, { expression: 'neutral', pose: 'rest', move: 'idle', t }, env)).ctx;
  const actual = full(duo('none'), lt).ctx, ct = costarTime('idle', lt);
  assert.deepEqual(eyePixels(actual, L.lead), eyePixels(direct(L.lead, lt), L.lead), 'the lead wears its own blink');
  assert.deepEqual(eyePixels(actual, L.costar), eyePixels(direct(L.costar, ct), L.costar), 'the co-star wears its own blink');
  assert.notDeepEqual(eyePixels(direct(L.lead, lt), L.lead), eyePixels(direct(L.lead, ct), L.lead), 'the two eye states differ');

});

test('duo frames are deterministic across envs, in every format and interaction', () => {
  for (const format of FORMAT_IDS) {
    const [W, H] = FORMATS[format];
    for (const kind of INTERACTIONS) {
      const shot = duo(kind, { expression: 'excited', move: 'enter' }, { expression: 'idea', move: 'hop' });
      for (const lt of [0.1, 1.9, 2, 2.1, 3.2]) {
        const once = (e = nodeEnv()) => render(W / 4, H / 4, (ctx) => {
          ctx.scale(0.25, 0.25);
          drawSceneFrame(ctx, 'animation', shot, lt, { firstT: 0.4, cueT: 2 }, W, H, e);
        }).hash;
        assert.equal(once(), once(env), `${format} ${kind} @${lt}`);
      }
    }
  }
});
