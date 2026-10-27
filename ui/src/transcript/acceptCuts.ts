// Batch ripple-cut accept — the ONE labeled undo checkpoint / ONE revision / ONE SSE for a set of
// source-space PendingCuts, so both the `x`-selection cut,
// the "Accept N" pause batch, and the "Remove all fillers" batch share exactly one accept path — and
// so the "one undo step" invariant is unit-testable against the real stores without a DOM.

import { api, type CutRange, type RestoreRange, type RippleCutResult } from "../api";
import { useProjectStore, applyLocal, setWarnings, type PendingCut } from "../store/useProjectStore";
import { saveWithBase } from "../persist";
import { srcRangeToTimeline, batchCutLabel } from "./cutmap";
import { toast } from "../toast";
import type { Project } from "../types/project";

// Convert source-space pending cuts to timeline ranges against `proj` (accept-time). A cut whose
// source range is no longer on the timeline (fully cut meanwhile) is dropped and counted; one that now
// spans a fresh gap splits into several ranges — apply_cuts merges adjacent/overlapping.
export function planCuts(
  proj: Project,
  cuts: PendingCut[],
): { ranges: CutRange[]; appliedCuts: PendingCut[]; dropped: number; partial: number } {
  const ranges: CutRange[] = [];
  const appliedCuts: PendingCut[] = [];
  let dropped = 0;
  let partial = 0;
  for (const c of cuts) {
    const rs = srcRangeToTimeline(proj, c.media, c.srcStartF, c.srcEndF);
    if (rs.length === 0) {
      dropped += 1;
      continue;
    }
    const covered = rs.reduce((total, range) => total + (range.endF - range.startF), 0);
    if (covered < c.srcEndF - c.srcStartF) partial += 1;
    appliedCuts.push(c);
    ranges.push(...rs);
  }
  return { ranges, appliedCuts, dropped, partial };
}

// Accept a batch of source-space cuts as ONE labeled undo checkpoint / ONE revision / ONE SSE.
export async function acceptSourceCuts(
  projectName: string | null,
  cuts: PendingCut[],
  fps: number,
  onWarnings?: (warnings: RippleCutResult["warnings"]) => void,
  onBusyChange?: (busy: boolean) => void,
): Promise<boolean> {
  void fps; // retained for signature symmetry with the timeline helpers; the label reads cuts' durSec
  if (!projectName || cuts.length === 0) return false;
  return acceptRippleVerb(projectName, (proj) => {
    const { ranges, appliedCuts, dropped, partial } = planCuts(proj, cuts);
    if (ranges.length === 0) {
      toast("Nothing to cut — already removed");
      return null; // the whole batch is stale; treat it as consumed so the caller can clear it
    }
    const notes: string[] = [];
    if (dropped) notes.push(`${dropped} cut${dropped > 1 ? "s" : ""} already removed`);
    if (partial) notes.push(`${partial} cut${partial > 1 ? "s" : ""} partially overlapped already-removed source; only the remaining source was cut.`);
    // Label the checkpoint by the applied cuts. Surface stale/dropped cuts in the notes.
    return { run: () => api.rippleCut(projectName, proj, ranges), label: batchCutLabel(appliedCuts), notes, failure: "Cut preview failed" };
  }, onWarnings, onBusyChange);
}

// Put cut source ranges back on the timeline (the inverse verb, ripple_restore) through the same
// one-checkpoint / one-revision discipline, so word-anchored overlays follow the restored words.
export async function restoreSourceRanges(
  projectName: string | null,
  ranges: RestoreRange[],
  label: string,
  onWarnings?: (warnings: RippleCutResult["warnings"]) => void,
  onBusyChange?: (busy: boolean) => void,
): Promise<boolean> {
  if (!projectName || ranges.length === 0) return false;
  return acceptRippleVerb(projectName, (proj) => ({
    run: () => api.rippleRestore(projectName, proj, ranges), label, notes: [], failure: "Restore failed",
  }), onWarnings, onBusyChange);
}

interface VerbPlan {
  run: () => Promise<RippleCutResult>;
  label: string;
  notes: string[];
  failure: string;
}

// snapshot {project, revision} synchronously BEFORE the async work, compute the verb against
// that project, and persist with THAT captured revision. A conflict, network failure, or concurrent
// local change aborts without applying the computed result, preserving the pending review for
// explicit recovery. `plan` returning null means there is nothing left to do (counted as consumed).
let acceptInFlight = false;
async function acceptRippleVerb(
  projectName: string,
  plan: (proj: Project) => VerbPlan | null,
  onWarnings?: (warnings: RippleCutResult["warnings"]) => void,
  onBusyChange?: (busy: boolean) => void,
): Promise<boolean> {
  if (acceptInFlight) {
    toast("A cut is already being accepted; the pending review is unchanged.");
    return false;
  }
  acceptInFlight = true;
  onBusyChange?.(true);
  try {
    const snap = useProjectStore.getState();
    if (!snap.project) return false;
    const capturedProject = snap.project;
    const capturedRevision = snap.revision;
    const isCurrent = () => {
      const current = useProjectStore.getState();
      return current.project === capturedProject && current.revision === capturedRevision;
    };
    const verb = plan(capturedProject);
    if (!verb) return true;
    let res: RippleCutResult;
    try {
      res = await verb.run();
    } catch (error) {
      toast(`${verb.failure}; pending review kept. ${error instanceof Error ? error.message : String(error)}`);
      return false;
    }
    if (!isCurrent()) {
      toast("The video changed while preparing this cut; pending review kept. Reconcile the newer video before retrying.");
      return false;
    }
    const ok = await saveWithBase(projectName, res.project, capturedRevision, isCurrent);
    if (ok) {
      applyLocal(() => ({ project: res.project }), { origin: "human", label: verb.label }, { markDirty: false });
      setWarnings(res.warnings); // drives the timeline per-element badges until the next edit
      onWarnings?.(res.warnings);
      for (const note of verb.notes) toast(note);
      return true;
    }
    // Conflict/network failure: saveWithBase preserves local truth and pending review for retry.
    return false;
  } finally {
    acceptInFlight = false;
    onBusyChange?.(false);
  }
}
