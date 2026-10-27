// Pure Project transforms for timeline drag / trim / split / nudge / delete, plus snapping. Every
// function returns a NEW immutable Project (new arrays/objects along the changed path) and never
// mutates its input — there is no immer, so Zustand selectors only re-render on a fresh object
// Frames are canonical; `fps` always comes from `project.fps`. Speech clips are
// transcript-driven: moveClip/nudgeClip skip the speech track; trimClip is a local source-window
// change allowed on any clip.

import type { Project, Clip, GraphicItem, TextItem, Track } from "../types/project";
import type { TranscriptWord } from "../api";
import { wordSrcFrame, srcToTimeline } from "../transcript/cutmap";

// The end of the nearest same-track neighbor to the LEFT of `curStart` (a lower bound for a placed
// element's start) and the start of the nearest neighbor to the RIGHT (an upper bound for its end).
// Excludes the element itself. Defaults are 0 (no left neighbor) / +Infinity (no right neighbor).
// These bounds are what keep every move/nudge/trim overlap-free BY CONSTRUCTION — the server's
// within-track overlap validate() can then never reject a locally-emitted doc.
function neighborBounds(
  track: Track,
  id: string,
  curStart: number,
): { leftBound: number; rightBound: number } {
  let leftBound = 0;
  let rightBound = Infinity;
  const consider = (start: number, end: number): void => {
    if (start < curStart) {
      if (end > leftBound) leftBound = end;
    } else if (start < rightBound) {
      rightBound = start;
    }
  };
  for (const c of track.clips ?? []) {
    if (c.id !== id) consider(c.tlStartF, c.tlStartF + (c.srcOutF - c.srcInF));
  }
  for (const it of track.items ?? []) {
    if (it.id !== id) consider(it.tlStartF, it.tlStartF + it.durF);
  }
  return { leftBound, rightBound };
}

// Set a new tlStartF on a non-speech clip or a text item, clamped/blocked so it never overlaps a
// same-track neighbor. `target` receives the element's current start, its timeline length, and its
// neighbor bounds, and returns the new start — or null to leave the element untouched (a blocked
// nudge). Returns the ORIGINAL `project` reference when nothing moved, so callers can treat a
// blocked/no-op edit as identity (=== the input) and skip recording a spurious undo/save.
function placeElement(
  project: Project,
  id: string,
  target: (cur: number, dur: number, leftBound: number, rightBound: number) => number | null,
): Project {
  let mutated = false;
  const tracks = project.tracks.map((t) => {
    if (t.kind !== "speech" && t.clips) {
      const idx = t.clips.findIndex((c) => c.id === id);
      if (idx >= 0) {
        const c = t.clips[idx];
        const { leftBound, rightBound } = neighborBounds(t, id, c.tlStartF);
        const next = target(c.tlStartF, c.srcOutF - c.srcInF, leftBound, rightBound);
        if (next === null || next === c.tlStartF) return t;
        const clips = t.clips.slice();
        clips[idx] = { ...c, tlStartF: next };
        mutated = true;
        return { ...t, clips };
      }
    }
    if (t.items) {
      const idx = t.items.findIndex((i) => i.id === id);
      if (idx >= 0) {
        const it = t.items[idx];
        const { leftBound, rightBound } = neighborBounds(t, id, it.tlStartF);
        const next = target(it.tlStartF, it.durF, leftBound, rightBound);
        if (next === null || next === it.tlStartF) return t;
        const items = t.items.slice();
        items[idx] = { ...it, tlStartF: next };
        mutated = true;
        return { ...t, items };
      }
    }
    return t;
  });
  return mutated ? { ...project, tracks } : project;
}

// Move a clip/item to `newTlStartF`, clamped to ≥0 and to the gap between same-track neighbors (a drag
// that overshoots comes to rest against the neighbor's edge).
export function moveClip(project: Project, clipId: string, newTlStartF: number): Project {
  return placeElement(project, clipId, (_cur, dur, leftBound, rightBound) => {
    const hi = rightBound === Infinity ? Infinity : rightBound - dur;
    if (leftBound > hi) return null; // gap narrower than the clip (degenerate) — leave it put
    return Math.min(hi, Math.max(leftBound, newTlStartF));
  });
}

