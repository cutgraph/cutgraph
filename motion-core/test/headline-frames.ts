// Text-bearing frames in every format, fingerprinted with explicitly registered fonts.
import { createHash } from 'node:crypto';
import type { Format, ProjectShot } from '../src/index.ts';
import * as motion from '../src/index.ts';
import { nodeEnv, surface } from '../scripts/node-env.ts';

type Api = Pick<typeof motion, 'FORMATS' | 'drawSceneFrame' | 'resolveShot'>;

export interface HeadlineFrame {
  name: string;
  format: Format;
  shot: ProjectShot;
  lt: number;
}

const LOOKS: ProjectShot[] = [
  { diagram: 'scatter', pose: 'shrug', expression: 'confused', move: 'idle', backdrop: 'chaos' },
  { diagram: 'route', pose: 'point', expression: 'happy', move: 'enter', backdrop: 'route', headline: 'A much longer headline that wraps onto a second line' },
  { diagram: 'glow', pose: 'cheer', expression: 'excited', move: 'celebrate', backdrop: 'result' },
  { diagram: 'route', pose: 'rest', move: 'idle', costar: { character: 'lens', expression: 'idea' }, interaction: 'face' },
];

export const HEADLINE_FRAMES: HeadlineFrame[] = (['16:9', '9:16', '1:1'] as Format[]).flatMap((format) =>
  LOOKS.flatMap((look, i) => [0.6, 2.5].map((lt): HeadlineFrame => ({
    name: `${format} ${look.costar ? 'duo' : look.diagram} ${i} @${lt}`, format, shot: { mode: 'animation', ...look }, lt,
  }))));

/** sha256 of the frame's RGBA pixels at half the format's size; the headline lands at 0.4 s. */
export function headlineFrameHash(f: HeadlineFrame, api: Api = motion, extra: ProjectShot = {}): string {
  const [W, H] = api.FORMATS[f.format];
  const { ctx } = surface(W / 2, H / 2);
  ctx.scale(0.5, 0.5);
  const shot = api.resolveShot({ ...f.shot, ...extra }, 'Build', 'Explore the garden');
  api.drawSceneFrame(ctx, 'animation', shot, f.lt, { firstT: 0.4, cueT: 1 }, W, H, nodeEnv());
  return createHash('sha256').update(ctx.getImageData(0, 0, W / 2, H / 2).data).digest('hex').slice(0, 16);
}
