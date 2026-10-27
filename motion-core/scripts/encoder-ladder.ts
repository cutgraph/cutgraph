/** Node encoding policy. Keep browser drawing code independent of process/tool discovery. */
import childProcess from 'node:child_process';
import { readFileSync } from 'node:fs';

export type H264Encoder = 'h264_nvenc' | 'h264_qsv' | 'h264_amf' | 'h264_mf' | 'libx264';
export type EncodingRole = 'export' | 'proxy' | 'script' | 'scene' | 'conform';
interface Definition {
  order: H264Encoder[];
  orders: { withLibx264: Record<EncodingRole, H264Encoder[]>; withoutLibx264: H264Encoder[] };
  unavailableMessage: string;
  probeTimeoutMessage: string;
  probeFailureMessage: string;
  defaults: Record<string, string>;
  profiles: Record<EncodingRole, Record<H264Encoder, string[]>>;
  initFailures: Record<H264Encoder, string[]>;
}
export const DEFINITION: Definition = JSON.parse(readFileSync(new URL('../../schema/encoder-ladder.json', import.meta.url), 'utf8'));
export const TEST_ENCODERS_ENV = 'CUTGRAPH_TEST_ENCODERS';
export class NoH264Encoder extends Error {
  constructor() { super(DEFINITION.unavailableMessage); }
}

export function parseEncoders(output: string): Set<string> {
  return new Set([...output.matchAll(/^\s*V[A-Z.]{5}\s+(\w+)\s/gm)].map((match) => match[1]));
}

export function selectEncoders(available: ReadonlySet<string>, role: EncodingRole, restriction = ''): H264Encoder[] {
  const allowed = restriction ? restriction.split(',').map((name) => name.trim()) : DEFINITION.order;
  if (allowed.some((name) => !DEFINITION.order.includes(name as H264Encoder))) {
    throw new Error(`${TEST_ENCODERS_ENV} must contain only H.264 ladder encoder names`);
  }
  const order = available.has('libx264') ? DEFINITION.orders.withLibx264[role] : DEFINITION.orders.withoutLibx264;
  return order.filter((name) => available.has(name) && allowed.includes(name));
}

export function encoderArgs(role: EncodingRole, encoder: H264Encoder, values: Record<string, string> = {}): string[] {
  const params = { ...DEFINITION.defaults, ...values };
  return ['-c:v', encoder, ...DEFINITION.profiles[role][encoder].map((arg) => arg.replace(/\{(\w+)\}/g, (_, key: string) => {
    if (!(key in params)) throw new Error(`Missing encoder parameter: ${key}`);
    return params[key];
  }))];
}

export function isInitFailure(encoder: H264Encoder, stderr: string): boolean {
  const low = stderr.toLowerCase();
  return low.includes(`unknown encoder '${encoder}'`) || low.includes(`unknown encoder "${encoder}"`)
    || DEFINITION.initFailures[encoder].some((phrase) => low.includes(phrase));
}

export type Emit = (event: object) => void;
const probes = new Map<string, Promise<Set<string>>>();
const failedInits = new Map<EncodingRole, { ffmpeg: string; encoders: Set<H264Encoder> }>();
export function probeEncoders(ffmpeg: string, emit: Emit): Promise<Set<string>> {
  let pending = probes.get(ffmpeg);
  if (!pending) {
    const args = ['-hide_banner', '-encoders'];
    emit({ event: 'ffmpeg', cmd: [ffmpeg, ...args] });
    pending = new Promise<Set<string>>((resolve, reject) => {
      childProcess.execFile(ffmpeg, args, { encoding: 'utf8', windowsHide: true, timeout: 30_000 }, (error, stdout, stderr) => {
        if (error) {
          emit({ event: 'encoderDiscoveryFailed', detail: stderr || error.message });
          reject(new Error(error.killed ? DEFINITION.probeTimeoutMessage : DEFINITION.probeFailureMessage));
        }
        else resolve(parseEncoders(stdout));
      });
    }).catch((error) => {
      probes.delete(ffmpeg);
      throw error;
    });
    probes.set(ffmpeg, pending);
  }
  return pending;
}

export async function runH264<T>(
  ffmpeg: string, role: EncodingRole,
  available: ReadonlySet<string>, attempt: (encoder: H264Encoder) => Promise<{ code: number | null; stderr: string; result: T }>,
  restriction = '',
): Promise<T> {
  const encoders = selectEncoders(available, role, restriction);
  const portable = !available.has('libx264');
  let failed = failedInits.get(role);
  if (!failed || failed.ffmpeg !== ffmpeg) {
    failed = { ffmpeg, encoders: new Set() };
    failedInits.delete(role);
    if (portable) failedInits.set(role, failed);
  }
  for (const encoder of encoders) {
    if (portable && failed.encoders.has(encoder) && encoder !== encoders.at(-1)) continue;
    const { code, stderr, result } = await attempt(encoder);
    if (code === 0) return result;
    if (!isInitFailure(encoder, stderr)) throw new Error(`ffmpeg ${encoder} failed (exit ${code}): ${stderr.trim().slice(-800)}`);
    if (portable) failed.encoders.add(encoder);
  }
  throw new NoH264Encoder();
}
