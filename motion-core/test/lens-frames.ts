import { createHash } from 'node:crypto';
import { FORMATS, drawSceneFrame, resolveShot, shotTiming } from '../src/index.ts';
import type { Format, MotionEnv, ProjectShot } from '../src/index.ts';
import { surface } from '../scripts/node-env.ts';

export interface LensFrame {
  name: string;
  format: Format;
  shot: ProjectShot;
  t: number;
}

const LOOKS: { name: string; shot: ProjectShot; t: number }[] = [
  { name: 'neutral', shot: { expression: 'neutral', pose: 'rest' }, t: 0.6 },
  { name: 'hop', shot: { expression: 'happy', move: 'hop' }, t: 0.5 },
  { name: 'flex', shot: { expression: 'proud', move: 'sway' }, t: 0.6 },
  { name: 'cameo', shot: { mode: 'cameo', cueWord: 1, expression: 'idea', move: 'wave' }, t: 1.4 },
  { name: 'entrance', shot: { costar: { character: 'lens', move: 'enter', bubble: 'say' }, interaction: 'face' }, t: 0.5 },
  { name: 'ensemble', shot: { bubble: 'think', ensemble: [{ character: 'lens', bubble: 'question' }, { character: 'lens', bubble: 'exclaims' }] }, t: 1.5 },
];

export const LENS_FRAMES: LensFrame[] = (Object.keys(FORMATS) as Format[]).flatMap((format) =>
  LOOKS.map(({ name, shot, t }) => ({
    name: `lens ${format} ${name}`, format, t,
    shot: { mode: 'animation', character: 'lens', backdrop: 'black', diagram: 'none', hideHeadline: true, ...shot },
  })));

export function lensFrameHash(f: LensFrame, env: MotionEnv): string {
  const [W, H] = FORMATS[f.format];
  const { ctx } = surface(W / 4, H / 4);
  ctx.scale(0.25, 0.25);
  const shot = resolveShot(f.shot, 'Beat', '');
  drawSceneFrame(ctx, f.shot.mode ?? 'animation', shot, f.t, shotTiming(0, 12, 30, 30, 180), W, H, env);
  return createHash('sha256').update(ctx.getImageData(0, 0, W / 4, H / 4).data).digest('hex').slice(0, 16);
}
