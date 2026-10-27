// The player composition rendered by the <FramePlayer>. A PURE function of the `project` contentProps:
// every track's clips/items become <FrameWindow>s, positioned in timeline-clock frames. Speech + b-roll
// video → <ClipVideo>; music → <ClipAudio>; text items → an absolutely-positioned <div> laid out by the shared
// resolveLayout, so the preview and compile.py's drawtext agree pixel-for-pixel.
//
import { Layer, ClipAudio, FrameImage, FrameWindow, ClipVideo, useFrame } from "../player";
import { Fragment, useEffect, useMemo, useState } from "react";
import type { CSSProperties } from "react";
import { api, type ChannelProfiles, type TranscriptWord } from "../api";
import type { Project, TextItem } from "../types/project";
import { resolveMedia, type CompareSide, type MediaAvailability, type MediaPreference } from "./resolveMedia";
import { resolveLayout } from "../layout";
import { resolvedTransform } from "../timeline/visuals";
import { clipGain } from "./audio";
import { AnimatedGraphic, graphicGeometry, isGraphicItem, type TrustedGraphicItem } from "../graphics/registry";
import { SceneLayer, type SceneRenderSpec } from "./SceneLayer";
import { useSourceProxy } from "./sourceProxy";
import { PillCaption } from "./PillCaption";

export type ResizeCorner = "nw" | "ne" | "se" | "sw";
export type TransformGesture = "move" | "resize";

function SelectionHandles({ id, onTransformDragStart }: {
  id: string;
  onTransformDragStart?: (id: string, clientX: number, clientY: number, gesture?: TransformGesture, corner?: ResizeCorner, unconstrained?: boolean) => void;
}) {
  return <>{(["nw", "ne", "se", "sw"] as const).map((corner) => <button
    key={corner}
    type="button"
    className={`preview-handle preview-handle-${corner}`}
    aria-label={`Resize selected visual ${corner.toUpperCase()} corner`}
    title="Resize proportionally; hold Shift for dominant-axis resize"
    onPointerDown={(event) => {
      event.stopPropagation();
      event.preventDefault();
      try { event.currentTarget.setPointerCapture(event.pointerId); } catch { /* capture is unavailable in static player tests */ }
      onTransformDragStart?.(id, event.clientX, event.clientY, "resize", corner, event.shiftKey);
    }}
  />)}</>;
}

export type CaptionInterval = {
  startF: number;
  endF: number;
  word: TranscriptWord;
  /** Flat transcript index, used to mirror captions.emphasis without changing the word object. */
  wordIdx?: number;
};

export type CaptionIndex = ReadonlyMap<string, readonly CaptionInterval[]>;

export type CaptionPage = {
  startF: number;
  endF: number;
  words: readonly CaptionInterval[];
};

type MappedCaptionInterval = CaptionInterval & { media: string };

const DEFAULT_CAPTION_STYLE = {
  look: "pill",
  wordsPerPage: 3,
  font: "Inter",
  bold: true,
  baseColor: "#ffffff",
  activeColor: "#ffff00",
  emphasisColor: "#ffff00",
  marginVPct: 0.15,
  alignment: 2,
  outlineRatio: 14,
} as const;

