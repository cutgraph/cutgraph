import { describe, expect, it } from "vitest";
import { applyJobEvent, initRow, isTerminal } from "../src/v3/story/export/progress";

describe("export progress rows from job events", () => {
  it("starts a row queued with no job yet", () => {
    const row = initRow("9:16", "demo-talk__9x16");
    expect(row).toMatchObject({ format: "9:16", project: "demo-talk__9x16", status: "queued", progress: 0, jobId: null });
    expect(isTerminal(row.status)).toBe(false);
  });

  it("adopts the job id and status from the first matching event", () => {
    const row = { ...initRow("16:9", "demo-talk"), jobId: "job-1" };
    const next = applyJobEvent(row, { id: "job-1", status: "running", progress: 0.4, progressStage: "Computing" });
    expect(next.status).toBe("running");
    expect(next.progress).toBe(0.4);
    expect(next.stage).toBe("Computing");
    expect(isTerminal(next.status)).toBe(false);
  });

  it("ignores an event for a different job id once a row has adopted one", () => {
    const row = { ...initRow("16:9", "demo-talk"), jobId: "job-1", status: "running" as const, progress: 0.5 };
    const next = applyJobEvent(row, { id: "job-2", status: "done", progress: 1 });
    expect(next).toEqual(row);
  });

  it("maps done/succeeded to a terminal done row with the output path", () => {
    const row = { ...initRow("1:1", "demo-talk__1x1"), jobId: "job-1" };
    const next = applyJobEvent(row, { id: "job-1", status: "done", progress: 1, progressStage: "Complete", output: "/media/compiled/demo-talk__1x1.mp4" });
    expect(next.status).toBe("done");
    expect(next.output).toBe("/media/compiled/demo-talk__1x1.mp4");
    expect(next.error).toBeNull();
    expect(isTerminal(next.status)).toBe(true);
  });

  it("maps a failure status to a terminal error row with a message", () => {
    const row = { ...initRow("9:16", "demo-talk__9x16"), jobId: "job-1" };
    const next = applyJobEvent(row, { id: "job-1", status: "error", progress: 1, error: "ffmpeg exited 1" });
    expect(next.status).toBe("error");
    expect(next.error).toBe("ffmpeg exited 1");
    expect(isTerminal(next.status)).toBe(true);
  });

  it("falls back to a generic message when a failed job carries no error text", () => {
    const row = { ...initRow("9:16", "demo-talk__9x16"), jobId: "job-1" };
    const next = applyJobEvent(row, { id: "job-1", status: "failed", progress: 1 });
    expect(next.status).toBe("error");
    expect(next.error).toBe("The render failed.");
  });

  it("distinguishes cancellation from a render failure", () => {
    const next = applyJobEvent(initRow("16:9", "demo-talk"), { id: "job-1", status: "cancelled" });
    expect(next.status).toBe("cancelled");
    expect(next.error).toBeNull();
    expect(isTerminal(next.status)).toBe(true);
  });

  it.each(["needs-approval", "running", "done", "failed"])("ignores %s events for a cancelled row", (status) => {
    const row = applyJobEvent(initRow("16:9", "demo-talk"), { id: "job-1", status: "cancelled", progress: 0.4 });
    expect(applyJobEvent(row, {
      id: "job-1", status, progress: 1, output: "/media/compiled/demo-talk.mp4", error: "Late failure",
      scenes: [{ id: "scene-1", state: "rendered" }],
    })).toEqual(row);
  });

  it("treats needs-approval/queued/retrying/fallback as still queued", () => {
    const row = { ...initRow("9:16", "demo-talk__9x16"), jobId: "job-1" };
    for (const status of ["needs-approval", "queued", "retrying", "fallback"]) {
      expect(applyJobEvent(row, { id: "job-1", status, progress: 0 }).status).toBe("queued");
    }
  });

  it("treats publishing as still running", () => {
    const row = { ...initRow("9:16", "demo-talk__9x16"), jobId: "job-1", status: "running" as const };
    expect(applyJobEvent(row, { id: "job-1", status: "publishing", progress: 0.9 }).status).toBe("running");
  });

  it("clears a prior error once a later event reports progress again", () => {
    const errored = { ...initRow("9:16", "demo-talk__9x16"), jobId: "job-1", status: "error" as const, error: "boom" };
    const next = applyJobEvent(errored, { id: "job-1", status: "running", progress: 0.2 });
    expect(next.error).toBeNull();
    expect(next.status).toBe("running");
  });
});
