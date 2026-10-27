// Writes a contact sheet of duo shots, every interaction through its beat in 16:9, then the fist
// bump on the beat and the point after it in 9:16 and 1:1. The co-star defaults to the registry default.
//
//   node motion-core/scripts/duo-sheet.ts [out-dir] [costar-rig]
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { DEFAULT_CHARACTER, FORMATS, INTERACTIONS, characterOf, drawSceneFrame, resolveShot } from '../src/index.ts';
import type { Ctx, Format, Interaction, ShotTiming } from '../src/index.ts';
import { nodeEnv, surface } from './node-env.ts';

const OUT = resolve(process.argv[2] ?? 'outputs/duo');
const COSTAR = process.argv[3] ?? DEFAULT_CHARACTER;
const env = nodeEnv();
const CUE = 2;
const TIMING: ShotTiming = { firstT: 0.4, cueT: CUE };
const MOMENTS = [-1, -0.45, -0.15, 0, 0.1, 0.25, 1.2];
const FW = 360, GAP = 14, PAD = 36, LABEL_W = 130;
const BG = '#0f1316', INK = '#eef2f4', MUTED = '#8a98a4', GOLD = '#f5c518';

const shot = (interaction: Interaction) => resolveShot({
  mode: 'animation', expression: 'excited', pose: 'rest', move: 'idle', diagram: 'brief', headline: 'Read the gallery map',
  costar: { character: COSTAR, palette: characterOf(COSTAR).palettes[0], expression: 'happy', move: 'idle' },
  interaction,
}, 'Payoff', 'Launch');

function label(ctx: Ctx, s: string, x: number, y: number, size: number, color = MUTED, align: CanvasTextAlign = 'left'): void {
  ctx.font = '600 ' + size + 'px "Inter"';
  ctx.fillStyle = color;
  ctx.textAlign = align;
  ctx.fillText(s, x, y);
  ctx.textAlign = 'left';
}

function frame(ctx: Ctx, x: number, y: number, format: Format, w: number, kind: Interaction, lt: number): number {
  const [W, H] = FORMATS[format], h = Math.round((w * H) / W);
  ctx.save();
  ctx.beginPath();
  ctx.rect(x, y, w, h);
  ctx.clip();
  ctx.translate(x, y);
  ctx.scale(w / W, h / H);
  drawSceneFrame(ctx, 'animation', shot(kind), lt, TIMING, W, H, env);
  ctx.restore();
  return h;
}

const rowH = Math.round((FW * 9) / 16);
const tallW = 200, tallH = Math.round((tallW * 16) / 9), squareW = 330;
const W = PAD * 2 + LABEL_W + MOMENTS.length * (FW + GAP);
const H = PAD + 90 + INTERACTIONS.length * (rowH + GAP + 8) + 70 + tallH + 40 + PAD;
const { canvas, ctx } = surface(W, H);
ctx.fillStyle = BG;
ctx.fillRect(0, 0, W, H);
label(ctx, 'motion-core · duo shots', PAD, PAD + 28, 32, INK);
label(ctx, `Lead Character (excited) left, co-star ${COSTAR} right; the beat is the cue word at 2.0 s. Columns: time from the beat.`, PAD, PAD + 58, 18);

let y = PAD + 96;
MOMENTS.forEach((u, c) => label(ctx, (u === 0 ? 'beat' : (u > 0 ? '+' : '−') + Math.abs(u) + ' s'), PAD + LABEL_W + c * (FW + GAP) + FW / 2, y - 8, 18, u === 0 ? GOLD : INK, 'center'));
for (const kind of INTERACTIONS) {
  label(ctx, kind, PAD, y + rowH / 2 + 7, 20, INK);
  MOMENTS.forEach((u, c) => frame(ctx, PAD + LABEL_W + c * (FW + GAP), y, '16:9', FW, kind, CUE + u));
  y += rowH + GAP + 8;
}

y += 40;
label(ctx, '9:16 and 1:1 · fist bump on the beat, high five on the beat, point 1 s after', PAD, y, 18, GOLD);
y += 20;
let x = PAD;
for (const [format, kind, u] of [['9:16', 'fistbump', 0], ['9:16', 'highfive', 0], ['9:16', 'point', 1], ['1:1', 'fistbump', 0], ['1:1', 'highfive', 0], ['1:1', 'point', 1]] as const) {
  const w = format === '9:16' ? tallW : squareW;
  frame(ctx, x, y, format, w, kind, CUE + u);
  label(ctx, `${format} ${kind}`, x, y + (format === '9:16' ? tallH : squareW) + 24, 16, INK);
  x += w + GAP * 2;
}

mkdirSync(OUT, { recursive: true });
writeFileSync(resolve(OUT, 'duo-contact-sheet.png'), canvas.toBuffer('image/png'));
console.log('wrote ' + resolve(OUT, 'duo-contact-sheet.png'));
