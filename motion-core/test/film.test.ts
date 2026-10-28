import { BOX_W, BOX_H } from '../src/ideas.ts';
import { after } from 'node:test';
import { CHARACTERS } from '../src/characters.ts';
import { paletteRig } from './palette-rig.ts';
const GUEST = paletteRig('film-rig');
const previousRig = CHARACTERS[GUEST.id];
Object.assign(CHARACTERS, { [GUEST.id]: GUEST });
after(() => {
  if (previousRig) Object.assign(CHARACTERS, { [GUEST.id]: previousRig });
  else Reflect.deleteProperty(CHARACTERS, GUEST.id);
});
import { characterOf as defaultRigOf, DEFAULT_CHARACTER as defaultRigId } from '../src/index.ts';
const DEFAULT_RIG = defaultRigOf(defaultRigId);
// Short-film staging: camera pans, independent diagrams, bubbles and labels on an ensemble stage.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { test } from 'node:test';
import {
  BOARD_H, BOARD_KINDS, BOARD_LABEL_MAX, BOARD_LABELS_MAX, BOARD_W, BUBBLES, CAMERAS, DIAGRAMS, FORMATS, LABEL_BADGES, LABEL_HEADLINE, PAN_S,
  boardRect, boardState, bubbleBeat, bubbleLayout, crownOf, drawBoard, drawSceneFrame, duoLayout, ensembleLayout,
  headlineFoot, layoutFor, panAt, panShift, resolveShot, shotTiming, stageLabels, stagingOf, widthOf,
} from '../src/index.ts';
import type { BoardPlace, BoardRect, BoardSpot, BubbleId, Format, MotionEnv, ProjectShot, ShotTiming } from '../src/index.ts';
import { nodeEnv, surface } from '../scripts/node-env.ts';
import { FILM_SHOTS, filmShotHash } from './film-frames.ts';
import { ROBUSTNESS_FRAMES } from './robustness-frames.ts';
import { loadGolden } from './goldens.ts';

const env = nodeEnv(), FPS = 30;
const golden = loadGolden<Record<string, string>>('film-frames');
const robustness = loadGolden<Record<string, string>>('robustness-frames');
const FORMAT_IDS = Object.keys(FORMATS) as Format[];

test('the reference film and independent robustness frames match their recordings in every format', { skip: golden.skip }, () => {
  const GOLDEN = golden.value!;
  assert.equal(FILM_SHOTS.length * FORMAT_IDS.length, Object.keys(GOLDEN).length);
  for (const f of FILM_SHOTS) for (const format of FORMAT_IDS) {
    assert.equal(filmShotHash(f, format, env), GOLDEN[`${f.name} ${format}`], `${f.name} ${format}`);
  }
  assert.deepEqual(ROBUSTNESS_FRAMES.map((f) => f.name), [
    'missing-character', 'missing-costar', 'missing-ensemble-entrance', 'missing-cameo', 'unbranded-signoff',
    'grid-diagram', 'chain-diagram', 'radial-diagram', 'tree-diagram', 'list-diagram',
    'neutral-pair', 'independent-push', 'independent-pull', 'neutral-badges',
  ]);
  assert.equal(Object.keys(robustness.value!).length, ROBUSTNESS_FRAMES.length * FORMAT_IDS.length);
  for (const f of ROBUSTNESS_FRAMES) for (const format of FORMAT_IDS) {
    assert.equal(filmShotHash(f, format, env), robustness.value![`${f.name} ${format}`], `${f.name} ${format}`);
  }
});

// ---------------------------------------------------------------- helpers

const timingOf = (dur: number, cue: number | null): ShotTiming =>
  shotTiming(0, 12, cue === null ? null : Math.round(cue * FPS), FPS, Math.round(dur * FPS));

const guest = (palette: string, extra: Partial<ProjectShot> = {}) => ({ character: GUEST.id, palette, pose: 'hips', expression: 'neutral', move: 'idle', ...extra });

function frame(stored: ProjectShot, lt: number, format: Format, timing: ShotTiming, scale = 1, renderEnv: MotionEnv = env): { data: Uint8ClampedArray; W: number; H: number; hash: string } {
  const [FW, FH] = FORMATS[format], W = Math.round(FW * scale), H = Math.round(FH * scale), { ctx } = surface(W, H);
  ctx.scale(W / FW, H / FH);
  drawSceneFrame(ctx, 'animation', resolveShot({ mode: 'animation', hideHeadline: true, ...stored }, 'Beat', 'x'), lt, timing, FW, FH, renderEnv);
  const { data } = ctx.getImageData(0, 0, W, H);
  return { data, W, H, hash: createHash('sha256').update(data).digest('hex') };
}

/** Where a colour is in a frame: its pixels' count, bounding box and centroid. */
function find(p: { data: Uint8ClampedArray; W: number; H: number }, [r, g, b]: readonly number[], side?: 'left' | 'right') {
  let first = Infinity;
  if (side === 'left') for (let x = 0; x < p.W; x++) {
    for (let y = 0; y < p.H; y++) {
      const i = (y * p.W + x) * 4;
      if (p.data[i] === r && p.data[i + 1] === g && p.data[i + 2] === b) { first = x; break; }
    }
    if (Number.isFinite(first)) break;
  }
  const leadWidth = widthOf(DEFAULT_RIG, duoLayout(p.W, p.H, DEFAULT_RIG, DEFAULT_RIG, true).lead.h);
  const out = { n: 0, x0: Infinity, x1: -Infinity, y0: Infinity, y1: -Infinity, sx: 0, cells: new Set<string>() };
  for (let y = 0; y < p.H; y++) for (let x = 0; x < p.W; x++) {
    const i = (y * p.W + x) * 4;
    if (p.data[i] !== r || p.data[i + 1] !== g || p.data[i + 2] !== b) continue;
    if (side && (side === 'left' ? x > first + leadWidth : x < p.W / 2)) continue;
    out.n++; out.sx += x; out.cells.add(`${x},${y}`);
    out.x0 = Math.min(out.x0, x); out.x1 = Math.max(out.x1, x); out.y0 = Math.min(out.y0, y); out.y1 = Math.max(out.y1, y);
  }
  return { ...out, cx: out.sx / out.n };
}

