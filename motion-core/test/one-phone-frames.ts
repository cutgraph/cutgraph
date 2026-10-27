// One phone's frames (a host image, then one still loading; mid-spring, the slam's speed lines, at rest; each
// format), recorded before phones 2 and 3 were added: a shot with no `more` must draw exactly as it did.
import { createHash } from 'node:crypto';
import { createCanvas } from '@napi-rs/canvas';
import { FORMATS, drawSceneFrame, resolveShot, shotTiming } from '../src/index.ts';
import type { MotionEnv, StoredStaging } from '../src/index.ts';
import { surface } from '../scripts/node-env.ts';

export function onePhoneHash(env: MotionEnv, more?: unknown): string {
  const art = createCanvas(118, 256), a = art.getContext('2d');
  a.fillStyle = '#ff00ff'; a.fillRect(0, 0, 118, 256); a.fillStyle = '#00c8ff'; a.fillRect(0, 0, 118, 40);
  const e: MotionEnv = { ...env, image: (src) => (src === 'lib/one.png' ? (art as unknown as CanvasImageSource) : null) };
  const h = createHash('sha256');
  for (const image of ['lib/one.png', 'lib/loading.png']) for (const [W, H] of Object.values(FORMATS)) for (const t of [0.15, 0.35, 1.2]) {
    const { ctx } = surface(W / 4, H / 4);
    ctx.scale(0.25, 0.25);
    const screen = { asset: 'a1', image, ...(more !== undefined && { more }) } as StoredStaging['screen'];
    const shot = resolveShot({ mode: 'animation', layout: 'screen', backdrop: 'vision', headline: ' ', eyebrow: '', screen }, 'Beat', ' ');
    drawSceneFrame(ctx, 'animation', shot, t, shotTiming(0, null, null, 30, 60), W, H, e);
    h.update(ctx.getImageData(0, 0, W / 4, H / 4).data);
  }
  return h.digest('hex');
}
