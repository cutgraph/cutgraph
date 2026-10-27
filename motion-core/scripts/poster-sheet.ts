// A single Lens performer with a headline, at full and small preview sizes.
//   node motion-core/scripts/poster-sheet.ts [out-dir]
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { DEFAULT_CHARACTER, POSTER_FORMATS, characterOf, drawPoster, resolvePoster } from '../src/index.ts';
import type { PosterFormat, ProjectPoster } from '../src/index.ts';
import { nodeEnv, surface } from './node-env.ts';

const OUT = resolve(process.argv[2] ?? 'outputs/poster-sheet');

const CHARACTER = { character: DEFAULT_CHARACTER, palette: characterOf(DEFAULT_CHARACTER).palettes[0] };

const SAMPLE: ProjectPoster = {
  headline: 'Museum Visit',
  ...CHARACTER, pose: 'point', expression: 'curious',
  ensemble: [], backdrop: 'work',
};

const env = nodeEnv();
mkdirSync(OUT, { recursive: true });
for (const format of Object.keys(POSTER_FORMATS) as PosterFormat[]) {
  const [W, H] = POSTER_FORMATS[format], slug = format.replace(':', 'x');
  const big = surface(W, H);
  drawPoster(big.ctx, resolvePoster(SAMPLE), W, H, env);
  writeFileSync(resolve(OUT, `museum-visit-poster-${slug}.png`), big.canvas.toBuffer('image/png'));
  const [w, h] = W > H ? [168, 94] : [94, 168];
  const small = surface(w, h);
  small.ctx.imageSmoothingQuality = 'high';
  small.ctx.drawImage(big.canvas as unknown as CanvasImageSource, 0, 0, w, h);
  writeFileSync(resolve(OUT, `museum-visit-poster-${slug}-${w}x${h}.png`), small.canvas.toBuffer('image/png'));
}
console.log('wrote', OUT);
