// New-video-from-footage: a small pure state machine driving the processing card.
// `idle -> uploading -> creating -> transcribing -> scenes -> ready | error`. Side effects
// (upload/create/transcribe/scenes-auto API calls) live in runFlow.ts; this file only ever turns
// one FlowState + one FlowEvent into the next FlowState, so it is trivial to unit test.

export type Phase = "idle" | "uploading" | "creating" | "transcribing" | "scenes" | "ready" | "error";
export type Step = "upload" | "create" | "transcribe" | "scenes";

export interface FlowState {
  phase: Phase;
  wsId: string;
  fileName: string;
  title: string;
  progress: number; // 0-1, meaningful only while phase === "uploading"
  message?: string; // transient detail under the active row (e.g. "Queued for transcription")
  sceneCount?: number;
  ws?: string;
  video?: string;
  errorStep?: Step;
  errorMessage?: string;
}

export const IDLE_FLOW: FlowState = {
  phase: "idle", wsId: "", fileName: "", title: "", progress: 0,
};

export type FlowEvent =
  | { type: "START"; wsId: string; fileName: string; title: string }
  | { type: "UPLOAD_PROGRESS"; progress: number }
  | { type: "UPLOADED" }
  | { type: "CREATED" }
  | { type: "TRANSCRIBING"; message?: string }
  | { type: "TRANSCRIBED" }
  | { type: "READY"; ws: string; video: string; sceneCount: number }
  | { type: "FAILED"; step: Step; message: string }
  | { type: "RETRY" }
  | { type: "RESET" };

const RETRY_PHASE: Record<Step, Phase> = {
  upload: "uploading", create: "creating", transcribe: "transcribing", scenes: "scenes",
};

export function transition(state: FlowState, event: FlowEvent): FlowState {
  switch (event.type) {
    case "START":
      return { ...IDLE_FLOW, phase: "uploading", wsId: event.wsId, fileName: event.fileName, title: event.title };
    case "UPLOAD_PROGRESS":
      return state.phase === "uploading" ? { ...state, progress: event.progress } : state;
    case "UPLOADED":
      return state.phase === "uploading" ? { ...state, phase: "creating", progress: 1 } : state;
    case "CREATED":
      return state.phase === "creating" ? { ...state, phase: "transcribing", message: undefined } : state;
    case "TRANSCRIBING":
      return state.phase === "transcribing" ? { ...state, message: event.message } : state;
    case "TRANSCRIBED":
      return state.phase === "transcribing" ? { ...state, phase: "scenes", message: undefined } : state;
    case "READY":
      return state.phase === "scenes"
        ? { ...state, phase: "ready", ws: event.ws, video: event.video, sceneCount: event.sceneCount }
        : state;
    case "FAILED":
      return { ...state, phase: "error", errorStep: event.step, errorMessage: event.message };
    case "RETRY":
      if (state.phase !== "error" || !state.errorStep) return state;
      return {
        ...state,
        phase: RETRY_PHASE[state.errorStep],
        progress: state.errorStep === "upload" ? 0 : state.progress,
        errorStep: undefined,
        errorMessage: undefined,
      };
    case "RESET":
      return IDLE_FLOW;
    default:
      return state;
  }
}

export const PHASE_ORDER: Phase[] = ["idle", "uploading", "creating", "transcribing", "scenes", "ready"];

// The processing card shows 3 rows (upload / transcribe / scenes); the "creating" phase is a
// usually-instant precursor with no row of its own, folded visually into "transcribe".
export type Row = "upload" | "transcribe" | "scenes";

function stepRow(step: Step): Row {
  return step === "create" ? "transcribe" : step;
}

const ROW_BOUNDARY: Record<Row, Phase> = { upload: "uploading", transcribe: "transcribing", scenes: "scenes" };

export function rowStatus(state: FlowState, row: Row): "pending" | "active" | "done" | "error" {
  const boundaryAt = PHASE_ORDER.indexOf(ROW_BOUNDARY[row]);
  if (state.phase === "error") {
    const failedRow = stepRow(state.errorStep ?? "upload");
    if (failedRow === row) return "error";
    return boundaryAt < PHASE_ORDER.indexOf(ROW_BOUNDARY[failedRow]) ? "done" : "pending";
  }
  const at = PHASE_ORDER.indexOf(state.phase);
  if (at > boundaryAt) return "done";
  if (at === boundaryAt) return "active";
  if (row === "transcribe" && at === PHASE_ORDER.indexOf("creating")) return "active";
  return "pending";
}
