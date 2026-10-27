// Word-anchored inserts from the drawer: a library asset becomes a clip on a video/audio track, a
// registry graphic an item on a text track. Every element carries a word anchor on the speech media,
// so a later transcript cut reflows it with its sentence (pipeline/edits.py). Pure: each function
// returns a new Project (or the same one for a no-op) and never mutates its input.

import type { TranscriptWord } from "../../../api";
import type { Anchor, Clip, GraphicItem, Project, Track } from "../../../types/project";
import { resolveTimed, sec_to_frame, srcRangeToTimeline, srcToTimeline, wordSrcFrame } from "../../../transcript/cutmap";
import { audioKind } from "../../../timeline/audioKind";
import { canPlaceOnTrack } from "../../../timeline/ops";
import { GRAPHIC_DEFAULT_DURATION_F } from "../../../graphics/ops";
import { defaultGraphicProps, graphicDefinition, type GraphicComponentId } from "../../../graphics/registry";
import { workspaceMediaRef } from "../../../preview/resolveMedia";
import { assetTab, type LibraryAsset } from "./library";

export const BROLL_DEFAULT_F = 150;
// B-roll laid over a selection runs at least this long, the co-pilot's shortest phrase (copilot.py).
export const BROLL_MIN_S = 2;
export const MUSIC_DUCK_DB = -12;

export type CueKind = "broll" | "graphic" | "music" | "sfx";

// A lower third belongs bottom-left inside title-safe; the other templates read best centred.
const GRAPHIC_POS: Partial<Record<GraphicComponentId, { x: number; y: number }>> = {
  "lower-third": { x: 0.27, y: 0.8 },
};

export function graphicPos(componentId: GraphicComponentId): { x: number; y: number } {
  return { ...(GRAPHIC_POS[componentId] ?? { x: 0.5, y: 0.5 }) };
}

export interface WordAnchor {
  anchor: Anchor;
  tlStartF: number;
}

export type InsertResult = { project: Project; id: string; tlStartF: number } | { error: string };

// The anchor for "starts on word `wordIdx`": an untimed word resolves to its nearest timed
// neighbour, and a word inside a cut has no timeline frame to start on.
export function wordAnchor(project: Project, words: TranscriptWord[], media: string, wordIdx: number): WordAnchor | null {
  const timed = resolveTimed(words)[wordIdx];
  if (timed === undefined || timed < 0) return null;
  const tlStartF = srcToTimeline(project, media, wordSrcFrame(words[timed], project.fps));
  if (tlStartF === null) return null;
  return { anchor: { mode: "word", ref: { media, wordIdx: timed }, offsetF: 0 }, tlStartF };
}

// The kept word under a timeline frame (the last one starting at or before it), else the first kept
// word — where a click-to-insert lands when the script has no caret.
export function wordAtFrame(project: Project, words: TranscriptWord[], media: string, frame: number): number | null {
  let best: number | null = null;
  let bestTl = -Infinity;
  let first: number | null = null;
  let firstTl = Infinity;
  words.forEach((word, i) => {
    if (!Number.isFinite(word.start)) return;
    const tl = srcToTimeline(project, media, wordSrcFrame(word, project.fps));
    if (tl === null) return;
    if (tl <= frame && tl > bestTl) { best = i; bestTl = tl; }
    if (tl < firstTl) { first = i; firstTl = tl; }
  });
  return best ?? first;
}

function speechEndF(project: Project): number {
  return (project.tracks.find((t) => t.kind === "speech")?.clips ?? [])
    .reduce((end, c) => Math.max(end, c.tlStartF + c.srcOutF - c.srcInF), 0);
}

function elementIds(project: Project): Set<string> {
  return new Set(project.tracks.flatMap((t) => [t.id, ...(t.clips ?? []).map((c) => c.id), ...(t.items ?? []).map((i) => i.id)]));
}

function uniqueId(ids: Set<string>, base: string, sep: string): string {
  const stem = base.slice(0, 56) || "item";
  let id = stem;
  let n = 2;
  while (ids.has(id)) id = `${stem}${sep}${n++}`;
  return id;
}

