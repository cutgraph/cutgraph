import { describe, expect, it } from "vitest";
import { IDLE_FLOW, rowStatus, transition, type FlowState } from "../src/v3/newvideo/flow";

const start = (): FlowState =>
  transition(IDLE_FLOW, { type: "START", wsId: "garden", fileName: "seedling.mp4", title: "seedling day" });

describe("new-video flow state machine", () => {
  it("starts idle", () => {
    expect(IDLE_FLOW.phase).toBe("idle");
  });

  it("walks the happy path idle -> uploading -> creating -> transcribing -> scenes -> ready", () => {
    let s = start();
    expect(s).toMatchObject({ phase: "uploading", wsId: "garden", fileName: "seedling.mp4", title: "seedling day", progress: 0 });

    s = transition(s, { type: "UPLOAD_PROGRESS", progress: 0.4 });
    expect(s.progress).toBe(0.4);

    s = transition(s, { type: "UPLOADED" });
    expect(s).toMatchObject({ phase: "creating", progress: 1 });

    s = transition(s, { type: "CREATED" });
    expect(s.phase).toBe("transcribing");

    s = transition(s, { type: "TRANSCRIBING", message: "Queued" });
    expect(s.message).toBe("Queued");

    s = transition(s, { type: "TRANSCRIBED" });
    expect(s.phase).toBe("scenes");
    expect(s.message).toBeUndefined();

    s = transition(s, { type: "READY", ws: "garden", video: "seedling_day", sceneCount: 3 });
    expect(s).toMatchObject({ phase: "ready", ws: "garden", video: "seedling_day", sceneCount: 3 });
  });

  it("ignores events that don't apply to the current phase", () => {
    const idle = transition(IDLE_FLOW, { type: "UPLOADED" });
    expect(idle).toBe(IDLE_FLOW);

    const uploading = start();
    expect(transition(uploading, { type: "CREATED" })).toBe(uploading);
    expect(transition(uploading, { type: "TRANSCRIBED" })).toBe(uploading);
  });

  it("moves to the error phase from any step, keeping which step failed", () => {
    const uploading = start();
    const failed = transition(uploading, { type: "FAILED", step: "upload", message: "Disk full" });
    expect(failed).toMatchObject({ phase: "error", errorStep: "upload", errorMessage: "Disk full" });
  });

  it("retries back into the step that failed, resetting only upload's progress", () => {
    let s = start();
    s = transition(s, { type: "UPLOAD_PROGRESS", progress: 0.7 });
    s = transition(s, { type: "FAILED", step: "upload", message: "Network error" });
    let retried = transition(s, { type: "RETRY" });
    expect(retried).toMatchObject({ phase: "uploading", progress: 0, errorStep: undefined, errorMessage: undefined });

    s = start();
    s = transition(s, { type: "UPLOADED" }); // creating
    s = transition(s, { type: "CREATED" }); // transcribing
    s = transition(s, { type: "FAILED", step: "transcribe", message: "WhisperX missing" });
    retried = transition(s, { type: "RETRY" });
    expect(retried).toMatchObject({ phase: "transcribing", errorStep: undefined });
    expect(retried.progress).toBe(1); // upload's progress is untouched by a non-upload retry
  });

  it("RETRY is a no-op outside the error phase", () => {
    const uploading = start();
    expect(transition(uploading, { type: "RETRY" })).toBe(uploading);
  });

  it("RESET always returns to idle", () => {
    const s = transition(start(), { type: "FAILED", step: "scenes", message: "boom" });
    expect(transition(s, { type: "RESET" })).toBe(IDLE_FLOW);
  });
});

describe("rowStatus", () => {
  const at = (phase: FlowState["phase"], extra: Partial<FlowState> = {}): FlowState => ({ ...IDLE_FLOW, phase, ...extra });

  it("nothing has started yet", () => {
    const s = at("idle");
    expect(rowStatus(s, "upload")).toBe("pending");
    expect(rowStatus(s, "transcribe")).toBe("pending");
    expect(rowStatus(s, "scenes")).toBe("pending");
  });

  it("uploading: the upload row is active, the rest pending", () => {
    const s = at("uploading");
    expect(rowStatus(s, "upload")).toBe("active");
    expect(rowStatus(s, "transcribe")).toBe("pending");
    expect(rowStatus(s, "scenes")).toBe("pending");
  });

  it("creating folds into the transcribe row as active, with upload already done", () => {
    const s = at("creating");
    expect(rowStatus(s, "upload")).toBe("done");
    expect(rowStatus(s, "transcribe")).toBe("active");
    expect(rowStatus(s, "scenes")).toBe("pending");
  });

  it("transcribing: upload done, transcribe active, scenes pending", () => {
    const s = at("transcribing");
    expect(rowStatus(s, "upload")).toBe("done");
    expect(rowStatus(s, "transcribe")).toBe("active");
    expect(rowStatus(s, "scenes")).toBe("pending");
  });

  it("scenes: upload and transcribe done, scenes active", () => {
    const s = at("scenes");
    expect(rowStatus(s, "upload")).toBe("done");
    expect(rowStatus(s, "transcribe")).toBe("done");
    expect(rowStatus(s, "scenes")).toBe("active");
  });

  it("ready: every row done", () => {
    const s = at("ready");
    expect(rowStatus(s, "upload")).toBe("done");
    expect(rowStatus(s, "transcribe")).toBe("done");
    expect(rowStatus(s, "scenes")).toBe("done");
  });

  it("an upload failure marks only the upload row as error", () => {
    const s = at("error", { errorStep: "upload" });
    expect(rowStatus(s, "upload")).toBe("error");
    expect(rowStatus(s, "transcribe")).toBe("pending");
    expect(rowStatus(s, "scenes")).toBe("pending");
  });

  it("a create failure marks the transcribe row as error (folded), upload done", () => {
    const s = at("error", { errorStep: "create" });
    expect(rowStatus(s, "upload")).toBe("done");
    expect(rowStatus(s, "transcribe")).toBe("error");
    expect(rowStatus(s, "scenes")).toBe("pending");
  });

  it("a transcribe failure leaves upload done and marks transcribe as error", () => {
    const s = at("error", { errorStep: "transcribe" });
    expect(rowStatus(s, "upload")).toBe("done");
    expect(rowStatus(s, "transcribe")).toBe("error");
    expect(rowStatus(s, "scenes")).toBe("pending");
  });

  it("a scenes failure leaves upload and transcribe done and marks scenes as error", () => {
    const s = at("error", { errorStep: "scenes" });
    expect(rowStatus(s, "upload")).toBe("done");
    expect(rowStatus(s, "transcribe")).toBe("done");
    expect(rowStatus(s, "scenes")).toBe("error");
  });
});
