import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { parseArgs } from 'node:util';
import type { Character, CharacterState, Ctx } from '../src/types.ts';
import { LENS } from '../src/lens.ts';
import { nodeEnv, surface } from './node-env.ts';

const { values } = parseArgs({ options: { out: { type: 'string' } } });
const out = resolve(values.out ?? 'outputs/lens-sheet');
const env = nodeEnv();
const PAD = 28, LABEL = 88, TW = 160, TH = 182, GAP = 8;
const BG = '#10171e', TILE = '#1a211d', INK = '#eef2f4', MUTED = '#aab7c1';

function label(ctx: Ctx, text: string, x: number, y: number, size = 15, color = INK): void {
  ctx.fillStyle = color;
  ctx.font = `600 ${size}px "Inter"`;
  ctx.fillText(text, x, y);
}

function tile(ctx: Ctx, rig: Character, x: number, y: number, state: CharacterState): void {
  ctx.save();
  ctx.fillStyle = TILE;
  ctx.fillRect(x, y, TW, TH);
  ctx.beginPath(); ctx.rect(x, y, TW, TH); ctx.clip();
  rig.draw(ctx, x + TW / 2, y + TH - 20, rig.rows! * 3, state, env);
  ctx.restore();
}

function sheet(rig: Character): Buffer {
  const expressions = Object.keys(rig.expressions), poses = ['rest', 'point', 'wave'];
  const W = PAD * 2 + LABEL + expressions.length * (TW + GAP), H = 100 + 4 * (TH + 30) + 126;
  const { canvas, ctx } = surface(W, H);
  ctx.fillStyle = BG; ctx.fillRect(0, 0, W, H);
  label(ctx, `${rig.name} / model sheet`, PAD, 40, 27);
  label(ctx, '45 x 50 rig pixels / 1px outline / expressions, poses and a small-size check', PAD, 68, 16, MUTED);
  expressions.forEach((expression, k) => label(ctx, expression, PAD + LABEL + k * (TW + GAP), 96));
  poses.forEach((pose, row) => {
    const y = 108 + row * (TH + 30);
    label(ctx, pose, PAD, y + TH / 2);
    expressions.forEach((expression, k) => tile(ctx, rig, PAD + LABEL + k * (TW + GAP), y,
      { expression, pose, move: 'idle', t: 0.6 }));
  });
  const y = 108 + 3 * (TH + 30);
  label(ctx, 'poses', PAD, y + TH / 2);
  rig.poses.forEach((pose, k) => {
    const x = PAD + LABEL + k * (TW + GAP);
    tile(ctx, rig, x, y, { pose, expression: 'happy', move: 'idle', t: 0.6 });
    label(ctx, pose, x, y + TH + 20, 14, MUTED);
  });
  label(ctx, '1x / 2x', PAD, H - 42, 14, MUTED);
  rig.draw(ctx, PAD + LABEL + 30, H - 18, rig.rows!, { t: 0.6 }, env);
  rig.draw(ctx, PAD + LABEL + 110, H - 18, rig.rows! * 2, { expression: 'happy', t: 0.6 }, env);
  return canvas.toBuffer('image/png');
}

function strip(rig: Character): Buffer {
  const frames = 12, W = PAD * 2 + frames * (TW + GAP), H = TH + 120;
  const { canvas, ctx } = surface(W, H);
  ctx.fillStyle = BG; ctx.fillRect(0, 0, W, H);
  label(ctx, `${rig.name} / hop / 1.2 second loop / read frames left to right at 10 fps`, PAD, 35, 20);
  for (let k = 0; k < frames; k++) {
    const x = PAD + k * (TW + GAP), t = k / 10;
    tile(ctx, rig, x, 62, { expression: 'happy', pose: 'rest', move: 'hop', t });
    label(ctx, `${t.toFixed(1)} s`, x, H - 25, 14, MUTED);
  }
  return canvas.toBuffer('image/png');
}

mkdirSync(out, { recursive: true });
for (const rig of [LENS]) {
  for (const [suffix, png] of [['model-sheet', sheet(rig)], ['hop-strip', strip(rig)]] as const) {
    const path = resolve(out, `${rig.id}-${suffix}.png`);
    writeFileSync(path, png);
    console.log(path);
  }
}
