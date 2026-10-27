// Pure source⇄timeline mapping for the transcript panel. Frames are canonical (mirrors
// project.json); `fps` always comes from `project.fps`. A word is "cut" when its source
// frame is covered by NO speech clip of that media — such words render struck and are skipped by
// the karaoke walk. Speech-track-only: b-roll/music/text layers never bound a transcript word.

import type { Project, Clip } from "../types/project";
import type { TranscriptWord, TranscriptFiller } from "../api";
import type { PendingCut } from "../store/useProjectStore";

// Mirror pipeline/timebase.py sec_to_frame: round half away from zero (NOT banker's rounding).
export function sec_to_frame(sec: number, fps: number): number {
  const x = sec * fps;
  return x >= 0 ? Math.floor(x + 0.5) : Math.ceil(x - 0.5);
}

export function wordSrcFrame(word: TranscriptWord, fps: number): number {
  return sec_to_frame(word.start, fps);
}

function speechClips(project: Project): Clip[] {
  return project.tracks.find((t) => t.kind === "speech")?.clips ?? [];
}

// The speech clip whose source window [srcInF, srcOutF) covers `srcF` of `media`, or null.
export function speechClipAt(project: Project, media: string, srcF: number): Clip | null {
  for (const c of speechClips(project)) {
    if (c.media.source === media && srcF >= c.srcInF && srcF < c.srcOutF) return c;
  }
  return null;
}

// Source frame → timeline frame; null ⇒ the frame is inside a cut region (no covering clip).
export function srcToTimeline(project: Project, media: string, srcF: number): number | null {
  const c = speechClipAt(project, media, srcF);
  return c ? c.tlStartF + (srcF - c.srcInF) : null;
}

// Source range [srcStartF, srcEndF) of `media` → the SOURCE sub-ranges still on the timeline: intersect
// with each speech clip's source window, one run per contiguous KEPT region (already-cut/struck source
// is excluded). Empty when the whole range is cut. Shared by wordSpanToCut (selection → pending) and
// srcRangeToTimeline (pending → timeline at accept). Sorted ascending by source frame.
export function coveredSrcRuns(
  project: Project,
  media: string,
  srcStartF: number,
  srcEndF: number,
): { clip: Clip; a: number; b: number }[] {
  const out: { clip: Clip; a: number; b: number }[] = [];
  for (const c of speechClips(project)) {
    if (c.media.source !== media) continue;
    const a = Math.max(srcStartF, c.srcInF);
    const b = Math.min(srcEndF, c.srcOutF);
    if (a >= b) continue;
    out.push({ clip: c, a, b });
  }
  out.sort((x, y) => x.a - y.a);
  return out;
}

// Timeline cut range(s) for a source range [srcStartF, srcEndF) of `media`, mapped through the CURRENT
// clip list (accept-time conversion). A struck gap inside the range SPLITS it into multiple
// timeline ranges (each a kept run); a fully-cut range yields [] (dropped at accept time). Never uses
// a raw source-frame delta as a timeline delta — that was the over-cut bug.
export function srcRangeToTimeline(
  project: Project,
  media: string,
  srcStartF: number,
  srcEndF: number,
): { startF: number; endF: number }[] {
  return coveredSrcRuns(project, media, srcStartF, srcEndF).map(({ clip, a, b }) => {
    const startF = clip.tlStartF + (a - clip.srcInF);
    return { startF, endF: startF + (b - a) };
  });
}

// SOURCE range(s) covering a contiguous word span `first`..`last` of `media`, clipped to the words
// actually selected and split into one run per KEPT region — a selection spanning an already-cut
// (struck) gap yields MULTIPLE source ranges, not one over-covering range. Empty when the
// whole span is cut or belongs to another media. Callers resolve untimed words before passing TIMED words.
export function wordSpanToCut(
  project: Project,
  media: string,
  first: TranscriptWord,
  last: TranscriptWord,
  fps: number,
): { srcStartF: number; srcEndF: number }[] {
  const startSrcF = sec_to_frame(first.start, fps);
  const endSrcF = sec_to_frame(last.end, fps);
  if (!Number.isFinite(startSrcF) || !Number.isFinite(endSrcF) || endSrcF <= startSrcF) return [];
  return coveredSrcRuns(project, media, startSrcF, endSrcF).map(({ a, b }) => ({
    srcStartF: a,
    srcEndF: b,
  }));
}

// A source range the user dismissed (intentional pause) or a neighbouring kept word — filler padding
// must never expand across one. Same shape as the panel's `kept` state and PendingCut src bounds.
export interface KeptRange {
  media: string;
  srcStartF: number;
  srcEndF: number;
}

