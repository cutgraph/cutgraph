import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useStore } from "zustand";
import {
  useProjectStore,
  setPlayhead,
  setSelection,
  setTimelineSel,
  setTether,
  setWarnings,
  beginPreview,
  commit,
  cancelPreview,
  applyLocal,
  undo,
} from "../../../store/useProjectStore";
import type { Anchor, Clip, GraphicItem, Project, TextItem, Track } from "../../../types/project";
import { api, type PeaksDoc, type ProjectAsset, type TranscriptWord } from "../../../api";
import { columns } from "../../../transcript/waveform";
import { timelineToSrc } from "../../../transcript/cutmap";
import { audioKind, audioLaneKind } from "../../../timeline/audioKind";
import { useTranscript } from "../../../transcript/useTranscript";
import { persistCurrent } from "../../../persist";
import { acceptSourceCuts } from "../../../transcript/acceptCuts";
import { toast } from "../../../toast";
import {
  clipPeakWindow,
  fitPxPerFrame,
  frameToPx,
  projectDurationF,
  pxToFrame,
  rulerTicks,
} from "../../../timeline/geometry";
import {
  moveElementToTrack,
  trimClip,
  splitClipAt,
  deleteElement,
  nudgeClip,
  snapFrame,
} from "../../../timeline/ops";
import { flipAnchor, setElementAnchor } from "../../../timeline/anchors";
import { addTextItem } from "../../../timeline/textItems";

// Editable multi-track timeline: ruler, clip blocks, playhead, per-clip waveform in
// speech clips, seam notches, the timeline→transcript half of the bidirectional selection, plus
// pointer drag/trim, keyboard split/nudge/delete with frame + word-boundary + clip-edge snapping.
// Every drag is ONE undo entry (beginPreview on first move → immutable setState per move → commit on
// pointer-up); single-shot edits go through applyLocal. Each edit auto-persists through the
// revision-gated save. Timeline reads `project` from the store, so a remote ripple (SSE) reflows
// word-pinned clips within one tick automatically. For anchoring, each non-speech element shows a
// pin (amber = word-pinned, steel = time-pinned) that click-flips word↔time, hovering a word-pinned
// element tethers to its transcript word, and ripple-cut warnings render as per-element badges.
// Non-ripple edits clear the stale warning set.

// Snap radius in pixels: a drag edge within this of a frame/word/clip-edge target locks onto it.
const SNAP_PX = 8;

// A live pointer drag. beginPreview is deferred to the first move so a plain click (no movement)
// stays a pure selection with zero undo history. base = the pre-drag project (all moves apply against
// it, never compounding); words/fps snapshot the transcript for snapping.
interface DragCtx {
  mode: "move" | "in" | "out";
  clipId: string;
  label: string;
  base: Project;
  projectName: string;
  startX: number;
  sourceTrackId: string;
  origTlStartF: number;
  origSrcInF: number;
  origSrcOutF: number;
  pxPerFrame: number;
  words: TranscriptWord[];
  fps: number;
  started: boolean;
  changed: boolean;
}

function moveLabel(kind: Track["kind"], clip: Clip): string {
  if (kind === "video") return "Move b-roll";
  if (kind === "audio") return audioKind(clip) === "sfx" ? "Move sound" : "Move music";
  if (kind === "text") return "Move text";
  return "Move clip";
}

// Locate a clip or text item by id, returning its owning track.
function findElement(
  project: Project,
  id: string,
): { track: Track; clip?: Clip; item?: TextItem | GraphicItem } | null {
  for (const track of project.tracks) {
    const clip = (track.clips ?? []).find((c) => c.id === id);
    if (clip) return { track, clip };
    const item = (track.items ?? []).find((i) => i.id === id);
    if (item) return { track, item };
  }
  return null;
}

const GUTTER = 120; // fixed name-gutter width per row (matches pxToFrame(offsetX - GUTTER) seek math)
const RULER_H = 22;
const ROW_H = 58;
const WAVE_H = 34;
const MIN_PX_PER_FRAME = 0.02;
const MAX_PX_PER_FRAME = 8;
const DEFAULT_PX_PER_FRAME = 0.5;
const ZOOM_STEP = 1.2;

// Track render order top→bottom: text overlays, b-roll (video), speech, then audio, which is named
// for what it holds (timeline/audioKind.ts).
const KIND_LABEL: Record<Exclude<Track["kind"], "audio">, string> = {
  text: "text",
  video: "b-roll",
  speech: "speech",
};
const AUDIO_LABEL = { music: "music", sfx: "sound", audio: "audio" } as const;

function laneLabel(track: Track): string {
  return track.kind === "audio" ? AUDIO_LABEL[audioLaneKind(track)] : KIND_LABEL[track.kind];
}

// Clip fill by anchor: word-pinned = amber, time-pinned (or unanchored) = steel. Applied
// to text + b-roll clips; speech (cyan) and music (green) are coloured by kind instead.
function anchorClass(anchor?: Anchor): string {
  return anchor?.mode === "word" ? "tl-wordpin" : "tl-timepin";
}

function isGraphicTimelineItem(item: TextItem | GraphicItem): item is GraphicItem {
  return "type" in item && item.type === "graphic";
}

function clampZoom(v: number): number {
  return Math.min(MAX_PX_PER_FRAME, Math.max(MIN_PX_PER_FRAME, v));
}

