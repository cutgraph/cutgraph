import { create } from "zustand";
import { temporal } from "zundo";
import type { Project } from "../types/project";
import { toast } from "../toast";
import { useSaveStatus } from "../saveStatus";
import { deepEqual } from "./rebase";

// Frames are the single source of truth (mirrors project.json); seconds are a
// display-only projection (frame_to_sec below).

const HISTORY_LIMIT = 50;

// A pending ripple-cut proposal, anchored in SOURCE-frame space: it names the media and a
// source range, not a mutable timeline position, so it survives later ripples / remote SSE edits.
// It is converted to timeline frames ONLY at accept time (cutmap.srcRangeToTimeline). `kind`
// distinguishes an `x`-key selection from a detect_silence pause (drives the accept label).
export interface PendingCut {
  media: string;
  srcStartF: number;
  srcEndF: number;
  kind: "selection" | "pause" | "filler";
  durSec: number;
  conf?: number;
  /** Search-review metadata; view-only and intentionally excluded from project persistence. */
  matchCount?: number;
  matchIds?: string[];
  reviewWarnings?: string[];
}

// A timeline→transcript selection span, in SOURCE-frame space so it survives ripples/SSE.
// Set when a speech clip is selected on the timeline; both panels read it for the bidirectional
// highlight (the transcript lens lights the words whose source frame falls inside the span).
export interface TimelineSel {
  media: string;
  srcStartF: number;
  srcEndF: number;
}

// Hover tether: the transcript word a word-pinned timeline element is anchored to. Set on
// pointer-enter of a word-pinned clip/item, cleared on leave; the transcript lens highlights that word.
export interface Tether {
  media: string;
  wordIdx: number;
}

// A ripple-cut warning surfaced after a transcript cut (edits.py flat shape). `orphaned-anchor` /
// `time-pinned-crossing` / `time-pinned-drift` / `anchor-collision`; the Timeline badges every element
// whose id appears here and the transcript warning handler toasts every type.
export type RippleWarning = { type: string; itemId: string };

export interface ProjectState {
  project: Project | null;
  revision: number;
  playheadF: number;
  previewSec: number;
  selection: string | null;
  // Pending ripple-cut proposals (x-key strikethroughs + silence pauses): view state only, NEVER
  // tracked by undo and NEVER sent to the server until the user accepts them (source-space).
  pendingCuts: PendingCut[];
  // Timeline→transcript selection span (source-frame, view state only — excluded from partialize).
  timelineSel: TimelineSel | null;
  // Hover tether + last ripple-cut warnings: view state only (excluded from partialize). tether drives
  // the transcript phrase highlight; lastWarnings drives per-clip warning badges + toasts.
  tether: Tether | null;
  lastWarnings: RippleWarning[];
}

// Only these top-level keys carry undo history; revision/playheadF never do.
type TrackedState = Pick<ProjectState, "project" | "selection">;

export interface Meta {
  origin: "human" | "agent";
  label: string;
}

// The origin/label channel runs in lockstep with zundo's own history: pastMeta mirrors
// temporal.pastStates, futureMeta mirrors temporal.futureStates. Every recorded checkpoint
// pushes one entry (trimmed with the same HISTORY_LIMIT), and undo/redo MUST go through the
// wrappers below so a meta entry moves between the two stacks exactly as zundo moves the
// matching history entry. Calling temporal.undo()/redo() directly desyncs the channel.
let pastMeta: Meta[] = [];
let futureMeta: Meta[] = [];

// A new checkpoint: one entry onto pastMeta (trimmed like pastStates), redo future cleared.
function recordMeta(meta: Meta): void {
  pastMeta = [...pastMeta, meta].slice(-HISTORY_LIMIT);
  futureMeta = [];
}

export function metaState(): { past: readonly Meta[]; future: readonly Meta[] } {
  return { past: pastMeta, future: futureMeta };
}

// Undo/redo wrappers — the ONLY sanctioned way to step history. Each moves the matching
// meta entry in lockstep with zundo's pastStates/futureStates.
export function undo(): void {
  const t = useProjectStore.temporal.getState();
  if (t.pastStates.length === 0) return;
  const m = pastMeta[pastMeta.length - 1];
  t.undo();
  if (m !== undefined) {
    pastMeta = pastMeta.slice(0, -1);
    futureMeta = [...futureMeta, m];
  }
  useSaveStatus.getState().markDirty();
}

export function redo(): void {
  const t = useProjectStore.temporal.getState();
  if (t.futureStates.length === 0) return;
  const m = futureMeta[futureMeta.length - 1];
  t.redo();
  if (m !== undefined) {
    futureMeta = futureMeta.slice(0, -1);
    pastMeta = [...pastMeta, m];
  }
  useSaveStatus.getState().markDirty();
}

