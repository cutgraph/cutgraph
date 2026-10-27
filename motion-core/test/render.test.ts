import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { isAbsolute, join, resolve } from 'node:path';
import { after, test } from 'node:test';
import { drawSceneFrame, resolveShot, shotTiming } from '../src/index.ts';
import { createCanvas } from '@napi-rs/canvas';
import { checkSpec, renderScene, sceneFrames, sceneImages } from '../bin/render-scene.ts';
import type { SceneSpec } from '../bin/render-scene.ts';
import { DEFINITION } from '../scripts/encoder-ladder.ts';
import { nodeEnv, surface } from '../scripts/node-env.ts';

const CLI = resolve(import.meta.dirname, '../bin/render-scene.ts');
const ffmpeg = execFileSync(process.platform === 'win32' ? 'where.exe' : 'which', ['ffmpeg'], { encoding: 'utf8' }).trim().split(/\r?\n/)[0];
assert.ok(isAbsolute(ffmpeg));
const dir = mkdtempSync(join(tmpdir(), 'render-scene-'));
after(() => rmSync(dir, { recursive: true, force: true }));

const W = 320, H = 180;
const ANIMATION: SceneSpec = {
  width: W, height: H, fps: 30, durF: 45, mode: 'animation',
  shot: { mode: 'animation', pose: 'cheer', headline: 'Real export' }, role: 'Hook', title: 'Intro',
  startF: 300, firstWordF: 309, cueWordF: 330, alpha: false,
};
// A cameo whose shot names no cue word, as the export sends it: no cue word frame, so it springs in at once.
const CAMEO: SceneSpec = {
  ...ANIMATION, mode: 'cameo', shot: { mode: 'cameo' }, role: 'Wrap', durF: 24, alpha: true, cueWordF: null,
};
// The same cameo landing on its cue word half a second in.
const CAMEO_CUED: SceneSpec = { ...CAMEO, shot: { mode: 'cameo', cueWord: 3 }, cueWordF: 315 };

const sha = (b: Buffer): string => createHash('sha256').update(b).digest('hex');

function probe(file: string): Record<string, string> {
  const out = execFileSync('ffprobe', [
    '-v', 'error', '-count_frames', '-select_streams', 'v:0',
    '-show_entries', 'stream=codec_name,pix_fmt,width,height,r_frame_rate,nb_read_frames:format=duration',
    '-of', 'default=noprint_wrappers=1', file,
  ], { encoding: 'utf8' });
  return Object.fromEntries(out.trim().split(/\r?\n/).map((l) => l.split('=') as [string, string]));
}

/** Frame k of an encoded file, decoded back to straight RGBA. */
function decodedFrame(file: string, k: number): Buffer {
  return execFileSync('ffmpeg', [
    '-v', 'error', '-i', file, '-vf', `select=eq(n\\,${k})`, '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgba', '-',
  ], { maxBuffer: 64 << 20 });
}

test('scene frames are deterministic and identical to a fresh motion-core draw of the same (shot, t)', () => {
  for (const spec of [ANIMATION, CAMEO, CAMEO_CUED]) {
    const first = [...sceneFrames(spec)].map(sha);
    assert.equal(first.length, spec.durF);
    assert.deepEqual([...sceneFrames(spec)].map(sha), first, spec.mode + ' renders the same bytes twice');
    const shot = resolveShot(spec.shot, spec.role, spec.title);
    const timing = shotTiming(spec.startF, spec.firstWordF, spec.cueWordF, spec.fps);
    for (const k of [0, 7, spec.durF - 1]) {
      const { ctx } = surface(W, H);
      drawSceneFrame(ctx, spec.mode as 'animation' | 'cameo', shot, k / spec.fps, timing, W, H, nodeEnv());
      const { data } = ctx.getImageData(0, 0, W, H);
      assert.equal(sha(Buffer.from(data.buffer, data.byteOffset, data.byteLength)), first[k], `${spec.mode} frame ${k}`);
    }
  }
});

test('a cameo with a cue word renders nothing until that word, then springs in', () => {
  const alpha = (frame: Buffer): number => {
    let n = 0;
    for (let i = 3; i < frame.length; i += 4) if (frame[i] > 0) n++;
    return n;
  };
  const inks = [...sceneFrames(CAMEO_CUED)].map(alpha);
  assert.deepEqual(inks.slice(0, 15), Array(15).fill(0), 'blank before the cue word frame');
  assert.ok(inks[23] > 200, 'the default rig is in by the end');
  assert.ok([...sceneFrames(CAMEO)].map(alpha)[8] > 0, 'without a cue word it is already springing in');
});

test('a host-resolved custom expression draws its own face in every frame', () => {
  const frames = (spec: SceneSpec): string[] => [...sceneFrames(spec)].map(sha);
  const custom: SceneSpec = { ...CAMEO, shot: { mode: 'cameo', expression: 'beaming' }, custom: { eyes: 'arc', mouth: 'grin' } };
  const happy: SceneSpec = { ...CAMEO, shot: { mode: 'cameo', expression: 'happy' } };
  const unresolved: SceneSpec = { ...CAMEO, shot: { mode: 'cameo', expression: 'beaming' } };
  assert.deepEqual(frames(custom), frames(happy), 'the parts of the built-in happy face draw the happy face');
  assert.notDeepEqual(frames({ ...custom, custom: { eyes: 'side', mouth: 'grin' } }), frames(custom));
  assert.notDeepEqual(frames(unresolved), frames(custom), 'an id the host did not resolve is not the custom face');
});