function slug(name: string): string {
  return name.replace(/\.[^.]+$/, "").replace(/[^A-Za-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "");
}

// Put `element` on the first track of `kind` whose span is free, else on a new layer, so an insert
// never shifts off its word to dodge a neighbour. Track order is paint order: a new footage layer
// goes above the other footage but under every title and graphic.
function place(project: Project, kind: Track["kind"], element: Clip | GraphicItem, durF: number): Project {
  const open = project.tracks.find((t) => t.kind === kind && canPlaceOnTrack(project, t.id, kind, element.tlStartF, durF));
  const byStart = <T extends { tlStartF: number }>(list: T[]) => list.sort((a, b) => a.tlStartF - b.tlStartF);
  const add = (t: Track): Track => kind === "text"
    ? { ...t, items: byStart([...(t.items ?? []), element as GraphicItem]) }
    : { ...t, clips: byStart([...(t.clips ?? []), element as Clip]) };
  if (open) return { ...project, tracks: project.tracks.map((t) => (t === open ? add(t) : t)) };
  const layer = add({ id: uniqueId(elementIds(project), kind, "_"), kind });
  const footage = project.tracks.map((t) => t.kind === "speech" || t.kind === "video");
  const at = kind === "video" ? footage.lastIndexOf(true) + 1 : project.tracks.length;
  return { ...project, tracks: [...project.tracks.slice(0, at), layer, ...project.tracks.slice(at)] };
}

function naturalF(entry: LibraryAsset, fps: number): number {
  const s = entry.asset.durationS;
  return entry.asset.kind !== "image" && s && s > 0 ? Math.max(1, Math.round(s * fps)) : BROLL_DEFAULT_F;
}

// Timeline frames from `startF` to the end of word `last`: the kept run that starts there, which is
// contiguous because cuts ripple and a selection never leaves its scene.
export function wordRunF(project: Project, words: TranscriptWord[], media: string, first: number, last: number, startF: number): number {
  const eff = resolveTimed(words);
  const a = words[eff[first]];
  const b = words[eff[last]];
  if (!a || !b) return 0;
  const runs = srcRangeToTimeline(project, media, sec_to_frame(a.start, project.fps), sec_to_frame(b.end, project.fps));
  let end = startF;
  for (const r of runs.sort((x, y) => x.startF - y.startF)) if (r.startF <= end && r.endF > end) end = r.endF;
  return end - startF;
}

// `endWord` makes B-roll span the words wordIdx..endWord (a selection), for at least BROLL_MIN_S and
// never past a video's own length; every other kind keeps its length and just starts on wordIdx.
export function insertAsset(
  project: Project,
  words: TranscriptWord[],
  media: string,
  wordIdx: number,
  entry: LibraryAsset,
  endWord?: number,
): InsertResult {
  const at = wordAnchor(project, words, media, wordIdx);
  if (!at) return { error: "That word is cut, so nothing can start on it." };
  const tab = assetTab(entry.asset);
  const room = Math.max(1, speechEndF(project) - at.tlStartF);
  const natural = naturalF(entry, project.fps);
  const spanF = tab === "broll" && endWord !== undefined
    ? Math.max(wordRunF(project, words, media, wordIdx, endWord, at.tlStartF), BROLL_MIN_S * project.fps)
    : null;
  const length = tab !== "broll" ? natural
    : spanF === null ? Math.min(natural, BROLL_DEFAULT_F)
    : entry.asset.kind === "image" ? spanF : Math.min(natural, spanF);
  const durF = Math.max(1, Math.min(length, room));
  const id = uniqueId(elementIds(project), slug(entry.asset.name) || tab, "_");
  const clip: Clip = {
    id,
    media: { source: workspaceMediaRef(entry.workspaceId, entry.asset.file), proxy: null },
    srcInF: 0,
    srcOutF: durF,
    tlStartF: at.tlStartF,
    gainDb: 0,
    ...(tab === "music" ? { duckDb: MUSIC_DUCK_DB } : {}),
    anchor: at.anchor,
  };
  const kind = entry.asset.kind === "audio" ? "audio" : "video";
  return { project: place(project, kind, clip, durF), id, tlStartF: at.tlStartF };
}

export function insertGraphicAt(project: Project, words: TranscriptWord[], media: string, wordIdx: number, componentId: GraphicComponentId): InsertResult {
  const at = wordAnchor(project, words, media, wordIdx);
  if (!at) return { error: "That word is cut, so nothing can start on it." };
  const spec = graphicDefinition(componentId);
  if (!spec) return { error: "That graphic isn't in the registry." };
  const durF = Math.max(1, Math.min(GRAPHIC_DEFAULT_DURATION_F, speechEndF(project) - at.tlStartF));
  const id = uniqueId(elementIds(project), `graphic-${componentId}`, "-");
  const item = {
    type: "graphic", id, componentId, componentVersion: spec.version,
    props: defaultGraphicProps(componentId), pos: graphicPos(componentId), scale: 1,
    tlStartF: at.tlStartF, durF, anchor: at.anchor,
  } as GraphicItem;
  return { project: place(project, "text", item, durF), id, tlStartF: at.tlStartF };
}

export interface Cue {
  id: string;
  kind: CueKind;
  clip?: Clip;
  item?: GraphicItem;
  tlStartF: number;
  wordIdx: number | null;
}

// The drawer's view of a word-anchored element; speech is never a cue.
export function findCue(project: Project, id: string): Cue | null {
  for (const track of project.tracks) {
    if (track.kind === "speech") continue;
    const clip = track.clips?.find((c) => c.id === id);
    if (clip) {
      const kind: CueKind = track.kind === "audio" ? audioKind(clip) : "broll";
      return { id, kind, clip, tlStartF: clip.tlStartF, wordIdx: anchorWord(clip.anchor) };
    }
    const item = track.items?.find((i) => i.id === id);
    if (item && "type" in item && item.type === "graphic") {
      return { id, kind: "graphic", item, tlStartF: item.tlStartF, wordIdx: anchorWord(item.anchor) };
    }
  }
  return null;
}

function anchorWord(anchor: Anchor | undefined): number | null {
  return anchor?.mode === "word" && anchor.ref ? anchor.ref.wordIdx : null;
}

function mapElement(project: Project, id: string, clipFn: (c: Clip) => Clip, itemFn: (i: GraphicItem) => GraphicItem): Project {
  return {
    ...project,
    tracks: project.tracks.map((t) => {
      if (t.clips?.some((c) => c.id === id)) return { ...t, clips: t.clips.map((c) => (c.id === id ? clipFn(c) : c)) };
      if (t.items?.some((i) => i.id === id)) return { ...t, items: t.items.map((i) => (i.id === id && "type" in i ? itemFn(i) : i)) };
      return t;
    }),
  };
}

// Swap a clip's media for another asset of the same kind, keeping its word, start and layer. The
// span never grows, so the swap can't collide with a neighbour; a shorter video trims it.
export function swapAsset(project: Project, id: string, entry: LibraryAsset): Project {
  const cue = findCue(project, id);
  if (!cue?.clip) return project;
  const source = workspaceMediaRef(entry.workspaceId, entry.asset.file);
  if (cue.clip.media.source === source) return project;
  const span = cue.clip.srcOutF - cue.clip.srcInF;
  const durF = entry.asset.kind === "image" ? span : Math.min(span, naturalF(entry, project.fps));
  return mapElement(project, id, (c) => ({ ...c, media: { source, proxy: null }, srcInF: 0, srcOutF: durF }), (i) => i);
}

export function swapGraphic(project: Project, id: string, componentId: GraphicComponentId): Project {
  const cue = findCue(project, id);
  const spec = graphicDefinition(componentId);
  if (!cue?.item || !spec || cue.item.componentId === componentId) return project;
  return mapElement(project, id, (c) => c, (i) => ({
    ...i, componentId, componentVersion: spec.version, props: defaultGraphicProps(componentId),
    pos: graphicPos(componentId),
  } as GraphicItem));
}
