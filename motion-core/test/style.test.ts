import { characterOf as defaultRigOf, DEFAULT_CHARACTER as defaultRigId } from '../src/index.ts';
const DEFAULT_RIG = defaultRigOf(defaultRigId);
// The house style (CHARACTERS.md), measured on every registered rig: one pixel size per scene, a 1px outline
// around every part, a silhouette that reads on the dark backdrops, and the motion rules (a breathing idle,
// a squash on landing, follow-through on the way up and down).
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { CHARACTERS, drawCharacter } from '../src/index.ts';
import type { Character, CharacterState } from '../src/index.ts';
import { nodeEnv } from '../scripts/node-env.ts';
import { render } from './helpers.ts';

const env = nodeEnv();
const RIGS = Object.values(CHARACTERS);
const OUTLINE = 0x111214, BACKDROP = 0x1a211d;

/** One rig pixel per screen pixel, feet at (60, 80): each pixel's rgb, or -1 where it is not opaque. */
function pixels(rig: Character, st: CharacterState): { at: (x: number, y: number) => number; w: number; h: number } {
  const w = 120, h = 100;
  const f = render(w, h, (ctx) => drawCharacter(ctx, 60, 80, rig.rows!, { ...st, character: rig.id }, env));
  const { data } = f.ctx.getImageData(0, 0, w, h);
  const at = (x: number, y: number): number => {
    if (x < 0 || y < 0 || x >= w || y >= h) return -1;
    const i = (y * w + x) * 4;
    return data[i + 3] === 255 ? (data[i] << 16) | (data[i + 1] << 8) | data[i + 2] : -1;
  };
  return { at, w, h };
}

const N4 = [[1, 0], [-1, 0], [0, 1], [0, -1]] as const;

/** The opaque pixels on the silhouette's edge: each touches a pixel that is not the rig's. */
function edge(p: ReturnType<typeof pixels>): [number, number][] {
  const out: [number, number][] = [];
  for (let y = 0; y < p.h; y++) {
    for (let x = 0; x < p.w; x++) if (p.at(x, y) >= 0 && N4.some(([dx, dy]) => p.at(x + dx, y + dy) < 0)) out.push([x, y]);
  }
  return out;
}

function bounds(p: ReturnType<typeof pixels>): { top: number; bottom: number; width: number } {
  let top = p.h, bottom = -1, left = p.w, right = -1;
  for (let y = 0; y < p.h; y++) for (let x = 0; x < p.w; x++) {
    if (p.at(x, y) < 0) continue;
    top = Math.min(top, y); bottom = Math.max(bottom, y); left = Math.min(left, x); right = Math.max(right, x);
  }
  return { top, bottom, width: right - left + 1 };
}

function luminance(rgb: number): number {
  const lin = (c: number): number => { const s = c / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
  return 0.2126 * lin(rgb >> 16) + 0.7152 * lin((rgb >> 8) & 255) + 0.0722 * lin(rgb & 255);
}
const contrast = (a: number, b: number): number => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};

test('one pixel size per scene: every rig declares its rows, and a duo draws it at the default rig\'s pixel size', () => {
  for (const rig of RIGS) {
    assert.ok(rig.rows, `${rig.id} declares its rows`);
    assert.ok(Math.abs((rig.scale ?? 1) * DEFAULT_RIG.rows! - rig.rows!) < 1e-9, `${rig.id}: scale ${rig.scale} is its rows over the default rig's`);
  }
});

test('a 1px outline rings every part: the silhouette\'s edge is the outline colour in every pose', () => {
  for (const rig of RIGS) {
    for (const pose of rig.poses) {
      const p = pixels(rig, { pose, move: 'idle', t: 0.6 }), rim = edge(p);
      assert.ok(rim.length > 100, `${rig.id} ${pose} draws`);
      const off = rim.filter(([x, y]) => p.at(x, y) !== OUTLINE);
      assert.deepEqual(off, [], `${rig.id} ${pose}: edge pixels that are not the outline`);
    }
  }
});

// Just inside the outline, the silhouette's colours stand out from the darkest scene backdrop.
test('every rig reads on the dark backdrops: most of the silhouette just inside its outline contrasts 2:1', () => {
  for (const rig of RIGS) {
    const p = pixels(rig, { pose: rig.poses[0], move: 'idle', t: 0.6 });
    const inner = new Set<number>();
    for (const [x, y] of edge(p)) {
      for (const [dx, dy] of N4) {
        const c = p.at(x + dx, y + dy);
        if (c >= 0 && c !== OUTLINE) inner.add((y + dy) * p.w + x + dx);
      }
    }
    const colours = [...inner].map((i) => p.at(i % p.w, Math.floor(i / p.w)));
    const reads = colours.filter((c) => contrast(c, BACKDROP) >= 2).length / colours.length;
    assert.ok(reads >= 0.6, `${rig.id}: ${(reads * 100).toFixed(0)}% of its silhouette reads`);
  }
});

test('the idle breathes: the body moves while the feet stay planted', () => {
  for (const rig of RIGS) {
    const frames = Array.from({ length: 30 }, (_, k) => pixels(rig, { pose: rig.poses[0], move: 'idle', t: 0.2 + k * 0.1 }));
    const feet = new Set(frames.map((f) => bounds(f).bottom));
    assert.equal(feet.size, 1, `${rig.id}: the feet never leave the ground`);
    const looks = new Set(frames.map((f) => { let s = ''; for (let y = 0; y < f.h; y += 2) s += f.at(60, y) + ','; return s; }));
    assert.ok(looks.size > 1, `${rig.id}: the idle is alive`);
  }
});

// Across one hop: a grounded frame wider than rest (the squash keeps its volume), and at the same height off
// the ground, a different overall height rising and falling (arms trail the body).
test('a hop squashes on landing and the arms follow through', () => {
  for (const rig of RIGS) {
    const rest = bounds(pixels(rig, { pose: rig.poses[0], move: 'idle', t: 0.6 }));
    const hop = Array.from({ length: 120 }, (_, k) => ({ k, ...bounds(pixels(rig, { pose: rig.poses[0], move: 'hop', t: k / 60 })) }));
    assert.ok(hop.some((f) => f.bottom === rest.bottom && f.width > rest.width), `${rig.id}: squashes on the ground`);
    const air = hop.filter((f) => f.bottom < rest.bottom);
    const apex = Math.min(...air.map((f) => f.bottom));
    const phase = (f: typeof air[number]): 'up' | 'down' => {
      const next = hop[f.k + 1] ?? f, prev = hop[f.k - 1] ?? f;
      return next.bottom < f.bottom || (next.bottom === f.bottom && prev.bottom > f.bottom) ? 'up' : 'down';
    };
    const tall = (dir: 'up' | 'down', b: number): number[] => air.filter((f) => f.bottom === b && f.bottom > apex && phase(f) === dir).map((f) => f.bottom - f.top);
    const lags = [...new Set(air.map((f) => f.bottom))].some((b) => {
      const up = tall('up', b), down = tall('down', b);
      return up.length > 0 && down.length > 0 && Math.max(...down) > Math.min(...up);
    });
    assert.ok(lags, `${rig.id}: taller on the way down than on the way up`);
  }
});
