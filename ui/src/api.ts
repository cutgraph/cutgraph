// The UI contract for video documents, playback and co-pilot edits.

import type { Project } from "./types/project";
import { markSelfPending, clearSelfPending } from "./selfPending";

export interface ProjectEnvelope {
  project: Project;
  revision: number;
}

// --- Provider-neutral code-chat selection --------------------------
// The panel picks these; they ride each chat turn and map onto ClaudeAgentOptions server-side.
// null model/effort ⇒ SDK default. mode is "default" | "plan".
export interface AgentPanelSettings {
  providerId?: string;
  profileId?: string;
  model: string | null;
  effort: string | null;
  mode: string;
}

export interface ChatModelOption {
  id: string | null;
  label: string;
}

export interface AgentAvailability {
  available: boolean;
  error: { code: string; message: string } | null;
}

export interface AgentCapabilities {
  proposalOnly: boolean;
  planReadOnly: boolean;
  autoFullAccess: boolean;
}

export interface AgentProfile {
  id: string;
  label: string;
  model: string | null;
  efforts: string[];
  modes: string[];
  capabilities: AgentCapabilities;
  availability: AgentAvailability;
}

export interface AgentProvider {
  id: string;
  label: string;
  availability: AgentAvailability;
  profiles: AgentProfile[];
}

export interface ChatConfig {
  schemaVersion: number;
  providers: AgentProvider[];
  selectionDefaults: Required<AgentPanelSettings>;
  models: ChatModelOption[];
  efforts: string[];
  modes: string[];
  defaults: AgentPanelSettings;
}

export type SaveResult =
  | { ok: true; project: Project; revision: number }
  | { ok: false; currentRevision: number };

export interface CreatedProject {
  project: Project;
  revision: number;
  transcription: { status: string; error?: string };
}

export interface ProjectAsset {
  id: string;
  name: string;
  kind: "video" | "audio" | "image";
  source: string;
  bytes: number;
  mtime: number;
  duration: number | null;
  width: number | null;
  height: number | null;
  status: "ready" | "processing" | "offline" | "error";
  primary: boolean;
  usageCount: number;
}

export interface ComputeJob {
  id: string;
  kind: string;
  status: string;
  // Durable registry timestamp; optional for older SSE payloads that are
  // enriched by the compute-jobs reconciliation endpoint.
  createdAt?: string;
  createdOrdinal?: number;
  startedAt?: string;
  completedAt?: string;
  attemptState?: "retrying";
  requiresApproval?: boolean;
  progress: number;
  progressStage: string;
  sequence?: number;
  canCancel: boolean;
  canRetry: boolean;
  error?: string;
  errorCode?: string;
  output?: string;
  parentJobId?: string;
  retryState?: "retrying";
  retryChildJobId?: string;
}

export type TranscriptState = "not_started" | "queued" | "running" | "ready" | "stale" | "failed";
export interface TranscriptLifecycle {
  state: TranscriptState;
  job: ComputeJob | null;
  message?: string;
  errorCode?: string;
}

// --- Transcript (GET /api/transcript/<p> reshape) ----------------------------
// Flat word list (WhisperX `word`→`w`, leading space stripped) + segments for paragraphing.
// Times are float SECONDS in the source clock; the panel converts via cutmap.sec_to_frame.
export interface TranscriptWord {
  w: string;
  start: number;
  end: number;
  segIdx: number;
}

// A verbatim disfluency ("um", "uh", …) served in a SEPARATE array, NEVER inside `words`:
// the flat `words` list is the cross-phase `wordIdx` contract (anchors, corrections, caption emphasis)
// and must stay index-stable whether or not a filler pass has run. `segIdx` names the containing
// segment so the panel can interleave fillers by TIME for display without minting an index into the
// word space; filler cut proposals are derived from `start`/`end` (source seconds), like silence.
export interface TranscriptFiller {
  w: string;
  start: number;
  end: number;
  segIdx: number;
}

export interface TranscriptSegment {
  segIdx: number;
  start: number;
  end: number;
  text: string;
}

export interface TranscriptDoc {
  language: string;
  duration: number;
  words: TranscriptWord[];
  segments: TranscriptSegment[];
  fillers: TranscriptFiller[];
}

// --- Ripple-cut verb (POST /api/verbs/ripple_cut/<p>) ------------------------
// Stateless compute: returns the next project doc + overlay warnings; it does NOT persist. The
// client applies it as one labeled undo step and saves it through the revision-gated PUT.
export interface CutRange {
  startF: number;
  endF: number;
}