const CHARACTER_COLOR = [159, 186, 233], TEAL = [43, 179, 163];

// ---------------------------------------------------------------- the stored fields

test('the new ids resolve: a pan, the independent diagrams and none, the "?", the "!!" and a headline over the co-star', () => {
  assert.deepEqual([...CAMERAS], ['push-in', 'pull-out', 'pan']);
  assert.deepEqual([...BUBBLES], ['think', 'say', 'exclaim', 'question', 'exclaims']);
  assert.equal(resolveShot({ mode: 'animation', costar: { character: 'lens', bubble: 'exclaims' } }, 'Beat', 'x').costar?.bubble, 'exclaims');
  assert.deepEqual(DIAGRAMS.slice(6), ['none', ...BOARD_KINDS]);
  assert.deepEqual(stagingOf({ camera: 'pan', headlineAt: 'costar' }), { camera: 'pan', headlineAt: 'costar' });
  assert.deepEqual(stagingOf({ headlineAt: 'lead' }), {}, 'an unknown spot is dropped');
  const s = resolveShot({ mode: 'animation', diagram: 'chain', costar: { character: 'lens', bubble: 'question' } }, 'Beat', 'x');
  assert.equal(s.diagram, 'chain');
  assert.equal(s.costar?.bubble, 'question');
  assert.ok(!('headlineAt' in resolveShot({ mode: 'animation' }, 'Beat', 'x')), 'absent stays absent');
});

test('the "?" pops like the "!", with its overshoot and shake, centred just over the co-star\'s head; the "!" keeps its place', () => {
  for (const u of [-0.01, 0.1, 0.18, 0.26, 0.35, 2]) assert.deepEqual(bubbleBeat('question', u), bubbleBeat('exclaim', u), `@${u}`);
  for (const format of FORMAT_IDS) {
    const [W, H] = FORMATS[format], D = duoLayout(W, H, DEFAULT_RIG, GUEST, true), c = D.costar;
    const q = bubbleLayout('question', c, GUEST, W, H), bang = bubbleLayout('exclaim', c, GUEST, W, H), crown = c.by - crownOf(GUEST, c.h);
    assert.ok(Math.abs(q.body.x + q.body.w / 2 - c.x) <= 1, `${format}: centred over the co-star's head`);
    assert.equal(q.body.y + q.body.h, Math.round(crown) - 2 * q.q, `${format}: two cells over its crown`);
    assert.ok(bang.body.x + bang.body.w / 2 < c.x && bang.body.y + bang.body.h === Math.round(c.by - c.h) - 2 * bang.q, `${format}: the "!" as before`);
  }
});

test('the "none" diagram draws nothing where the classic diagrams draw', () => {
  const t = timingOf(4, 1), base: ProjectShot = { backdrop: 'work', pose: 'rest', expression: 'happy', move: 'idle' };
  const none = frame({ ...base, diagram: 'none' }, 2.5, '16:9', t, 0.25), blank = frame({ ...base, diagram: 'blank' }, 2.5, '16:9', t, 0.25);
  assert.notEqual(none.hash, blank.hash);
  assert.equal(none.hash, frame({ ...base, diagram: 'none' }, 2.5, '16:9', t, 0.25).hash, 'deterministic');
});

// ---------------------------------------------------------------- the pan

const PAN_SAMPLE: ProjectShot = { backdrop: 'work', diagram: 'none', camera: 'pan', ...{ character: 'lens', pose: 'rest', expression: 'curious', move: 'idle' },
  costar: guest('teal', { pose: 'wave', expression: 'happy' }), interaction: 'face' };

test('a pan eases the world right: its offset is whole pixels, only grows, and is 0 once it settles', () => {
  assert.equal(panAt('push-in', 1, 4), null);
  assert.equal(panAt(undefined, 1), null);
  const at = (lt: number) => panAt('pan', lt, 4.0)!;
  assert.deepEqual([at(0).rest, at(0).alpha, at(0).dur], [1, 1, PAN_S]);
  assert.deepEqual([at(PAN_S).rest, at(PAN_S).alpha], [0, 0]);
  assert.equal(panAt('pan', 0.5, 1.5)!.dur, 1.5, 'a short scene is all pan');
  let last = -Infinity;
  for (let lt = 0; lt <= PAN_S + 0.1; lt += 1 / FPS) {
    const s = panShift(at(lt), 480);
    assert.ok(Number.isInteger(s) && s <= 0 && s >= last, `@${lt.toFixed(2)}: ${s}`);
    last = s;
  }
  assert.equal(last, 0);
  assert.ok(at(PAN_S * 0.3).rest < 0.5, 'an ease-out: most of the way in the first third');
});