// A verbatim filler token → a source-space PendingCut{kind:"filler"}. The token
// rides its own `fillers[]` array — never the word index space — so the cut is derived purely
// from its start/end TIMES, exactly like a silence proposal. The span [sec_to_frame(start),
// sec_to_frame(end)) is EXPANDED by `padF` each side to swallow the clicky co-articulated edges
// (config.FILLER_PAD_MS), then the padding is CLIPPED so it never crosses into a `kept` range (an
// intentional pause the user protected, or a neighbouring kept word) and the result is intersected
// with the speech clips still on the timeline. Returns null when nothing of the filler itself survives
// (fully cut already, wrong media, or squeezed to zero width). The covering run MUST overlap the filler
// word, never a stray padding sliver of unrelated kept content (no `?? runs[0]` fallback).
export function fillerToCut(
  filler: Pick<TranscriptFiller, "start" | "end">,
  padF: number,
  fps: number,
  project: Project,
  media: string,
  kept: KeptRange[],
): PendingCut | null {
  const w0 = sec_to_frame(filler.start, fps);
  const w1 = sec_to_frame(filler.end, fps);
  if (!Number.isFinite(w0) || !Number.isFinite(w1) || w1 <= w0) return null;
  let a = w0 - padF;
  let b = w1 + padF;
  for (const k of kept) {
    if (k.media !== media) continue;
    if (_overlap(a, b, k.srcStartF, k.srcEndF)) {
      // Clip on the side the kept range sits: left of the word ⇒ raise the start; right ⇒ lower the end.
      if (k.srcEndF <= w0) a = Math.max(a, k.srcEndF);
      else if (k.srcStartF >= w1) b = Math.min(b, k.srcStartF);
      else return null; // a kept range straddles the filler word itself — respect it, propose nothing
    }
  }
  if (b <= a) return null;
  const runs = coveredSrcRuns(project, media, a, b);
  // The covering run must overlap the filler WORD [w0,w1). When the word itself sits inside an already-
  // cut region (only its padding reaches a surviving clip), there is no such run → return null. Falling
  // back to runs[0] here would emit a padding sliver of UNRELATED kept content as a spurious cut.
  const run = runs.find((r) => r.a < w1 && w0 < r.b);
  if (!run) return null;
  const srcStartF = run.a;
  const srcEndF = run.b;
  if (srcEndF <= srcStartF) return null;
  return { media, srcStartF, srcEndF, kind: "filler", durSec: (srcEndF - srcStartF) / fps, conf: 1 };
}

// The `kept` guards protecting a filler's real-word neighbours: the previous timed word's end and
// the next timed word's start (by TIME, since fillers carry no word index) bound the ±padF expansion so
// it never eats a real word's audio. Returned as KeptRange[] fed into fillerToCut's existing side-aware
// clip — a left guard ends at the prev word's end frame, a right guard starts at the next word's start
// frame. Empty when a filler has no timed neighbour on a side (leading/trailing).
export function fillerNeighborKept(
  filler: Pick<TranscriptFiller, "start" | "end">,
  timed: TimedWord[],
  media: string,
  fps: number,
): KeptRange[] {
  let prevEnd = -Infinity;
  let nextStart = Infinity;
  for (const t of timed) {
    if (t.end <= filler.start && t.end > prevEnd) prevEnd = t.end;
    if (t.start >= filler.end && t.start < nextStart) nextStart = t.start;
  }
  const out: KeptRange[] = [];
  if (Number.isFinite(prevEnd)) {
    const f = sec_to_frame(prevEnd, fps);
    out.push({ media, srcStartF: f - 1, srcEndF: f });
  }
  if (Number.isFinite(nextStart)) {
    const f = sec_to_frame(nextStart, fps);
    out.push({ media, srcStartF: f, srcEndF: f + 1 });
  }
  return out;
}

// EXPAND each filler cut by this much to swallow the clicky co-articulated edges (mirrors
// config.FILLER_PAD_MS; clipped to kept + neighbouring words in fillerToCut). Distinct from the
// pause path's inward SILENCE_PAD_MS.
const FILLER_PAD_MS = 40;

// "Remove all fillers": one padded source-space cut per filler still on the timeline,
// deduped, for the ONE batch-accept checkpoint. The padding is clipped to `kept` intentional pauses AND to
// each filler's real-word neighbours; a filler already inside a cut region yields no cut and is
// counted in `skipped`, never emitted as a spurious sliver.
export function fillerBatch(
  fillers: TranscriptFiller[],
  project: Project,
  media: string,
  timed: TimedWord[],
  fps: number,
  kept: KeptRange[] = [],
): { cuts: PendingCut[]; skipped: number } {
  const padF = sec_to_frame(FILLER_PAD_MS / 1000, fps);
  const cuts: PendingCut[] = [];
  const seen = new Set<string>();
  let skipped = 0;
  for (const f of fillers) {
    const cut = fillerToCut(f, padF, fps, project, media, [...kept, ...fillerNeighborKept(f, timed, media, fps)]);
    if (!cut) {
      skipped += 1;
      continue;
    }
    const key = `${cut.media}:${cut.srcStartF}:${cut.srcEndF}`;
    if (seen.has(key)) continue;
    seen.add(key);
    cuts.push(cut);
  }
  return { cuts, skipped };
}

function _overlap(a0: number, a1: number, b0: number, b1: number): boolean {
  return a0 < b1 && b0 < a1;
}

// A display token for the interleaved transcript paragraph. A real word carries its flat
// `wordIdx` (the cross-phase index space, used for keys/highlights/selection); a filler carries ONLY a
// synthetic `key` — it is display-only and NEVER mints an index into the word space.
export type DisplayToken =
  | { filler: false; word: TranscriptWord; idx: number }
  | { filler: true; token: TranscriptFiller; key: string };

