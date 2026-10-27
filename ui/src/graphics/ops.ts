import type { GraphicItem, Project, Track } from "../types/project";
import { defaultGraphicProps, graphicDefinition, type GraphicComponentId, validateGraphicProps } from "./registry";

export const GRAPHIC_DEFAULT_DURATION_F = 180;

function allIds(project: Project): Set<string> {
  return new Set(project.tracks.flatMap((track) => [track.id, ...(track.clips ?? []), ...(track.items ?? [])].map((item) => typeof item === "string" ? item : item.id)));
}

function firstVisualTrack(project: Project): Track | null {
  return project.tracks.find((track) => track.kind === "text") ?? null;
}

function firstFreeFrame(track: Track, requested: number, duration: number): number {
  let start = Math.max(0, Math.round(requested));
  const items = [...(track.items ?? [])].sort((a, b) => a.tlStartF - b.tlStartF);
  let changed = true;
  while (changed) {
    changed = false;
    const hit = items.find((item) => start < item.tlStartF + item.durF && start + duration > item.tlStartF);
    if (hit) { start = hit.tlStartF + hit.durF; changed = true; }
  }
  return start;
}

export function insertGraphic(project: Project, componentId: GraphicComponentId, playheadF: number): { project: Project; itemId: string; startF: number } {
  const spec = graphicDefinition(componentId);
  if (!spec || !validateGraphicProps(componentId, spec.defaults)) return { project, itemId: "", startF: 0 };
  const existing = firstVisualTrack(project);
  const startF = firstFreeFrame(existing ?? { id: "graphics", kind: "text", items: [] }, playheadF, GRAPHIC_DEFAULT_DURATION_F);
  const ids = allIds(project);
  const stem = `graphic-${componentId}`;
  let itemId = stem;
  let serial = 2;
  while (ids.has(itemId)) itemId = `${stem}-${serial++}`;
  const item = {
    type: "graphic", id: itemId, componentId, componentVersion: spec.version,
    props: defaultGraphicProps(componentId) as GraphicItem["props"], pos: { x: 0.5, y: 0.5 }, scale: 1,
    tlStartF: startF, durF: GRAPHIC_DEFAULT_DURATION_F,
  } as GraphicItem;
  const tracks = existing
    ? project.tracks.map((track) => track.id === existing.id ? { ...track, items: [...(track.items ?? []), item].sort((a, b) => a.tlStartF - b.tlStartF) } : track)
    : [...project.tracks, { id: uniqueTrackId(ids, "graphics"), kind: "text" as const, items: [item] }];
  return { project: { ...project, tracks }, itemId, startF };
}

function uniqueTrackId(ids: Set<string>, stem: string): string {
  let id = stem;
  let serial = 2;
  while (ids.has(id)) id = `${stem}-${serial++}`;
  return id;
}

export function updateGraphic(project: Project, id: string, props: unknown): Project {
  const found = graphicDefinitionFor(project, id);
  if (!found || !validateGraphicProps(found.item.componentId, props)) return project;
  return { ...project, tracks: project.tracks.map((track) => track.id === found.track.id ? {
    ...track, items: (track.items ?? []).map((item) => item.id === id ? ({ ...item, props: props as GraphicItem["props"] } as GraphicItem) : item),
  } : track) };
}

/** Update one graphic's timeline timing while preserving the text-track no-overlap contract. */
export function updateGraphicTiming(project: Project, id: string, patch: Partial<Pick<GraphicItem, "tlStartF" | "durF">>): Project {
  const found = graphicDefinitionFor(project, id);
  if (!found) return project;
  const current = found.item;
  const others = (found.track.items ?? []).filter((item) => item.id !== id);
  const requestedStart = Number.isFinite(patch.tlStartF) ? Math.max(0, Math.round(patch.tlStartF!)) : current.tlStartF;
  const requestedDur = Number.isFinite(patch.durF) ? Math.max(1, Math.round(patch.durF!)) : current.durF;
  const left = others.filter((item) => item.tlStartF <= requestedStart)
    .reduce((bound, item) => Math.max(bound, item.tlStartF + item.durF), 0);
  const right = others.filter((item) => item.tlStartF > requestedStart)
    .reduce((bound, item) => Math.min(bound, item.tlStartF), Infinity);
  const start = Math.max(left, Math.min(requestedStart, right === Infinity ? requestedStart : right - 1));
  const dur = Math.max(1, Math.min(requestedDur, right === Infinity ? requestedDur : right - start));
  if (start === current.tlStartF && dur === current.durF) return project;
  return {
    ...project,
    tracks: project.tracks.map((track) => track === found.track
      ? { ...track, items: (track.items ?? []).map((item) => item.id === id ? { ...item, tlStartF: start, durF: dur } : item).sort((a, b) => a.tlStartF - b.tlStartF) }
      : track),
  };
}

export function graphicDefinitionFor(project: Project, id: string): { track: Track; item: GraphicItem } | null {
  for (const track of project.tracks) for (const item of track.items ?? []) {
    if (item.id === id && "type" in item && item.type === "graphic") return { track, item };
  }
  return null;
}
