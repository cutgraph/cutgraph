// Safe, immutable text-title mutations. Text tracks cannot contain overlapping items (the project
// contract validates that invariant), so authoring finds an open layer and inspector timing changes
// clamp to the selected layer's neighbours instead of emitting a document the server will reject.

import type { GraphicItem, Project, TextItem, Track } from "../types/project";
import { moveElementToTrack } from "./ops";

export const DEFAULT_TEXT_DURATION_F = 150; // five seconds at the fixed 30 fps timebase
export const DEFAULT_TEXT_ITEM: Omit<TextItem, "id" | "tlStartF" | "durF"> = {
  text: "Add a title",
  preset: "hook",
  pos: { x: 0.5, y: 0.18 },
};

export type TextItemPatch = Partial<Pick<TextItem, "text" | "preset" | "pos" | "scale" | "tlStartF" | "durF">>;

function itemEnd(item: TextItem | GraphicItem): number {
  return item.tlStartF + item.durF;
}

// The v1 compiler's video spine is speech plus placed video. Text cannot outlast that spine: player
// would continue on a black frame while ffmpeg's concat has already ended. A project with no visual
// clip keeps the generic helper usable; compile will still surface its normal no-video error.
function renderableEndF(project: Project): number {
  return project.tracks
    .filter((track) => track.kind === "speech" || track.kind === "video")
    .flatMap((track) => track.clips ?? [])
    .reduce((end, clip) => Math.max(end, clip.tlStartF + clip.srcOutF - clip.srcInF), 0);
}

function uniqueId(project: Project, base: string): string {
  const ids = new Set(project.tracks.flatMap((track) => [
    ...(track.clips ?? []).map((clip) => clip.id),
    ...(track.items ?? []).map((item) => item.id),
  ]));
  let id = base;
  let n = 2;
  while (ids.has(id)) id = `${base}_${n++}`;
  return id;
}

function layerId(project: Project): string {
  const ids = new Set(project.tracks.map((track) => track.id));
  let n = 1;
  while (ids.has(`text_${n}`)) n += 1;
  return `text_${n}`;
}

function isOpen(track: Track, start: number, dur: number, excludeId?: string): boolean {
  const end = start + dur;
  return (track.items ?? []).every((item) => item.id === excludeId || end <= item.tlStartF || start >= itemEnd(item));
}

function clampInt(value: number | undefined, fallback: number, min: number): number {
  return Number.isFinite(value) ? Math.max(min, Math.round(value!)) : fallback;
}

export function textTracks(project: Project): Track[] {
  return project.tracks.filter((track) => track.kind === "text");
}

export function findTextItem(project: Project, id: string): { track: Track; item: TextItem } | null {
  for (const track of textTracks(project)) {
    const item = (track.items ?? []).find((candidate) => candidate.id === id);
    if (item && !("type" in item)) return { track, item: item as TextItem };
  }
  return null;
}

// Adds to the first layer that can hold the full default duration at the playhead. If every existing
// layer is occupied, a new text layer is created so simultaneous titles remain valid and render on top.
export function addTextItem(project: Project, playheadF: number): { project: Project; itemId: string } {
  const renderEnd = renderableEndF(project);
  const requestedStart = clampInt(playheadF, 0, 0);
  const start = renderEnd > 0 ? Math.min(requestedStart, Math.max(0, renderEnd - DEFAULT_TEXT_DURATION_F)) : requestedStart;
  const dur = renderEnd > 0 ? Math.max(1, Math.min(DEFAULT_TEXT_DURATION_F, renderEnd - start)) : DEFAULT_TEXT_DURATION_F;
  const existing = textTracks(project).find((track) => isOpen(track, start, dur));
  const id = uniqueId(project, "title");
  const item: TextItem = { id, ...DEFAULT_TEXT_ITEM, pos: { ...DEFAULT_TEXT_ITEM.pos }, tlStartF: start, durF: dur };
  if (existing) {
    return {
      project: {
        ...project,
        tracks: project.tracks.map((track) => track === existing
          ? { ...track, items: [...(track.items ?? []), item].sort((a, b) => a.tlStartF - b.tlStartF) }
          : track),
      },
      itemId: id,
    };
  }
  return { project: { ...project, tracks: [...project.tracks, { id: layerId(project), kind: "text", items: [item] }] }, itemId: id };
}

// Returns the same project for an absent item or a no-op. Timing values are constrained to the gap on
// its current layer; malformed UI values fall back to the current valid values.
export function updateTextItem(project: Project, id: string, patch: TextItemPatch): Project {
  const found = findTextItem(project, id);
  if (!found) return project;
  const { track, item } = found;
  const others = (track.items ?? []).filter((candidate) => candidate.id !== id);
  const requestedDur = clampInt(patch.durF, item.durF, 1);
  const renderEnd = renderableEndF(project);
  const rawStart = clampInt(patch.tlStartF, item.tlStartF, 0);
  const requestedStart = renderEnd > 0 ? Math.min(rawStart, Math.max(0, renderEnd - 1)) : rawStart;
  const left = others.filter((candidate) => candidate.tlStartF <= requestedStart)
    .reduce((bound, candidate) => Math.max(bound, itemEnd(candidate)), 0);
  const right = others.filter((candidate) => candidate.tlStartF > requestedStart)
    .reduce((bound, candidate) => Math.min(bound, candidate.tlStartF), Infinity);
  const start = Math.max(left, Math.min(requestedStart, right === Infinity ? requestedStart : right - requestedDur));
  const dur = Math.max(1, Math.min(requestedDur, right === Infinity ? requestedDur : right - start,
    renderEnd > 0 ? renderEnd - start : Infinity));
  const next: TextItem = {
    ...item,
    ...patch,
    text: typeof patch.text === "string" ? patch.text : item.text,
    preset: typeof patch.preset === "string" ? patch.preset : item.preset,
    pos: patch.pos ? {
      x: Number.isFinite(patch.pos.x) ? Math.max(0, Math.min(1, patch.pos.x)) : item.pos.x,
      y: Number.isFinite(patch.pos.y) ? Math.max(0, Math.min(1, patch.pos.y)) : item.pos.y,
    } : item.pos,
    scale: Number.isFinite(patch.scale) ? Math.max(0.1, Math.min(4, patch.scale!)) : item.scale ?? 1,
    tlStartF: start,
    durF: dur,
  };
  if (JSON.stringify(next) === JSON.stringify(item)) return project;
  return {
    ...project,
    tracks: project.tracks.map((candidate) => candidate === track
      ? { ...candidate, items: (candidate.items ?? []).map((current) => current.id === id ? next : current).sort((a, b) => a.tlStartF - b.tlStartF) }
      : candidate),
  };
}

// Moving a title between text tracks is allowed only when its whole current span fits. "new" always
// succeeds by creating an otherwise-empty layer; an occupied selected layer is left unchanged.
export function moveTextItemToLayer(project: Project, id: string, targetId: string | "new"): Project {
  const found = findTextItem(project, id);
  if (!found) return project;
  const { track, item } = found;
  if (targetId === track.id) return project;
  const target = targetId === "new" ? null : textTracks(project).find((candidate) => candidate.id === targetId);
  if (target) return moveElementToTrack(project, id, target.id, item.tlStartF);
  const destination: Track = { id: layerId(project), kind: "text", items: [] };
  const without = project.tracks.map((candidate) => candidate === track
    ? { ...candidate, items: (candidate.items ?? []).filter((current) => current.id !== id) }
    : candidate);
  return { ...project, tracks: [...without, { ...destination, items: [item] }] };
}