test('the lead stands still in the world, carried from just inside the left edge into its slot, with crisp pixels', () => {
  for (const format of FORMAT_IDS) {
    const [W, H] = FORMATS[format], D = duoLayout(W, H, DEFAULT_RIG, GUEST, true), t = timingOf(4.0, null), g = Math.floor(D.lead.h / DEFAULT_RIG.rows!);
    const settled = find(frame(PAN_SAMPLE, PAN_S + 0.4, format, t), CHARACTER_COLOR, 'left'), start = find(frame(PAN_SAMPLE, 0, format, t), CHARACTER_COLOR, 'left');
    const half = widthOf(DEFAULT_RIG, D.lead.h) / 2;
    assert.ok(Math.abs(settled.cx - D.lead.x) < half * 0.3, `${format}: ends on its slot (${settled.cx.toFixed(0)} vs ${D.lead.x.toFixed(0)})`);
    assert.ok(start.x0 >= 2 && start.x0 < W * 0.08, `${format}: starts just inside the left edge (x ${start.x0})`);
    assert.ok(settled.cx - start.cx >= W * 0.1, `${format}: travels right (${(settled.cx - start.cx).toFixed(0)} px)`);
    // Mid-pan, the lead is exactly its settled self moved by whole pixels: same pose, same crisp grid.
    const lt = PAN_S * 0.35, pan = panAt('pan', lt, 4.0)!, mid = find(frame(PAN_SAMPLE, lt, format, t), CHARACTER_COLOR, 'left');
    const still = find(frame({ ...PAN_SAMPLE, camera: undefined }, lt, format, t), CHARACTER_COLOR, 'left'), dx = mid.x0 - still.x0;
    assert.ok(dx < 0 && Number.isInteger(dx) && pan.rest > 0, `${format}: shifted ${dx}`);
    assert.deepEqual([...mid.cells].sort(), [...still.cells].map((c) => { const [x, y] = c.split(',').map(Number); return `${x + dx},${y}`; }).sort(),
      `${format}: the same rig pixels, ${g} screen pixels each, moved by ${dx}`);
  }
});

test('once the pan settles the frame is the stage as laid out, byte for byte, so the next scene starts where it ends', () => {
  for (const format of FORMAT_IDS) {
    const t = timingOf(4.0, null);
    for (const lt of [PAN_S, PAN_S + 0.9, 4.0 - 1 / FPS]) {
      assert.equal(frame(PAN_SAMPLE, lt, format, t, 0.25).hash, frame({ ...PAN_SAMPLE, camera: undefined }, lt, format, t, 0.25).hash, `${format} @${lt}`);
    }
    assert.notEqual(frame(PAN_SAMPLE, 0.6, format, t, 0.25).hash, frame({ ...PAN_SAMPLE, camera: undefined }, 0.6, format, t, 0.25).hash, `${format}: panning`);
  }
});

test('a co-star walking in from the right arrives as the pan settles, and not before', () => {
  const walker: ProjectShot = { ...PAN_SAMPLE, costar: guest('teal', { pose: 'wave', expression: 'happy', move: 'enter' }) };
  for (const format of FORMAT_IDS) {
    const [W, H] = FORMATS[format], D = duoLayout(W, H, DEFAULT_RIG, GUEST, true), t = timingOf(4.0, null);
    assert.equal(find(frame(walker, 0.3, format, t, 0.5), TEAL, 'right').n, 0, `${format}: off the right edge at first`);
    const walking = find(frame(walker, PAN_S - 0.4, format, t), TEAL, 'right'), arrived = find(frame(walker, PAN_S + 0.02, format, t), TEAL, 'right');
    assert.ok(walking.n > 0 && walking.cx > D.costar.x + W * 0.05, `${format}: still walking in just before (${walking.cx.toFixed(0)})`);
    assert.ok(Math.abs(arrived.cx - D.costar.x) < W * 0.03, `${format}: on its slot as it settles (${arrived.cx.toFixed(0)} vs ${D.costar.x.toFixed(0)})`);
  }
});

test('the pan moves the backdrop in parallax, and an unpanned backdrop is untouched', () => {
  const t = timingOf(4.0, null), plain: ProjectShot = { ...PAN_SAMPLE, costar: undefined, interaction: undefined };
  const a = frame(plain, 0.2, '16:9', t, 0.5), b = frame({ ...plain, camera: undefined }, 0.2, '16:9', t, 0.5);
  let changed = 0;
  // The floor below the feet line: the markings slide with the world.
  for (let y = Math.round(a.H * 0.9); y < a.H; y++) for (let x = 0; x < a.W; x++) if (a.data[(y * a.W + x) * 4] !== b.data[(y * a.W + x) * 4]) changed++;
  assert.ok(changed > 200, `floor markings (${changed} px)`);
});

// ---------------------------------------------------------------- independent diagrams

const DUO = (W: number, H: number): BoardSpot[] => {
  const D = duoLayout(W, H, DEFAULT_RIG, GUEST, true);
  return [{ ...D.lead, rig: DEFAULT_RIG }, { ...D.costar, rig: GUEST }];
};
const ROW4 = (W: number, H: number): BoardSpot[] =>
  ensembleLayout(W, H, [DEFAULT_RIG, GUEST, GUEST, GUEST]).slots.map((s, i) => ({ ...s, rig: i ? GUEST : DEFAULT_RIG }));

const place = (spots: BoardSpot[], bubbles: (BubbleId | undefined)[], over: number[] = []): BoardPlace =>
  ({ spots, bubbles, over, top: 0 });
const LABELS = ['PARTS', 'Copy', 'Tabs', 'Curve'];
interface Box { x0: number; y0: number; x1: number; y1: number }
const overlaps = (a: Box, b: Box) => a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;
const rectBox = (R: BoardRect): Box => ({ x0: R.x, x1: R.x + R.w, y0: R.y, y1: R.y + R.h });
const box = (b: { x: number; y: number; w: number; h: number }): Box => ({ x0: b.x, y0: b.y, x1: b.x + b.w, y1: b.y + b.h });
const bodyBox = (s: BoardSpot): Box => ({ x0: s.x - widthOf(s.rig, s.h) / 2, x1: s.x + widthOf(s.rig, s.h) / 2, y0: s.by - Math.max(s.h, crownOf(s.rig, s.h)), y1: s.by });
const bubbleBox = (s: BoardSpot, kind: BubbleId, W: number, H: number): Box => box(bubbleLayout(kind, s, s.rig, W, H).body);

