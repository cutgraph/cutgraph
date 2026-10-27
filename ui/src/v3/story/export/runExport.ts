// Export orchestration: for one chosen format, (re)build its render doc, submit
// render_final, and approve it immediately — the Export click IS the approval (no separate human
// gate for a render the user just asked for by clicking Export).
import { api } from "../../../api";
import { saveWithBase } from "../../../persist";
import { applyLocal, useProjectStore } from "../../../store/useProjectStore";
import type { Project } from "../../../types/project";
import { planRenderFor, workingCaptionsPatch, type RenderPlan } from "./derivedDoc";
import type { ExportFormat } from "./formats";
import { subscribeJobEvents } from "./jobEvents";
import { applyJobEvent, initRow, isTerminal, reusedRow, type ExportRow } from "./progress";

// An unexported format has no render document yet. Its GET returns 404; the PUT creates it
// with revision 0 as the base.
async function renderDocRevision(project: string): Promise<number> {
  try {
    return (await api.getProject(project)).revision;
  } catch (e) {
    if (e instanceof Error && e.message.startsWith("404")) return 0;
    throw e;
  }
}

async function ensureRenderDoc(video: string, working: Project, plan: RenderPlan, captionsOn: boolean): Promise<void> {
  if (plan.derived) {
    const revision = await renderDocRevision(plan.project);
    const saved = await api.saveProject(plan.project, plan.derived, revision);
    if (!saved.ok) throw new Error("The render document changed while exporting — try again.");
    return;
  }
  const patch = workingCaptionsPatch(working, captionsOn);
  if (!patch) return;
  const store = useProjectStore.getState();
  if (!store.project || store.project.name !== video) return;
  const next: Project = { ...store.project, captions: patch };
  applyLocal(() => ({ project: next }), { origin: "human", label: "Set captions" });
  const ok = await saveWithBase(video, next, store.revision, () => useProjectStore.getState().project?.name === video);
  if (!ok) throw new Error("Could not save the captions setting.");
}

// Owns one row's whole lifecycle: emits it queued, resolves the job id, subscribes to its SSE
// progress (auto-unsubscribing at a terminal status), and approves it. `onRow` is called with every
// intermediate state so the caller (a React state setter) never has to reconstruct it.
//
// render_final is deduplicated server-side by render doc content (jobs.new_or_existing_render_final):
// an export that matches one already submitted reuses that job instead of creating a fresh one, so
// its status on arrival can be anything — still "needs-approval" (a previous click never approved
// it), in flight, or already terminal — not only the "needs-approval" a brand-new job always starts
// in. Fetching its current presentation before deciding whether to approve (and seeding the row from
// it) covers a job that finished before this call ever subscribed to its SSE events.
async function driveRow(
  format: ExportFormat,
  project: string,
  jobIdPromise: Promise<string>,
  onRow: (row: ExportRow) => void,
  registerCleanup: (fn: () => void) => void,
  isCancelled: (jobId: string) => boolean,
): Promise<void> {
  let row = initRow(format, project);
  let unsubscribe = (): void => {};
  onRow(row);
  try {
    const jobId = await jobIdPromise;
    row = { ...row, jobId, status: "queued" };
    onRow(row);

    unsubscribe = subscribeJobEvents(project, (job) => {
      if (job.id !== jobId) return;
      if (isCancelled(jobId)) {
        unsubscribe();
        return;
      }
      row = applyJobEvent(row, job);
      onRow(row);
      if (isTerminal(row.status)) unsubscribe();
    });
    registerCleanup(unsubscribe);

    const current = await api.getComputeJob(jobId);
    if (isCancelled(jobId)) {
      unsubscribe();
      return;
    }
    // A live SSE event may have already resolved this row while the seed fetch was in flight —
    // never let a stale snapshot regress a terminal row.
    if (!isTerminal(row.status)) {
      row = applyJobEvent(row, current);
      // A new job waits for the approval below, so one already done is a reused, unchanged export.
      if (row.status === "done") row = reusedRow(row);
      onRow(row);
    }
    if (isTerminal(row.status)) {
      unsubscribe();
      return;
    }

    if (current.status === "needs-approval") await api.approveJob(jobId);
  } catch (e) {
    // Approval can reject after the sheet has confirmed cancellation without an SSE event.
    if (row.status === "cancelled" || (row.jobId && isCancelled(row.jobId))) {
      unsubscribe();
      return;
    }
    onRow({ ...row, status: "error", error: e instanceof Error ? e.message : String(e) });
  }
}

export interface StartExportOptions {
  video: string;
  working: Project;
  format: ExportFormat;
  captionsOn: boolean;
  onRow: (row: ExportRow) => void;
  isCancelled: (jobId: string) => boolean;
  registerCleanup: (fn: () => void) => void;
}

export function startFormatExport(opts: StartExportOptions): void {
  const { video, working, format, captionsOn, onRow, registerCleanup, isCancelled } = opts;
  const plan = planRenderFor(working, format, captionsOn);
  onRow(initRow(format, plan.project));

  void (async () => {
    try {
      await ensureRenderDoc(video, working, plan, captionsOn);
    } catch (e) {
      onRow({ ...initRow(format, plan.project), status: "error", error: e instanceof Error ? e.message : String(e) });
      return;
    }
    const jobIdPromise = api.createJob("render_final", plan.project).then((created) => {
      const jobId = created.jobId ?? created.id;
      if (!jobId) throw new Error("The render could not be queued.");
      return jobId;
    });
    await driveRow(format, plan.project, jobIdPromise, onRow, registerCleanup, isCancelled);
  })();
}

export interface RetryExportOptions {
  project: string;
  format: ExportFormat;
  previousJobId: string;
  onRow: (row: ExportRow) => void;
  isCancelled: (jobId: string) => boolean;
  registerCleanup: (fn: () => void) => void;
}

export function retryFormatExport(opts: RetryExportOptions): void {
  const { project, format, previousJobId, onRow, registerCleanup, isCancelled } = opts;
  void driveRow(format, project, api.retryJob(previousJobId).then((job) => job.id), onRow, registerCleanup, isCancelled);
}
