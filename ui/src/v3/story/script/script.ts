// Pure derivations behind the Script lens: which words each section shows, where cue chips and filler
// tokens sit among them, and the source ranges a word selection cuts or restores.

import type { RestoreRange, TranscriptFiller, TranscriptWord } from "../../../api";
import { graphicDefinition, graphicLines, isGraphicItem, type TrustedGraphicItem } from "../../../graphics/registry";
import type { PendingCut } from "../../../store/useProjectStore";
import { audioKind } from "../../../timeline/audioKind";
import { resolveTimed, sec_to_frame, srcToTimeline, wordSpanToCut } from "../../../transcript/cutmap";
import type { Project } from "../../../types/project";
import type { SceneSpan } from "../model";

export interface Section {
  key: string;
  /** null when the project has no scenes: one section holds every word. */
  span: SceneSpan | null;
  startWord: number;
  endWord: number;
}

export function scriptSections(spans: SceneSpan[], wordCount: number): Section[] {
  if (spans.length) return spans.map((span) => ({ key: span.scene.id, span, startWord: span.startWord, endWord: span.endWord }));
  return wordCount ? [{ key: "all", span: null, startWord: 0, endWord: wordCount }] : [];
}

export function rangeAllCut(cut: Set<number>, a: number, b: number): boolean {
  for (let i = a; i <= b; i++) if (!cut.has(i)) return false;
  return true;
}

// The kept source runs under words a..b as selection cuts (the `x`/Delete path).
export function selectionCuts(project: Project, media: string, words: TranscriptWord[], a: number, b: number): PendingCut[] {
  const eff = resolveTimed(words);
  const first = words[eff[a]];
  const last = words[eff[b]];
  if (!first || !last) return [];
  return wordSpanToCut(project, media, first, last, project.fps).map((r) => ({
    media,
    srcStartF: r.srcStartF,
    srcEndF: r.srcEndF,
    kind: "selection",
    durSec: (r.srcEndF - r.srcStartF) / project.fps,
  }));
}

// The source span of words a..b, for ripple_restore to put back whatever of it is cut.
export function restoreRanges(project: Project, media: string, words: TranscriptWord[], a: number, b: number): RestoreRange[] {
  const eff = resolveTimed(words);
  const first = words[eff[a]];
  const last = words[eff[b]];
  if (!first || !last) return [];
  const srcStartF = sec_to_frame(first.start, project.fps);
  const srcEndF = sec_to_frame(last.end, project.fps);
  return srcEndF > srcStartF ? [{ media, srcStartF, srcEndF }] : [];
}

export type CueKind = "broll" | "graphic" | "music" | "sfx";

export interface WordCue {
  id: string;
  kind: CueKind;
  label: string;
}

export const CUE_ICON: Record<CueKind, string> = { broll: "▣", graphic: "◆", music: "♪", sfx: "✧" };

const stemLabel = (path: string): string =>
  (path.split(/[\\/]/).pop() ?? path).replace(/\.[^.]+$/, "").replace(/[-_]+/g, " ");

// Every word-anchored clip or text item, grouped by the flat word it is pinned to.
export function cuesByWord(
  project: Project,
  media: string | null,
  labels?: ReadonlyMap<string, string>,
): Map<number, WordCue[]> {
  const out = new Map<number, WordCue[]>();
  const add = (wordIdx: number, cue: WordCue): void => {
    const list = out.get(wordIdx);
    if (list) list.push(cue);
    else out.set(wordIdx, [cue]);
  };
  for (const track of project.tracks) {
    if (track.kind === "speech") continue;
    for (const clip of track.clips ?? []) {
      const ref = clip.anchor?.mode === "word" ? clip.anchor.ref : null;
      if (!ref || ref.media !== media) continue;
      const kind: CueKind = track.kind === "video" ? "broll" : audioKind(clip);
      add(ref.wordIdx, { id: clip.id, kind, label: stemLabel(labels?.get(clip.media.source) ?? clip.media.source) });
    }
    for (const item of track.items ?? []) {
      const ref = item.anchor?.mode === "word" ? item.anchor.ref : null;
      if (!ref || ref.media !== media) continue;
      const label = isGraphicItem(item)
        ? [graphicDefinition(item.componentId)?.name, graphicLines(item as TrustedGraphicItem)[0]].filter(Boolean).join(": ")
        : item.text;
      add(ref.wordIdx, { id: item.id, kind: "graphic", label });
    }
  }
  return out;
}

export type FillerState = "pending" | "cut";

export interface FillerToken {
  key: string;
  w: string;
  state: FillerState;
}

// Fillers ride their own array (never the word index space); each is shown before the first word that
// starts after it, and is "cut" once its middle frame is off the timeline.
export function fillersByWord(
  project: Project,
  media: string | null,
  words: TranscriptWord[],
  fillers: TranscriptFiller[],
): Map<number, FillerToken[]> {
  const out = new Map<number, FillerToken[]>();
  if (!media) return out;
  let prev = -Infinity;
  const starts = words.map((w) => (prev = Number.isFinite(w.start) ? w.start : prev));
  for (const f of fillers) {
    let lo = 0;
    let hi = starts.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (starts[mid] > f.start) hi = mid;
      else lo = mid + 1;
    }
    const mid = sec_to_frame((f.start + f.end) / 2, project.fps);
    const state: FillerState = srcToTimeline(project, media, mid) === null ? "cut" : "pending";
    const token = { key: `f:${f.segIdx}:${f.start}`, w: f.w, state };
    const list = out.get(lo);
    if (list) list.push(token);
    else out.set(lo, [token]);
  }
  return out;
}

export function clock(frames: number, fps: number): string {
  const t = Math.max(0, Math.floor(frames / fps));
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`;
}