test('content-sized layouts build the requested node count and topology', () => {
  assert.deepEqual(BOARD_KINDS, ['grid', 'chain', 'radial', 'tree', 'list']);
  for (const count of [1, 2, 3, 4, 5, 6]) {
    const states = BOARD_KINDS.map((kind) => boardState(kind, 2, { cueT: 0.2, endT: 3 }, count));
    assert.ok(states.every((st) => st.boxes.length === count));
    assert.deepEqual(states.map((st) => st.edges.length), [0, count - 1, count - 1, count - 1, 0]);
    assert.ok(states[2].edges.every(([a]) => a === 0));
    assert.ok(states[3].edges.every(([a, b]) => a === Math.floor((b - 1) / 2)));
    assert.ok(states[4].boxes.every((b, i, boxes) => b.x === boxes[0].x && (!i || b.y > boxes[i - 1].y)));
  }
  assert.equal(boardState('grid', 2, {}).boxes.length, 3);
  assert.notDeepEqual(boardState('grid', 2, {}, 2).boxes, boardState('grid', 2, {}, 6).boxes.slice(0, 2));
});

test('each diagram is deterministic, finishes within a short scene and has bounded nonoverlapping nodes', () => {
  for (const kind of BOARD_KINDS) for (const count of [1, 2, 3, 4, 5, 6]) for (const fps of [24, 30, 60]) for (const dur of [1 / fps, 0.2, 3]) {
    const timing = { cueT: 1.7, endT: dur };
    const start = boardState(kind, -0.01, timing, count), end = boardState(kind, dur, timing, count);
    assert.ok(start.boxes.every((b) => b.alpha === 0));
    assert.ok(end.boxes.every((b) => b.alpha === 1 && b.scale === 1));
    for (const lt of [0, dur / 2, dur]) {
      const st = boardState(kind, lt, timing, count);
      assert.deepEqual(boardState(kind, lt, timing, count), st);
      for (const b of st.boxes) {
        assert.ok(Object.values(b).every(Number.isFinite));
        assert.ok(b.x - BOX_W / 2 > 0 && b.x + BOX_W / 2 < BOARD_W && b.y - BOX_H / 2 > 0 && b.y + BOX_H / 2 < BOARD_H);
      }
      st.boxes.forEach((a, i) => st.boxes.slice(i + 1).forEach((b) =>
        assert.ok(Math.abs(a.x - b.x) >= BOX_W || Math.abs(a.y - b.y) >= BOX_H)));
    }
  }
});

test('current diagram regions avoid bodies, crowns, bubbles and headlines in every format and performer layout', () => {
  for (const format of FORMAT_IDS) {
    const [W, H] = FORMATS[format];
    const L = layoutFor(W, H);
    const rows: [BoardSpot[], number[]][] = [
      [DUO(W, H), []], [ROW4(W, H), [2, 3]],
      [[{ x: L.characterX, by: L.characterY, h: L.characterH, rig: DEFAULT_RIG }], []],
      [ensembleLayout(W, H, [GUEST, DEFAULT_RIG, GUEST]).slots.map((s, i) => ({ ...s, rig: i === 1 ? DEFAULT_RIG : GUEST })), [1, 2]],
    ];
    for (const [spots, over] of rows) for (const bubble of [undefined, ...BUBBLES]) for (let active = 0; active < spots.length; active++) {
      const bubbles = spots.map((_, i) => i === active ? bubble : undefined), p = place(spots, bubbles, over), R = boardRect(W, H, p), area = rectBox(R);
      assert.ok(R.w > W * 0.2, `${format}: readable region ${R.w}`);
      assert.ok(area.x0 >= 0 && area.y0 >= 0 && area.x1 <= W && area.y1 <= H);
      spots.forEach((s, i) => {
        assert.ok(!overlaps(area, bodyBox(s)), `${format}: body ${i}`);
        if (bubbles[i]) assert.ok(!overlaps(area, bubbleBox(s, bubbles[i], W, H)), `${format}: ${bubble} ${i}`);
      });
      const { ctx } = surface(W, H);
      const heading = resolveShot({ mode: 'animation', headline: 'Notes', eyebrow: 'Overview' }, '', '');
      const foot = headlineFoot(ctx, heading, L, env);
      assert.ok(foot > H * 0.15, 'the real headline reserves a nontrivial region');
      const withHeading = boardRect(W, H, { ...p, bubbles: spots.map(() => undefined), top: foot });
      assert.ok(withHeading.y >= foot - 0.001 && withHeading.w > 0, `${format}: diagram stays below the actual headline (${withHeading.y}, ${foot}, ${withHeading.w})`);
    }
  }
});

test('diagram choice does not alter staging, choose a bright backdrop or attach a speech card', () => {
  for (const format of FORMAT_IDS) {
    const t = timingOf(3, 0.2);
    for (const kind of BOARD_KINDS) {
      const base: ProjectShot = { diagram: kind, backdrop: 'black', pose: 'rest', hideHeadline: true, costar: guest('teal', { bubble: 'say' }) };
      assert.equal(resolveShot(base, 'Beat', 'x').backdrop, 'black');
      assert.equal(frame(base, 0, format, t, 0.25).hash, frame({ ...base, diagram: 'none' }, 0, format, t, 0.25).hash);
      const drawn = frame(base, 2, format, t, 0.25), plain = frame({ ...base, diagram: 'none' }, 2, format, t, 0.25);
      const [W, H] = FORMATS[format], R = boardRect(W, H, place(DUO(W, H), [undefined, 'say']));
      for (let y = 0; y < drawn.H; y++) for (let x = 0; x < drawn.W; x++) {
        if (x >= Math.floor(R.x / 4) && x <= Math.ceil((R.x + R.w) / 4) && y >= Math.floor(R.y / 4) && y <= Math.ceil((R.y + R.h) / 4)) continue;
        const i = (y * drawn.W + x) * 4;
        assert.deepEqual(drawn.data.slice(i, i + 4), plain.data.slice(i, i + 4), `${format} ${kind}: staging outside the diagram`);
      }
      assert.equal(frame(base, 2, format, t, 0.25).hash, drawn.hash);
    }
  }
});

