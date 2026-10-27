import { describe, expect, it } from "vitest";
import { applyJobEvent, initRow, stageLabel } from "../src/v3/story/export/progress";

describe("export row status line", () => {
  const row = { ...initRow("16:9", "demo-talk"), jobId: "job-1" };

  it("shows the server's scene-render stage while animated scenes render", () => {
    const next = applyJobEvent(row, { id: "job-1", status: "running", progress: 0.2, progressStage: "Rendering animation 1/2" });
    expect(stageLabel(next)).toBe("Rendering animation 1/2");
    expect(next.progress).toBe(0.2);
  });

  it("falls back to the plain status once the compile runs or the row settles", () => {
    expect(stageLabel(applyJobEvent(row, { id: "job-1", status: "running", progress: 0.35, progressStage: "Computing" }))).toBe("Rendering…");
    expect(stageLabel(applyJobEvent(row, { id: "job-1", status: "done", progress: 1, progressStage: "Complete" }))).toBe("Rendered");
    expect(stageLabel(row)).toBe("Queued");
  });
});
