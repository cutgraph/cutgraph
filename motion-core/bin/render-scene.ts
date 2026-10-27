// Renders one project scene's animated picture to a video file with no browser: motion-core draws every
// frame on an @napi-rs/canvas surface and the raw RGBA frames stream into an ffmpeg child.
//
//   node motion-core/bin/render-scene.ts <spec.json | -> <out.mp4 | out.mov>
//
// An opaque scene (animation) uses the shared H.264 ladder; an alpha scene
// (cameo) encodes to ProRes 4444 with alpha. stdout carries one JSON line per event: {"event":"ffmpeg"}
// with the full command before each encode (the caller logs it to run.log) and a final {"event":"done"}
// with the measured throughput. A `screen` shot's images (each phone's `image`, a file the host resolved
// from its library) are decoded once before the first frame.

import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { isAbsolute } from 'node:path';
import { loadImage } from '@napi-rs/canvas';
import { drawSceneFrame, phonesOf, resolveShot, shotMode, shotTiming } from '../src/index.ts';
import type { PartSpec, ProjectShot } from '../src/index.ts';
import { nodeEnv, surface } from '../scripts/node-env.ts';
import { loadPacks, packArgs } from '../scripts/packs.ts';
import { encoderArgs, probeEncoders, runH264, TEST_ENCODERS_ENV } from '../scripts/encoder-ladder.ts';
import type { H264Encoder } from '../scripts/encoder-ladder.ts';

export interface SceneSpec {
  width: number;
  height: number;
  fps: number;
  durF: number;
  mode: string;
  /** As stored, except that the host resolved `character` from a profile id to its rig's registry id. */
  shot: ProjectShot | null;
  role: string;
  title: string;
  startF: number;
  firstWordF: number | null;
  cueWordF: number | null;
  alpha: boolean;
  /** The spec of a custom (host-defined) `shot.expression`, resolved by the host from its cast. */
  custom?: PartSpec;
}

export type Encoder = H264Encoder | 'prores_ks';

export interface RenderResult {
  event: 'done';
  out: string;
  encoder: Encoder;
  frames: number;
  seconds: number;
  fps: number;
}

const isInt = (n: unknown, min: number): n is number => Number.isInteger(n) && (n as number) >= min;
const isFrame = (n: unknown): boolean => n === null || isInt(n, 0);

export function checkSpec(spec: SceneSpec): SceneSpec {
  const ok = isInt(spec.width, 2) && isInt(spec.height, 2) && spec.width % 2 === 0 && spec.height % 2 === 0
    && isInt(spec.fps, 1) && isInt(spec.durF, 1) && isInt(spec.startF, 0)
    && isFrame(spec.firstWordF) && isFrame(spec.cueWordF) && typeof spec.alpha === 'boolean';
  if (!ok) throw new Error('invalid scene spec: ' + JSON.stringify(spec));
  if (shotMode({ mode: spec.mode }) === 'footage') throw new Error('a footage scene has no picture to render');
  return spec;
}

/** The images a scene draws, decoded: each of its `screen` shot's phones' that the host resolved. */
export async function sceneImages(spec: SceneSpec): Promise<Map<string, CanvasImageSource>> {
  const srcs = new Set(phonesOf(resolveShot(spec.shot, spec.role, spec.title)).flatMap((p) => p.image ?? []));
  return new Map(await Promise.all([...srcs].map(async (src) => [src, (await loadImage(src)) as unknown as CanvasImageSource] as const)));
}

/**
 * Frame k of the scene for k in [0, durF), as straight-alpha RGBA bytes. Every frame is drawn on one
 * reused surface and env at scene-local time k / fps, exactly as the preview draws it, with `images`
 * from sceneImages.
 */
export function* sceneFrames(spec: SceneSpec, images?: ReadonlyMap<string, CanvasImageSource>): Generator<Buffer> {
  const { width: W, height: H, fps } = spec;
  const mode = shotMode({ mode: spec.mode });
  const shot = { ...resolveShot(spec.shot, spec.role, spec.title), ...(spec.custom && { custom: spec.custom }) };
  const timing = shotTiming(spec.startF, spec.firstWordF, spec.cueWordF, fps, spec.durF);
  const env = nodeEnv(images);
  const { ctx } = surface(W, H);
  for (let k = 0; k < spec.durF; k++) {
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, W, H);
    ctx.save();
    drawSceneFrame(ctx, mode, shot, k / fps, timing, W, H, env);
    ctx.restore();
    const { data } = ctx.getImageData(0, 0, W, H);
    yield Buffer.from(data.buffer, data.byteOffset, data.byteLength);
  }
}

