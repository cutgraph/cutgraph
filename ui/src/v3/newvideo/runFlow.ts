// Orchestrates the new-video-from-footage flow: drives `flow.ts`'s pure reducer through real API
// calls. Every network/XHR/SSE detail is behind FlowDeps so this file (and its tests) never touch
// a live server — `liveDeps` is the only place that does.
import { api, type SaveResult, type TranscriptLifecycle } from "../../api";
import { persistCurrent } from "../../persist";
import { applySystem, useProjectStore } from "../../store/useProjectStore";
import type { Project } from "../../types/project";
import { v3api } from "../api";
import type { FlowEvent, FlowState, Step } from "./flow";

export type SourceTarget = { kind: "upload"; file: File } | { kind: "library"; assetId: string; name: string };

export interface FlowMeta {
  sourceSlug?: string;
  project?: string;
}

export interface FlowHandle {
  getFlow: () => FlowState;
  dispatch: (event: FlowEvent) => void;
  getMeta: () => FlowMeta;
  setMeta: (patch: Partial<FlowMeta>) => void;
}

export interface FlowDeps {
  upload: (file: File, onProgress: (progress: number) => void) => Promise<{ slug: string }>;
  useLibraryAsset: (wsId: string, assetId: string) => Promise<{ slug: string }>;
  createProject: (project: string, source: string, title: string) => Promise<{ revision: number }>;
  assignVideo: (wsId: string, project: string) => Promise<void>;
  waitForTranscription: (project: string, onUpdate: (message?: string) => void) => Promise<void>;
  // Reads the project's CURRENT revision itself, right before writing: assignVideo (and, in the
  // background, the proxy write — see setProxyWhenReady) already bumped it past whatever revision
  // createProject returned, so trusting a cached baseRevision here 409s for real.
  applyScenes: (project: string) => Promise<{ revision: number; sceneCount: number }>;
  // Fire-and-forget: sets the speech clip's media.proxy once the scrub proxy lands. Never awaited
  // by resumeFlow — a slow/failed proxy encode must not hold up the ready state.
  setProxyWhenReady: (project: string) => void;
}

