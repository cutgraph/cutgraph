// Snapshot-coherent persistence. Every save keeps the project and its base revision together, so a
// concurrent write cannot silently replace another editor's changes.
import { api, type SaveResult } from "./api";
import { applySystem, markSynced, syncedBase, useProjectStore } from "./store/useProjectStore";
import { changes, fold, overlaps } from "./store/rebase";
import { toast } from "./toast";
import type { Project } from "./types/project";
import { useSaveStatus } from "./saveStatus";

// A conflict or network failure deliberately leaves the local document in place. The user can retry
// after a transient failure or explicitly reload the server version; neither outcome discards work.
export function saveWithBase(
  projectName: string,
  next: Project,
  baseRevision: number,
  isCurrent?: () => boolean,
): Promise<boolean> {
  return save(projectName, next, baseRevision, isCurrent, false);
}

async function save(
  projectName: string,
  next: Project,
  baseRevision: number,
  isCurrent: (() => boolean) | undefined,
  rebase: boolean,
): Promise<boolean> {
  if (isCurrent && !isCurrent()) return false;
  const token = useSaveStatus.getState().start();
  let result: SaveResult;
  try {
    result = await api.saveProject(projectName, next, baseRevision);
    if (!result.ok && rebase) {
      const rebased = await rebaseOnServer(projectName, baseRevision);
      if (rebased) result = await api.saveProject(projectName, rebased.project, rebased.revision);
    }
  } catch (error) {
    const message = String(error);
    useSaveStatus.getState().failed(token, message);
    toast(`Save failed. Your local edits are still available: ${message}`);
    return false;
  }
  if (result.ok) {
    if (isCurrent && !isCurrent()) {
      useSaveStatus.getState().failed(token, "The project changed while this save was in flight.");
      toast("Save completed for an older project state. Your newer local edits are still available; reload or reconcile before retrying.");
      return false;
    }
    useProjectStore.setState({ revision: result.revision });
    markSynced(projectName, result.project, result.revision);
    useSaveStatus.getState().saved(token);
    return true;
  }
  useSaveStatus.getState().conflict(token);
  toast("Save conflict. Your local edits are still available; reload only when ready to discard them.");
  return false;
}

// The server forces `revision` on every write, so it is never a change either side made.
const docChanges = (base: Project, next: Project) => changes({ ...base, revision: 0 }, { ...next, revision: 0 });

// The safety net for a background writer: the server moved on from the revision this tab last
// synced. When its changes and the open document's touch disjoint paths, fold its changes into the
// document (and its undo history) and return that to retry on the server's revision. An unknown base,
// any overlap or a live drag keeps the conflict: a server change is never overwritten.
async function rebaseOnServer(name: string, baseRevision: number): Promise<{ project: Project; revision: number } | null> {
  const base = syncedBase(name, baseRevision);
  if (!base) return null;
  const server = await api.getProject(name).catch(() => null);
  const { project: local, revision } = useProjectStore.getState();
  if (!server || server.revision <= baseRevision || local?.name !== name || revision !== baseRevision) return null;
  const theirs = docChanges(base, server.project);
  if (overlaps(theirs, docChanges(base, local))) return null;
  // Paths index into arrays, so a local reorder can hide a real overlap: what would be saved must still
  // hold every one of the server's changes.
  if (overlaps(theirs, docChanges(server.project, fold(local, theirs, base)))) return null;
  if (!applySystem((doc) => fold(doc, theirs, base), server.revision)) return null;
  markSynced(name, server.project, server.revision);
  return { project: useProjectStore.getState().project!, revision: server.revision };
}

// One persist is in flight at a time. Requests made while it runs coalesce to one save of the latest
// document, with the revision produced by the preceding successful save.
let persistInFlight = false;
let persistPending = false;
let persistName: string | null = null;

export async function persistCurrent(projectName: string): Promise<boolean> {
  persistName = projectName;
  if (persistInFlight) {
    persistPending = true;
    return false;
  }
  persistInFlight = true;
  try {
    let ok = await saveCurrentOnce();
    while (persistPending) {
      persistPending = false;
      ok = await saveCurrentOnce();
    }
    return ok;
  } finally {
    persistInFlight = false;
  }
}

function saveCurrentOnce(): Promise<boolean> {
  const { project, revision } = useProjectStore.getState();
  if (!project || persistName === null) return Promise.resolve(false);
  return save(persistName, project, revision, undefined, true);
}