// media path → slug (basename minus extension), the key for the peaks sidecar.
function mediaSlug(source: string): string {
  const base = source.split(/[\\/]/).pop() ?? source;
  const dot = base.lastIndexOf(".");
  return dot > 0 ? base.slice(0, dot) : base;
}

// A module cache deduplicates peaks fetches across speech clips sharing the same media.
const peaksCache = new Map<string, Promise<PeaksDoc>>();
function loadPeaks(slug: string): Promise<PeaksDoc> {
  let p = peaksCache.get(slug);
  if (!p) {
    p = api.getPeaks(slug);
    peaksCache.set(slug, p);
  }
  return p;
}

function useMeasuredWidth(): [React.RefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(0);
  // Measured before paint so a freshly mounted timeline never flashes at the fallback zoom.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    setW(el.clientWidth);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

// Windowed waveform for one speech clip: the file peaks sliced to [srcInF, srcOutF) then reduced to
// the clip's pixel width. Mirrors WaveformStrip's DPR-aware, --accent draw.
function ClipWaveform({
  slug,
  srcInF,
  srcOutF,
  fps,
  widthPx,
}: {
  slug: string;
  srcInF: number;
  srcOutF: number;
  fps: number;
  widthPx: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [peaks, setPeaks] = useState<PeaksDoc | null>(null);

  useEffect(() => {
    let cancelled = false;
    loadPeaks(slug)
      .then((d) => {
        if (!cancelled) setPeaks(d);
      })
      .catch(() => {
        if (!cancelled) setPeaks(null);
      });
    return () => {
      cancelled = true;
    };
  }, [slug]);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas || !peaks) return;
    const w = Math.max(1, Math.floor(widthPx));
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.floor(w * dpr);
    canvas.height = Math.floor(WAVE_H * dpr);
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, WAVE_H);
    const windowed = clipPeakWindow(peaks.peaks, srcInF, srcOutF, fps, peaks.sampleRate, peaks.windowMs);
    if (windowed.length === 0) return;
    const mid = WAVE_H / 2;
    ctx.strokeStyle = getComputedStyle(canvas).getPropertyValue("--accent").trim() || "#c8860a";
    ctx.lineWidth = 1;
    ctx.beginPath();
    columns(windowed, w).forEach(([mn, mx], i) => {
      const x = i + 0.5;
      ctx.moveTo(x, mid - mx * mid);
      ctx.lineTo(x, mid - mn * mid);
    });
    ctx.stroke();
  }, [peaks, srcInF, srcOutF, fps, widthPx]);

  useEffect(() => {
    draw();
  }, [draw]);

  return <canvas ref={canvasRef} className="tl-wave" style={{ width: "100%", height: WAVE_H }} />;
}

// A labelled timeline span drawn in its own row above the tracks (the v3 Story view's scenes).
export interface SceneBand {
  id: string;
  label: string;
  color: string;
  startF: number;
  endF: number;
}

const BAND_H = 34;

// The Story view's lanes: Picture, B-roll, Graphics and Music, each holding
// the tracks of one kind, then Captions. A lane with two busy tracks (a music bed and sound effects)
// shows both rows under its one name.
export type Lane = "picture" | "broll" | "graphics" | "music";
const LANES: [Lane, Track["kind"], string][] = [
  ["picture", "speech", "Picture"],
  ["broll", "video", "B-roll"],
  ["graphics", "text", "Graphics"],
  ["music", "audio", "Music"],
];

/** A block the v3 lens lays over a lane: an animated scene in Picture, a cameo in Graphics, a caption page. */
export interface LaneBlock {
  id: string;
  label: string;
  kind: string;
  startF: number;
  endF: number;
}

/** Timeline presentation: lanes, readable names and derived blocks. */
export interface TimelineLanes {
  label: (element: Clip | TextItem | GraphicItem) => string;
  over: Partial<Record<Lane, LaneBlock[]>>;
  captions: LaneBlock[];
  captionsOn: boolean;
}

interface LaneRow {
  lane: Lane;
  name: string;
  track: Track | null;
  first: boolean;
}

function laneRows(tracks: Track[]): LaneRow[] {
  return LANES.flatMap(([lane, kind, name]): LaneRow[] => {
    const all = tracks.filter((t) => t.kind === kind);
    const busy = all.filter((t) => (t.clips?.length ?? 0) + (t.items?.length ?? 0) > 0);
    const shown = busy.length ? busy : all.slice(0, 1);
    return shown.length
      ? shown.map((track, i) => ({ lane, name, track, first: i === 0 }))
      : [{ lane, name, track: null, first: true }];
  });
}

const LANE_GUTTER = 88;
const LANE_GUTTER_NARROW = 64;
const NARROW_PX = 560;
const LANE_H = 42;