// Tagged BT.709 so the frames composite onto footage without a matrix shift.
const BT709 = ['-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709'];

export function ffmpegArgs(spec: SceneSpec, out: string, encoder: Encoder): string[] {
  const input = [
    '-y', '-hide_banner', '-loglevel', 'error',
    '-f', 'rawvideo', '-pix_fmt', 'rgba', '-s', `${spec.width}x${spec.height}`, '-r', String(spec.fps), '-i', '-',
  ];
  if (encoder === 'prores_ks') {
    return [...input, '-vf', 'scale=out_color_matrix=bt709:out_range=tv,format=yuva444p10le',
      '-c:v', 'prores_ks', '-profile:v', '4444', '-pix_fmt', 'yuva444p10le', ...BT709, out];
  }
  const codec = encoderArgs('scene', encoder);
  return [...input, '-vf', 'scale=out_color_matrix=bt709:out_range=tv,format=yuv420p',
    ...codec, '-pix_fmt', 'yuv420p', ...BT709, '-movflags', '+faststart', out];
}

/** Streams frames into one ffmpeg child, waiting on `drain` whenever the pipe is full. */
async function encode(frames: Iterable<Buffer>, args: string[], ffmpeg: string): Promise<{ code: number | null; stderr: string }> {
  const child = spawn(ffmpeg, args, { stdio: ['pipe', 'ignore', 'pipe'], windowsHide: true });
  let stderr = '';
  child.stderr.setEncoding('utf8').on('data', (s: string) => { stderr += s; });
  let exited = false;
  const exit = new Promise<number | null>((resolve) => {
    child.on('error', (e) => { stderr += e.message; exited = true; resolve(null); });
    child.on('close', (code) => { exited = true; resolve(code); });
  });
  // A dead ffmpeg surfaces as EPIPE here; its exit code and stderr carry the real failure.
  child.stdin.on('error', () => {});
  for (const frame of frames) {
    if (exited) break;
    if (!child.stdin.write(frame)) {
      await Promise.race([new Promise((resolve) => child.stdin.once('drain', resolve)), exit]);
    }
  }
  child.stdin.end();
  return { code: await exit, stderr };
}

export async function renderScene(
  spec: SceneSpec, out: string, emit: (event: object) => void = () => {}, ffmpeg = process.env.CUTGRAPH_FFMPEG ?? '',
): Promise<RenderResult> {
  checkSpec(spec);
  if (!isAbsolute(ffmpeg)) throw new Error('CUTGRAPH_FFMPEG must name an absolute executable path');
  const images = await sceneImages(spec);
  const attempt = async (encoder: Encoder) => {
    const args = ffmpegArgs(spec, out, encoder);
    emit({ event: 'ffmpeg', cmd: [ffmpeg, ...args] });
    const t0 = performance.now();
    const { code, stderr } = await encode(sceneFrames(spec, images), args, ffmpeg);
    const seconds = (performance.now() - t0) / 1000;
    const result: RenderResult = { event: 'done', out, encoder, frames: spec.durF, seconds, fps: spec.durF / seconds };
    return { code, stderr, result };
  };
  if (spec.alpha) {
    const { code, stderr, result } = await attempt('prores_ks');
    if (code !== 0) throw new Error(`ffmpeg prores_ks failed (exit ${code}): ${stderr.trim().slice(-800)}`);
    return result;
  }
  return runH264(ffmpeg, 'scene', await probeEncoders(ffmpeg, emit), attempt, process.env[TEST_ENCODERS_ENV]);
}

async function main(argv: string[]): Promise<void> {
  const { args, dir } = packArgs(argv);
  const [specPath, out, ffmpeg] = args;
  if (!specPath || !out) throw new Error('usage: render-scene.ts <spec.json | -> <out.mp4 | out.mov>');
  const spec = JSON.parse(readFileSync(specPath === '-' ? 0 : specPath, 'utf8')) as SceneSpec;
  if (dir !== undefined) await loadPacks(dir);
  const emit = (event: object): void => { process.stdout.write(JSON.stringify(event) + '\n'); };
  emit(await renderScene(spec, out, emit, ffmpeg));
}

if (import.meta.main) {
  main(process.argv.slice(2)).catch((e: unknown) => {
    process.stderr.write((e instanceof Error ? e.message : String(e)) + '\n');
    process.exitCode = 1;
  });
}