test('labels resolve with six available entries, truncation, partial labels and no labels when all blank', () => {
  assert.deepEqual([BOARD_LABELS_MAX, BOARD_LABEL_MAX], [6, 12]);
  const labels = (boardLabels: unknown) => resolveShot({ mode: 'animation', diagram: 'chain', boardLabels: boardLabels as never }, 'Beat', 'x').boardLabels;
  assert.deepEqual(labels(LABELS), LABELS);
  assert.deepEqual(labels([' NOTES ', '', 'A much longer label', 4, 'e', 'f', 'g']), ['NOTES', '', 'A much longe', '', 'e', 'f']);
  assert.equal(labels(['', '  ']), undefined);
  assert.equal(labels('NOTES'), undefined);
  assert.ok(!('boardLabels' in resolveShot({ mode: 'animation', diagram: 'chain' }, 'Beat', 'x')));
});

test('every layout fits content-sized labels inside their own nodes in three formats, including partial labels', () => {
  const long = ['ABCDEFGHIJKL', 'MNOPQRSTUVWX', 'Longer label', 'Two words', 'Sixth', 'Last'];
  for (const format of FORMAT_IDS) for (const kind of BOARD_KINDS) for (const labels of [LABELS, long, ['', 'Second', '', 'Last']]) {
    const [W, H] = FORMATS[format], spots = DUO(W, H), R = boardRect(W, H, place(spots, [undefined, 'question'])), s = R.w / BOARD_W;
    const { ctx } = surface(W, H), t = timingOf(3, 0.2);
    drawBoard(ctx, kind, 2, t, R, '"Inter"', labels);
    const ink = find({ data: ctx.getImageData(0, 0, W, H).data, W, H }, [244, 251, 252]);
    const nodes = boardState(kind, 2, t, labels.length).boxes;
    nodes.forEach((b, i) => {
      const x = R.x + b.x * s, y = R.y + b.y * s;
      const inCell = [...ink.cells].map((c) => c.split(',').map(Number)).filter(([px, py]) => Math.abs(px - x) < BOX_W * s / 2 && Math.abs(py - y) < BOX_H * s / 2);
      assert.ok(labels[i] ? inCell.length > 30 : inCell.length === 0, `${format} ${kind} slot ${i}: visible fitted text`);
      if (labels === LABELS) {
        const ys = inCell.map(([, py]) => py);
        assert.ok(Math.max(...ys) - Math.min(...ys) + 1 >= (format === '9:16' ? 26 : 15), `${format} ${kind} slot ${i}: readable rendered cap/descender height`);
      }
      assert.ok(inCell.every(([px, py]) => Math.abs(px - x) < BOX_W * s * 0.45 && Math.abs(py - y) < BOX_H * s * 0.45));
    });
    for (const cell of ink.cells) {
      const [px, py] = cell.split(',').map(Number);
      assert.ok(nodes.some((b) => Math.abs(px - R.x - b.x * s) < BOX_W * s / 2 && Math.abs(py - R.y - b.y * s) < BOX_H * s / 2), `${format} ${kind}: all text inside nodes`);
    }
  }
});

/** Separate node plates from their links and text using the rendered fill, without preset coordinates. */
function nodePlates(p: { data: Uint8ClampedArray; W: number; H: number }, R: BoardRect): Box[] {
  const pixels = new Set<number>();
  for (let y = Math.ceil(R.y); y < R.y + R.h; y++) for (let x = Math.ceil(R.x); x < R.x + R.w; x++) {
    const i = (y * p.W + x) * 4;
    if (p.data[i] === 19 && p.data[i + 1] === 29 && p.data[i + 2] === 31) pixels.add(y * p.W + x);
  }
  const plates: Box[] = [];
  while (pixels.size) {
    const start = pixels.values().next().value!;
    pixels.delete(start);
    const pending = [start], bounds: Box = { x0: p.W, y0: p.H, x1: 0, y1: 0 };
    let n = 0;
    while (pending.length) {
      const cell = pending.pop()!, x = cell % p.W, y = Math.floor(cell / p.W);
      n++;
      bounds.x0 = Math.min(bounds.x0, x); bounds.x1 = Math.max(bounds.x1, x);
      bounds.y0 = Math.min(bounds.y0, y); bounds.y1 = Math.max(bounds.y1, y);
      for (const next of [cell - 1, cell + 1, cell - p.W, cell + p.W]) if (pixels.delete(next)) pending.push(next);
    }
    if (n > 100) plates.push(bounds);
  }
  // Enclosed glyph counters share the plate fill; only outer components are node plates.
  return plates.filter((b, i) => !plates.some((outer, j) => j !== i
    && outer.x0 <= b.x0 && outer.y0 <= b.y0 && outer.x1 >= b.x1 && outer.y1 >= b.y1));
}

