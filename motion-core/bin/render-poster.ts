// Renders a video's thumbnail to a PNG with no browser: motion-core draws the poster on an
// @napi-rs/canvas surface, exactly as the Export sheet's preview draws it. No ffmpeg is involved.
//
//   node motion-core/bin/render-poster.ts <spec.json | -> <out.png>
//
// stdout carries one JSON line, {"event":"done"} with the output's size and the render time.

import { readFileSync, writeFileSync } from 'node:fs';
import { drawPoster, resolvePoster } from '../src/index.ts';
import type { ProjectPoster } from '../src/index.ts';
import { nodeEnv, surface } from '../scripts/node-env.ts';
import { loadPacks, packArgs } from '../scripts/packs.ts';

export interface PosterSpec {
  width: number;
  height: number;
  /** As stored, except that the host resolved every profile to its rig, palette and custom spec. */
  poster: ProjectPoster;
}

const isSize = (n: unknown): n is number => Number.isInteger(n) && (n as number) >= 16 && (n as number) <= 4096;

export function checkPosterSpec(spec: PosterSpec): PosterSpec {
  if (!isSize(spec?.width) || !isSize(spec.height) || !spec.poster || typeof spec.poster !== 'object') {
    throw new Error('invalid poster spec: ' + JSON.stringify(spec));
  }
  return spec;
}

/** The poster's PNG bytes, drawn on a fresh surface and env. */
export function posterPng(spec: PosterSpec): Buffer {
  const { width: W, height: H } = checkPosterSpec(spec);
  const { canvas, ctx } = surface(W, H);
  drawPoster(ctx, resolvePoster(spec.poster), W, H, nodeEnv());
  return canvas.toBuffer('image/png');
}

async function main(argv: string[]): Promise<void> {
  const { args, dir } = packArgs(argv);
  const [specPath, out] = args;
  if (!specPath || !out) throw new Error('usage: render-poster.ts <spec.json | -> <out.png>');
  const spec = JSON.parse(readFileSync(specPath === '-' ? 0 : specPath, 'utf8')) as PosterSpec;
  if (dir !== undefined) await loadPacks(dir);
  const t0 = performance.now();
  const png = posterPng(spec);
  writeFileSync(out, png);
  const done = { event: 'done', out, width: spec.width, height: spec.height, bytes: png.length, seconds: (performance.now() - t0) / 1000 };
  process.stdout.write(JSON.stringify(done) + '\n');
}

if (import.meta.main) {
  main(process.argv.slice(2)).catch((e: unknown) => {
    process.stderr.write((e instanceof Error ? e.message : String(e)) + '\n');
    process.exitCode = 1;
  });
}