export function slugify(value: string): string {
  return value.replace(/\.[^/.]+$/, "").replace(/[^A-Za-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "");
}

const errMsg = (e: unknown): string => (e instanceof Error ? e.message : String(e));

export async function runNewVideoFlow(
  handle: FlowHandle,
  input: { wsId: string; title: string; source: SourceTarget },
  deps: FlowDeps,
): Promise<void> {
  const fileName = input.source.kind === "upload" ? input.source.file.name : input.source.name;
  handle.setMeta({ sourceSlug: undefined, project: undefined });
  handle.dispatch({ type: "START", wsId: input.wsId, fileName, title: input.title });
  await resumeFlow(handle, input.source, deps);
}

export async function retryFlow(handle: FlowHandle, source: SourceTarget | null, deps: FlowDeps): Promise<void> {
  handle.dispatch({ type: "RETRY" });
  await resumeFlow(handle, source, deps);
}

// Re-entrant by design: START and RETRY both land the flow on a phase, then hand off here. Each
// step is only attempted when the flow is still sitting on the phase it owns, so calling this
// again after a step already advanced (or after an unrelated FAILED) is always safe. `source` is
// only ever null when resuming a flow reconciled from the server after a page reload — it can no
// longer be sitting on "uploading" (a raw file/XHR can't survive a reload), so this is safe.
export async function resumeFlow(handle: FlowHandle, source: SourceTarget | null, deps: FlowDeps): Promise<void> {
  const { getFlow, dispatch, getMeta, setMeta } = handle;

  if (getFlow().phase === "uploading") {
    if (!source) return fail(dispatch, "upload", new Error("The file was lost; choose it again."));
    try {
      const { slug } = source.kind === "upload"
        ? await deps.upload(source.file, (progress) => dispatch({ type: "UPLOAD_PROGRESS", progress }))
      : await deps.useLibraryAsset(getFlow().wsId, source.assetId);
      setMeta({ sourceSlug: slug });
      dispatch({ type: "UPLOADED" });
    } catch (e) {
      return fail(dispatch, "upload", e);
    }
  }

  if (getFlow().phase === "creating") {
    try {
      const flow = getFlow();
      const project = getMeta().project ?? (slugify(flow.title) || slugify(flow.fileName) || "video");
      const sourceSlug = getMeta().sourceSlug;
      if (!sourceSlug) throw new Error("The uploaded source was lost; start over.");
      await deps.createProject(project, sourceSlug, flow.title.trim());
      setMeta({ project });
      if (flow.wsId && flow.wsId !== "unsorted") {
        await deps.assignVideo(flow.wsId, project).catch(() => {
          // Best-effort: the video still exists and can be assigned later from the project page.
        });
      }
      deps.setProxyWhenReady(project); // fire-and-forget; never gates CREATED
      dispatch({ type: "CREATED" });
    } catch (e) {
      return fail(dispatch, "create", e);
    }
  }

  if (getFlow().phase === "transcribing") {
    try {
      const project = getMeta().project;
      if (!project) throw new Error("The video was lost; start over.");
      await deps.waitForTranscription(project, (message) => dispatch({ type: "TRANSCRIBING", message }));
      dispatch({ type: "TRANSCRIBED" });
    } catch (e) {
      return fail(dispatch, "transcribe", e);
    }
  }

  if (getFlow().phase === "scenes") {
    try {
      const flow = getFlow();
      const meta = getMeta();
      if (!meta.project) throw new Error("The video was lost; start over.");
      const result = await deps.applyScenes(meta.project);
      dispatch({ type: "READY", ws: flow.wsId, video: meta.project, sceneCount: result.sceneCount });
    } catch (e) {
      return fail(dispatch, "scenes", e);
    }
  }
}

function fail(dispatch: (event: FlowEvent) => void, step: Step, e: unknown): void {
  dispatch({ type: "FAILED", step, message: errMsg(e) });
}

// ── live deps: the real network/XHR/SSE wiring, used outside of tests ────────────

function uploadWithProgress(file: File, onProgress: (progress: number) => void): Promise<{ slug: string }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", `/upload/${encodeURIComponent(file.name)}`);
    if (file.type) xhr.setRequestHeader("Content-Type", file.type);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as { slug: string });
        } catch {
          reject(new Error("Upload succeeded but the response was unreadable."));
        }
      } else {
        reject(new Error(xhrErrorMessage(xhr)));
      }
    };
    xhr.onerror = () => reject(new Error("Upload failed. Check your connection and try again."));
    xhr.send(file);
  });
}

function xhrErrorMessage(xhr: XMLHttpRequest): string {
  try {
    const body = JSON.parse(xhr.responseText) as { error?: unknown };
    if (typeof body?.error === "string") return body.error;
  } catch {
    // non-JSON error body; the status line is all we have
  }
  return `${xhr.status} ${xhr.statusText}`.trim();
}

function watchProjectEvents(project: string, onEvent: () => void): () => void {
  if (typeof EventSource === "undefined") return () => {};
  try {
    const source = new EventSource(`/api/events?project=${encodeURIComponent(project)}`);
    source.onmessage = () => onEvent();
    return () => source.close();
  } catch {
    return () => {};
  }
}

