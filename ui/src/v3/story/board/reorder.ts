// Board reorder: the server re-sequences the speech clips (POST /api/verbs/reorder_scenes, stateless
// like ripple_cut), the result is saved through the revision-gated PUT, and only then applied as ONE
// labelled undo step. Ctrl+Z, or Undo on the toast, restores the previous document and saves it as a
// forward revision. Word-anchored cues follow their words; the video's length stays the speech's
// (projectDurationF), so a cue that now runs past the end is cut there, not a silent tail.

import { persistCurrent, saveWithBase } from "../../../persist";
import { applyLocal, setWarnings, undo, useProjectStore, type RippleWarning } from "../../../store/useProjectStore";
import type { Project } from "../../../types/project";
import { toast } from "../../Toast";

export interface ReorderResult {
  project: Project;
  warnings: RippleWarning[];
}

export function moveItem<T>(list: readonly T[], from: number, to: number): T[] {
  const next = [...list];
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}

async function postReorder(video: string, project: Project, order: string[], baseRevision: number): Promise<ReorderResult> {
  const res = await fetch(`/api/verbs/reorder_scenes/${encodeURIComponent(video)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project, order, baseRevision }),
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as { error?: unknown } | null;
    if (res.status === 409) throw new Error("the video was changed elsewhere; reload it first");
    throw new Error(typeof body?.error === "string" ? body.error : `${res.status} ${res.statusText}`);
  }
  return (await res.json()) as ReorderResult;
}

// The toast's Undo takes back this move only. Once anything else has changed the story, the next undo
// step is that change, so it's left to the top bar's Undo.
function undoMove(video: string, moved: Project): void {
  if (useProjectStore.getState().project !== moved) {
    toast("The story changed after the move. Use Undo in the top bar.");
    return;
  }
  undo();
  void persistCurrent(video);
}

let inFlight = false;

export async function reorderScenes(video: string, order: string[], label: string): Promise<boolean> {
  const { project, revision } = useProjectStore.getState();
  if (!project || inFlight) return false;
  inFlight = true;
  const isCurrent = () => {
    const s = useProjectStore.getState();
    return s.project === project && s.revision === revision;
  };
  try {
    let result: ReorderResult;
    try {
      result = await postReorder(video, project, order, revision);
    } catch (e) {
      toast(`Couldn't reorder the story: ${e instanceof Error ? e.message : String(e)}`);
      return false;
    }
    if (!isCurrent()) {
      toast("The video changed while reordering, so nothing moved. Try again.");
      return false;
    }
    if (!(await saveWithBase(video, result.project, revision, isCurrent))) return false;
    applyLocal(() => ({ project: result.project }), { origin: "human", label }, { markDirty: false });
    setWarnings(result.warnings);
    const n = result.warnings.length;
    toast(n ? `${label}. ${n} overlay${n === 1 ? " needs" : "s need"} a look.` : label, {
      label: "Undo",
      run: () => undoMove(video, result.project),
    });
    return true;
  } finally {
    inFlight = false;
  }
}