for (const kind of BOARD_KINDS) test(`${kind}: scene pixels follow content count with blank and fitted labels in every format`, () => {
  const t = timingOf(3, 0.2);
  const maximum = ['ABCDEFGHIJKL', 'MNOPQRSTUVWX', 'WWWWWWWWWWWW', 'iiiiiiiiiiii', '0123456789AB', 'Last'];
  for (const format of FORMAT_IDS) for (const labels of [undefined, ['Only'], LABELS, ['', 'Second', ''], maximum]) {
    const [W, H] = FORMATS[format], R = boardRect(W, H, place(DUO(W, H), [undefined, undefined]));
    const stored: ProjectShot = { diagram: kind, backdrop: 'black', pose: 'rest', costar: guest('teal'), interaction: 'face' };
    const p = frame({ ...stored, boardLabels: labels }, 3, format, t), plates = nodePlates(p, R);
    assert.equal(plates.length, labels?.length ?? 3, `${format}: rendered plates follow content count`);
    plates.forEach((b) => assert.ok(b.x0 >= R.x && b.y0 >= R.y && b.x1 <= R.x + R.w && b.y1 <= R.y + R.h));
    const ink = find(p, [244, 251, 252]);
    assert.ok([...ink.cells].every((c) => {
      const [x, y] = c.split(',').map(Number);
      return plates.some((b) => x > b.x0 && x < b.x1 && y > b.y0 && y < b.y1);
    }), `${format}: every label pixel stays inside a node`);
    const s = R.w / BOARD_W;
    boardState(kind, 3, t, labels?.length ?? 3).boxes.forEach((node, i) => {
      const x = R.x + node.x * s, y = R.y + node.y * s;
      const inCell = [...ink.cells].map((c) => c.split(',').map(Number))
        .filter(([px, py]) => Math.abs(px - x) < BOX_W * s / 2 && Math.abs(py - y) < BOX_H * s / 2);
      assert.ok(labels?.[i] ? inCell.length > 30 : inCell.length === 0, `${format} slot ${i}: labelled or empty`);
      if (labels?.[i]) {
        const single = frame({ ...stored, boardLabels: labels.map((label, slot) => slot === i ? label : '') }, 3, format, t);
        const singleInk = find(single, [244, 251, 252]);
        assert.ok(inCell.every(([px, py]) => singleInk.cells.has(`${px},${py}`)), `${format}: each label is independent`);
      }
    });
  }
});

test('independent presets retain identical pixels through shuffled and interleaved bubbles, labels and layouts in one environment', () => {
  const t = timingOf(3, 0.2), cases: { stored: ProjectShot; format: Format }[] = [];
  for (const format of FORMAT_IDS) for (const [i, kind] of BOARD_KINDS.entries()) {
    cases.push({ format, stored: { diagram: kind, backdrop: 'black', bubble: BUBBLES[i], boardLabels: i % 2 ? ['', 'Branch', '', 'Leaf'] : LABELS,
      ...(i % 3 ? { costar: guest('teal', { bubble: BUBBLES[(i + 1) % BUBBLES.length] }) } : {}),
      ...(i % 3 === 2 ? { ensemble: [guest('slate', { bubble: 'question' })] } : {}) } });
    cases.push({ format, stored: { diagram: kind, layout: (['screen', 'title', 'signoff'] as const)[i % 3], backdrop: 'black', mark: i % 2 ? 'END' : undefined,
      badges: [{ text: 'Notes', tone: 'ink' }, { text: 'Index', tone: 'paper' }], boardLabels: ['Separate'] } });
  }
  const expected = cases.map(({ stored, format }) => frame(stored, 2.8, format, t, 0.25, nodeEnv()).hash);
  const shared = nodeEnv(), order = [...cases.keys()].reverse();
  for (const i of [...order, ...order.slice(7), ...order.slice(0, 7), ...cases.keys()]) {
    const { stored, format } = cases[i];
    assert.equal(frame(stored, 2.8, format, t, 0.25, shared).hash, expected[i], `${format} ${stored.diagram} ${stored.layout ?? 'stage'}: no render-order state`);
  }
});

test('independent diagrams render visibly with missing lead, co-star and ensemble profiles while preserving every unknown id', () => {
  const unknown = ['missing-lead-rig', 'missing-costar-rig', 'missing-member-rig'];
  for (const format of FORMAT_IDS) for (const kind of BOARD_KINDS) {
    const stored: ProjectShot = { diagram: kind, backdrop: 'black', hideHeadline: true, character: unknown[0], bubble: 'think', boardLabels: ['First', '', 'Third'],
      costar: { character: unknown[1], bubble: 'say' }, ensemble: [{ character: unknown[2], bubble: 'exclaims' }] };
    const shot = resolveShot(stored, 'Beat', 'x');
    assert.deepEqual([shot.character, shot.costar?.character, shot.ensemble?.[0].character], unknown);
    const [W, H] = FORMATS[format], { ctx } = surface(W, H), labels = stageLabels(ctx, shot, W, H, env);
    assert.ok(labels.board && Object.values(labels.board).every(Number.isFinite) && labels.board.w > 0 && labels.board.h > 0);
    drawSceneFrame(ctx, 'animation', shot, 3, timingOf(3, 0.2), W, H, env);
    const p = { data: ctx.getImageData(0, 0, W, H).data, W, H };
    assert.equal(nodePlates(p, labels.board!).length, 3, `${format} ${kind}: diagrams stay visible with missing performers`);
    const slots = ensembleLayout(W, H, [DEFAULT_RIG, DEFAULT_RIG, DEFAULT_RIG]).slots;
    for (const slot of slots) {
      let visible = 0;
      for (let y = Math.floor(slot.by - slot.h); y < slot.by; y++) for (let x = Math.floor(slot.x - slot.h * 0.2); x < slot.x + slot.h * 0.2; x++) {
        const i = (y * W + x) * 4;
        if (p.data[i] + p.data[i + 1] + p.data[i + 2] > 150 && p.data[i + 3] === 255) visible++;
      }
      assert.ok(visible > 100, `${format} ${kind}: each missing performer has a visible placeholder`);
    }
    const m = ctx.getTransform();
    assert.ok([m.a, m.b, m.c, m.d, m.e, m.f].every(Number.isFinite));
  }
});

// ---------------------------------------------------------------- labels on an ensemble stage

