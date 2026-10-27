// Pure geometry for the timeline panel — frame↔px, ruler ticks, mm:ss labels, and the peak
// windowing that slices a file-wide waveform down to one speech clip's source span. No DOM,
// no React, so every helper unit-tests under vitest's node env. Frames are canonical
// and `fps` always comes from `project.fps`.

import type { Project } from "../types/project";

// The video's length: where the speech ends, or later only where a time-pinned element runs past it.
// A word-anchored cue never lengthens the video. It starts on its word and keeps its authored length,
// and whatever of it runs past this end isn't played, so a reorder, cut or restore that moves its word
// can't add a silent tail, and moving the word back plays the cue whole again. With no speech and no
// time pin, the longest element sets it; 0 for an empty project. The export trims to the same end
// (pipeline/edits.py timeline_end_f).
export function projectDurationF(project: Project): number {
  let pinned = 0;
  let longest = 0;
  for (const track of project.tracks) {
    const spans = [
      ...(track.clips ?? []).map((c) => ({ end: c.tlStartF + c.srcOutF - c.srcInF, anchor: c.anchor })),
      ...(track.items ?? []).map((i) => ({ end: i.tlStartF + i.durF, anchor: i.anchor })),
    ];
    for (const { end, anchor } of spans) {
      longest = Math.max(longest, end);
      if (track.kind === "speech" || anchor?.mode !== "word") pinned = Math.max(pinned, end);
    }
  }
  return pinned || longest;
}

// How many frames of an element starting at `tlStartF` for `durF` frames play before the video ends.
export function playedF(project: Project, tlStartF: number, durF: number): number {
  return Math.max(0, Math.min(tlStartF + durF, projectDurationF(project)) - tlStartF);
}

// "M:SS" from a timeline frame (floor to whole seconds; minutes uncapped).
export function mmss(frame: number, fps: number): string {
  const totalSec = Math.floor(frame / fps);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export interface RulerTick {
  frame: number;
  label: string;
}

// A ruler tick draws no denser than one per MIN_TICK_PX, and only on whole-second boundaries.
const MIN_TICK_PX = 64;

// Ticks on whole-second boundaries, spaced so consecutive ticks are ≥ MIN_TICK_PX apart. The
// step is the smallest whole number of seconds whose pixel width clears the threshold.
export function rulerTicks(durationF: number, fps: number, pxPerFrame: number): RulerTick[] {
  if (durationF <= 0 || fps <= 0 || pxPerFrame <= 0) return [];
  const pxPerSec = fps * pxPerFrame;
  const secStep = Math.max(1, Math.ceil(MIN_TICK_PX / pxPerSec));
  const frameStep = secStep * fps;
  const ticks: RulerTick[] = [];
  for (let f = 0; f <= durationF; f += frameStep) {
    ticks.push({ frame: f, label: mmss(f, fps) });
  }
  return ticks;
}

export function frameToPx(frame: number, pxPerFrame: number): number {
  return frame * pxPerFrame;
}

// The scale that makes the complete timeline fit the visible horizontal viewport after its fixed
// track-name gutter. Kept pure so a toolbar action and a ResizeObserver-driven initial layout use
// exactly the same calculation.
export function fitPxPerFrame(
  viewportPx: number,
  durationF: number,
  gutterPx: number,
  fallbackPxPerFrame: number,
): number {
  if (viewportPx <= gutterPx) return fallbackPxPerFrame;
  // Fit is intentionally NOT constrained by the manual zoom rails. Its contract is geometric:
  // frame zero starts after the gutter and the final project frame ends at the viewport's right edge.
  return (viewportPx - gutterPx) / Math.max(1, durationF);
}

// Half-up (Math.round), matching the ruler/seek round-trip; callers clamp to ≥0 as needed.
export function pxToFrame(px: number, pxPerFrame: number): number {
  return Math.round(px / pxPerFrame);
}

// Slice the file-wide s16 peaks (one pair per `windowMs` window) to the [srcInF, srcOutF) source
// span of a speech clip. The renderer then reduces the slice to its pixel width via waveform
// columns(). peakIdx(srcF) = floor((srcF/fps*1000) / windowMs); sampleRate rides the PeaksDoc
// contract but the window index is a function of time and windowMs only.
export function clipPeakWindow(
  peaks: [number, number][],
  srcInF: number,
  srcOutF: number,
  fps: number,
  _sampleRate: number,
  windowMs: number,
): [number, number][] {
  if (fps <= 0 || windowMs <= 0) return [];
  const peakIdx = (srcF: number): number => Math.floor(((srcF / fps) * 1000) / windowMs);
  const startIdx = Math.max(0, peakIdx(srcInF));
  const endIdx = Math.max(startIdx, peakIdx(srcOutF));
  return peaks.slice(startIdx, endIdx);
}
