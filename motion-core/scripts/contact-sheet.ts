// Writes the visual evidence: a contact sheet of the rig and scene parts, and one full shot per format.
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import {
  DEFAULT_CHARACTER, DIAGRAMS, FORMATS, GRAPHICS, characterOf,
  drawCaptions, drawCharacter, drawGraphic, drawShot, timeline,
} from '../src/index.ts';
import type { Ctx, ExpressionId, Format, MotionEnv } from '../src/index.ts';
import { sampleProject } from './fixture.ts';
import { nodeEnv, surface } from './node-env.ts';

const OUT = resolve(process.argv[2] ?? 'outputs/contact-sheet');
const env = nodeEnv();
const DEFAULT_RIG = characterOf(DEFAULT_CHARACTER);
const project = sampleProject();
const TL = timeline(project);

const TILE = 200, GAP = 10, LABEL_W = 170, FRAME_W = 520, FRAME_H = 292, PAD = 40;
const BG = '#0f1316', TILE_BG = '#1a2027', INK = '#eef2f4', MUTED = '#8a98a4';

function label(ctx: Ctx, s: string, x: number, y: number, size: number, color = MUTED, align: CanvasTextAlign = 'left'): void {
  ctx.font = '600 ' + size + 'px "Inter"';
  ctx.fillStyle = color;
  ctx.textAlign = align;
  ctx.fillText(s, x, y);
  ctx.textAlign = 'left';
}

function characterTile(ctx: Ctx, x: number, y: number, st: Parameters<typeof drawCharacter>[4], e: MotionEnv): void {
  ctx.fillStyle = TILE_BG;
  ctx.fillRect(x, y, TILE, TILE);
  DEFAULT_RIG.draw(ctx, x + TILE / 2, y + TILE * 0.9, TILE * 0.82, st, e);
}

function inFrame(ctx: Ctx, x: number, y: number, w: number, h: number, W: number, H: number, paint: () => void): void {
  ctx.save();
  ctx.beginPath();
  ctx.rect(x, y, w, h);
  ctx.clip();
  ctx.translate(x, y);
  ctx.scale(w / W, h / H);
  paint();
  ctx.restore();
}

function shotTime(si: number): { lt: number; t: number } {
  const S = TL.scenes[si], lt = (S.cueT ?? 1) + 1.4;
  return { lt, t: S.start + lt };
}

function contactSheet(): Buffer {
  const expressions = Object.keys(DEFAULT_RIG.expressions) as ExpressionId[];
  const cols = Math.max(DEFAULT_RIG.poses.length, DEFAULT_RIG.moves.length);
  const W = PAD * 2 + LABEL_W + cols * (TILE + GAP);
  const gridH = 40 + expressions.length * (TILE + GAP);
  const movesH = 60 + TILE + 30;
  const framesH = (n: number): number => 60 + Math.ceil(n / 3) * (FRAME_H + 40);
  const H = PAD + 70 + gridH + movesH + framesH(DIAGRAMS.length) + framesH(GRAPHICS.length) + PAD;
  const { canvas, ctx } = surface(W, H);
  ctx.fillStyle = BG;
  ctx.fillRect(0, 0, W, H);
  label(ctx, 'motion-core · contact sheet', PAD, PAD + 30, 34, INK);
  label(ctx, 'Rig at t = 0.6 s · expressions × poses (idle), then moves, diagrams and graphics', PAD, PAD + 62, 20);

  let y = PAD + 110;
  label(ctx, 'EXPRESSION × POSE', PAD, y, 18, '#f5c518');
  DEFAULT_RIG.poses.forEach((p, c) => label(ctx, p, PAD + LABEL_W + c * (TILE + GAP) + TILE / 2, y, 20, INK, 'center'));
  y += 20;
  for (const ex of expressions) {
    label(ctx, ex, PAD, y + TILE / 2 + 8, 22, INK);
    DEFAULT_RIG.poses.forEach((pose, c) => characterTile(ctx, PAD + LABEL_W + c * (TILE + GAP), y, { pose, expression: ex, move: 'idle', t: 0.6 }, env));
    y += TILE + GAP;
  }

  y += 40;
  label(ctx, 'MOVES (neutral · rest, t = 0.3 s)', PAD, y, 18, '#f5c518');
  y += 20;
  DEFAULT_RIG.moves.forEach((move, c) => {
    const x = PAD + LABEL_W + c * (TILE + GAP);
    characterTile(ctx, x, y, { pose: 'rest', expression: 'neutral', move, t: 0.3 }, env);
    label(ctx, move, x + TILE / 2, y + TILE + 26, 20, INK, 'center');
  });
  y += TILE + 30;

  const frames = (title: string, items: readonly string[], paint: (item: string, i: number) => void): void => {
    y += 40;
    label(ctx, title, PAD, y, 18, '#f5c518');
    y += 20;
    items.forEach((item, i) => {
      const x = PAD + (i % 3) * (FRAME_W + GAP * 3), fy = y + Math.floor(i / 3) * (FRAME_H + 40);
      inFrame(ctx, x, fy, FRAME_W, FRAME_H, 1920, 1080, () => paint(item, i));
      label(ctx, item, x, fy + FRAME_H + 26, 20, INK);
    });
    y += Math.ceil(items.length / 3) * (FRAME_H + 40);
  };

  // A diagram without its own sample scene borrows a diagram scene's timing.
  const diagramScene = project.scenes.findIndex((sc) => sc.shot?.diagram);
  frames('DIAGRAMS (sample scenes, 16:9, 1.4 s after the cue word)', DIAGRAMS, (d) => {
    const own = project.scenes.findIndex((sc) => sc.shot?.diagram === d);
    const si = own >= 0 ? own : diagramScene;
    const sc = project.scenes[si], { lt } = shotTime(si);
    if (sc.shot) drawShot(ctx, { ...sc.shot, diagram: d }, lt, TL.scenes[si], 1920, 1080, env);
  });

  const GRAPHIC_TEXT: Record<string, string> = {
    'lower-third': 'Your name · Studio', title: 'A simple pasta supper', callout: 'Chop the garlic first',
    steps: 'Boil · Toss · Serve', 'character-cameo': '',
  };
  frames('GRAPHICS (fully in, t = 1.6 s)', GRAPHICS, (item) => {
    ctx.fillStyle = '#20262c';
    ctx.fillRect(0, 0, 1920, 1080);
    drawGraphic(ctx, { item, text: GRAPHIC_TEXT[item], start: 0 }, 1, 1920, 1080, 1.6, env);
  });

  return canvas.toBuffer('image/png');
}

function shotFrame(format: Format, si: number): Buffer {
  const [W, H] = FORMATS[format], S = TL.scenes[si], sc = project.scenes[si], { lt, t } = shotTime(si);
  const { canvas, ctx } = surface(W, H);
  if (sc.shot) drawShot(ctx, sc.shot, lt, S, W, H, env);
  drawCaptions(ctx, S.words, t, W, H, env);
  return canvas.toBuffer('image/png');
}

mkdirSync(OUT, { recursive: true });
writeFileSync(resolve(OUT, 'contact-sheet.png'), contactSheet());
const SHOT_SCENE = 3;
for (const [format, name] of [['16:9', 'shot-16x9'], ['9:16', 'shot-9x16'], ['1:1', 'shot-1x1']] as const) {
  writeFileSync(resolve(OUT, name + '.png'), shotFrame(format, SHOT_SCENE));
}
console.log('wrote ' + OUT);
