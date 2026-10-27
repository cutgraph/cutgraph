// Pure anchor helpers for the timeline pin UI. An anchor lives on a NON-speech element
// (b-roll clip, music clip, text item) and decides how it reflows under a transcript cut (never
// silently drift): a WORD pin re-derives its tlStartF from a spoken word (moves with the sentence), a
// TIME pin stays put on the timeline (and raises a crossing warning when a cut passes under it). Every
// function returns a NEW immutable Project (no immer). Frames are canonical; `fps`
// always comes from `project.fps`.

import type { Project, Anchor } from "../types/project";
import type { TranscriptWord } from "../api";
import { wordSrcFrame, srcToTimeline } from "../transcript/cutmap";

// The word whose current timeline position is closest to a non-speech clip's start, expressed as a
// word anchor that keeps the clip visually put (offsetF carries the residual). `speechMedia` is the
// speech track's media.source — it MUST be `ref.media` exactly, or the server's word reflow silently
// falls through to time-pin (edits.py). Untimed words (no start) and words inside an applied cut
// (srcToTimeline null) are skipped. null when no timed word maps onto the timeline.
export function nearestWordAnchor(
  project: Project,
  words: TranscriptWord[],
  fps: number,
  clipTlStartF: number,
  speechMedia: string,
): Anchor | null {
  let bestIdx = -1;
  let bestTl = 0;
  let bestDist = Infinity;
  words.forEach((w, i) => {
    if (!Number.isFinite(w.start)) return;
    const wTl = srcToTimeline(project, speechMedia, wordSrcFrame(w, fps));
    if (wTl === null) return;
    const dist = Math.abs(wTl - clipTlStartF);
    if (dist < bestDist) {
      bestDist = dist;
      bestIdx = i;
      bestTl = wTl;
    }
  });
  if (bestIdx < 0) return null;
  return {
    mode: "word",
    ref: { media: speechMedia, wordIdx: bestIdx },
    offsetF: clipTlStartF - bestTl,
  };
}

// Toggle an element's anchor: word (or absent) → time; time → word (nearest-word at the clip's current
// start, falling back to a plain time pin when no word maps onto the timeline). Flipping to word sets
// offsetF so the clip does NOT jump — it changes only future-cut reflow behavior.
export function flipAnchor(
  current: Anchor | null | undefined,
  project: Project,
  words: TranscriptWord[],
  fps: number,
  clipTlStartF: number,
  speechMedia: string,
): Anchor {
  if (current?.mode === "time") {
    return nearestWordAnchor(project, words, fps, clipTlStartF, speechMedia) ?? { mode: "time" };
  }
  return { mode: "time" };
}

// Immutably set the anchor on the clip or text item with the given id (rebuild only the matched
// track/array so untouched tracks keep their identity for Zustand selectors).
export function setElementAnchor(project: Project, id: string, anchor: Anchor): Project {
  const tracks = project.tracks.map((t) => {
    if (t.clips) {
      const idx = t.clips.findIndex((c) => c.id === id);
      if (idx >= 0) {
        const clips = t.clips.slice();
        clips[idx] = { ...clips[idx], anchor };
        return { ...t, clips };
      }
    }
    if (t.items) {
      const idx = t.items.findIndex((i) => i.id === id);
      if (idx >= 0) {
        const items = t.items.slice();
        items[idx] = { ...items[idx], anchor };
        return { ...t, items };
      }
    }
    return t;
  });
  return { ...project, tracks };
}

// The flat transcript word index a word anchor tethers to (ref.wordIdx), or null for a time/absent
// anchor. project/words are part of the contract signature but the highlight target is the stored
// wordIdx itself.
export function tetherWordIdx(
  _project: Project,
  _words: TranscriptWord[],
  anchor: Anchor | undefined,
): number | null {
  if (anchor?.mode === "word" && anchor.ref) return anchor.ref.wordIdx;
  return null;
}
