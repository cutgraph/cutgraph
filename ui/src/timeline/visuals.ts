// Immutable project-document helpers for the manual-composition controls. Coordinates are normalized
// to the project canvas, so they survive a responsive FramePlayer resize and stay meaningful to a future
// final compositor. Transform overrides are optional: deleting one is the reset-to-default action.

import type { Clip, GraphicItem, Project, TextItem, Track } from "../types/project";
import { resolveLayout } from "../layout";
import { graphicGeometry, isGraphicItem, type TrustedGraphicItem } from "../graphics/registry";

export const TRANSFORM_SCALE_MIN = 0.1;
export const TRANSFORM_SCALE_MAX = 4;
export const TRANSFORM_FITS = ["fill", "contain", "cover"] as const;
export type TransformFit = typeof TRANSFORM_FITS[number];
export type VisualTransform = NonNullable<Clip["transform"]>;
export interface GraphicTransform { x: number; y: number; scale: number }
export interface NormalizedVisualBounds { x: number; y: number; width: number; height: number }
export type VisualPatch = Partial<VisualTransform> & Partial<GraphicTransform>;

export const GRAPHIC_SCALE_MIN = 0.1;
export const GRAPHIC_SCALE_MAX = 4;
export const VISUAL_NUDGE = 0.01;
export const VISUAL_NUDGE_LARGE = 0.05;

export type ElementMatch = { track: Track; clip?: Clip; item?: TextItem | GraphicItem };

export function findElement(project: Project, id: string): ElementMatch | null {
  for (const track of project.tracks) {
    const clip = (track.clips ?? []).find((candidate) => candidate.id === id);
    if (clip) return { track, clip };
    const item = (track.items ?? []).find((candidate) => candidate.id === id);
    if (item) return { track, item };
  }
  return null;
}

export function isVisualTrack(track: Track): boolean {
  return track.kind === "speech" || track.kind === "video" || track.kind === "text";
}

function isImage(clip: Clip): boolean {
  return /\.(png|jpe?g|webp|gif)$/i.test(clip.media.source);
}

function isSpeech(project: Project, clip: Clip): boolean {
  return project.tracks.some((t) => t.kind === "speech" && t.clips?.some((c) => c.id === clip.id));
}

// Never stretch: when the source's shape differs from the frame (a phone recording in a 16:9 video),
// speech footage is shown whole and B-roll fills the frame. With matching shapes both equal the old
// `fill`. Images contain and cropped shorts cover, as before. Mirrors compile._resolved_transform.
export function defaultTransform(project: Project, clip: Clip): VisualTransform {
  return {
    x: 0.5,
    y: 0.5,
    scale: 1,
    fit: isImage(clip) ? "contain" : project.crop ? "cover" : isSpeech(project, clip) ? "contain" : "cover",
  };
}

export function resolvedTransform(project: Project, clip: Clip): VisualTransform {
  return clip.transform ?? defaultTransform(project, clip);
}

function clampUnit(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : fallback;
}

function clampScale(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.max(TRANSFORM_SCALE_MIN, Math.min(TRANSFORM_SCALE_MAX, value))
    : fallback;
}

function fit(value: unknown, fallback: TransformFit): TransformFit {
  return typeof value === "string" && (TRANSFORM_FITS as readonly string[]).includes(value)
    ? value as TransformFit
    : fallback;
}

export function normalizeTransform(project: Project, clip: Clip, patch: Partial<VisualTransform>): VisualTransform {
  const base = resolvedTransform(project, clip);
  return {
    x: clampUnit(patch.x, base.x),
    y: clampUnit(patch.y, base.y),
    scale: clampScale(patch.scale, base.scale),
    fit: fit(patch.fit, base.fit),
  };
}

function clampPosition(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : fallback;
}

function clampGraphicScale(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.max(GRAPHIC_SCALE_MIN, Math.min(GRAPHIC_SCALE_MAX, value))
    : fallback;
}

export function graphicTransform(item: GraphicItem): GraphicTransform {
  return { x: item.pos.x, y: item.pos.y, scale: item.scale };
}

/** The one transform read path shared by preview gestures and inspector geometry. */
export function visualTransform(project: Project, id: string): VisualTransform | GraphicTransform | null {
  const found = findElement(project, id);
  if (found?.clip && found.track.kind !== "audio") return resolvedTransform(project, found.clip);
  if (found?.item && isGraphicItem(found.item)) return graphicTransform(found.item);
  if (found?.item && !isUnsupportedGraphic(found.item)) {
    return { x: found.item.pos.x, y: found.item.pos.y, scale: found.item.scale ?? 1 };
  }
  return null;
}