// Diff-shaped undo history: store only the changed top-level keys of the
// PAST state, or null when nothing tracked changed. Keeps entries small and preserves
// the RFC-6902 op-log upgrade path.
function diffTracked(
  past: Partial<TrackedState>,
  current: Partial<TrackedState>,
): Partial<TrackedState> | null {
  const changed: Partial<TrackedState> = {};
  let any = false;
  for (const key of ["project", "selection"] as const) {
    if (!deepEqual(past[key], current[key])) {
      // @ts-expect-error index write across the union is safe for these keys
      changed[key] = past[key];
      any = true;
    }
  }
  return any ? changed : null;
}

export const useProjectStore = create<ProjectState>()(
  temporal(
    (): ProjectState => ({
      project: null,
      revision: 0,
      playheadF: 0,
      previewSec: 0,
      selection: null,
      pendingCuts: [],
      timelineSel: null,
      tether: null,
      lastWarnings: [],
    }),
    {
      limit: HISTORY_LIMIT,
      partialize: (state): TrackedState => ({
        project: state.project,
        selection: state.selection,
      }),
      diff: diffTracked,
    },
  ),
);

function trackedSnapshot(): TrackedState {
  const s = useProjectStore.getState();
  return { project: s.project, selection: s.selection };
}

// Undo-tracking pause OWNERSHIP. zundo's `isTracking` is a plain boolean with no nesting, so a second
// pauser+resumer clobbers a pause it did not take — the exact bug where applyRemote's resume() un-paused
// a live drag mid-flight, letting every subsequent pointer-move record a stray checkpoint. We
// gate pause/resume on an owner token: acquiring is a no-op if someone else already owns the pause, and
// releasing is a no-op unless YOU own it. A drag owns its pause end to end; applyRemote/selection edits
// use their own owner and can never resume the drag's pause.
type PauseOwner = "drag" | "apply" | "select";
let pauseOwner: PauseOwner | null = null;

function pauseTracking(owner: PauseOwner): void {
  if (pauseOwner !== null) return; // someone else holds the pause — leave it to them
  pauseOwner = owner;
  useProjectStore.temporal.getState().pause();
}

function resumeTracking(owner: PauseOwner): void {
  if (pauseOwner !== owner) return; // never resume a pause we do not own
  pauseOwner = null;
  useProjectStore.temporal.getState().resume();
}

// The server's document at `revision` as this tab last saw it: the base a save conflict rebases from.
let synced: { name: string; revision: number; project: Project } | null = null;

export function markSynced(name: string, project: Project, revision: number): void {
  synced = { name, revision, project };
}

export function syncedBase(name: string, revision: number): Project | null {
  return synced && synced.name === name && synced.revision === revision ? synced.project : null;
}

// Local, human-authored edit: normal setState records one diff entry; recordMeta pushes
// the matching entry onto pastMeta in lockstep.
export function applyLocal(
  mutator: (state: ProjectState) => Partial<ProjectState>,
  meta: { origin: "human" | "agent"; label: string },
  options: { markDirty?: boolean } = {},
): void {
  useProjectStore.setState(mutator);
  recordMeta({ origin: meta.origin, label: meta.label });
  if (options.markDirty !== false) useSaveStatus.getState().markDirty();
}

// Adopt an SSE-delivered newer project. When nothing is loaded yet (the SSE effect can
// open before the initial GET resolves) there is no prior state to check-point against —
// adopt silently so Undo can't restore a null project. Otherwise record exactly ONE
// labeled checkpoint: pause recording, swap state, resume, push a past entry + clear redo.
export function applyRemote(project: Project, revision: number): void {
  const t = useProjectStore.temporal.getState();
  markSynced(project.name, project, revision);
  if (useProjectStore.getState().project === null) {
    pauseTracking("apply");
    useProjectStore.setState({ project, revision });
    resumeTracking("apply");
    useSaveStatus.getState().markSaved();
    return;
  }
  let prevPartial = trackedSnapshot();
  if (previewSnapshot !== null) {
    // A live drag preview loses to the remote write. Drop the throwaway draft, release the
    // drag's tracking pause, and checkpoint the PRE-drag state (previewSnapshot) — never the draft —
    // before adopting remote. The Timeline tears down its DOM drag when it sees the revision advance.
    prevPartial = previewSnapshot;
    previewSnapshot = null;
    resumeTracking("drag");
    toast("Timeline updated remotely — drag cancelled");
  }
  if (t.futureStates.length > 0) toast("Remote edit dropped your redo history");
  pauseTracking("apply");
  useProjectStore.setState({ project, revision });
  resumeTracking("apply");
  useProjectStore.temporal.setState((s) => ({
    pastStates: [...s.pastStates, prevPartial].slice(-HISTORY_LIMIT),
    futureStates: [],
  }));
  recordMeta({ origin: "agent", label: `Remote update r${revision}` });
  useSaveStatus.getState().markSaved();
}

// Adopt our own accepted write (initial load / save echo): paused apply, no undo entry.
export function adoptSaved(project: Project, revision: number): void {
  markSynced(project.name, project, revision);
  pauseTracking("apply");
  useProjectStore.setState({ project, revision });
  resumeTracking("apply");
  useSaveStatus.getState().markSaved();
}

