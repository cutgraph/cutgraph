import assert from 'node:assert/strict';
import { test } from 'node:test';
import { LEAD, TAIL, sceneAt, timeline, wordAt, wordDur } from '../src/index.ts';

const near = (a: number | undefined, b: number): void => assert.ok(a !== undefined && Math.abs(a - b) < 1e-9, a + ' != ' + b);

test('timeline lays words end to end and collapses cut words onto the next kept word', () => {
  const TL = timeline({
    scenes: [
      { words: [{ w: 'So,' }, { w: 'um', cut: true }, { w: 'start.' }], cues: [{ type: 'graphic', item: 'callout', at: 2 }] },
      { words: [{ w: 'Next' }], cues: [], shot: { pose: 'rest', expression: 'happy', move: 'idle', backdrop: 'work', diagram: 'blank', headline: 'x', cueAt: 0 } },
    ],
  });
  const [a, b] = TL.scenes;
  const [so, um, start] = a.words;
  near(so.start, LEAD);
  near(so.end, LEAD + wordDur('So,'));
  assert.equal(start.start, so.end);
  assert.ok(um.cut);
  assert.equal(um.start, start.start);
  assert.equal(start.seg, 1);
  near(a.end, start.end + TAIL);
  assert.equal(b.start, a.end);
  assert.deepEqual(TL.words.map((w) => w.w), ['So,', 'start.', 'Next']);
  assert.equal(a.cues[0].start, start.start);
  assert.equal(a.cues[0].end, a.end);
  near(b.cueT, LEAD);
  near(b.firstT, LEAD);
  assert.equal(TL.dur, b.end);
  assert.equal(sceneAt(TL, b.start + 0.1), b);
  assert.equal(wordAt(TL, start.start + 0.01), start);
  assert.equal(wordAt(TL, 0), null);
});