const LABEL_SAMPLE: ProjectShot = {
  hideHeadline: false, backdrop: 'work', diagram: 'tree', character: 'lens', pose: 'rest', expression: 'neutral', move: 'idle',
  costar: { character: 'lens', pose: 'rest', expression: 'curious', move: 'idle' },
  ensemble: [
    { character: 'lens', pose: 'wave', expression: 'happy', move: 'idle' },
    { character: 'lens', pose: 'point', expression: 'focused', move: 'idle' },
    { character: 'lens', pose: 'think', expression: 'neutral', move: 'idle' },
  ],
  headline: 'EVENING WALK', eyebrow: '', accent: '#2bb3a3', headlineAt: 'costar',
  badges: [{ text: 'HI', tone: 'accent' }, { text: '7', tone: 'ink' }, { text: 'OAK', tone: 'paper' }],
};

test('the headline sits over the co-star and the badges under the members, clear of the board, the heads and each other, in every format', () => {
  const { ctx } = surface(8, 8);
  for (const format of FORMAT_IDS) {
    const [W, H] = FORMATS[format], shot = resolveShot({ mode: 'animation', ...LABEL_SAMPLE }, 'Beat', 'x'), L = stageLabels(ctx, shot, W, H, env);
    const spots = ensembleLayout(W, H, Array.from({ length: 5 }, () => DEFAULT_RIG)).slots.map((s) => ({ ...s, rig: DEFAULT_RIG }));
    const h = L.headline!, B = L.badges!, tag = format;
    ctx.font = `900 ${h.fs}px "Inter"`;
    const w = Math.max(...h.lines.map((l) => ctx.measureText(l).width));
    const text: Box = { x0: h.cx - w / 2, x1: h.cx + w / 2, y0: h.y - h.fs * 0.8, y1: h.y + (h.lines.length - 1) * h.fs * 1.05 + h.fs * 0.25 };
    assert.ok(h.fs >= 56, `${tag}: a headline you can read (${h.fs} px)`);
    assert.ok(text.x0 >= 0 && text.x1 <= W && text.y0 >= 0, `${tag}: in frame ${JSON.stringify(text)}`);
    assert.ok(!overlaps(text, rectBox(L.board!)), `${tag}: clear of the board`);
    for (const s of spots) assert.ok(!overlaps(text, bodyBox(s)), `${tag}: clear of the performer at ${s.x.toFixed(0)}`);
    assert.ok(text.x0 < spots[1].x && spots[1].x < text.x1, `${tag}: over the co-star`);
    assert.ok(B.fb >= 36 && B.items.length === 3, `${tag}: badges you can read (${B.fb} px)`);
    const boxes = B.items.map((b) => ({ x0: b.x - b.w / 2, x1: b.x + b.w / 2, y0: b.y - B.bh / 2, y1: b.y + B.bh / 2 }));
    boxes.forEach((b, i) => {
      assert.ok(b.x0 >= 0 && b.x1 <= W && b.y1 + B.bh * 0.12 <= H, `${tag}: badge ${i} in frame ${JSON.stringify(b)}`);
      assert.ok(b.y0 >= spots[0].by, `${tag}: badge ${i} under the feet`);
      assert.ok(b.x0 < spots[i + 2].x && spots[i + 2].x < b.x1, `${tag}: badge ${i} under member ${i}`);
    });
    boxes.forEach((b, i) => boxes.slice(i + 1).forEach((other) => assert.ok(!overlaps(b, other), `${tag}: the badges apart`)));
  }
});

test('a headline over the co-star keeps clear of every head, bubble and board in an actual duo and ensemble, in every format', () => {
  const { ctx } = surface(8, 8);
  const lens = (bubble?: BubbleId) => ({ character: 'lens', pose: 'rest', expression: 'neutral', move: 'idle', ...(bubble && { bubble }) });
  const cases: { name: string; stored: ProjectShot; spots: (W: number, H: number) => BoardSpot[] }[] = [
    { name: 'duo', stored: { ...lens('think'), costar: lens('say'), interaction: 'face', diagram: 'chain', boardLabels: ['Map', 'Desk'] },
      spots: (W, H) => { const D = duoLayout(W, H, DEFAULT_RIG, DEFAULT_RIG, true); return [D.lead, D.costar].map((s) => ({ ...s, rig: DEFAULT_RIG })); } },
    { name: 'duo without a board', stored: { ...lens('exclaim'), costar: lens('question'), diagram: 'none' },
      spots: (W, H) => { const D = duoLayout(W, H, DEFAULT_RIG, DEFAULT_RIG, false); return [D.lead, D.costar].map((s) => ({ ...s, rig: DEFAULT_RIG })); } },
    { name: 'ensemble', stored: { ...lens('say'), costar: lens('exclaims'), ensemble: [lens('question'), lens(), lens('think')], diagram: 'list' },
      spots: (W, H) => ensembleLayout(W, H, Array.from({ length: 5 }, () => DEFAULT_RIG)).slots.map((s) => ({ ...s, rig: DEFAULT_RIG })) },
  ];
  for (const format of FORMAT_IDS) for (const { name, stored, spots: spotsOf } of cases) {
    const [W, H] = FORMATS[format], tag = `${format} ${name}`;
    const shot = resolveShot({ mode: 'animation', hideHeadline: false, headlineAt: 'costar', headline: 'GALLERY HOURS', backdrop: 'work', ...stored }, 'Beat', 'x');
    const L = stageLabels(ctx, shot, W, H, env), h = L.headline!, spots = spotsOf(W, H);
    const kinds = [shot.bubble, shot.costar?.bubble, ...(shot.ensemble ?? []).map((m) => m.bubble)];
    ctx.font = `900 ${h.fs}px "Inter"`;
    const w = Math.max(...h.lines.map((l) => ctx.measureText(l).width));
    const text: Box = { x0: h.cx - w / 2, x1: h.cx + w / 2, y0: h.y - h.fs * 0.8, y1: h.y + (h.lines.length - 1) * h.fs * 1.05 + h.fs * 0.25 };
    assert.ok(h.fs >= 40, `${tag}: a headline you can read (${h.fs} px)`);
    assert.ok(text.x0 >= 0 && text.x1 <= W && text.y0 >= 0, `${tag}: in frame ${JSON.stringify(text)}`);
    assert.equal(!!L.board, shot.diagram !== 'none', `${tag}: the board is placed only when drawn`);
    if (L.board) assert.ok(!overlaps(text, rectBox(L.board)), `${tag}: clear of the board`);
    spots.forEach((s, i) => {
      assert.ok(!overlaps(text, bodyBox(s)), `${tag}: clear of performer ${i}'s head`);
      const kind = kinds[i];
      if (kind) assert.ok(!overlaps(text, bubbleBox(s, kind, W, H)), `${tag}: clear of performer ${i}'s ${kind}`);
    });
  }
});