// Interleave one segment's words with its fillers, ordered by start TIME for display. Words
// keep their flat index and document order; each filler is placed before the first timed word whose
// start exceeds it (else trailing) and gets a synthetic key `f:<segIdx>:<start>`. Untimed words (null
// start) never gate a filler — a filler lands before the next timed word or at the paragraph tail.
export function interleaveFillers(
  segWords: { word: TranscriptWord; idx: number }[],
  fillers: TranscriptFiller[],
): DisplayToken[] {
  const fs = [...fillers].sort((x, y) => x.start - y.start);
  const out: DisplayToken[] = [];
  let fi = 0;
  const emitFiller = (f: TranscriptFiller): void => {
    out.push({ filler: true, token: f, key: `f:${f.segIdx}:${f.start}` });
  };
  for (const sw of segWords) {
    const ws = sw.word.start;
    if (Number.isFinite(ws)) {
      while (fi < fs.length && fs[fi].start < ws) emitFiller(fs[fi++]);
    }
    out.push({ filler: false, word: sw.word, idx: sw.idx });
  }
  while (fi < fs.length) emitFiller(fs[fi++]);
  return out;
}

// The undo-checkpoint label for a batch of accepted cuts. A homogeneous batch names its kind and
// count ("Removed N pause(s)" / "Removed N filler(s)"); anything else (a selection, or a mixed batch)
// falls back to the total duration ("Cut Xs" from the summed durSec).
export function batchCutLabel(cuts: PendingCut[]): string {
  const n = cuts.length;
  if (n > 0 && cuts.every((c) => c.kind === "pause")) return `Removed ${n} pause${n === 1 ? "" : "s"}`;
  if (n > 0 && cuts.every((c) => c.kind === "filler")) return `Removed ${n} filler${n === 1 ? "" : "s"}`;
  const sec = cuts.reduce((acc, c) => acc + c.durSec, 0).toFixed(1);
  return `Cut ${sec}s`;
}

// Timeline frame → {media, source frame} of the covering speech clip, or null in a gap.
export function timelineToSrc(
  project: Project,
  tlF: number,
): { media: string; srcF: number } | null {
  for (const c of speechClips(project)) {
    const dur = c.srcOutF - c.srcInF;
    if (tlF >= c.tlStartF && tlF < c.tlStartF + dur) {
      return { media: c.media.source, srcF: c.srcInF + (tlF - c.tlStartF) };
    }
  }
  return null;
}

// ── Karaoke / word-timing helpers (robust to WhisperX unaligned words) ────────────────────────────
// WhisperX leaves some tokens (numerals, symbols) unaligned → the reshape emits null start/end. Such
// "untimed" words must never coerce to frame 0 and corrupt the binary search or seek a click to the
// clip head. Only TIMED words (finite start AND end) can be the current karaoke word; untimed
// words render attached to their neighbor with no highlight of their own.

export interface TimedWord {
  idx: number; // flat index into the original word list
  start: number;
  end: number;
}

function isTimed(w: TranscriptWord): boolean {
  return Number.isFinite(w.start) && Number.isFinite(w.end);
}

// The timed subsequence of the flat word list, in order (monotonic in `start`).
export function timedWords(words: TranscriptWord[]): TimedWord[] {
  const out: TimedWord[] = [];
  for (let i = 0; i < words.length; i++) {
    const w = words[i];
    if (isTimed(w)) out.push({ idx: i, start: w.start, end: w.end });
  }
  return out;
}

// Flat index of the current karaoke word at source-seconds `sec`: the rightmost timed word whose
// [start,end) contains `sec` and whose flat index is not struck (inside an applied cut); -1 if none.
// Binary search runs over the timed subsequence, so a null start can never break the sorted invariant.
export function currentWordIdx(timed: TimedWord[], sec: number, struck: Set<number>): number {
  let lo = 0;
  let hi = timed.length - 1;
  let hit = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (timed[mid].start <= sec) {
      hit = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  if (hit < 0) return -1;
  const t = timed[hit];
  if (sec >= t.end || struck.has(t.idx)) return -1;
  return t.idx;
}

// For each flat word, the index of the timed word it resolves to for seeking/selection: itself
// when timed, else the nearest timed neighbor (previous preferred, then next). -1 only when NO word is
// timed. So a click or selection boundary on an unaligned word falls back to its neighbor, not frame 0.
export function resolveTimed(words: TranscriptWord[]): number[] {
  const n = words.length;
  const timed = words.map(isTimed);
  const out = new Array<number>(n).fill(-1);
  let prev = -1;
  for (let i = 0; i < n; i++) {
    if (timed[i]) prev = i;
    out[i] = timed[i] ? i : prev;
  }
  // Leading untimed words got -1 (no previous timed word); fill them from the next timed word.
  let next = -1;
  for (let i = n - 1; i >= 0; i--) {
    if (timed[i]) next = i;
    else if (out[i] === -1) out[i] = next;
  }
  return out;
}