function isUnsupportedGraphic(item: TextItem | GraphicItem): boolean {
  if (!("type" in item)) return false;
  const type = (item as { type?: unknown }).type;
  return type !== "graphic" || !isGraphicItem(item);
}

export function normalizeGraphicTransform(item: GraphicItem, patch: Partial<GraphicTransform>): GraphicTransform {
  const base = graphicTransform(item);
  return {
    x: clampPosition(patch.x, base.x),
    y: clampPosition(patch.y, base.y),
    scale: clampGraphicScale(patch.scale, base.scale),
  };
}

/** Update a graphic's authoritative normalized position/scale. No DOM-only transform is created. */
export function updateGraphicTransform(project: Project, id: string, patch: Partial<GraphicTransform>): Project {
  let changed = false;
  const tracks = project.tracks.map((track) => {
    const index = (track.items ?? []).findIndex((item) => item.id === id && isGraphicItem(item));
    if (index < 0) return track;
    const item = (track.items ?? [])[index] as GraphicItem;
    const next = normalizeGraphicTransform(item, patch);
    if (next.x === item.pos.x && next.y === item.pos.y && next.scale === item.scale) return track;
    changed = true;
    const items = (track.items ?? []).slice();
    items[index] = { ...item, pos: { x: next.x, y: next.y }, scale: next.scale };
    return { ...track, items };
  });
  return changed ? { ...project, tracks } : project;
}

/** One transform entry point for Inspector, preview, keyboard, and agent operations. */
export function updateVisualTransform(project: Project, id: string, patch: VisualPatch): Project {
  const found = findElement(project, id);
  if (found?.item && isUnsupportedGraphic(found.item)) return project;
  if (found?.clip) return updateClipTransform(project, id, patch);
  if (found?.item && isGraphicItem(found.item)) return updateGraphicTransform(project, id, patch);
  if (found?.item) {
    const pos = {
      x: clampPosition(patch.x, found.item.pos.x),
      y: clampPosition(patch.y, found.item.pos.y),
    };
    const scale = clampGraphicScale(patch.scale, found.item.scale ?? 1);
    if (pos.x === found.item.pos.x && pos.y === found.item.pos.y && scale === (found.item.scale ?? 1)) return project;
    return {
      ...project,
      tracks: project.tracks.map((track) => track === found.track
        ? { ...track, items: (track.items ?? []).map((item) => item.id === id ? { ...item, pos, scale } : item) }
        : track),
    };
  }
  return project;
}

export function resetVisualTransform(project: Project, id: string): Project {
  const found = findElement(project, id);
  if (found?.clip) return resetClipTransform(project, id);
  if (found?.item && isGraphicItem(found.item)) {
    if (found.item.pos.x === 0.5 && found.item.pos.y === 0.5 && found.item.scale === 1) return project;
    return updateGraphicTransform(project, id, { x: 0.5, y: 0.5, scale: 1 });
  }
  if (found?.item) {
    if (found.item.pos.x === 0.5 && found.item.pos.y === 0.5 && (found.item.scale ?? 1) === 1) return project;
    return updateVisualTransform(project, id, { x: 0.5, y: 0.5, scale: 1 });
  }
  return project;
}

function textBounds(item: TextItem, width: number, height: number): NormalizedVisualBounds {
  const layout = resolveLayout(item, width, height);
  // Text width is not persisted; this conservative hit box is sized from the same scaled font used
  // by the preview and is only used for selection/keyboard targeting.
  const textWidth = Math.max(layout.font_px, Math.round(item.text.length * layout.font_px * 0.58));
  return { x: item.pos.x - textWidth / width / 2, y: item.pos.y - layout.font_px / height / 2,
    width: textWidth / width, height: (layout.font_px * 1.2) / height };
}

export function visualBounds(project: Project, id: string): NormalizedVisualBounds | null {
  const found = findElement(project, id);
  if (!found) return null;
  if (found.item && isUnsupportedGraphic(found.item)) return null;
  if (found.clip && found.track.kind !== "audio") {
    const transform = resolvedTransform(project, found.clip);
    return { x: transform.x - transform.scale / 2, y: transform.y - transform.scale / 2,
      width: transform.scale, height: transform.scale };
  }
  if (found.item && isGraphicItem(found.item)) {
    const geometry = graphicGeometry(found.item as TrustedGraphicItem, project.width, project.height);
    return { x: geometry.box.x / project.width, y: geometry.box.y / project.height,
      width: geometry.box.width / project.width, height: geometry.box.height / project.height };
  }
  if (found.item) return textBounds(found.item, project.width, project.height);
  return null;
}