test('the headline fades in a quarter of the way in, then the badges drop one after another from 45%', () => {
  const dur = 5.6, t = timingOf(dur, null), [FW, FH] = FORMATS['16:9'], { ctx } = surface(8, 8);
  const L = stageLabels(ctx, resolveShot({ mode: 'animation', ...LABEL_SAMPLE }, 'Beat', 'x'), FW, FH, env);
  const count = (lt: number, box: Box, test: (r: number, g: number, b: number) => boolean) => {
    const p = frame(LABEL_SAMPLE, lt, '16:9', t, 0.5);
    let n = 0;
    for (let y = Math.max(0, Math.floor(box.y0 / 2)); y < Math.min(p.H, box.y1 / 2); y++) for (let x = Math.max(0, Math.floor(box.x0 / 2)); x < Math.min(p.W, box.x1 / 2); x++) {
      const i = (y * p.W + x) * 4;
      if (test(p.data[i], p.data[i + 1], p.data[i + 2])) n++;
    }
    return n;
  };
  const h = L.headline!, text: Box = { x0: h.cx - 400, x1: h.cx + 400, y0: h.y - h.fs, y1: h.y + h.fs * 1.3 };
  const white = (r: number, g: number, b: number) => r > 235 && g > 235 && b > 235;
  assert.equal(count(dur * LABEL_HEADLINE - 0.05, text, white), 0, 'no headline before a quarter of the way in');
  assert.ok(count(dur * LABEL_HEADLINE + 0.8, text, white) > 400, 'the headline in');
  const [greeting, time, oak] = L.badges!.items.map((b) => ({ x0: b.x - b.w / 2, x1: b.x + b.w / 2, y0: b.y - L.badges!.bh / 2, y1: b.y + L.badges!.bh / 2 }));
  const ink = (r: number, g: number, b: number) => r >= 45 && r <= 70 && g >= 50 && g <= 75 && b >= 60 && b <= 85;
  const accent = (r: number, g: number, b: number) => g > 100 && g > r + 45 && g > b + 5;
  const t0 = dur * LABEL_BADGES;
  assert.equal(count(t0 - 0.05, greeting, accent), 0, 'no badge before 45%');
  assert.ok(count(t0 + 1, greeting, accent) > 300, 'HI accent plate at rest under the first member');
  assert.ok(count(t0 + 0.25 - 0.02, time, ink) < count(t0 + 1.3, time, ink) / 4, '7 ink plate a beat later');
  assert.ok(count(t0 + 1.3, time, ink) > 300, '7 ink plate at rest under the second member');
  assert.ok(count(t0 + 0.5 - 0.02, oak, white) < count(t0 + 1.6, oak, white) / 4, 'OAK paper plate after the second badge');
  assert.ok(count(t0 + 1.6, oak, white) > 300, 'OAK paper plate at rest under the third member');
});

test('member badges land without the title card\'s sheen: each one at rest stays the same from landing on', () => {
  const dur = 5.6, t = timingOf(dur, null), [FW, FH] = FORMATS['16:9'], { ctx } = surface(8, 8), stored = { ...LABEL_SAMPLE, backdrop: 'black' as const };
  const B = stageLabels(ctx, resolveShot({ mode: 'animation', ...stored }, 'Beat', 'x'), FW, FH, env).badges!;
  const crop = (lt: number, i: number) => {
    const p = frame(stored, lt, '16:9', t, 0.5), b = B.items[i], out: number[] = [];
    for (let y = Math.floor((b.y - B.bh / 2) / 2); y < Math.ceil((b.y + B.bh / 2) / 2); y++) {
      for (let x = Math.floor((b.x - b.w / 2) / 2); x < Math.ceil((b.x + b.w / 2) / 2); x++) out.push(...p.data.slice((y * p.W + x) * 4, (y * p.W + x) * 4 + 4));
    }
    return out;
  };
  B.items.forEach((_, i) => {
    const rest = dur * LABEL_BADGES + i * 0.25 + 0.7, settled = crop(rest + 0.9, i);
    assert.ok(settled.filter((v, k) => k % 4 === 0 && v > 60).length > 100, `badge ${i} is drawn`);
    for (const u of [0.1, 0.3, 0.5]) assert.deepEqual(crop(rest + u, i), settled, `badge ${i}, ${u} s after landing: no sheen crosses it`);
  });
});

test('labels are drawn only on a duo or an ensemble that has them; a title card keeps its own', () => {
  const t = timingOf(4, null);
  const plain = { ...LABEL_SAMPLE, headlineAt: undefined, badges: undefined };
  assert.equal(frame({ ...plain, headlineAt: 'costar', costar: undefined, ensemble: undefined }, 3, '16:9', t, 0.25).hash,
    frame({ ...plain, costar: undefined, ensemble: undefined }, 3, '16:9', t, 0.25).hash, 'no co-star: the headline keeps its own place');
  assert.notEqual(frame(LABEL_SAMPLE, 3, '16:9', t, 0.25).hash, frame(plain, 3, '16:9', t, 0.25).hash);
});