// A system write into the open document: a background scrub proxy landing, or another writer's change
// folded in by a save rebase. It rewrites every undo/redo snapshot the same way and records no
// undo step, so undo never presents it as the user's action and never reverts it. Refused (false)
// with no document or during a live drag, whose draft is recomputed from its pre-drag base.
export function applySystem(mutate: (project: Project) => Project, revision?: number): boolean {
  const { project } = useProjectStore.getState();
  if (!project || previewSnapshot !== null) return false;
  const rewrite = <S extends Partial<TrackedState>>(s: S): S => (s.project ? { ...s, project: mutate(s.project) } : s);
  pauseTracking("apply");
  useProjectStore.setState({ project: mutate(project), ...(revision !== undefined && { revision }) });
  resumeTracking("apply");
  useProjectStore.temporal.setState((t) => ({ pastStates: t.pastStates.map(rewrite), futureStates: t.futureStates.map(rewrite) }));
  return true;
}

// Drag preview/commit scaffold: pause recording while a
// drag is live, then commit exactly one checkpoint. `previewSnapshot != null` IS the "drag owns the
// tracking pause" flag the tracking gate and applyRemote's drag-cancel key off.
let previewSnapshot: TrackedState | null = null;

export function beginPreview(): void {
  previewSnapshot = trackedSnapshot();
  pauseTracking("drag");
}

export function commit(label: string): void {
  resumeTracking("drag");
  if (!previewSnapshot) return;
  const prev = previewSnapshot;
  previewSnapshot = null;
  useProjectStore.temporal.setState((s) => ({
    pastStates: [...s.pastStates, prev].slice(-HISTORY_LIMIT),
    futureStates: [],
  }));
  recordMeta({ origin: "human", label });
  useSaveStatus.getState().markDirty();
}

// Abort a live drag preview (pointercancel / Escape). Drop the draft, restore the pre-drag tracked
// state WITHOUT recording a checkpoint (we are still under the drag's pause), and release that pause.
// No-op when no preview is active.
export function cancelPreview(): void {
  const snap = previewSnapshot;
  previewSnapshot = null;
  if (snap) {
    useProjectStore.setState({ project: snap.project, selection: snap.selection });
  }
  resumeTracking("drag");
}

// Selection is view state, not a document edit: change it without recording an undo
// checkpoint (paused apply, like adoptSaved), so selecting a clip never spends history
// or desyncs the meta channel. The current selection still rides along in the next real
// checkpoint via partialize.
export function setSelection(id: string | null): void {
  if (useProjectStore.getState().selection === id) return;
  pauseTracking("select");
  useProjectStore.setState({ selection: id });
  resumeTracking("select");
}

// Playhead is view state (timeline-clock frames), never a document edit: change it without an undo
// checkpoint. `playheadF`/`previewSec` are excluded from `partialize`, so a set that touches only
// them yields a null tracked diff and zundo records nothing — no pause needed, no meta desync.
// Living in the store (untracked) is what lets the playhead survive view/player remounts.
export function setPlayhead(frameF: number): void {
  if (useProjectStore.getState().playheadF === frameF) return;
  useProjectStore.setState({ playheadF: frameF });
}

// Current playback position (source seconds) published by the preview <video> to drive karaoke.
export function setPreviewSec(sec: number): void {
  useProjectStore.setState({ previewSec: sec });
}

// Pending-cut proposals are view state (excluded from partialize like playheadF), so replacing them
// records no undo checkpoint — they only touch the document once accepted through applyLocal.
export function setPendingCuts(cuts: PendingCut[]): void {
  useProjectStore.setState({ pendingCuts: cuts });
}

export function clearPendingCuts(): void {
  if (useProjectStore.getState().pendingCuts.length === 0) return;
  useProjectStore.setState({ pendingCuts: [] });
}

// Timeline→transcript selection is view state (excluded from partialize like playheadF), so setting
// it records no undo checkpoint and needs no pause — the tracked diff is null. Set from a speech-clip
// selection, cleared to null on a non-speech clip or an empty-timeline click.
export function setTimelineSel(sel: TimelineSel | null): void {
  useProjectStore.setState({ timelineSel: sel });
}

// Hover tether is view state (excluded from partialize like playheadF): setting it records no undo
// checkpoint and needs no pause. Set on pointer-enter of a word-pinned timeline element, null on leave.
export function setTether(t: Tether | null): void {
  useProjectStore.setState({ tether: t });
}

// Ripple-cut warnings are view state: replaced wholesale after every ripple (drives badges + toasts),
// cleared ([]) on the next successful non-ripple edit so stale drift warnings don't linger.
export function setWarnings(w: RippleWarning[]): void {
  useProjectStore.setState({ lastWarnings: w });
}

// Display-only projection; frames stay canonical.
export function frameToSec(frame: number, fps: number): number {
  return frame / fps;
}