function isActive(item: Clip | TextItem | GraphicItem, frameF: number): boolean {
  const duration = "durF" in item ? item.durF : item.srcOutF - item.srcInF;
  return frameF >= item.tlStartF && frameF < item.tlStartF + duration;
}

/** Return all visuals under a point from top layer to bottom layer (deterministic chooser order). */
export function hitTestVisuals(project: Project, x: number, y: number, frameF = 0): string[] {
  const result: string[] = [];
  for (let trackIndex = project.tracks.length - 1; trackIndex >= 0; trackIndex -= 1) {
    const track = project.tracks[trackIndex];
    if (!isVisualTrack(track)) continue;
    const elements: (Clip | TextItem | GraphicItem)[] = [...(track.clips ?? []), ...(track.items ?? [])];
    for (let elementIndex = elements.length - 1; elementIndex >= 0; elementIndex -= 1) {
      const element = elements[elementIndex];
      if (!isActive(element, frameF)) continue;
      const bounds = visualBounds(project, element.id);
      if (bounds && x >= bounds.x && x <= bounds.x + bounds.width && y >= bounds.y && y <= bounds.y + bounds.height) result.push(element.id);
    }
  }
  return result;
}

export function hitTestTopVisual(project: Project, x: number, y: number, frameF = 0): string | null {
  return hitTestVisuals(project, x, y, frameF)[0] ?? null;
}

/** Remove only an overlay/graphic or non-speech visual. Primary speech media is never removable here. */
export function removeOverlay(project: Project, id: string): Project {
  const found = findElement(project, id);
  if (!found || (found.clip && found.track.kind === "speech") || (found.clip && found.track.kind === "audio")) return project;
  return {
    ...project,
    tracks: project.tracks.map((track) => track === found.track
      ? { ...track, clips: found.clip ? (track.clips ?? []).filter((clip) => clip.id !== id) : track.clips,
        items: found.item ? (track.items ?? []).filter((item) => item.id !== id) : track.items }
      : track),
  };
}

export function updateClipTransform(project: Project, id: string, patch: Partial<VisualTransform>): Project {
  const found = findElement(project, id);
  const clip = found?.clip;
  if (!found || !clip || found.track.kind === "audio") return project;
  const transform = normalizeTransform(project, clip, patch);
  if (JSON.stringify(clip.transform) === JSON.stringify(transform)) return project;
  return {
    ...project,
    tracks: project.tracks.map((track) => track === found.track
      ? { ...track, clips: (track.clips ?? []).map((clip) => clip.id === id ? { ...clip, transform } : clip) }
      : track),
  };
}

export function resetClipTransform(project: Project, id: string): Project {
  const found = findElement(project, id);
  const clip = found?.clip;
  if (!found || !clip || !clip.transform) return project;
  return {
    ...project,
    tracks: project.tracks.map((track) => track === found.track
      ? { ...track, clips: (track.clips ?? []).map((clip) => clip.id === id ? { ...clip, transform: undefined } : clip) }
      : track),
  };
}

export function layerInfo(project: Project, id: string): { index: number; count: number } | null {
  const found = findElement(project, id);
  if (!found || !isVisualTrack(found.track)) return null;
  const layers = project.tracks.filter(isVisualTrack);
  const index = layers.indexOf(found.track);
  return index < 0 ? null : { index, count: layers.length };
}

// Project track order IS the preview stacking order: later visual tracks paint above earlier ones.
// Swap only visual tracks so moving a layer never disturbs the independent audio-track placement.
export function moveVisualLayer(project: Project, id: string, direction: -1 | 1): Project {
  const layer = layerInfo(project, id);
  if (!layer) return project;
  const target = layer.index + direction;
  if (target < 0 || target >= layer.count) return project;
  const layers = project.tracks.filter(isVisualTrack);
  [layers[layer.index], layers[target]] = [layers[target], layers[layer.index]];
  let cursor = 0;
  return { ...project, tracks: project.tracks.map((track) => isVisualTrack(track) ? layers[cursor++] : track) };
}