export interface RippleCutResult {
  project: Project;
  warnings: { type: string; itemId: string }[];
}

// --- Ripple-restore verb (POST /api/verbs/ripple_restore/<p>) -----------------
// The inverse of ripple_cut, same stateless contract: puts cut SOURCE ranges back on the speech track.
export interface RestoreRange {
  media: string;
  srcStartF: number;
  srcEndF: number;
}

// --- Waveform peaks (GET /api/peaks/<slug>) ----------------------------------
export interface PeaksDoc {
  sampleRate: number;
  windowMs: number;
  peaks: [number, number][];
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    // The server names the offending clip in a JSON {"error": "..."} body (e.g. an
    // overlap validation on 400); surface it instead of the bare status line.
    let detail = "";
    let code: string | undefined;
    try {
      const body = (await res.json()) as { error?: unknown; code?: unknown };
      if (body && typeof body.error === "string") detail = body.error;
      if (body && typeof body.code === "string") code = body.code;
    } catch {
      // non-JSON body — the status line is all we have
    }
    throw Object.assign(new Error(`${res.status} ${res.statusText}${detail ? `: ${detail}` : ""}`), { code, detail });
  }
  return res.json() as Promise<T>;
}

export type ChannelProfiles = Record<string, { captionStyle?: Record<string, unknown> }>;