function assColorToCss(value: unknown, fallback: string): string {
  if (typeof value !== "string") return fallback;
  const normalized = value.trim();
  if (/^#[0-9a-f]{6}$/i.test(normalized)) return normalized;
  const hex = normalized.replace(/^&H/i, "");
  if (!/^[0-9a-f]+$/i.test(hex)) return fallback;
  // ASS stores colours as AABBGGRR (or BBGGRR when alpha is omitted), while CSS is RRGGBB.
  if (hex.length >= 8) return `#${hex.slice(6, 8)}${hex.slice(4, 6)}${hex.slice(2, 4)}`;
  if (hex.length === 6) return `#${hex.slice(4, 6)}${hex.slice(2, 4)}${hex.slice(0, 2)}`;
  return fallback;
}

function captionStyle(project: Project, channels: ChannelProfiles = {}): {
  look: "pill" | "outline";
  wordsPerPage: number;
  font: string;
  bold: boolean;
  baseColor: string;
  activeColor: string;
  emphasisColor: string;
  marginVPct: number;
  alignment: number;
  outlineRatio: number;
} {
  const channelRaw = project.channel ? channels[project.channel]?.captionStyle ?? {} : {};
  // Match pipeline.compile: defaults <- effective channel profile <- project override.
  const raw = { ...channelRaw, ...(project.captions?.style ?? {}) } as Record<string, unknown>;
  const wordsPerPage = typeof raw.wordsPerPage === "number" && Number.isFinite(raw.wordsPerPage)
    ? Math.max(1, Math.floor(raw.wordsPerPage)) : DEFAULT_CAPTION_STYLE.wordsPerPage;
  const marginVPct = typeof raw.marginVPct === "number" && Number.isFinite(raw.marginVPct)
    ? Math.max(0, Math.min(0.5, raw.marginVPct)) : DEFAULT_CAPTION_STYLE.marginVPct;
  const outlineRatio = typeof raw.outlineRatio === "number" && Number.isFinite(raw.outlineRatio) && raw.outlineRatio > 0
    ? raw.outlineRatio : DEFAULT_CAPTION_STYLE.outlineRatio;
  const alignment = typeof raw.alignment === "number" && Number.isFinite(raw.alignment)
    ? Math.max(1, Math.min(9, Math.floor(raw.alignment))) : DEFAULT_CAPTION_STYLE.alignment;
  const font = typeof raw.font === "string" && raw.font.trim() ? raw.font.replace(/\s+Bold$/i, "") : DEFAULT_CAPTION_STYLE.font;
  return {
    look: raw.look === "outline" ? "outline" : DEFAULT_CAPTION_STYLE.look,
    wordsPerPage,
    font,
    bold: raw.bold !== false,
    baseColor: assColorToCss(raw.baseColor, DEFAULT_CAPTION_STYLE.baseColor),
    activeColor: assColorToCss(raw.activeColor, DEFAULT_CAPTION_STYLE.activeColor),
    emphasisColor: assColorToCss(raw.emphasisColor, assColorToCss(raw.activeColor, DEFAULT_CAPTION_STYLE.activeColor)),
    marginVPct,
    alignment,
    outlineRatio,
  };
}

function captionPosition(project: Project, alignment: number, marginVPct: number): CSSProperties {
  const column = (alignment - 1) % 3;
  const row = Math.floor((alignment - 1) / 3);
  const inset = `${80 / project.width * 100}%`;
  const vertical = row === 2
    ? { top: `${marginVPct * 100}%` }
    : row === 1
      ? { top: "50%", transform: "translateY(-50%)" }
      : { bottom: `${marginVPct * 100}%` };
  return {
    left: inset,
    right: inset,
    textAlign: column === 0 ? "left" : column === 2 ? "right" : "center",
    ...vertical,
  };
}

function captionWordOrder(left: CaptionInterval, right: CaptionInterval): number {
  if (left.startF !== right.startF) return right.startF - left.startF;
  if (left.endF !== right.endF) return right.endF - left.endF;
  if (left.word.segIdx !== right.word.segIdx) return left.word.segIdx - right.word.segIdx;
  if (left.word.w < right.word.w) return -1;
  if (left.word.w > right.word.w) return 1;
  return 0;
}

function sourceCaptionIntervals(project: Project, words: TranscriptWord[], media?: string | null): MappedCaptionInterval[] {
  const intervals: MappedCaptionInterval[] = [];
  for (const track of project.tracks) {
    if (track.kind !== "speech") continue;
    for (const clip of track.clips ?? []) {
      if (media && clip.media.source !== media) continue;
      for (const [wordIdx, word] of words.entries()) {
        // player samples frame N at the continuous time N/fps. Ceil starts so a word at 1.01s
        // first appears on frame 31, matching the ASS event's continuous start rather than
        // activating one frame early at frame 30.
        const sourceStartF = Math.ceil(word.start * project.fps);
        const sourceEndF = Math.max(sourceStartF + 1, Math.ceil(word.end * project.fps));
        // Caption words are source-timed. Clip the interval at both source trim bounds so a
        // crossing word cannot leak past the end of a trimmed speech clip.
        const boundedStartF = Math.max(sourceStartF, clip.srcInF);
        const boundedEndF = Math.min(sourceEndF, clip.srcOutF);
        if (boundedStartF >= boundedEndF) continue;
        intervals.push({
          media: clip.media.source,
          startF: clip.tlStartF + boundedStartF - clip.srcInF,
          endF: clip.tlStartF + boundedEndF - clip.srcInF,
          word,
          wordIdx,
        });
      }
    }
  }
  return intervals;
}

/**
 * Convert possibly-overlapping source intervals into disjoint timeline spans. At every span the
 * latest-starting word wins; ties use the stable end/segment/text order above. This makes a query
 * a single binary-search candidate check while retaining deterministic overlap behavior.
 */
function normalizeCaptionIntervals(raw: CaptionInterval[]): CaptionInterval[] {
  if (raw.length < 2) return raw.slice();
  const starts = new Map<number, number[]>();
  const ends = new Map<number, number[]>();
  const boundaries = new Set<number>();
  for (const [index, interval] of raw.entries()) {
    (starts.get(interval.startF) ?? (starts.set(interval.startF, []), starts.get(interval.startF)!)).push(index);
    (ends.get(interval.endF) ?? (ends.set(interval.endF, []), ends.get(interval.endF)!)).push(index);
    boundaries.add(interval.startF);
    boundaries.add(interval.endF);
  }
  const orderedBoundaries = [...boundaries].sort((left, right) => left - right);
  const active = new Array<boolean>(raw.length).fill(false);
  const heap: number[] = [];
  const higherPriority = (leftIndex: number, rightIndex: number): boolean => {
    const order = captionWordOrder(raw[leftIndex], raw[rightIndex]);
    return order < 0 || (order === 0 && leftIndex < rightIndex);
  };
  const swap = (left: number, right: number): void => {
    const value = heap[left];
    heap[left] = heap[right];
    heap[right] = value;
  };
  const push = (index: number): void => {
    heap.push(index);
    let child = heap.length - 1;
    while (child > 0) {
      const parent = Math.floor((child - 1) / 2);
      if (higherPriority(heap[parent], heap[child])) break;
      swap(parent, child);
      child = parent;
    }
  };
  const discardInactive = (): void => {
    while (heap.length > 0 && !active[heap[0]]) {
      const last = heap.pop()!;
      if (heap.length === 0) break;
      heap[0] = last;
      let parent = 0;
      while (true) {
        const left = parent * 2 + 1;
        const right = left + 1;
        let highest = parent;
        if (left < heap.length && higherPriority(heap[left], heap[highest])) highest = left;
        if (right < heap.length && higherPriority(heap[right], heap[highest])) highest = right;
        if (highest === parent) break;
        swap(parent, highest);
        parent = highest;
      }
    }
  };
  const normalized: CaptionInterval[] = [];
  for (let boundaryIndex = 0; boundaryIndex < orderedBoundaries.length - 1; boundaryIndex += 1) {
    const boundary = orderedBoundaries[boundaryIndex];
    for (const index of ends.get(boundary) ?? []) active[index] = false;
    for (const index of starts.get(boundary) ?? []) {
      active[index] = true;
      push(index);
    }
    const nextBoundary = orderedBoundaries[boundaryIndex + 1];
    discardInactive();
    const selected = heap[0];
    if (selected === undefined || boundary >= nextBoundary) continue;
    const previous = normalized[normalized.length - 1];
    if (previous && previous.endF === boundary && previous.word === raw[selected].word) {
      previous.endF = nextBoundary;
    } else {
      normalized.push({ startF: boundary, endF: nextBoundary, word: raw[selected].word, wordIdx: raw[selected].wordIdx });
    }
  }
  return normalized;
}

/** Build the immutable source-to-timeline caption index once per project/word resource. */
export function buildCaptionIndex(project: Project, words: TranscriptWord[]): CaptionIndex {
  const index = new Map<string, CaptionInterval[]>();
  for (const interval of sourceCaptionIntervals(project, words)) {
    (index.get(interval.media) ?? (index.set(interval.media, []), index.get(interval.media)!)).push(interval);
  }
  for (const intervals of index.values()) {
    intervals.sort((left, right) => right.startF - left.startF || right.endF - left.endF || left.word.segIdx - right.word.segIdx || (left.word.w < right.word.w ? -1 : left.word.w > right.word.w ? 1 : 0));
  }
  for (const [source, intervals] of index) index.set(source, normalizeCaptionIntervals(intervals));
  return index;
}

/** Group the same mapped source words into the fixed-size ASS karaoke pages. */
export function buildCaptionPages(project: Project, words: TranscriptWord[], media?: string | null, channels: ChannelProfiles = {}): CaptionPage[] {
  const intervals = sourceCaptionIntervals(project, words, media).sort(
    (left, right) => left.startF - right.startF || left.endF - right.endF || left.word.segIdx - right.word.segIdx,
  );
  const size = captionStyle(project, channels).wordsPerPage;
  const pages: CaptionPage[] = [];
  for (let index = 0; index < intervals.length; index += size) {
    const pageWords = intervals.slice(index, index + size);
    if (pageWords.length === 0) continue;
    pages.push({
      startF: pageWords[0].startF,
      endF: Math.max(...pageWords.map((word) => word.endF)),
      words: pageWords,
    });
  }
  return pages;
}

/** Find the active interval with one bounded binary-search candidate check. */
export function findActiveCaption(intervals: readonly CaptionInterval[] | undefined, frame: number, onComparison?: () => void): CaptionInterval | null {
  if (!intervals?.length) return null;
  let low = 0;
  let high = intervals.length - 1;
  let candidate = -1;
  while (low <= high) {
    onComparison?.();
    const middle = Math.floor((low + high) / 2);
    if (intervals[middle].startF <= frame) {
      candidate = middle;
      low = middle + 1;
    } else {
      high = middle - 1;
    }
  }
  if (candidate < 0 || frame >= intervals[candidate].endF) return null;
  return intervals[candidate];
}

function findKaraokeCaption(page: CaptionPage, frame: number): CaptionInterval | null {
  if (frame < page.startF || frame >= page.endF) return null;
  for (const [index, word] of page.words.entries()) {
    const nextStartF = index + 1 < page.words.length ? page.words[index + 1].startF : page.endF;
    if (frame >= word.startF && frame < nextStartF) return word;
  }
  return null;
}

function CaptionOverlay({ project, words, enabled, media }: {
  project: Project;
  words: TranscriptWord[];
  enabled: boolean;
  media?: string | null;
}) {
  const [channels, setChannels] = useState<ChannelProfiles>({});
  useEffect(() => {
    if (!enabled || !project.channel) return;
    const controller = new AbortController();
    setChannels({});
    void api.getChannels(controller.signal).then((doc) => {
      if (!controller.signal.aborted) setChannels(doc.channels ?? {});
    }).catch(() => {});
    return () => controller.abort();
  }, [enabled, project.channel]);
  const look = useMemo(() => captionStyle(project, channels).look, [project, channels]);
  if (!enabled || project.captions?.enabled !== true || words.length === 0) return null;
  return look === "pill"
    ? <PillCaption project={project} words={words} />
    : <OutlineCaption project={project} words={words} media={media} channels={channels} />;
}

/** The outline karaoke look (`look: "outline"`): white outlined words with a sweeping accent fill. */
function OutlineCaption({ project, words, media, channels }: {
  project: Project;
  words: TranscriptWord[];
  media?: string | null;
  channels: ChannelProfiles;
}) {
  const captionPages = useMemo(() => buildCaptionPages(project, words, media, channels), [project, words, media, channels]);
  const style = useMemo(() => captionStyle(project, channels), [project, channels]);
  const frame = useFrame();
  const page = captionPages.find((candidate) => candidate.startF <= frame && frame < candidate.endF);
  const active = page ? findKaraokeCaption(page, frame) : null;
  if (active && page) {
    const exactIndex = page.words.findIndex((word) => word.wordIdx === active.wordIdx && word.word === active.word);
    const activeIndex = exactIndex >= 0 ? exactIndex : page.words.findIndex((word) => word.word === active.word);
    const currentIndex = activeIndex >= 0 ? activeIndex : 0;
    const nextStartF = currentIndex + 1 < page.words.length ? page.words[currentIndex + 1].startF : active.endF;
    const progress = Math.max(0, Math.min(1, (frame - active.startF) / Math.max(1, nextStartF - active.startF)));
    const emphasis = new Set(project.captions?.emphasis ?? []);
    const fontSize = 72 * project.height / 1080;
    const outline = Math.max(3, fontSize / style.outlineRatio);
    return <div
      className="caption-overlay compare-caption-overlay"
      data-caption-word={active.word.w}
      aria-label={`Caption: ${page.words.map((word) => word.word.w).join(" ")}`}
      // Layout is inline, not in a host stylesheet, so the caption sits above the footage in every
      // host that mounts the composition.
      style={{
        position: "absolute", zIndex: 5, lineHeight: 1.2, pointerEvents: "none", whiteSpace: "nowrap",
        ...captionPosition(project, style.alignment, style.marginVPct),
        fontSize, fontFamily: '"Inter"', fontWeight: style.bold ? 700 : 400,
        textShadow: `0 ${Math.max(1, fontSize / 40)}px ${Math.max(1, fontSize / 40)}px #000`,
        WebkitTextStroke: `${outline}px #000`, paintOrder: "stroke fill",
      }}
    >{page.words.map((word, index) => {
      const isCurrent = index === currentIndex;
      const isPast = index < currentIndex;
      const isEmphasis = word.wordIdx !== undefined && emphasis.has(word.wordIdx);
      const tokenColor = isEmphasis ? style.emphasisColor : isPast ? style.activeColor : style.baseColor;
      const tokenStyle = isCurrent && !isEmphasis ? {
        color: "transparent",
        backgroundImage: `linear-gradient(90deg, ${style.activeColor} 0%, ${style.activeColor} ${progress * 100}%, ${style.baseColor} ${progress * 100}%, ${style.baseColor} 100%)`,
        backgroundClip: "text" as const,
        WebkitBackgroundClip: "text" as const,
        WebkitTextFillColor: "transparent",
      } : { color: tokenColor };
      return <Fragment key={`${word.word.segIdx}-${word.startF}-${index}`}>
        {index > 0 ? " " : null}
        <span
          className={`caption-word ${isCurrent ? "caption-word-active" : isPast ? "caption-word-past" : "caption-word-future"}`}
          style={{ display: "inline-block", ...tokenStyle }}
        >{word.word.w}</span>
      </Fragment>;
    })}</div>;
  }
  return null;
}

export function ProjectComposition({ project, selection, onSelect, onSelectAt, onTransformDragStart, onTransformKeyDown, mediaPreference = "proxy", allowSourceGeneration = true, mediaManifest, mediaSide, captionsEnabled = false, captionWords, captionMedia, scenes }: {
  project: Project;
  selection?: string | null;
  onSelect?: (id: string) => void;
  onSelectAt?: (id: string, clientX: number, clientY: number) => void;
  onTransformDragStart?: (id: string, clientX: number, clientY: number, gesture?: TransformGesture, corner?: ResizeCorner, unconstrained?: boolean) => void;
  onTransformKeyDown?: (id: string, key: string, larger: boolean) => void;
  mediaPreference?: MediaPreference;
  allowSourceGeneration?: boolean;
  /** Present only for Compare; ordinary Preview keeps its legacy lazy-source behavior. */
  mediaManifest?: MediaAvailability[];
  mediaSide?: CompareSide;
  captionsEnabled?: boolean;
  captionWords?: TranscriptWord[];
  captionMedia?: string | null;
  /** Animated and cameo scenes (v3 Story view only); drawn above every footage track, below text. */
  scenes?: SceneRenderSpec[];
}) {
  useSourceProxy(allowSourceGeneration ? project : null);
  const { width, height } = project;
  const sceneSlot = scenes?.length
    ? project.tracks.map((t) => t.kind === "speech" || t.kind === "video").lastIndexOf(true) + 1
    : -1;
  const sceneLayer = scenes?.length
    ? <SceneLayer key="scene-layer" scenes={scenes} width={width} height={height} fps={project.fps} />
    : null;
  return (
    <Layer style={{ backgroundColor: "black" }}>
      {/* Document track order is the preview layer stack: later visual tracks paint on top. */}
      {project.tracks.flatMap((track, trackIdx) =>
        [
          ...(trackIdx === sceneSlot ? [sceneLayer] : []),
          ...(track.clips ?? []).map((clip) => {
          const availability = mediaManifest
            ? mediaManifest.find((item) => item.id === clip.id && item.side === mediaSide) ?? { id: clip.id, side: mediaSide ?? "before", sourceAvailable: false, proxyAvailable: false }
            : undefined;
          const url = resolveMedia(clip.media, mediaPreference, allowSourceGeneration, availability);
          const dur = clip.srcOutF - clip.srcInF;
          if (dur <= 0) return null;
          if (!url) return <FrameWindow key={clip.id} startFrame={clip.tlStartF} frameCount={dur}>
            <div className="preview-media-unavailable" role="img" aria-label={`Media unavailable for ${clip.id}`}>
              Media unavailable for side-effect-free Compare
            </div>
          </FrameWindow>;
          const gain = clipGain(project, track.kind, clip);
          if (track.kind === "audio") {
            return <FrameWindow key={clip.id} startFrame={clip.tlStartF} frameCount={dur}>
              <ClipAudio src={url} sourceStartFrame={clip.srcInF} gain={gain} />
            </FrameWindow>;
          }
          const transform = resolvedTransform(project, clip);
          const selected = selection === clip.id;
          const image = /\.(png|jpe?g|webp|gif)$/i.test(clip.media.source);
          const boxStyle = {
            position: "absolute" as const,
            left: `${transform.x * 100}%`,
            top: `${transform.y * 100}%`,
            width: `${transform.scale * 100}%`,
            height: `${transform.scale * 100}%`,
            transform: "translate(-50%, -50%)",
            outline: selected ? "3px solid #4cc9f0" : undefined,
            outlineOffset: selected ? "-3px" : undefined,
            cursor: "pointer",
            overflow: "hidden" as const,
          };
          const mediaStyle = {
            width: "100%",
            height: "100%",
            // Keep the browser composition's default fit identical to compile._resolved_transform
            // (see timeline/visuals.defaultTransform): speech contains, other video covers, images
            // contain, and cropped projects cover.
            objectFit: transform.fit,
            objectPosition: "50% 50%",
          };
          return (
            <FrameWindow key={clip.id} startFrame={clip.tlStartF} frameCount={dur}>
              <div tabIndex={selected ? 0 : -1} aria-label="Selected visual" onKeyDown={(event) => {
                if (selected && ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
                  event.preventDefault();
                  onTransformKeyDown?.(clip.id, event.key, event.shiftKey);
                }
              }} style={boxStyle} onPointerDown={(event) => {
                event.stopPropagation();
                try { event.currentTarget.setPointerCapture(event.pointerId); } catch { /* test doubles may omit capture */ }
                onTransformDragStart?.(clip.id, event.clientX, event.clientY, "move");
                (onSelectAt ?? ((id: string) => onSelect?.(id)))(clip.id, event.clientX, event.clientY);
              }}>
                {image
                  ? <FrameImage src={url} style={mediaStyle} />
                  : <ClipVideo src={url} sourceStartFrame={clip.srcInF} gain={gain} style={mediaStyle} />}
                {selected && <SelectionHandles id={clip.id} onTransformDragStart={onTransformDragStart} />}
              </div>
            </FrameWindow>
          );
          }),
          ...(track.items ?? []).map((item) => {
          if (item.durF <= 0) return null;
          if ("type" in item) {
            // Any discriminated item must be a trusted registry graphic. Future/unknown
            // discriminants are not legacy titles and must never reach title layout/rendering.
            if (item.type !== "graphic" || !isGraphicItem(item)) return null;
            const selected = selection === item.id;
            const geometry = graphicGeometry(item as TrustedGraphicItem, width, height);
            return (
              <FrameWindow key={item.id} startFrame={item.tlStartF} frameCount={item.durF}>
                <div tabIndex={selected ? 0 : -1} aria-label={`${item.componentId} graphic`} onKeyDown={(event) => {
                  if (selected && ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
                    event.preventDefault();
                    onTransformKeyDown?.(item.id, event.key, event.shiftKey);
                  }
                }} onPointerDown={(event) => {
                  event.stopPropagation();
                  try { event.currentTarget.setPointerCapture(event.pointerId); } catch { /* test doubles may omit capture */ }
                  onTransformDragStart?.(item.id, event.clientX, event.clientY, "move");
                  (onSelectAt ?? ((id: string) => onSelect?.(id)))(item.id, event.clientX, event.clientY);
                }} style={{
                  position: "absolute", left: geometry.box.x, top: geometry.box.y,
                  width: geometry.box.width, height: geometry.box.height,
                  outline: selected ? "3px solid #4cc9f0" : undefined, outlineOffset: 4, cursor: "pointer",
                }}>
                  <AnimatedGraphic item={item as TrustedGraphicItem} geometry={geometry} canvasHeight={height} />
                  {selected && <SelectionHandles id={item.id} onTransformDragStart={onTransformDragStart} />}
                </div>
              </FrameWindow>
            );
          }
          // The discriminated branch above handles every graphic/future item. At this point the
          // remaining legacy item is a title; keep the narrowing explicit for the schema union.
          const textItem = item as TextItem;
          const box = resolveLayout(textItem, width, height);
          const textWidth = Math.max(box.font_px, Math.round(textItem.text.length * box.font_px * 0.58));
          const textHeight = Math.max(1, Math.round(box.font_px * 1.2));
          const selected = selection === textItem.id;
          return (
            <FrameWindow key={textItem.id} startFrame={textItem.tlStartF} frameCount={textItem.durF}>
              <div
                tabIndex={selected ? 0 : -1}
                aria-label={`Title: ${textItem.text}`}
                onKeyDown={(event) => {
                  if (selected && ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
                    event.preventDefault();
                    onTransformKeyDown?.(textItem.id, event.key, event.shiftKey);
                  }
                }}
                onPointerDown={(event) => {
                  event.stopPropagation();
                  try { event.currentTarget.setPointerCapture(event.pointerId); } catch { /* test doubles may omit capture */ }
                  onTransformDragStart?.(textItem.id, event.clientX, event.clientY, "move");
                  (onSelectAt ?? ((id: string) => onSelect?.(id)))(textItem.id, event.clientX, event.clientY);
                }}
                style={{
                  position: "absolute",
                  left: box.x_px,
                  top: box.y_px,
                  width: textWidth,
                  height: textHeight,
                  boxSizing: "border-box",
                  transform: "translate(-50%, -50%)",
                  fontSize: box.font_px,
                  textAlign: box.align,
                  color: "#fff",
                  fontFamily: '"Liberation Sans"',
                  fontWeight: 700,
                  whiteSpace: "pre-wrap",
                  textShadow: "0 2px 8px rgba(0,0,0,0.6)",
                  outline: selected ? "3px solid #4cc9f0" : undefined,
                  outlineOffset: 4,
                  cursor: "pointer",
                }}
              >
                {textItem.text}
                {selected && <SelectionHandles id={textItem.id} onTransformDragStart={onTransformDragStart} />}
              </div>
            </FrameWindow>
          );
          }),
        ],
      )}
      {sceneSlot === project.tracks.length && sceneLayer}
      {captionWords && captionWords.length > 0 && <CaptionOverlay project={project} words={captionWords} enabled={captionsEnabled} media={captionMedia} />}
    </Layer>
  );
}
