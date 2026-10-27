// Recording or importing the narration of a script video: upload the take, wait for its
// WhisperX transcription, then apply it (the server aligns the script words onto it). A module-level
// store, so an import keeps going while its sheet is closed or the person switches spaces.
import { create } from "zustand";
import { api } from "../../api";
import { applyRemote, useProjectStore } from "../../store/useProjectStore";
import { reconcileTranscript } from "../../transcript/useTranscript";
import type { Project } from "../../types/project";
import { errorText } from "../common";
import { liveDeps as footageDeps } from "../newvideo/runFlow";
import { toast } from "../Toast";
import { scriptApi, type Applied, type TakeStatus, type Uploaded } from "./api";

export type NarrationPhase = "idle" | "uploading" | "transcribing" | "aligning" | "done" | "error";

export interface NarrationState {
  video: string | null;
  phase: NarrationPhase;
  fileName: string;
  progress: number;
  take: string | null;
  message: string | null;
  failedAt: NarrationPhase | null;
  error: string | null;
  matched: number | null;
  total: number | null;
}

export const IDLE_NARRATION: NarrationState = {
  video: null, phase: "idle", fileName: "", progress: 0, take: null, message: null,
  failedAt: null, error: null, matched: null, total: null,
};

export const useNarration = create<NarrationState>(() => IDLE_NARRATION);

export const BUSY: ReadonlySet<NarrationPhase> = new Set(["uploading", "transcribing", "aligning"]);

export interface NarrationDeps {
  upload: (video: string, file: File, onProgress: (progress: number) => void) => Promise<Uploaded>;
  status: (video: string, take?: string) => Promise<TakeStatus>;
  revision: (video: string) => Promise<number>;
  apply: (video: string, take: string, baseRevision: number) => Promise<Applied>;
  adopt: (video: string, applied: Applied) => void;
  wait: (ms: number) => Promise<void>;
}

const POLL_MS = 1500;

const set = (patch: Partial<NarrationState>) => useNarration.setState(patch);

function speechProxy(project: Project): string | null | undefined {
  return project.tracks.find((t) => t.kind === "speech")?.clips?.[0]?.media.proxy;
}

export const liveNarrationDeps: NarrationDeps = {
  upload: scriptApi.uploadTake,
  status: scriptApi.status,
  revision: async (video) => (await api.getProject(video)).revision,
  apply: scriptApi.apply,
  adopt: (video, applied) => {
    const store = useProjectStore.getState();
    if (store.project?.name === video && applied.revision > store.revision) applyRemote(applied.project, applied.revision);
    reconcileTranscript(video);
    if (!speechProxy(applied.project)) footageDeps.setProxyWhenReady(video);
  },
  wait: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
};

function fail(e: unknown): void {
  set({ failedAt: useNarration.getState().phase, phase: "error", error: errorText(e), message: null });
}

export async function importTake(video: string, file: File, deps: NarrationDeps = liveNarrationDeps): Promise<void> {
  if (BUSY.has(useNarration.getState().phase)) return;
  useNarration.setState({ ...IDLE_NARRATION, video, phase: "uploading", fileName: file.name });
  try {
    const uploaded = await deps.upload(video, file, (progress) => set({ progress }));
    if (uploaded.transcription.status === "error") {
      set({ phase: "transcribing" });
      throw new Error(uploaded.transcription.error || "Transcription couldn't start.");
    }
    await finishTake(video, uploaded.take, deps);
  } catch (e) {
    fail(e);
  }
}

async function finishTake(video: string, take: string, deps: NarrationDeps): Promise<void> {
  set({ phase: "transcribing", take, progress: 1, message: "Queued for transcription…" });
  for (;;) {
    const status = await deps.status(video, take);
    if (status.state === "ready" || status.state === "applied") break;
    if (status.state === "failed") throw new Error(status.message || "Transcription failed.");
    if (status.state !== "queued" && status.state !== "running") throw new Error("Transcription didn't start. Try the recording again.");
    set({ message: status.state === "running" ? "Transcribing every word with WhisperX…" : "Queued for transcription…" });
    await deps.wait(POLL_MS);
  }
  set({ phase: "aligning", message: null });
  const applied = await deps.apply(video, take, await deps.revision(video));
  deps.adopt(video, applied);
  set({ phase: "done", matched: applied.alignment?.matched ?? null, total: applied.alignment?.total ?? null });
  if (applied.alignment) toast(`Narration in · ${applied.alignment.matched} of ${applied.alignment.total} script words heard`);
}

/** After a reload mid-import, pick a still-transcribing take back up. */
export async function resumeNarration(video: string, deps: NarrationDeps = liveNarrationDeps): Promise<void> {
  const current = useNarration.getState();
  if (current.video === video && current.phase !== "idle") return;
  let status: TakeStatus;
  try {
    status = await deps.status(video);
  } catch {
    return;
  }
  if (!status.take || (status.state !== "queued" && status.state !== "running")) return;
  if (BUSY.has(useNarration.getState().phase)) return;
  useNarration.setState({ ...IDLE_NARRATION, video, fileName: status.take });
  try {
    await finishTake(video, status.take, deps);
  } catch (e) {
    fail(e);
  }
}

export function resetNarration(): void {
  if (!BUSY.has(useNarration.getState().phase)) useNarration.setState(IDLE_NARRATION);
}