function transcribeMessage(lifecycle: TranscriptLifecycle): string {
  if (lifecycle.state === "queued") return "Queued for transcription…";
  if (lifecycle.state === "running") return "Transcribing every word with WhisperX…";
  return "Starting transcription…";
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

const POLL_MS = 1000;

async function liveWaitForTranscription(project: string, onUpdate: (message?: string) => void): Promise<void> {
  let wake: (() => void) | null = null;
  const unwatch = watchProjectEvents(project, () => wake?.());
  try {
    for (;;) {
      const lifecycle = await api.transcription(project);
      if (lifecycle.state === "ready") return;
      if (lifecycle.state === "failed" || lifecycle.state === "stale") {
        throw new Error(lifecycle.message || lifecycle.job?.error || "Transcription failed.");
      }
      onUpdate(transcribeMessage(lifecycle));
      await Promise.race([sleep(POLL_MS), new Promise<void>((resolve) => { wake = resolve; })]);
    }
  } finally {
    unwatch();
  }
}

function sceneCountOf(project: Project): number {
  return Array.isArray(project.scenes) ? project.scenes.length : 0;
}

const PROXY_POLL_MS = 1500;

// This tab's own background writes to a video (auto scenes, the proxy PATCH) run one at a time, each on
// the revision read right before it writes, so neither can stale the other's base. A writer
// elsewhere can still land in between; the write then retries once on a fresh revision.
let backgroundWrites: Promise<unknown> = Promise.resolve();

function writeOnFreshRevision(project: string, write: (revision: number) => Promise<SaveResult>): Promise<SaveResult> {
  const attempt = async () => write((await api.getProject(project)).revision);
  const run = backgroundWrites.then(async () => {
    const first = await attempt();
    return first.ok ? first : attempt();
  });
  backgroundWrites = run.catch(() => undefined);
  return run;
}

function withSpeechProxy(project: Project, proxy: string): Project {
  const clip = project.tracks[0]?.clips?.[0];
  if (!clip || clip.media.proxy === proxy) return project;
  const [track, ...tracks] = project.tracks;
  const [, ...clips] = track.clips ?? [];
  return { ...project, tracks: [{ ...track, clips: [{ ...clip, media: { ...clip.media, proxy } }, ...clips] }, ...tracks] };
}

// The video open in this tab takes the proxy as a system write through the store's serialised save, so
// it can never race the user's own edits. A video open nowhere here is PATCHed in line with
// this tab's other background writes. A live drag also takes the PATCH; the save rebase then folds it in.
async function setProxy(project: string, proxy: string): Promise<void> {
  const open = useProjectStore.getState().project;
  if (open?.name === project) {
    if (open.tracks[0]?.clips?.[0]?.media.proxy === proxy) return;
    if (applySystem((doc) => withSpeechProxy(doc, proxy))) {
      await persistCurrent(project);
      return;
    }
  }
  const ops = [{ op: "replace", path: "/tracks/0/clips/0/media/proxy", value: proxy }];
  await writeOnFreshRevision(project, (revision) => api.patchProject(project, ops, revision)).catch(() => {
    // best-effort — a failed write just means the source plays instead
  });
}

// Best-effort, fire-and-forget: waits for the scrub proxy (via the same status-field-or-SSE signal
// api.proxyStatus/the "proxy" job event ride), then sets it on the speech clip.
export async function liveSetProxyWhenReady(project: string): Promise<void> {
  let wake: (() => void) | null = null;
  const unwatch = watchProjectEvents(project, () => wake?.());
  try {
    for (let attempt = 0; attempt < 40; attempt += 1) {
      const status = await api.proxyStatus(project).catch(() => null);
      if (status?.state === "ready" && status.proxy) {
        await setProxy(project, status.proxy);
        return;
      }
      if (status?.state === "none") return; // no encode was ever registered for this project; nothing to wait for
      await Promise.race([sleep(PROXY_POLL_MS), new Promise<void>((resolve) => { wake = resolve; })]);
    }
  } finally {
    unwatch();
  }
}

export const liveDeps: FlowDeps = {
  upload: uploadWithProgress,
  useLibraryAsset: async (wsId, assetId) => {
    const result = await v3api.useAssetAsSource(wsId, assetId);
    return { slug: result.slug };
  },
  createProject: async (project, source, title) => {
    const created = await api.createProject(project, source, title);
    return { revision: created.revision };
  },
  assignVideo: async (wsId, project) => {
    await v3api.assignVideo(wsId, project);
  },
  waitForTranscription: liveWaitForTranscription,
  applyScenes: async (project) => {
    const result = await writeOnFreshRevision(project, (revision) => api.autoScenes(project, revision));
    if (!result.ok) {
      throw new Error("The video changed before scenes could be applied. Reopen it and try Auto scenes.");
    }
    return { revision: result.revision, sceneCount: sceneCountOf(result.project) };
  },
  setProxyWhenReady: (project) => {
    void liveSetProxyWhenReady(project);
  },
};
