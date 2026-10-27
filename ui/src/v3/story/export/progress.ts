// Per-format export progress rows, driven by SSE `job` events. Pure state — no DOM/SSE
// here — so the reducer is unit-testable on its own.
import type { ExportFormat } from "./formats";

export type RowStatus = "queued" | "running" | "done" | "error" | "cancelled";

/** One animated scene's part in an export: served from the scene cache, or rendered for it. */
export type SceneState = "cached" | "waiting" | "rendering" | "rendered";

export interface SceneProgress {
  id: string;
  state: SceneState;
}

export interface ExportRow {
  format: ExportFormat;
  /** The project this format renders from (the working doc, or its derived render doc). */
  project: string;
  status: RowStatus;
  progress: number;
  stage: string;
  jobId: string | null;
  output: string | null;
  error: string | null;
  /** The job's animated scenes in story order, once it has looked them up in the cache. */
  scenes: SceneProgress[];
}

export function initRow(format: ExportFormat, project: string): ExportRow {
  return { format, project, status: "queued", progress: 0, stage: "Queued", jobId: null, output: null, error: null, scenes: [] };
}

const DONE_STATUSES = new Set(["done", "succeeded"]);
const RUNNING_STATUSES = new Set(["running", "publishing"]);
const FAILED_STATUSES = new Set([
  "error", "failed", "stale", "interrupted", "unknown_execution",
]);

export interface JobEventLike {
  id: string;
  status: string;
  progress?: number;
  progressStage?: string;
  output?: string;
  error?: string;
  scenes?: SceneProgress[];
}

/** Fold one `job` SSE event into a row — a no-op unless the event names this row's own job. */
export function applyJobEvent(row: ExportRow, job: JobEventLike): ExportRow {
  if (row.status === "cancelled") return row;
  if (row.jobId !== null && row.jobId !== job.id) return row;
  const status: RowStatus = job.status === "cancelled"
    ? "cancelled"
    : DONE_STATUSES.has(job.status)
      ? "done"
      : FAILED_STATUSES.has(job.status)
        ? "error"
        : RUNNING_STATUSES.has(job.status)
          ? "running"
          : "queued";
  return {
    ...row,
    jobId: job.id,
    status,
    progress: typeof job.progress === "number" ? job.progress : row.progress,
    stage: job.progressStage ?? row.stage,
    output: job.output ?? row.output,
    error: status === "error" ? job.error ?? "The render failed." : null,
    scenes: job.scenes ?? row.scenes,
  };
}

/** A row whose job was already finished when this export reached it: the server reused an unchanged
 * export, so this click rendered nothing and every scene came from the cache. */
export function reusedRow(row: ExportRow): ExportRow {
  return { ...row, scenes: row.scenes.map((scene) => ({ ...scene, state: "cached" })) };
}

export function isTerminal(status: RowStatus): boolean {
  return status === "done" || status === "error" || status === "cancelled";
}

const STATUS_LABEL: Record<RowStatus, string> = {
  queued: "Queued",
  running: "Rendering…",
  done: "Rendered",
  error: "Failed",
  cancelled: "Cancelled",
};

const SCENE_LABEL: Record<SceneState, string> = {
  cached: "Cached ✓",
  waiting: "Waiting",
  rendering: "Rendering…",
  rendered: "Rendered ✓",
};

/** One scene row's state line; a scene still waiting when its export failed never rendered. */
export function sceneLabel(scene: SceneProgress, row: ExportRow): string {
  return (row.status === "error" || row.status === "cancelled") && (scene.state === "waiting" || scene.state === "rendering") ? "Not rendered" : SCENE_LABEL[scene.state];
}

/** A row's status line: the server's "Rendering animation N/M" while animated scenes render first. */
export function stageLabel(row: ExportRow): string {
  return row.status === "running" && row.stage.startsWith("Rendering animation") ? row.stage : STATUS_LABEL[row.status];
}
