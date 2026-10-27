// Independent staging feature examples in landscape and portrait.
//   node scripts/staging-sheet.ts [out.png] [screen image]
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { loadImage } from '@napi-rs/canvas';
import { FORMATS, drawSceneFrame, resolveShot, shotTiming } from '../src/index.ts';
import type { Ctx, Format, ProjectShot } from '../src/index.ts';
import { nodeEnv, surface } from './node-env.ts';
import { FILM_IMAGES } from '../test/film-frames.ts';

const [outArg, imageArg] = process.argv.slice(2);
const OUT = resolve(outArg ?? 'outputs/staging-contact-sheet.png');
const IMAGE = 'museum-map';
const images = new Map(FILM_IMAGES);
if (imageArg) images.set(IMAGE, (await loadImage(imageArg)) as unknown as CanvasImageSource);
const env = nodeEnv(images);
const FPS = 30;
const BG = '#0f1316', INK = '#eef2f4', MUTED = '#8a98a4', GOLD = '#f5c518';

interface Beat {
  label: string;
  shot: ProjectShot;
  lt: number;
  durS: number;
  cueS: number | null;
}

const BEATS: Beat[] = [
  {label: 'grid diagram',
    shot: {mode: 'animation', character: 'lens', backdrop: 'work', pose: 'rest', move: 'idle', eyebrow: '', diagram: 'grid', boardLabels: ['TOPIC', 'One', 'Two']}, lt: 1.6, durS: 3.8, cueS: 0.4},
  {label: 'camera pan',
    shot: {mode: 'animation', character: 'lens', backdrop: 'work', pose: 'rest', move: 'idle', eyebrow: '', camera: 'pan', diagram: 'none'}, lt: 0.7, durS: 4.6, cueS: null},
  {label: 'camera push-in',
    shot: {mode: 'animation', character: 'lens', backdrop: 'work', pose: 'rest', move: 'idle', eyebrow: '', camera: 'push-in', diagram: 'none'}, lt: 3.05, durS: 3.1, cueS: 0.5},
  {label: 'camera pull-out',
    shot: {mode: 'animation', character: 'lens', backdrop: 'work', pose: 'rest', move: 'idle', eyebrow: '', camera: 'pull-out', diagram: 'none'}, lt: 0, durS: 3.4, cueS: 0.8},
  {label: 'screen image',
    shot: {mode: 'animation', character: 'lens', backdrop: 'work', pose: 'rest', move: 'idle', eyebrow: '', layout: 'screen', headline: 'Map detail', screen: {image: 'museum-map'}}, lt: 1.7, durS: 2.6, cueS: null},
  {label: 'accent badge',
    shot: {mode: 'animation', character: 'lens', backdrop: 'work', pose: 'rest', move: 'idle', eyebrow: '', layout: 'title', headline: 'Layout sample', badges: [{text: 'SAMPLE', tone: 'accent'}]}, lt: 1.9, durS: 2.8, cueS: null},
  {label: 'neutral badges',
    shot: {mode: 'animation', character: 'lens', backdrop: 'work', pose: 'rest', move: 'idle', eyebrow: '', layout: 'title', headline: 'Tone samples', badges: [{text: 'INK', tone: 'ink'}, {text: 'PAPER', tone: 'paper'}]}, lt: 2.1, durS: 3.3, cueS: null},
  {label: 'word mark',
    shot: {mode: 'animation', character: 'lens', backdrop: 'work', pose: 'rest', move: 'idle', eyebrow: '', layout: 'signoff', headline: 'End card', mark: 'DONE'}, lt: 1.8, durS: 2.9, cueS: null},
  {label: 'ensemble',
    shot: {mode: 'animation', character: 'lens', backdrop: 'work', pose: 'rest', move: 'idle', eyebrow: '', diagram: 'none', ensemble: [{character: 'lens', pose: 'wave'}]}, lt: 1.5, durS: 3.2, cueS: null},
  {label: 'acting mark',
    shot: {mode: 'animation', character: 'lens', backdrop: 'work', pose: 'rest', move: 'idle', eyebrow: '', diagram: 'none', bubble: 'question'}, lt: 1.2, durS: 3.6, cueS: 0.7},
];

function label(ctx: Ctx, s: string, x: number, y: number, size: number, color = MUTED): void {
  ctx.font = '600 ' + size + 'px "Inter"';
  ctx.fillStyle = color;
  ctx.textAlign = 'left';
  ctx.fillText(s, x, y);
}

function frame(ctx: Ctx, x: number, y: number, format: Format, w: number, b: Beat): number {
  const [W, H] = FORMATS[format], h = Math.round((w * H) / W);
  const shot = resolveShot(b.shot, 'Beat', 'Scene');
  ctx.save();
  ctx.beginPath();
  ctx.rect(x, y, w, h);
  ctx.clip();
  ctx.translate(x, y);
  ctx.scale(w / W, h / H);
  drawSceneFrame(ctx, 'animation', shot, b.lt, shotTiming(0, 12, b.cueS === null ? null : Math.round(b.cueS * FPS), FPS, Math.round(b.durS * FPS)), W, H, env);
  ctx.restore();
  return h;
}

const PAD = 36, GAP = 14, WIDE = 380, TALL = 196, PER_ROW = 5;
const wideH = Math.round((WIDE * 9) / 16), tallH = Math.round((TALL * 16) / 9);
const wideRows = Math.ceil(BEATS.length / PER_ROW), tallPerRow = 8, tallRows = Math.ceil(BEATS.length / tallPerRow);
const SW = PAD * 2 + PER_ROW * WIDE + (PER_ROW - 1) * GAP;
const SH = PAD + 90 + wideRows * (wideH + 40) + 50 + tallRows * (tallH + 40) + PAD;
const { canvas, ctx } = surface(SW, SH);
ctx.fillStyle = BG;
ctx.fillRect(0, 0, SW, SH);
label(ctx, 'motion-core · staging features', PAD, PAD + 28, 32, INK);
label(ctx, 'Each cell demonstrates a separate feature. Times are scene-local.', PAD, PAD + 58, 18);

let y = PAD + 96;
label(ctx, '16:9', PAD, y - 10, 18, GOLD);
BEATS.forEach((b, i) => {
  const c = i % PER_ROW, x = PAD + c * (WIDE + GAP);
  if (i && c === 0) y += wideH + 40;
  frame(ctx, x, y, '16:9', WIDE, b);
  label(ctx, `${b.label} · ${b.lt.toFixed(2)} s`, x, y + wideH + 22, 15, INK);
});
y += wideH + 90;
label(ctx, '9:16', PAD, y - 10, 18, GOLD);
BEATS.forEach((b, i) => {
  const c = i % tallPerRow, x = PAD + c * (TALL + GAP + 38);
  if (i && c === 0) y += tallH + 40;
  frame(ctx, x, y, '9:16', TALL, b);
  label(ctx, b.label, x, y + tallH + 22, 15, INK);
});

mkdirSync(dirname(OUT), { recursive: true });
writeFileSync(OUT, canvas.toBuffer('image/png'));
console.log('wrote ' + OUT);