export default function Timeline({
  draggedAsset,
  onAssetDrop,
  initialZoom,
  onZoomChange,
  className,
  sceneBands,
  onSceneBand,
  lanes,
}: {
  draggedAsset: () => ProjectAsset | null;
  onAssetDrop: (asset: ProjectAsset, trackId: string, frame: number) => void;
  initialZoom: number | null;
  onZoomChange: (zoom: number | null) => void;
  className?: string;
  sceneBands?: SceneBand[];
  onSceneBand?: (band: SceneBand) => void;
  lanes?: TimelineLanes;
}) {
  const project = useProjectStore((s) => s.project);
  const playheadF = useProjectStore((s) => s.playheadF);
  const selection = useProjectStore((s) => s.selection);
  const lastWarnings = useProjectStore((s) => s.lastWarnings);
  const projectName = project?.name ?? null;
  const { doc } = useTranscript(projectName);
  const words = doc?.words ?? [];
  const warnedIds = useMemo(() => new Set(lastWarnings.map((w) => w.itemId)), [lastWarnings]);
  const canUndo = useStore(useProjectStore.temporal, (s) => s.pastStates.length > 0);

  const [scrollRef, containerWidth] = useMeasuredWidth();
  const [zoomOverride, setZoomOverride] = useState<number | null>(initialZoom);
  const reportedZoom = useRef<number | null>(initialZoom);
  const bodyRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<DragCtx | null>(null);

  const durationF = project ? projectDurationF(project) : 0;
  const gutter = !lanes ? GUTTER : containerWidth > 0 && containerWidth < NARROW_PX ? LANE_GUTTER_NARROW : LANE_GUTTER;
  const rowH = lanes ? LANE_H : ROW_H;

  const fitZoom = useMemo(() => {
    return fitPxPerFrame(containerWidth, durationF, gutter, DEFAULT_PX_PER_FRAME);
  }, [containerWidth, durationF, gutter]);
  const pxPerFrame = zoomOverride ?? fitZoom;

  useEffect(() => {
    // Ignore the prop echo caused by this component's own persistence callback. A different value
    // means the workspace mode changed (wide ↔ narrow) or preferences were restored externally.
    if (initialZoom === reportedZoom.current) return;
    setZoomOverride(initialZoom);
    reportedZoom.current = initialZoom;
    const scroll = scrollRef.current;
    if (scroll) scroll.scrollLeft = 0;
  }, [initialZoom, scrollRef]);

  function setPersistedZoom(next: number | null): void {
    reportedZoom.current = next;
    setZoomOverride(next);
    onZoomChange(next);
  }

  const ticks = useMemo(
    () => (project ? rulerTicks(durationF, project.fps, pxPerFrame) : []),
    [project, durationF, pxPerFrame],
  );

  // Non-passive wheel → zoom (preventDefault keeps the page from scrolling under the gesture).
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (e.ctrlKey) return; // leave browser pinch-zoom alone
      e.preventDefault();
      const next = clampZoom((zoomOverride ?? fitZoom) * (e.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP));
      setPersistedZoom(next);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [scrollRef, fitZoom, zoomOverride]);

  const trackAtPoint = useCallback((clientX: number, clientY: number): string | null => {
    const row = document.elementFromPoint(clientX, clientY)?.closest<HTMLElement>(".tl-row[data-track-id]");
    return row?.dataset.trackId ?? null;
  }, []);

  // Live pointer-move: recompute the draft project from the pre-drag base (never compounding) and
  // write it immutably. beginPreview fires lazily on the first real move so a plain click records no
  // history. All snapping is on timeline frames; trim converts the snapped edge back to a source frame.
  const onMove = useCallback((e: PointerEvent) => {
    const d = dragRef.current;
    if (!d) return;
    const deltaF = Math.round((e.clientX - d.startX) / d.pxPerFrame);
    const sourceTrack = d.base.tracks.find((track) => track.id === d.sourceTrackId);
    const pointedTrack = d.mode === "move" ? trackAtPoint(e.clientX, e.clientY) : null;
    const targetTrackId = sourceTrack && pointedTrack
      && sourceTrack.kind !== "speech"
      && d.base.tracks.find((track) => track.id === pointedTrack)?.kind === sourceTrack.kind
      ? pointedTrack
      : d.sourceTrackId;
    const verticalChanged = targetTrackId !== d.sourceTrackId;
    if (deltaF === 0 && !verticalChanged && !d.started) return;
    if (!d.started) {
      beginPreview();
      d.started = true;
    }
    let next: Project;
    if (d.mode === "move") {
      const snapped = snapFrame(
        d.base, d.words, d.fps, d.origTlStartF + deltaF, d.pxPerFrame, SNAP_PX, d.clipId,
      );
      next = moveElementToTrack(d.base, d.clipId, targetTrackId, snapped);
    } else if (d.mode === "in") {
      const snappedTl = snapFrame(
        d.base, d.words, d.fps, d.origTlStartF + deltaF, d.pxPerFrame, SNAP_PX, d.clipId,
      );
      next = trimClip(d.base, d.clipId, "in", d.origSrcInF + (snappedTl - d.origTlStartF));
    } else {
      const origOutTl = d.origTlStartF + (d.origSrcOutF - d.origSrcInF);
      const snappedTl = snapFrame(
        d.base, d.words, d.fps, origOutTl + deltaF, d.pxPerFrame, SNAP_PX, d.clipId,
      );
      next = trimClip(d.base, d.clipId, "out", d.origSrcOutF + (snappedTl - origOutTl));
    }
    d.changed = next !== d.base;
    useProjectStore.setState({ project: next });
  }, [trackAtPoint]);

  // Window drag listeners are created per-drag and torn down together (pointer-up, pointercancel,
  // Escape, unmount, and remote-cancel). handlersRef holds the exact identities so removeEventListener
  // always matches what was added.
  const handlersRef = useRef<{
    move: (e: PointerEvent) => void;
    up: () => void;
    cancel: () => void;
    key: (e: KeyboardEvent) => void;
  } | null>(null);

  const detach = useCallback(() => {
    const h = handlersRef.current;
    if (!h) return;
    handlersRef.current = null;
    window.removeEventListener("pointermove", h.move);
    window.removeEventListener("pointerup", h.up);
    window.removeEventListener("pointercancel", h.cancel);
    window.removeEventListener("keydown", h.key);
  }, []);

  // Finish a drag: pointer-up commits + persists; pointercancel / Escape abort → restore the pre-drag
  // snapshot (cancelPreview), resume tracking, detach. A click with no movement is neither (no draft).
  const endDrag = useCallback(
    (abort: boolean) => {
      const d = dragRef.current;
      dragRef.current = null;
      detach();
      if (!d || !d.started) return;
      if (abort || !d.changed) {
        cancelPreview();
      } else {
        commit(d.label);
        setWarnings([]); // a fresh non-ripple edit supersedes any lingering drift warnings
        void persistCurrent(d.projectName);
      }
    },
    [detach],
  );

  const beginDrag = useCallback(
    (ctx: DragCtx) => {
      dragRef.current = ctx;
      const move = (e: PointerEvent): void => onMove(e);
      const up = (): void => endDrag(false);
      const cancel = (): void => endDrag(true);
      const key = (e: KeyboardEvent): void => {
        if (e.key === "Escape") {
          e.preventDefault();
          endDrag(true);
        }
      };
      handlersRef.current = { move, up, cancel, key };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
      window.addEventListener("pointercancel", cancel);
      window.addEventListener("keydown", key);
    },
    [onMove, endDrag],
  );

  // A remote adoption (applyRemote) advances BOTH project and revision in one setState. Tear down any
  // in-progress local drag (started or not) so a late pointer-up can't commit a stale draft over the
  // remote doc. applyRemote has already restored + checkpointed the store; here we only clear
  // the DOM drag. Our own save success advances the revision ALONE (project unchanged) → left running.
  useEffect(
    () =>
      useProjectStore.subscribe((s, prev) => {
        if (dragRef.current && s.revision !== prev.revision && s.project !== prev.project) {
          dragRef.current = null;
          detach();
        }
      }),
    [detach],
  );

  useEffect(() => detach, [detach]);

  if (!project) return null;

  const visualLayers = project.tracks.filter((track) => track.kind !== "audio");
  // The top timeline row is the top preview layer. Later visual tracks paint over earlier ones;
  // audio is not composited, so it stays beneath the visible stack.
  const rows = [...visualLayers].reverse().concat(project.tracks.filter((track) => track.kind === "audio"));
  const contentWidth = gutter + frameToPx(Math.max(1, durationF), pxPerFrame);

  // Content-space x from a pointer event, accounting for horizontal scroll.
  function contentX(e: { clientX: number }): number {
    const body = bodyRef.current;
    if (!body) return 0;
    return e.clientX - body.getBoundingClientRect().left;
  }

  function seekTo(e: React.MouseEvent): void {
    setPlayhead(Math.max(0, pxToFrame(contentX(e) - gutter, pxPerFrame)));
  }

  // Empty track-body click: seek + clear both selections (clip clicks stopPropagation so this fires
  // only on empty space).
  function onEmptyClick(e: React.MouseEvent): void {
    seekTo(e);
    setSelection(null);
    setTimelineSel(null);
  }

  function acceptsAsset(asset: ProjectAsset | null, track: Track): boolean {
    if (!asset) return false;
    return track.kind === (asset.kind === "audio" ? "audio" : "video");
  }

  function onAssetDragOver(e: React.DragEvent, track: Track): void {
    if (!acceptsAsset(draggedAsset(), track)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  }

  function onAssetDropOnTrack(e: React.DragEvent, track: Track): void {
    const asset = draggedAsset();
    if (!asset || !acceptsAsset(asset, track)) return;
    e.preventDefault();
    onAssetDrop(asset, track.id, Math.max(0, pxToFrame(contentX(e) - gutter, pxPerFrame)));
  }

  function onClipClick(e: React.MouseEvent, track: Track, clip: Clip): void {
    e.stopPropagation();
    setSelection(clip.id);
    if (track.kind === "speech") {
      setTimelineSel({ media: clip.media.source, srcStartF: clip.srcInF, srcEndF: clip.srcOutF });
    } else {
      setTimelineSel(null);
    }
  }

  function onItemClick(e: React.MouseEvent, item: TextItem): void {
    e.stopPropagation();
    setSelection(item.id);
    setTimelineSel(null);
  }

  function onGraphicClick(e: React.MouseEvent, item: GraphicItem): void {
    e.stopPropagation();
    setSelection(item.id);
    setTimelineSel(null);
  }

  function onGraphicKeyDown(e: React.KeyboardEvent, item: GraphicItem): void {
    if (e.key !== "Enter" && e.key !== " ") return;
    e.preventDefault();
    setSelection(item.id);
    setTimelineSel(null);
  }

  const fps = project.fps;
  const speechMedia = project.tracks.find((t) => t.kind === "speech")?.clips?.[0]?.media.source ?? "";

  // Click a pin → flip its element's anchor word↔time and persist as one labeled step. Flipping to word
  // sets offsetF so the clip does NOT jump (nearest-word at its current start); it changes only future
  // ripple behavior. stopPropagation keeps the flip from selecting/dragging the underlying clip.
  function flipPin(e: React.MouseEvent, el: { id: string; tlStartF: number; anchor?: Anchor }): void {
    e.stopPropagation();
    const next = flipAnchor(el.anchor, project!, words, fps, el.tlStartF, speechMedia);
    applyLocal((st) => ({ project: setElementAnchor(st.project!, el.id, next) }), {
      origin: "human",
      label: next.mode === "word" ? "Pin to word" : "Pin to time",
    });
    setWarnings([]);
    void persistCurrent(project!.name);
  }

  // The pin badge for a non-speech element: amber when word-pinned, steel otherwise. Its own
  // pointer-down stops propagation so grabbing the pin never starts a clip drag.
  function renderPin(el: { id: string; tlStartF: number; anchor?: Anchor }) {
    const isWord = el.anchor?.mode === "word";
    const wIdx = isWord && el.anchor?.ref ? el.anchor.ref.wordIdx : -1;
    const title = isWord
      ? `Pinned to “${words[wIdx]?.w ?? "word"}” — click to time-pin`
      : "Time-pinned — click to word-pin";
    return (
      <button
        type="button"
        className={`pin ${isWord ? "word" : "time"}`}
        title={title}
        onPointerDown={(e) => e.stopPropagation()}
        onClick={(e) => flipPin(e, el)}
      />
    );
  }

  // Hover a word-pinned element → tether-highlight its transcript word; leaving clears it.
  function onAnchorEnter(el: { anchor?: Anchor }): void {
    if (el.anchor?.mode === "word" && el.anchor.ref) {
      setTether({ media: el.anchor.ref.media, wordIdx: el.anchor.ref.wordIdx });
    }
  }
  function onAnchorLeave(): void {
    setTether(null);
  }

  function selectClip(track: Track, clip: Clip): void {
    setSelection(clip.id);
    if (track.kind === "speech") {
      setTimelineSel({ media: clip.media.source, srcStartF: clip.srcInF, srcEndF: clip.srcOutF });
    } else {
      setTimelineSel(null);
    }
  }

  // Clip body pointer-down. Speech clips are transcript-driven → select only, no move drag; their
  // edges still trim. Non-speech clip bodies start a move drag.
  function onClipPointerDown(e: React.PointerEvent, track: Track, clip: Clip): void {
    if (e.button !== 0) return;
    selectClip(track, clip);
    if (track.kind === "speech") return;
    e.stopPropagation();
    beginDrag({
      mode: "move", clipId: clip.id, label: moveLabel(track.kind, clip),
      base: project!, projectName: project!.name, startX: e.clientX,
      sourceTrackId: track.id,
      origTlStartF: clip.tlStartF, origSrcInF: clip.srcInF, origSrcOutF: clip.srcOutF,
      pxPerFrame, words, fps, started: false, changed: false,
    });
  }

  // Clip edge grip pointer-down → trim that edge (all clip kinds, speech included).
  function onEdgePointerDown(e: React.PointerEvent, track: Track, clip: Clip, edge: "in" | "out"): void {
    if (e.button !== 0) return;
    e.stopPropagation();
    selectClip(track, clip);
    beginDrag({
      mode: edge, clipId: clip.id, label: "Trim clip",
      base: project!, projectName: project!.name, startX: e.clientX,
      sourceTrackId: track.id,
      origTlStartF: clip.tlStartF, origSrcInF: clip.srcInF, origSrcOutF: clip.srcOutF,
      pxPerFrame, words, fps, started: false, changed: false,
    });
  }

  // Timeline visual item pointer-down → move drag. Preview direct manipulation remains separate;
  // this path is the single history/persist route for text and graphics timeline movement.
  function onItemPointerDown(e: React.PointerEvent, track: Track, item: TextItem | GraphicItem): void {
    if (e.button !== 0) return;
    e.stopPropagation();
    setSelection(item.id);
    setTimelineSel(null);
    beginDrag({
      mode: "move", clipId: item.id, label: isGraphicTimelineItem(item) ? "Move graphic" : "Move text",
      base: project!, projectName: project!.name, startX: e.clientX,
      sourceTrackId: track.id,
      origTlStartF: item.tlStartF, origSrcInF: 0, origSrcOutF: 0,
      pxPerFrame, words, fps, started: false, changed: false,
    });
  }

  // Split the selected clip at the playhead: for speech, map the playhead back to a source frame via
  // the covering clip; for non-speech, the source frame is the playhead offset into the clip.
  function doSplit(track: Track, clip: Clip): void {
    let atSrcF: number;
    if (track.kind === "speech") {
      const s = timelineToSrc(project!, playheadF);
      if (!s || s.media !== clip.media.source) return;
      atSrcF = s.srcF;
    } else {
      atSrcF = playheadF - clip.tlStartF + clip.srcInF;
    }
    if (atSrcF <= clip.srcInF || atSrcF >= clip.srcOutF) return;
    applyLocal((st) => ({ project: splitClipAt(st.project!, clip.id, atSrcF) }), {
      origin: "human", label: "Split",
    });
    setWarnings([]);
    void persistCurrent(project!.name);
  }

  // Toolbar eligibility and keyboard eligibility must stay identical: a split is only valid when the
  // playhead is strictly inside the selected clip's source span.
  function selectedSplitTarget(): { track: Track; clip: Clip } | null {
    if (!selection) return null;
    const found = findElement(project!, selection);
    if (!found?.clip) return null;
    const { track, clip } = found;
    let atSrcF: number;
    if (track.kind === "speech") {
      const mapped = timelineToSrc(project!, playheadF);
      if (!mapped || mapped.media !== clip.media.source) return null;
      atSrcF = mapped.srcF;
    } else {
      atSrcF = playheadF - clip.tlStartF + clip.srcInF;
    }
    if (atSrcF <= clip.srcInF || atSrcF >= clip.srcOutF) return null;
    return { track, clip };
  }

  function splitSelected(): void {
    const target = selectedSplitTarget();
    if (target) doSplit(target.track, target.clip);
  }

  function addText(): void {
    const result = addTextItem(project!, playheadF);
    applyLocal(() => ({ project: result.project, selection: result.itemId }), {
      origin: "human", label: "Add title",
    });
    setWarnings([]);
    void persistCurrent(project!.name);
  }

  function selectedDeleteTarget(): { kind: "speech"; clip: Clip } | { kind: "direct"; id: string } | null {
    if (!selection) return null;
    const found = findElement(project!, selection);
    if (!found) return null;
    if (found.clip && found.track.kind === "speech") return { kind: "speech", clip: found.clip };
    return { kind: "direct", id: selection };
  }

  async function deleteSelected(): Promise<void> {
    const target = selectedDeleteTarget();
    if (!target) return;
    if (target.kind === "speech") {
      const cut = {
        media: target.clip.media.source,
        srcStartF: target.clip.srcInF,
        srcEndF: target.clip.srcOutF,
        kind: "selection" as const,
        durSec: (target.clip.srcOutF - target.clip.srcInF) / fps,
      };
      const didCut = await acceptSourceCuts(project!.name, [cut], fps, (warnings) => {
        if (warnings.length) toast(`${warnings.length} anchored item${warnings.length === 1 ? "" : "s"} needs review`);
      });
      if (didCut) setSelection(null);
      return;
    }
    applyLocal((st) => ({ project: deleteElement(st.project!, target.id) }), {
      origin: "human", label: "Delete clip",
    });
    setWarnings([]);
    setSelection(null);
    void persistCurrent(project!.name);
  }

  // Keyboard edits (scoped to the focused timeline; guarded against input/contenteditable): `s` splits
  // the selected clip at the playhead, arrows nudge a non-speech clip (Shift = 10 frames), and
  // Del/Backspace removes the selection. Speech removal deliberately goes through the shared
  // transcript-safe ripple-cut path, never a raw clip deletion.
  function onKeyDown(e: React.KeyboardEvent<HTMLDivElement>): void {
    const el = e.target as HTMLElement;
    if (el.tagName === "INPUT" || el.isContentEditable) return;
    if (!selection) return;
    const found = findElement(project!, selection);
    if (!found) return;
    const { track, clip } = found;
    if (e.key === "s" || e.key === "S") {
      if (clip && selectedSplitTarget()) {
        e.preventDefault();
        splitSelected();
      }
    } else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      const movableId = clip && track.kind !== "speech" ? clip.id : found.item?.id;
      if (movableId) {
        e.preventDefault();
        const delta = (e.key === "ArrowLeft" ? -1 : 1) * (e.shiftKey ? 10 : 1);
        const next = nudgeClip(project!, movableId, delta);
        if (next === project) return; // blocked by a neighbor edge: no-op, no undo/save
        applyLocal(() => ({ project: next }), { origin: "human", label: found.item ? "Nudge graphic" : "Nudge clip" });
        setWarnings([]);
        void persistCurrent(project!.name);
      }
    } else if (e.key === "Delete" || e.key === "Backspace") {
      if (selectedDeleteTarget()) {
        e.preventDefault();
        void deleteSelected();
      }
    }
  }

  function zoom(factor: number): void {
    setPersistedZoom(clampZoom(pxPerFrame * factor));
  }

  // Match the header Undo behavior: move exactly one history checkpoint, then persist that new project
  // state as a forward revision so other editor sessions receive the reverted document through SSE.
  function onUndo(): void {
    if (!canUndo) return;
    undo();
    void persistCurrent(project!.name);
  }

  function fit(): void {
    const scroll = scrollRef.current;
    // Use the live client width at the click, not ResizeObserver state which can lag a panel resize.
    // Fit is allowed beyond the manual zoom rails because its promise is exact edge-to-edge geometry.
    const viewportPx = scroll?.clientWidth ?? containerWidth;
    setPersistedZoom(fitPxPerFrame(viewportPx, durationF, gutter, DEFAULT_PX_PER_FRAME));
    if (scroll) scroll.scrollLeft = 0;
  }

  const splitTarget = selectedSplitTarget();
  const deleteTarget = selectedDeleteTarget();
  const splitTitle = splitTarget
    ? "Split selected clip at playhead (S)"
    : "Select a clip and place the playhead inside it to split";
  const deleteTitle = deleteTarget
    ? deleteTarget.kind === "speech"
      ? "Remove selected primary footage and close the gap (Delete)"
      : "Delete selected item (Delete)"
    : "Select an item to delete";

  // A track's clips or items. The v3 lanes name them (a library name, a graphic's name) and leave out
  // anchor pins and the speech waveform.
  function trackElements(track: Track) {
    return track.kind === "text"
      ? (track.items ?? []).map((item) => {
          if (isGraphicTimelineItem(item)) {
            return <div
              key={item.id}
              className={`tl-clip tl-graphic tl-draggable ${anchorClass(item.anchor)}${selection === item.id ? " sel" : ""}`}
              style={{ left: gutter + frameToPx(item.tlStartF, pxPerFrame), width: Math.max(2, frameToPx(item.durF, pxPerFrame)) }}
              onPointerDown={(e) => onItemPointerDown(e, track, item)}
              onPointerEnter={() => onAnchorEnter(item)}
              onPointerLeave={onAnchorLeave}
              onClick={(e) => onGraphicClick(e, item)}
              onKeyDown={(e) => onGraphicKeyDown(e, item)}
              tabIndex={0}
              role="button"
              aria-label={`${lanes ? lanes.label(item) : item.componentId} graphic, ${item.anchor?.mode === "word" ? "word pinned" : "time pinned"}`}
              title={lanes ? lanes.label(item) : `${item.componentId} graphic`}
            >
              {!lanes && renderPin(item)}
              {warnedIds.has(item.id) && <span className="warnbadge" title="Anchor warning — see toast">⚠</span>}
              <span className="tl-label">{lanes ? lanes.label(item) : item.componentId}</span>
            </div>;
          }
          const left = gutter + frameToPx(item.tlStartF, pxPerFrame);
          const width = Math.max(2, frameToPx(item.durF, pxPerFrame));
          return (
            <div
              key={item.id}
              className={`tl-clip tl-text ${anchorClass(item.anchor)} tl-draggable${selection === item.id ? " sel" : ""}`}
              style={{ left, width }}
              onPointerDown={(e) => onItemPointerDown(e, track, item)}
              onPointerEnter={() => onAnchorEnter(item)}
              onPointerLeave={onAnchorLeave}
              onClick={(e) => onItemClick(e, item)}
              title={item.text}
            >
              {!lanes && renderPin(item)}
              {warnedIds.has(item.id) && (
                <span className="warnbadge" title="Anchor warning — see toast">
                  ⚠
                </span>
              )}
              <span className="tl-label">{item.text}</span>
            </div>
          );
        })
      : (track.clips ?? []).map((clip) => {
          const dur = clip.srcOutF - clip.srcInF;
          const left = gutter + frameToPx(clip.tlStartF, pxPerFrame);
          const width = Math.max(2, frameToPx(dur, pxPerFrame));
          const isSpeech = track.kind === "speech";
          return (
            <div
              key={clip.id}
              className={`tl-clip tl-${track.kind}${track.kind === "video" ? ` ${anchorClass(clip.anchor)}` : ""}${track.kind === "audio" ? ` tl-${audioKind(clip)}` : ""}${isSpeech ? "" : " tl-draggable"}${selection === clip.id ? " sel" : ""}`}
              style={{ left, width }}
              onPointerDown={(e) => onClipPointerDown(e, track, clip)}
              onPointerEnter={isSpeech ? undefined : () => onAnchorEnter(clip)}
              onPointerLeave={isSpeech ? undefined : onAnchorLeave}
              onClick={(e) => onClipClick(e, track, clip)}
              title={lanes ? lanes.label(clip) : clip.media.source}
            >
              <span
                className="tl-edge tl-edge-l"
                onPointerDown={(e) => onEdgePointerDown(e, track, clip, "in")}
              />
              <span
                className="tl-edge tl-edge-r"
                onPointerDown={(e) => onEdgePointerDown(e, track, clip, "out")}
              />
              {!isSpeech && !lanes && renderPin(clip)}
              {warnedIds.has(clip.id) && (
                <span className="warnbadge" title="Anchor warning — see toast">
                  ⚠
                </span>
              )}
              {isSpeech && !lanes && (
                <ClipWaveform
                  slug={mediaSlug(clip.media.source)}
                  srcInF={clip.srcInF}
                  srcOutF={clip.srcOutF}
                  fps={fps}
                  widthPx={width}
                />
              )}
              <span className="tl-label">{lanes ? lanes.label(clip) : clip.id}</span>
              {(clip.seams ?? []).map((seam, i) => (
                <span
                  key={i}
                  className="tl-seam"
                  style={{ left: frameToPx(seam.atF, pxPerFrame) }}
                />
              ))}
            </div>
          );
        });
  }

  function laneBlocks(blocks: LaneBlock[] | undefined) {
    return (blocks ?? []).map((b) => (
      <button
        key={b.id}
        type="button"
        className={`tl-clip tl-over tl-${b.kind}`}
        title={b.label}
        style={{ left: gutter + frameToPx(b.startF, pxPerFrame), width: Math.max(2, frameToPx(b.endF - b.startF, pxPerFrame)) }}
        onClick={(e) => {
          e.stopPropagation();
          setPlayhead(b.startF);
        }}
      >
        <span className="tl-label">{b.label}</span>
      </button>
    ));
  }

  return (
    <div className={className ? `timeline ${className}` : "timeline"} tabIndex={0} onKeyDown={onKeyDown}>
      {!lanes && <div className="tl-toolbar">
        <button
          className="tl-command"
          type="button"
          title="Undo last edit"
          disabled={!canUndo}
          onClick={onUndo}
        >
          <span aria-hidden="true">↶</span> Undo
        </button>
        <button
          className="tl-command"
          type="button"
          title={splitTitle}
          aria-label="Split selected clip at playhead"
          disabled={!splitTarget}
          onClick={splitSelected}
        >
          <span aria-hidden="true">✂</span> Split
        </button>
        <button
          className="tl-command danger"
          type="button"
          title={deleteTitle}
          aria-label="Delete selected item"
          disabled={!deleteTarget}
          onClick={() => void deleteSelected()}
        >
          <span aria-hidden="true">🗑️</span> Delete
        </button>
        <button className="tl-command" type="button" title="Add a title at the playhead" onClick={addText}>
          <span aria-hidden="true">T</span> Add text
        </button>
        <span className="tl-spacer" />
        <button className="iconbtn" title="Zoom out" aria-label="Zoom out timeline" onClick={() => zoom(1 / ZOOM_STEP)}>
          −
        </button>
        <button className="iconbtn" title="Zoom in" aria-label="Zoom in timeline" onClick={() => zoom(ZOOM_STEP)}>
          +
        </button>
        <button className="iconbtn tl-fit" title="Fit timeline" aria-label="Fit timeline" onClick={fit}>
          ⤢
        </button>
      </div>}

      <div className="tl-scroll" ref={scrollRef}>
        <div className="tl-body" ref={bodyRef} style={{ width: contentWidth }}>
          <div className="tl-ruler" style={{ height: RULER_H }} onClick={seekTo}>
            <div className="tl-corner" style={{ width: gutter }} />
            {ticks.map((t) => (
              <span
                key={t.frame}
                className="tl-tick"
                style={{ left: gutter + frameToPx(t.frame, pxPerFrame) }}
              >
                {t.label}
              </span>
            ))}
          </div>

          {sceneBands && sceneBands.length > 0 && (
            <div className="tl-bands" style={{ height: lanes ? LANE_H : BAND_H }} onClick={seekTo}>
              <div className="tl-gutter" style={{ width: gutter }}>
                <span className="tl-kind">{lanes ? "Scenes" : "scenes"}</span>
              </div>
              {sceneBands.map((band) => (
                <button
                  key={band.id}
                  type="button"
                  className="tl-band"
                  data-band={band.id}
                  title={band.label}
                  style={{
                    left: gutter + frameToPx(band.startF, pxPerFrame),
                    width: Math.max(2, frameToPx(band.endF - band.startF, pxPerFrame)),
                    ["--c" as string]: band.color,
                  }}
                  onClick={(e) => {
                    e.stopPropagation();
                    if (onSceneBand) onSceneBand(band);
                    else setPlayhead(band.startF);
                  }}
                >
                  {band.label}
                </button>
              ))}
            </div>
          )}

          {lanes
            ? laneRows(rows).map((row) => (
                <div
                  className={`tl-row tl-lane${row.first ? "" : " tl-cont"}`}
                  key={row.track?.id ?? row.lane}
                  data-lane={row.lane}
                  data-track-id={row.track?.id}
                  style={{ height: rowH }}
                  onClick={onEmptyClick}
                  onDragOver={row.track ? (e) => onAssetDragOver(e, row.track!) : undefined}
                  onDrop={row.track ? (e) => onAssetDropOnTrack(e, row.track!) : undefined}
                >
                  <div className="tl-gutter" style={{ width: gutter }}>
                    {row.first && <span className="tl-kind">{row.name}</span>}
                  </div>
                  {row.track && trackElements(row.track)}
                  {row.first && laneBlocks(lanes.over[row.lane])}
                </div>
              ))
            : rows.map((track) => (
                <div
                  className="tl-row"
                  key={track.id}
                  data-track-id={track.id}
                  style={{ height: ROW_H }}
                  onClick={onEmptyClick}
                  onDragOver={(e) => onAssetDragOver(e, track)}
                  onDrop={(e) => onAssetDropOnTrack(e, track)}
                >
                  <div className="tl-gutter" style={{ width: gutter }}>
                    <span className="tl-kind">{laneLabel(track)}</span>
                    <span className="tl-tid">{track.id}{track.kind !== "audio" ? ` · layer ${visualLayers.indexOf(track) + 1}` : ""}</span>
                  </div>
                  {trackElements(track)}
                </div>
              ))}

          {lanes && (
            <div
              className={`tl-row tl-lane tl-captions${lanes.captionsOn ? "" : " off"}`}
              data-lane="captions"
              style={{ height: rowH }}
              onClick={onEmptyClick}
              title={lanes.captionsOn ? undefined : "Captions are off. Turn them on with CC."}
            >
              <div className="tl-gutter" style={{ width: gutter }}>
                <span className="tl-kind">Captions</span>
              </div>
              {laneBlocks(lanes.captions)}
            </div>
          )}

          <div
            className="tl-playhead"
            style={{ left: gutter + frameToPx(playheadF, pxPerFrame) }}
          />
        </div>
      </div>
    </div>
  );
}