// Nudge a clip/item by `deltaF` (clamped ≥0). Unlike a drag, a nudge that would carry the element PAST
// a neighbor's edge is a no-op (identity) rather than a partial clamp.
function spanIsOpen(track: Track, start: number, dur: number, excludeId?: string): boolean {
  const end = start + dur;
  return [...(track.clips ?? []), ...(track.items ?? [])].every((element) => {
    if (element.id === excludeId) return true;
    const elementDur = "durF" in element ? element.durF : element.srcOutF - element.srcInF;
    return end <= element.tlStartF || start >= element.tlStartF + elementDur;
  });
}

/** Whether a compatible track can hold an element span without violating the project contract. */
export function canPlaceOnTrack(
  project: Project,
  trackId: string,
  kind: Track["kind"],
  startF: number,
  durF: number,
): boolean {
  const track = project.tracks.find((candidate) => candidate.id === trackId);
  return !!track && track.kind === kind && startF >= 0 && durF > 0 && spanIsOpen(track, startF, durF);
}

// Move a non-speech element to a compatible track. Cross-track drops preserve the requested timeline
// position only when its whole span fits; unlike an in-track horizontal drag they never silently clamp
// an element onto a different moment in the destination row.
export function moveElementToTrack(
  project: Project,
  id: string,
  targetTrackId: string,
  newTlStartF: number,
): Project {
  let source: Track | undefined;
  let clip: Clip | undefined;
  let item: TextItem | GraphicItem | undefined;
  for (const track of project.tracks) {
    clip = track.clips?.find((candidate) => candidate.id === id);
    if (clip) {
      source = track;
      break;
    }
    item = track.items?.find((candidate) => candidate.id === id);
    if (item) {
      source = track;
      break;
    }
  }
  const target = project.tracks.find((track) => track.id === targetTrackId);
  if (!source || !target || source.id === target.id) return moveClip(project, id, newTlStartF);
  // Speech is transcript-driven and may never move tracks. The other element kinds can move only
  // between like-kind rows, preserving the project's per-track overlap contract.
  if (source.kind === "speech" || source.kind !== target.kind || (!clip && !item)) {
    return project;
  }

  const start = Math.max(0, Math.round(newTlStartF));
  const dur = item ? item.durF : clip!.srcOutF - clip!.srcInF;
  if (!spanIsOpen(target, start, dur)) return project;
  return {
    ...project,
    tracks: project.tracks.map((track) => {
      if (item && track === source) {
        return { ...track, items: (track.items ?? []).filter((candidate) => candidate.id !== id) };
      }
      if (item && track === target) {
        const moved = { ...item, tlStartF: start };
        return { ...track, items: [...(track.items ?? []), moved].sort((a, b) => a.tlStartF - b.tlStartF) };
      }
      if (clip && track === source) {
        return { ...track, clips: (track.clips ?? []).filter((candidate) => candidate.id !== id) };
      }
      if (clip && track === target) {
        const moved = { ...clip, tlStartF: start };
        return { ...track, clips: [...(track.clips ?? []), moved].sort((a, b) => a.tlStartF - b.tlStartF) };
      }
      return track;
    }),
  };
}

export function nudgeClip(project: Project, clipId: string, deltaF: number): Project {
  return placeElement(project, clipId, (cur, dur, leftBound, rightBound) => {
    const hi = rightBound === Infinity ? Infinity : rightBound - dur;
    const desired = Math.max(0, cur + deltaF);
    if (desired < leftBound || desired > hi) return null;
    return desired;
  });
}

// Trim one edge of a clip. edge "in": keep the source frame under the edge fixed — set srcInF and
// shift tlStartF by the same delta; clamp so tlStartF stays ≥0, stays ≥ the left neighbor's end, and
// srcInF stays in [lo, srcOutF-1]. edge "out": set srcOutF, clamped to ≥ srcInF+1 (min 1-frame clip)
// and so the clip's end never crosses the right neighbor's start. Trim can never create overlap.
export function trimClip(
  project: Project,
  clipId: string,
  edge: "in" | "out",
  newSrcF: number,
): Project {
  const tracks = project.tracks.map((t) => {
    if (!t.clips) return t;
    const idx = t.clips.findIndex((c) => c.id === clipId);
    if (idx < 0) return t;
    const c = t.clips[idx];
    const { leftBound, rightBound } = neighborBounds(t, clipId, c.tlStartF);
    let updated: Clip;
    if (edge === "in") {
      // tlStartF + (nin - srcInF) must stay ≥ 0 AND ≥ the left neighbor's end (leftBound).
      const lo = Math.max(0, c.srcInF - c.tlStartF + leftBound);
      const nin = Math.min(Math.max(newSrcF, lo), c.srcOutF - 1);
      updated = { ...c, srcInF: nin, tlStartF: c.tlStartF + (nin - c.srcInF) };
    } else {
      // srcOutF extends the clip's end (tlStartF + dur); cap it at the right neighbor's start.
      const maxOut = rightBound === Infinity ? Infinity : c.srcInF + (rightBound - c.tlStartF);
      const nout = Math.min(Math.max(c.srcInF + 1, newSrcF), maxOut);
      updated = { ...c, srcOutF: nout };
    }
    const clips = t.clips.slice();
    clips[idx] = updated;
    return { ...t, clips };
  });
  return { ...project, tracks };
}