test("every phone's host-resolved image is decoded before the first frame, and drawn in its phone", async () => {
  const files = ['#ff0000', '#00ff00', '#0000ff'].map((color, i) => {
    const c = createCanvas(12, 26), x = c.getContext('2d');
    x.fillStyle = color;
    x.fillRect(0, 0, 12, 26);
    const file = join(dir, `phone-${i + 1}.png`);
    writeFileSync(file, c.toBuffer('image/png'));
    return file;
  });
  const spec: SceneSpec = {
    ...ANIMATION, width: 640, height: 360, durF: 60, shot: {
      mode: 'animation', layout: 'screen', backdrop: 'black', headline: ' ', eyebrow: '',
      screen: { asset: 'a', image: files[0], more: [{ asset: 'b', image: files[1] }, { asset: 'c' }, { asset: 'd', image: files[2] }] },
    },
  };
  const images = await sceneImages(spec);
  assert.deepEqual([...images.keys()], files.slice(0, 2), 'phones 1 and 2 (a third `more` is past phone 3)');
  assert.deepEqual(await sceneImages(ANIMATION), new Map(), 'no screen, no images');
  const last = [...sceneFrames(spec, images)].at(-1)!;
  const hues = [[255, 0, 0], [0, 255, 0]].map((hue) => {
    let n = 0;
    for (let i = 0; i < last.length; i += 4) if (hue.every((v, k) => Math.abs(last[i + k] - v) < 40)) n++;
    return n;
  });
  assert.ok(hues.every((n) => n > 500), `both images drawn: ${hues}`);
});

test('an animation scene encodes to opaque H.264 at the spec fps and length', async () => {
  const out = join(dir, 'anim.mp4');
  const done = await renderScene(ANIMATION, out, () => {}, ffmpeg);
  assert.equal(done.frames, 45);
  assert.ok(DEFINITION.order.includes(done.encoder as typeof DEFINITION.order[number]));
  const p = probe(out);
  assert.equal(p.codec_name, 'h264');
  assert.equal(p.pix_fmt, 'yuv420p', 'no alpha plane');
  assert.equal(`${p.width}x${p.height}`, `${W}x${H}`);
  assert.equal(p.r_frame_rate, '30/1');
  assert.equal(p.nb_read_frames, '45');
  assert.equal(Number(p.duration), 1.5);
  const rgba = decodedFrame(out, 20);
  for (let i = 3; i < rgba.length; i += 4) assert.equal(rgba[i], 255);
});

test('a cameo scene encodes to ProRes 4444 with its alpha preserved', async () => {
  const out = join(dir, 'cameo.mov');
  await renderScene(CAMEO, out, () => {}, ffmpeg);
  const p = probe(out);
  assert.equal(p.codec_name, 'prores');
  assert.match(p.pix_fmt, /^yuva/, 'an alpha plane survives the encode');
  assert.equal(p.nb_read_frames, '24');
  assert.equal(p.r_frame_rate, '30/1');
  const rgba = decodedFrame(out, 23);
  let ink = 0;
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      const a = rgba[(y * W + x) * 4 + 3];
      if (x < W / 2) assert.equal(a, 0, 'the left half stays transparent');
      if (a > 0) ink++;
    }
  }
  assert.ok(ink > 200, 'the default rig is drawn');
});

test('the CLI reads a spec from stdin and prints its ffmpeg command before the result', () => {
  const out = join(dir, 'cli.mp4');
  const r = spawnSync(process.execPath, [CLI, '-', out, ffmpeg], { input: JSON.stringify({ ...ANIMATION, durF: 6 }), encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr);
  const events = r.stdout.trim().split('\n').map((l) => JSON.parse(l));
  assert.equal(events[0].event, 'ffmpeg');
  assert.equal(events[0].cmd[0], ffmpeg);
  assert.ok(events[0].cmd.includes('-encoders'));
  assert.ok(events.slice(1, -1).every((event) => event.cmd.includes(out)));
  assert.equal(events.at(-1).event, 'done');
  assert.equal(events.at(-1).frames, 6);
  assert.equal(probe(out).nb_read_frames, '6');
});

test('the renderer refuses a relative executable path', async () => {
  await assert.rejects(renderScene(ANIMATION, join(dir, 'refused.mp4'), () => {}, 'ffmpeg'),
    /CUTGRAPH_FFMPEG must name an absolute executable path/);
});

test('a footage scene or a malformed spec is refused', () => {
  assert.throws(() => checkSpec({ ...ANIMATION, mode: 'footage' }), /footage/);
  assert.throws(() => checkSpec({ ...ANIMATION, width: 321 }), /invalid scene spec/);
  assert.throws(() => checkSpec({ ...ANIMATION, durF: 0 }), /invalid scene spec/);
  const r = spawnSync(process.execPath, [CLI, '-', join(dir, 'bad.mp4')], { input: '{"mode":"animation"}', encoding: 'utf8' });
  assert.equal(r.status, 1);
  assert.match(r.stderr, /invalid scene spec/);
});