export const api = {
  getChannels: (signal?: AbortSignal) =>
    fetch("/api/channels", { signal }).then(json<{ channels?: ChannelProfiles }>),
  createProject: (project: string, source: string, title?: string) =>
    fetch("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project, source, title }),
    }).then(json<CreatedProject>),

  createJob: (
    kind: string,
    project: string,
    params: Record<string, unknown> = {},
  ) =>
    fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind, project, params }),
    }).then(json<Partial<ComputeJob> & { jobId?: string; status: string }>),


  getComputeJobs: (project: string) =>
    fetch(`/api/compute/jobs?project=${encodeURIComponent(project)}`)
      .then(json<{ jobs: ComputeJob[] }>),

  getComputeJob: (id: string) =>
    fetch(`/api/compute/jobs/${encodeURIComponent(id)}`).then(json<ComputeJob>),

  cancelJob: (id: string) =>
    fetch(`/api/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" }).then(json<ComputeJob>),

  retryJob: (id: string) =>
    fetch(`/api/jobs/${encodeURIComponent(id)}/retry`, { method: "POST" }).then(json<ComputeJob>),

  thumb: (slug: string) => `/media/thumb/${encodeURIComponent(slug)}`,

  getTranscript: (p: string) =>
    fetch(`/api/transcript/${encodeURIComponent(p)}`).then(json<TranscriptDoc>),

  transcription: (p: string) =>
    fetch(`/api/project/${encodeURIComponent(p)}/transcription`).then(json<TranscriptLifecycle>),

  rippleCut: (p: string, project: Project, cuts: CutRange[]) =>
    fetch(`/api/verbs/ripple_cut/${encodeURIComponent(p)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project, cuts }),
    }).then(json<RippleCutResult>),

  rippleRestore: (p: string, project: Project, ranges: RestoreRange[]) =>
    fetch(`/api/verbs/ripple_restore/${encodeURIComponent(p)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project, ranges }),
    }).then(json<RippleCutResult>),

  getPeaks: (slug: string) => fetch(`/api/peaks/${encodeURIComponent(slug)}`).then(json<PeaksDoc>),

  // --- project.json v1 (revision-gated) ---
  getProject: (p: string) =>
    fetch(`/api/project/${encodeURIComponent(p)}`).then(json<ProjectEnvelope>),

  saveProject: (p: string, project: Project, baseRevision: number): Promise<SaveResult> =>
    gatedWrite(p, "PUT", baseRevision, { project, baseRevision }),

  patchProject: (p: string, ops: unknown[], baseRevision: number): Promise<SaveResult> =>
    gatedWrite(p, "PATCH", baseRevision, { ops, baseRevision }),

  // Proposes scenes from the transcript and applies them as one patch (new video from footage).
  // Same revision-gated write contract as gatedWrite, on a different route.
  autoScenes: async (p: string, baseRevision: number): Promise<SaveResult> => {
    const expected = baseRevision + 1;
    markSelfPending(expected);
    try {
      const res = await fetch(`/api/project/${encodeURIComponent(p)}/scenes/auto?apply=1`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ baseRevision }),
      });
      if (res.status === 409) {
        const conflict = (await res.json()) as { currentRevision?: number; error?: string };
        if (typeof conflict.currentRevision !== "number") {
          throw new Error(conflict.error || "The request conflicts with the current state.");
        }
        return { ok: false, currentRevision: conflict.currentRevision };
      }
      const env = await json<ProjectEnvelope>(res);
      return { ok: true, project: env.project, revision: env.revision };
    } finally {
      clearSelfPending(expected);
    }
  },

  // Project proxy readiness: the status-field half of the proxy-ready signal; the
  // server also fires an SSE `job` event of kind "proxy" on the same project channel.
  proxyStatus: (p: string) =>
    fetch(`/api/project/${encodeURIComponent(p)}/proxy`).then(json<{ state: "none" | "pending" | "ready"; proxy: string | null }>),

  // --- Claude co-editor chat ---
  // sendChat returns immediately ({ok}); the agent's reply streams back as agent events over the
  // existing SSE channel (chat never blocks on the agent). getChatLog tails the durable jsonl
  // for reconnect reconcile (the SSE stream is not replayable). `settings` rides each turn; the
  // server validates + rebuilds the warm session if they changed since the last turn.
  sendChat: (p: string, text: string, autoApprove = false, settings?: AgentPanelSettings) =>
    fetch(`/api/chat/${encodeURIComponent(p)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, autoApprove, ...(settings ?? {}) }),
    }).then(json<{ ok: boolean }>),

  // The selectable model/effort/mode knobs + defaults; static, no project needed.
  getChatConfig: (verify = true) => fetch(`/api/chat/config${verify ? "" : "?verify=0"}`).then(json<ChatConfig>),

  getChatLog: (p: string) =>
    fetch(`/api/chat/${encodeURIComponent(p)}/log`).then(json<{ entries: unknown[] }>),

  // --- Claude proposal accept/reject ---
  // Accept is one server-side guarded operation: the receipt/revision check, project write, and status
  // settlement happen together, so a normal revision increment cannot make its own receipt stale.
  acceptProposal: (p: string, id: string, reason?: string) =>
    fetch(`/api/proposal/${encodeURIComponent(p)}/${encodeURIComponent(id)}/accept`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(reason ? { reason } : {}),
    }).then(json<{ ok: true; project: Project; revision: number; proposal: unknown }>),

  // Legacy/status-only consumers still use this for reject and best-effort stale settlement.
  setProposalStatus: (p: string, id: string, status: string, reason?: string) =>
    fetch(`/api/proposal/${encodeURIComponent(p)}/${encodeURIComponent(id)}/status`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status, reason }),
    }).then(json<{ id: string; status: string }>),

  getProposals: (p: string, signal?: AbortSignal) =>
    fetch(`/api/chat/${encodeURIComponent(p)}/proposals`, { signal }).then(json<{ proposals: unknown[] }>),

  // --- Claude editing tools detached jobs ---
  // Long GPU renders return an opaque jobId polled here; the expensive render_final is human-gated and
  // released with approveJob. Job transitions also stream as `job` SSE events (chip status/progress).
  approveJob: (id: string) =>
    fetch(`/api/jobs/${encodeURIComponent(id)}/approve`, { method: "POST" }).then(json<ComputeJob>),

  // --- Export sheet "Show in folder" ---
  // Loopback-only on the server; selects one rendered artifact in the OS
  // file explorer. `project` resolves to that project's own compiled render; `path` names anything
  // else already under the app's state directory. Both are re-validated server-side.
  reveal: (target: { project: string } | { path: string }) =>
    fetch("/api/reveal", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(target),
    }).then(json<{ ok: true }>),
};

// One revision-gated write path for both PUT (full doc) and PATCH (op-log). Marks the
// expected revision (baseRevision + 1) self-pending for the request lifetime so the SSE
// echo of our own write is ignored; a 409 returns the server's authoritative revision.
async function gatedWrite(
  p: string,
  method: "PUT" | "PATCH",
  baseRevision: number,
  body: unknown,
): Promise<SaveResult> {
  const expected = baseRevision + 1;
  markSelfPending(expected);
  try {
    const res = await fetch(`/api/project/${encodeURIComponent(p)}`, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (res.status === 409) {
      const conflict = (await res.json()) as { currentRevision?: number; error?: string };
      if (typeof conflict.currentRevision !== "number") {
        throw new Error(conflict.error || "The request conflicts with the current state.");
      }
      return { ok: false, currentRevision: conflict.currentRevision };
    }
    const env = await json<ProjectEnvelope>(res);
    return { ok: true, project: env.project, revision: env.revision };
  } finally {
    clearSelfPending(expected);
  }
}
