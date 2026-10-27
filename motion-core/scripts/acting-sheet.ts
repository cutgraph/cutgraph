// Independent acting marks and performer placement, one row per feature.
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { FORMATS, drawSceneFrame, resolveShot } from '../src/index.ts';
import type { Ctx, Format, ProjectShot, ShotTiming } from '../src/index.ts';
import { nodeEnv, surface } from './node-env.ts';

const OUT = resolve(process.argv[2] ?? 'outputs/acting-sheet');
const env = nodeEnv();
const BG = '#0f1316', INK = '#eef2f4', MUTED = '#8a98a4', GOLD = '#f5c518';

interface Beat {
  title: string;
  shot: ProjectShot;
  timing: ShotTiming;
  moments: [number, string][];
}

const BEATS: Beat[] = [
  {title: 'Question mark',
    shot: {mode: 'animation', character: 'lens', pose: 'rest', expression: 'neutral', move: 'idle', diagram: 'none', hideHeadline: true, bubble: 'question'},
    timing: {firstT: 0.2, cueT: 0.6},
    moments: [[0.2, 'start'], [0.8, 'early'], [1.5, 'middle'], [2.4, 'settled']]},
  {title: 'Speech bubble',
    shot: {mode: 'animation', character: 'lens', pose: 'rest', expression: 'neutral', move: 'idle', diagram: 'none', hideHeadline: true, costar: {character: 'lens', bubble: 'say', pose: 'point'}},
    timing: {firstT: 0.2, cueT: 0.4},
    moments: [[0.2, 'start'], [0.8, 'early'], [1.5, 'middle'], [2.4, 'settled']]},
  {title: 'Ensemble placement',
    shot: {mode: 'animation', character: 'lens', pose: 'rest', expression: 'neutral', move: 'idle', diagram: 'none', hideHeadline: true, ensemble: [{character: 'lens', pose: 'wave'}, {character: 'lens', pose: 'point'}]},
    timing: {firstT: 0.2, cueT: 0.8},
    moments: [[0.2, 'start'], [0.8, 'early'], [1.5, 'middle'], [2.4, 'settled']]},
  {title: 'Entrance',
    shot: {mode: 'animation', character: 'lens', pose: 'rest', expression: 'neutral', move: 'idle', diagram: 'none', hideHeadline: true, costar: {character: 'lens', move: 'enter', pose: 'wave'}},
    timing: {firstT: 0.2, cueT: 0.5},
    moments: [[0.2, 'start'], [0.8, 'early'], [1.5, 'middle'], [2.4, 'settled']]},
  {title: 'Thinking dots',
    shot: {mode: 'animation', character: 'lens', pose: 'rest', expression: 'neutral', move: 'idle', diagram: 'none', hideHeadline: true, bubble: 'think'},
    timing: {firstT: 0.2, cueT: 0.3},
    moments: [[0.2, 'start'], [0.8, 'early'], [1.5, 'middle'], [2.4, 'settled']]},
  {title: 'Exclamation mark',
    shot: {mode: 'animation', character: 'lens', pose: 'rest', expression: 'neutral', move: 'idle', diagram: 'none', hideHeadline: true, bubble: 'exclaim'},
    timing: {firstT: 0.2, cueT: 0.9},
    moments: [[0.2, 'start'], [0.8, 'early'], [1.5, 'middle'], [2.4, 'settled']]},
  {title: 'Paired marks',
    shot: {mode: 'animation', character: 'lens', pose: 'rest', expression: 'neutral', move: 'idle', diagram: 'none', hideHeadline: true, bubble: 'exclaims'},
    timing: {firstT: 0.2, cueT: 0.7},
    moments: [[0.2, 'start'], [0.8, 'early'], [1.5, 'middle'], [2.4, 'settled']]},
];

function label(ctx: Ctx, s: string, x: number, y: number, size: number, color = MUTED): void {
  ctx.font = '600 ' + size + 'px "Inter"';
  ctx.fillStyle = color;
  ctx.fillText(s, x, y);
}

function sheet(format: Format, fw: number): string {
  const [W, H] = FORMATS[format], fh = Math.round((fw * H) / W), GAP = 14, PAD = 36, HEAD = 34;
  const width = PAD * 2 + 4 * fw + 3 * GAP, rowH = HEAD + fh + 30;
  const { canvas, ctx } = surface(width, PAD + 70 + BEATS.length * rowH + PAD);
  ctx.fillStyle = BG;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  label(ctx, `motion-core · acting · ${format}`, PAD, PAD + 26, 28, INK);
  label(ctx, 'Bubbles pop in on the cue word (or once a walker arrives); times are seconds into the scene.', PAD, PAD + 54, 16);
  let y = PAD + 70;
  for (const beat of BEATS) {
    label(ctx, beat.title, PAD, y + 24, 19, GOLD);
    const shot = resolveShot(beat.shot, 'Beat', beat.shot.headline ?? '');
    beat.moments.forEach(([lt, what], i) => {
      const x = PAD + i * (fw + GAP);
      ctx.save();
      ctx.beginPath();
      ctx.rect(x, y + HEAD, fw, fh);
      ctx.clip();
      ctx.translate(x, y + HEAD);
      ctx.scale(fw / W, fh / H);
      drawSceneFrame(ctx, 'animation', shot, lt, beat.timing, W, H, env);
      ctx.restore();
      label(ctx, `${lt.toFixed(2)} s · ${what}`, x, y + HEAD + fh + 20, 14, INK);
    });
    y += rowH;
  }
  mkdirSync(OUT, { recursive: true });
  const file = resolve(OUT, `acting-sheet-${format.replace(':', 'x')}.png`);
  writeFileSync(file, canvas.toBuffer('image/png'));
  return file;
}

for (const [format, fw] of [['16:9', 420], ['9:16', 220]] as const) console.log('wrote ' + sheet(format, fw));
