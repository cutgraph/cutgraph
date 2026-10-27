import assert from 'node:assert/strict';
import childProcess from 'node:child_process';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import {
  DEFINITION, encoderArgs, isInitFailure, NoH264Encoder, parseEncoders, probeEncoders, runH264, selectEncoders,
} from '../scripts/encoder-ladder.ts';
import type { EncodingRole, H264Encoder } from '../scripts/encoder-ladder.ts';

const fixtures = new URL('../../tests/fixtures/', import.meta.url);
const parity = JSON.parse(readFileSync(new URL('encoder_ladder_parity.json', fixtures), 'utf8'));

test('all profiles and initialization phrases match the shared parity fixture', () => {
  assert.deepEqual(DEFINITION.order, parity.order);
  assert.deepEqual(DEFINITION.orders, parity.orders);
  assert.equal(DEFINITION.unavailableMessage, parity.unavailableMessage);
  assert.equal(DEFINITION.probeTimeoutMessage, parity.probeTimeoutMessage);
  assert.equal(DEFINITION.probeFailureMessage, parity.probeFailureMessage);
  assert.deepEqual(DEFINITION.initFailures, parity.initFailures);
  assert.deepEqual(Object.keys(DEFINITION.profiles).sort(), Object.keys(parity.profiles).sort());
  for (const role of Object.keys(parity.profiles) as EncodingRole[]) {
    for (const encoder of DEFINITION.order) assert.deepEqual(encoderArgs(role, encoder), parity.profiles[role][encoder]);
  }
});

for (const { file, expected } of parity.selections) {
  test(`selects only present encoders from ${file}`, () => {
    const listing = readFileSync(new URL(`encoders/${file}`, fixtures), 'utf8');
    for (const [role, encoders] of Object.entries(expected)) {
      assert.deepEqual(selectEncoders(parseEncoders(listing), role as EncodingRole), encoders);
    }
  });
}

test('restrictions never add or reorder encoders, and reject unknown names', () => {
  for (const { available, role, value, expected } of parity.restrictions) {
    assert.deepEqual(selectEncoders(new Set(available as string[]), role, value), expected);
  }
  for (const invalid of [',', 'h264_nvenc,typo', 'libopenh264']) {
    assert.throws(() => selectEncoders(new Set(DEFINITION.order), 'export', invalid), /CUTGRAPH_TEST_ENCODERS/);
  }
});

test('missing profile parameters throw instead of becoming an undefined argument', () => {
  const profile = DEFINITION.profiles.export.libx264;
  DEFINITION.profiles.export.libx264 = ['{missing}'];
  try { assert.throws(() => encoderArgs('export', 'libx264'), /Missing encoder parameter: missing/); }
  finally { DEFINITION.profiles.export.libx264 = profile; }
});

test('init failures are remembered per role and reset when the executable changes', async () => {
  const calls: string[][] = [];
  const available = parseEncoders(readFileSync(new URL('encoders/lgpl.txt', fixtures), 'utf8'));
  for (const [binary, role] of [['one', 'scene'], ['one', 'scene'], ['one', 'proxy'],
    ['two', 'scene'], ['one', 'scene']] as [string, EncodingRole][]) {
    const selected = await runH264(binary, role, available, async (encoder) => {
      calls.push([binary, role, encoder]);
      return { code: encoder === 'h264_mf' ? 0 : 1, stderr: `Unknown encoder '${encoder}'`, result: encoder };
    });
    assert.equal(selected, 'h264_mf');
  }
  assert.deepEqual(calls, [
    ...parity.orders.withoutLibx264.map((encoder: string) => ['one', 'scene', encoder]),
    ['one', 'scene', 'h264_mf'],
    ...parity.orders.withoutLibx264.map((encoder: string) => ['one', 'proxy', encoder]),
    ...parity.orders.withoutLibx264.map((encoder: string) => ['two', 'scene', encoder]),
    ...parity.orders.withoutLibx264.map((encoder: string) => ['one', 'scene', encoder]),
  ]);
});

for (const role of ['export', 'proxy', 'scene'] as EncodingRole[]) {
  for (const restriction of ['', 'h264_nvenc']) {
    test(`GPL ${role} retries NVENC after a transient failure with restriction '${restriction}'`, async () => {
      const available = parseEncoders(readFileSync(new URL('encoders/gpl.txt', fixtures), 'utf8'));
      const calls: H264Encoder[] = [];
      const attempt = async (encoder: H264Encoder) => {
        calls.push(encoder);
        return { code: calls.length === 1 ? 1 : 0, stderr: 'OpenEncodeSessionEx failed: out of memory (10)', result: encoder };
      };
      if (restriction) await assert.rejects(runH264('gpl', role, available, attempt, restriction), NoH264Encoder);
      else assert.equal(await runH264('gpl', role, available, attempt), 'libx264');
      assert.equal(await runH264('gpl', role, available, attempt, restriction), 'h264_nvenc');
      assert.deepEqual(calls, restriction ? ['h264_nvenc', 'h264_nvenc'] : ['h264_nvenc', 'libx264', 'h264_nvenc']);
    });
  }
}

