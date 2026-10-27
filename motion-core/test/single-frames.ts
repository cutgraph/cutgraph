// A fixed set of one-character scene frames (animation and cameo, every format) for proving that the
// duo work left the single-character path byte-identical. Text-free on purpose: the headline lands
// after the last sampled moment and no diagram with lettering is used, so the hashes depend only on
// @napi-rs/canvas, never on the machine's fonts.
import { createHash } from 'node:crypto';
import { FORMATS, drawSceneFrame, resolveShot, shotTiming } from '../src/index.ts';
import type { Format, MotionEnv, ShotMode } from '../src/index.ts';
import { surface } from '../scripts/node-env.ts';

export interface SingleFrame {
  name: string;
  format: Format;
  mode: ShotMode;
  shot: Record<string, string>;
  role: string;
  lt: number;
}

const LOOKS: Record<string, string>[] = [
  { diagram: 'scatter', pose: 'shrug', expression: 'confused', move: 'idle', backdrop: 'chaos' },
  { diagram: 'route', pose: 'point', expression: 'happy', move: 'enter', backdrop: 'route' },
  { diagram: 'checks', pose: 'rest', expression: 'curious', move: 'nod', backdrop: 'proof' },
  { diagram: 'glow', pose: 'cheer', expression: 'excited', move: 'celebrate', backdrop: 'result' },
];
const CAMEOS: Record<string, string>[] = [
  { pose: 'wave', expression: 'happy', move: 'wave' },
  { pose: 'think', expression: 'idea', move: 'hop' },
];

export const SINGLE_FRAMES: SingleFrame[] = (Object.keys(FORMATS) as Format[]).flatMap((format) => [
  ...LOOKS.flatMap((shot, i) => [0.3, 1.2, 2.5].map((lt): SingleFrame => ({
    name: `${format} animation ${shot.diagram} @${lt}`, format, mode: 'animation', shot, role: 'Build', lt: lt + i * 0.05,
  }))),
  ...CAMEOS.flatMap((shot) => [0, 0.2, 0.6, 1.5].map((lt): SingleFrame => ({
    name: `${format} cameo ${shot.move} @${lt}`, format, mode: 'cameo', shot, role: 'Wrap', lt,
  }))),
]);

// The headline would land at 50 s; the diagram lands on the cue at 1 s. A cameo is sampled as its shot
// names no cue word, so it springs in at the scene start exactly as it did before cameos took a cue word.
const TIMING = shotTiming(0, 1500, 30, 30);
const CAMEO_TIMING = shotTiming(0, 1500, null, 30);

/** sha256 of the frame's RGBA pixels, drawn at a quarter of the format's size. */
export function singleFrameHash(f: SingleFrame, env: MotionEnv): string {
  const [W, H] = FORMATS[f.format];
  const { ctx } = surface(W / 4, H / 4);
  ctx.scale(0.25, 0.25);
  drawSceneFrame(ctx, f.mode, resolveShot({ mode: f.mode, ...f.shot }, f.role, 'Title'), f.lt, f.mode === 'cameo' ? CAMEO_TIMING : TIMING, W, H, env);
  return createHash('sha256').update(ctx.getImageData(0, 0, W / 4, H / 4).data).digest('hex').slice(0, 16);
}