// Split a clip at source frame atSrcF into left [srcInF, atSrcF) (keeps the id) and right
// [atSrcF, srcOutF) (id `${id}-s${atSrcF}`, tlStartF shifted by the left duration). The new boundary
// is contiguous and click-free, so NO seam is created there (seams are for ripple joins only);
// any pre-existing seams are partitioned to the half they fall in. No-op if atSrcF is at or outside
// the clip's source window.
export function splitClipAt(project: Project, clipId: string, atSrcF: number): Project {
  const tracks = project.tracks.map((t) => {
    if (!t.clips) return t;
    const idx = t.clips.findIndex((c) => c.id === clipId);
    if (idx < 0) return t;
    const c = t.clips[idx];
    if (atSrcF <= c.srcInF || atSrcF >= c.srcOutF) return t;
    const splitOff = atSrcF - c.srcInF;
    const left: Clip = { ...c, srcOutF: atSrcF };
    const right: Clip = {
      ...c,
      id: `${c.id}-s${atSrcF}`,
      srcInF: atSrcF,
      tlStartF: c.tlStartF + splitOff,
    };
    if (c.seams) {
      left.seams = c.seams.filter((s) => s.atF < splitOff);
      right.seams = c.seams
        .filter((s) => s.atF >= splitOff)
        .map((s) => ({ ...s, atF: s.atF - splitOff }));
    }
    const clips = t.clips.slice();
    clips.splice(idx, 1, left, right);
    return { ...t, clips };
  });
  return { ...project, tracks };
}

// Remove a clip or text item by id from whichever track holds it.
export function deleteElement(project: Project, id: string): Project {
  let mutated = false;
  const tracks = project.tracks.map((t) => {
    const hasClip = t.clips?.some((c) => c.id === id);
    const hasItem = t.items?.some((i) => i.id === id);
    if (!hasClip && !hasItem) return t;
    // Primary speech is transcript-owned. Removal must go through the source-cut workflow so
    // anchored titles/graphics and the speech spine receive one reconciled revision.
    if (hasClip && t.kind === "speech") return t;
    const next = { ...t };
    if (hasClip && t.clips) { next.clips = t.clips.filter((c) => c.id !== id); mutated = true; }
    if (hasItem && t.items) { next.items = t.items.filter((i) => i.id !== id); mutated = true; }
    return next;
  });
  return mutated ? { ...project, tracks } : project;
}

// Snap a candidate timeline frame to the nearest of: whole frame (inherent — the candidate is already
// integer), every OTHER clip's start/end edge, and every speech word's timeline position — but only
// when the nearest such target is within `thresholdPx`. Returns the snapped frame, or the candidate
// unchanged when nothing is close enough.
export function snapFrame(
  project: Project,
  words: TranscriptWord[],
  fps: number,
  candidateF: number,
  pxPerFrame: number,
  thresholdPx: number,
  excludeClipId?: string,
): number {
  const candidates: number[] = [];
  for (const t of project.tracks) {
    for (const c of t.clips ?? []) {
      if (c.id === excludeClipId) continue;
      candidates.push(c.tlStartF);
      candidates.push(c.tlStartF + (c.srcOutF - c.srcInF));
    }
  }
  const speechMedia = project.tracks.find((t) => t.kind === "speech")?.clips?.[0]?.media.source;
  if (speechMedia) {
    for (const w of words) {
      if (!Number.isFinite(w.start)) continue;
      const tl = srcToTimeline(project, speechMedia, wordSrcFrame(w, fps));
      if (tl !== null) candidates.push(tl);
    }
  }
  let best = candidateF;
  let bestDist = Infinity;
  for (const cand of candidates) {
    const dist = Math.abs(cand - candidateF);
    if (dist * pxPerFrame <= thresholdPx && dist < bestDist) {
      best = cand;
      bestDist = dist;
    }
  }
  return best;
}