for (const recover of [false, true]) {
  test(`LGPL retries the final encoder after exhausting failure memory, recovery=${recover}`, async () => {
    const available = parseEncoders(readFileSync(new URL('encoders/lgpl.txt', fixtures), 'utf8'));
    const calls: H264Encoder[] = [];
    const order = parity.orders.withoutLibx264;
    const binary = `lgpl-${recover}`;
    const attempt = async (encoder: H264Encoder) => {
      calls.push(encoder);
      return { code: recover && calls.length > order.length ? 0 : 1, stderr: `Unknown encoder '${encoder}'`, result: encoder };
    };
    await assert.rejects(runH264(binary, 'scene', available, attempt), NoH264Encoder);
    if (recover) assert.equal(await runH264(binary, 'scene', available, attempt), 'h264_mf');
    else await assert.rejects(runH264(binary, 'scene', available, attempt), NoH264Encoder);
    assert.deepEqual(calls, [...order, 'h264_mf']);
  });
}

test('the encoder listing is cached per executable path, including concurrent requests', async (t) => {
  const calls: string[] = [], events: object[] = [];
  const listing = readFileSync(new URL('encoders/lgpl.txt', fixtures), 'utf8');
  t.mock.method(childProcess, 'execFile', (file, args, options, callback) => {
    calls.push(file);
    assert.deepEqual(args, ['-hide_banner', '-encoders']);
    assert.equal(options.windowsHide, true);
    assert.equal(options.timeout, 30_000);
    callback(null, listing, '');
  });
  const emit = (event: object) => { events.push(event); };
  const results = await Promise.all(Array.from({ length: 8 }, () => probeEncoders('first', emit)));
  assert.ok(results.every((result) => result.has('h264_mf')));
  await probeEncoders('second', emit);
  assert.deepEqual(calls, ['first', 'second']);
  assert.deepEqual(events, ['first', 'second'].map((path) => ({ event: 'ffmpeg', cmd: [path, '-hide_banner', '-encoders'] })));
});

test('failed and timed-out discovery is retried, then cached after success', async (t) => {
  let calls = 0;
  const listing = readFileSync(new URL('encoders/lgpl.txt', fixtures), 'utf8');
  t.mock.method(childProcess, 'execFile', (_file, _args, _options, callback) => {
    calls++;
    if (calls === 1) callback(new Error('tool could not start'), '', 'discovery failed');
    else if (calls === 2) callback(Object.assign(new Error('timed out'), { killed: true }), '', '');
    else callback(null, listing, '');
  });
  await assert.rejects(probeEncoders('failed-tool', () => {}), { message: DEFINITION.probeFailureMessage });
  await assert.rejects(probeEncoders('failed-tool', () => {}), { message: DEFINITION.probeTimeoutMessage });
  assert.ok((await probeEncoders('failed-tool', () => {})).has('h264_mf'));
  await probeEncoders('failed-tool', () => {});
  assert.equal(calls, 3);
});

for (const encoder of DEFINITION.order) {
  const phrases: string[] = [...parity.initFailures[encoder], `Unknown encoder '${encoder}'`];
  for (const phrase of phrases) {
    test(`${encoder} advances on ${phrase}`, async () => {
      const order: H264Encoder[] = encoder === 'libx264' ? ['libx264'] : parity.orders.withoutLibx264;
      const index = order.indexOf(encoder);
      const following = order.slice(index + 1, index + 2);
      const calls: H264Encoder[] = [];
      const attempt = async (name: H264Encoder) => {
        calls.push(name);
        return { code: name === encoder ? 1 : 0, stderr: phrase.toUpperCase(), result: name };
      };
      if (following.length) {
        assert.equal(await runH264(`${encoder}:${phrase}`, 'scene', new Set([encoder, ...following]), attempt), following[0]);
      } else {
        await assert.rejects(runH264(`${encoder}:${phrase}`, 'scene', new Set([encoder]), attempt), /Media Feature Pack/);
      }
      assert.deepEqual(calls, [encoder, ...following]);
    });
  }
}

test('no candidates or complete initialization failure gives the installation guidance', async () => {
  const calls: H264Encoder[] = [];
  const attempt = async (encoder: H264Encoder) => {
    calls.push(encoder);
    return { code: 1, stderr: `Unknown encoder '${encoder}'`, result: encoder };
  };
  await assert.rejects(runH264('empty', 'scene', new Set(), attempt), NoH264Encoder);
  assert.deepEqual(calls, []);
  await assert.rejects(runH264('exhausted', 'scene', new Set(parity.orders.withoutLibx264 as string[]), attempt), /Windows edition/);
  assert.deepEqual(calls, parity.orders.withoutLibx264);
});

for (const stderr of parity.genuineErrors as string[]) {
  test(`a real failure is preserved: ${stderr.split('\n').at(-1)}`, async () => {
    for (const encoder of DEFINITION.order) assert.equal(isInitFailure(encoder, stderr), false);
    const calls: H264Encoder[] = [];
    await assert.rejects(runH264('genuine-error', 'scene', new Set(DEFINITION.order), async (encoder) => {
      calls.push(encoder);
      return { code: 1, stderr, result: encoder };
    }), (error: Error) => error.message.includes(stderr));
    assert.deepEqual(calls, [DEFINITION.order[0]]);
  });
}
