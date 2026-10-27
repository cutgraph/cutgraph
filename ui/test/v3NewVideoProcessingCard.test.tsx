// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ProcessingCard from "../src/v3/newvideo/ProcessingCard";
import { IDLE_FLOW, transition, type FlowState } from "../src/v3/newvideo/flow";

afterEach(cleanup);

function rowEl(container: HTMLElement, row: string): HTMLElement {
  const el = container.querySelector(`[data-row="${row}"]`);
  if (!el) throw new Error(`row ${row} not found`);
  return el as HTMLElement;
}

describe("ProcessingCard", () => {
  it("renders the upload row active with a progress bar while uploading", () => {
    const state: FlowState = transition(
      transition(
        { ...IDLE_FLOW },
        { type: "START", wsId: "garden", fileName: "seedling.mp4", title: "seedling day" },
      ),
      { type: "UPLOAD_PROGRESS", progress: 0.42 },
    );
    const { container } = render(<ProcessingCard state={state} onRetry={vi.fn()} />);

    expect(screen.getByText("Uploading seedling.mp4")).toBeTruthy();
    expect(rowEl(container, "upload").dataset.status).toBe("active");
    expect(rowEl(container, "transcribe").dataset.status).toBe("pending");
    expect(rowEl(container, "scenes").dataset.status).toBe("pending");

    const bar = container.querySelector('[role="progressbar"]');
    expect(bar?.getAttribute("aria-valuenow")).toBe("42");
  });

  it("marks upload done and transcribe active once creating/transcribing, with a status message", () => {
    let state: FlowState = transition(
      { ...IDLE_FLOW },
      { type: "START", wsId: "garden", fileName: "seedling.mp4", title: "seedling day" },
    );
    state = transition(state, { type: "UPLOADED" });
    state = transition(state, { type: "CREATED" });
    state = transition(state, { type: "TRANSCRIBING", message: "Queued for transcription…" });
    const { container } = render(<ProcessingCard state={state} onRetry={vi.fn()} />);

    expect(rowEl(container, "upload").dataset.status).toBe("done");
    expect(rowEl(container, "transcribe").dataset.status).toBe("active");
    expect(screen.getByText("Queued for transcription…")).toBeTruthy();
    expect(rowEl(container, "scenes").dataset.status).toBe("pending");
  });

  it("shows the scene count once ready, with every row done and no retry button", () => {
    let state: FlowState = transition(
      { ...IDLE_FLOW },
      { type: "START", wsId: "garden", fileName: "seedling.mp4", title: "seedling day" },
    );
    state = transition(state, { type: "UPLOADED" });
    state = transition(state, { type: "CREATED" });
    state = transition(state, { type: "TRANSCRIBED" });
    state = transition(state, { type: "READY", ws: "garden", video: "seedling_day", sceneCount: 4 });
    const { container } = render(<ProcessingCard state={state} onRetry={vi.fn()} />);

    expect(screen.getByRole("heading", { name: "Your story is ready" })).toBeTruthy();
    expect(screen.getByText("Laid out your story · 4 scenes")).toBeTruthy();
    for (const row of ["upload", "transcribe", "scenes"]) expect(rowEl(container, row).dataset.status).toBe("done");
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("shows the failed row's message and a working Retry button", async () => {
    let state: FlowState = transition(
      { ...IDLE_FLOW },
      { type: "START", wsId: "garden", fileName: "seedling.mp4", title: "seedling day" },
    );
    state = transition(state, { type: "UPLOADED" });
    state = transition(state, { type: "CREATED" });
    state = transition(state, { type: "FAILED", step: "transcribe", message: "WhisperX is not installed." });
    const onRetry = vi.fn();
    const { container } = render(<ProcessingCard state={state} onRetry={onRetry} />);

    expect(rowEl(container, "upload").dataset.status).toBe("done");
    expect(rowEl(container, "transcribe").dataset.status).toBe("error");
    expect(screen.getByText("WhisperX is not installed.")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("a create-step failure shows as an error on the folded-in transcribe row", () => {
    let state: FlowState = transition(
      { ...IDLE_FLOW },
      { type: "START", wsId: "garden", fileName: "seedling.mp4", title: "seedling day" },
    );
    state = transition(state, { type: "UPLOADED" });
    state = transition(state, { type: "FAILED", step: "create", message: "project already exists" });
    const { container } = render(<ProcessingCard state={state} onRetry={vi.fn()} />);

    expect(rowEl(container, "upload").dataset.status).toBe("done");
    expect(rowEl(container, "transcribe").dataset.status).toBe("error");
    expect(screen.getByText("project already exists")).toBeTruthy();
  });
});
