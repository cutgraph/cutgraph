// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Project } from "../src/types/project";

const getProject = vi.fn();
const saveProject = vi.fn();
const createJob = vi.fn();
const approveJob = vi.fn();
const cancelJob = vi.fn();
const getComputeJob = vi.fn();
const reveal = vi.fn();

vi.mock("../src/api", () => ({
  api: {
    getProject: (p: string) => getProject(p),
    saveProject: (p: string, project: unknown, rev: number) => saveProject(p, project, rev),
    createJob: (kind: string, project: string, params?: unknown) => createJob(kind, project, params),
    approveJob: (id: string) => approveJob(id),
    cancelJob: (id: string) => cancelJob(id),
    getComputeJob: (id: string) => getComputeJob(id),
    retryJob: vi.fn(),
    reveal: (target: unknown) => reveal(target),
  },
}));

const subscribeJobEvents = vi.fn<(project: string, onJob: (job: unknown) => void) => () => void>(() => () => {});
vi.mock("../src/v3/story/export/jobEvents", () => ({
  subscribeJobEvents: (project: string, onJob: (job: unknown) => void) => subscribeJobEvents(project, onJob),
}));

import ExportSheet from "../src/v3/story/ExportSheet";
import { useProjectStore } from "../src/store/useProjectStore";
import { useStory } from "../src/v3/story/storyStore";
import { startFormatExport } from "../src/v3/story/export/runExport";
import type { ExportRow } from "../src/v3/story/export/progress";

const doc = (overrides: Partial<Project> = {}): Project => ({
  name: "demo-talk", fps: 30, width: 1920, height: 1080, revision: 2, schemaVersion: 3,
  tracks: [], scenes: [], captions: { enabled: true },
  ...overrides,
});

// The Thumbnail section's preview canvas draws nothing in jsdom.
HTMLCanvasElement.prototype.getContext = vi.fn(() => null) as unknown as typeof HTMLCanvasElement.prototype.getContext;

beforeEach(() => {
  getProject.mockReset();
  saveProject.mockReset();
  createJob.mockReset();
  approveJob.mockReset();
  cancelJob.mockReset();
  getComputeJob.mockReset();
  reveal.mockReset();
  subscribeJobEvents.mockClear();
  reveal.mockResolvedValue({ ok: true });
  // The seed fetch driveRow makes right after subscribing (runExport.ts) — a fresh job is still
  // needs-approval at this point unless a test overrides it (e.g. to simulate a dedupe-reused job).
  getComputeJob.mockImplementation((id: string) => Promise.resolve({ id, status: "needs-approval", progress: 0, progressStage: "Awaiting approval" }));
  useProjectStore.setState({ project: doc(), revision: 2 });
  useStory.setState({ exportOpen: true });
});
afterEach(cleanup);

function pressed(button: HTMLElement): string | null {
  return button.getAttribute("aria-pressed");
}

describe("ExportSheet", () => {
  it("silences approval errors in the export runner after confirmed cancellation", async () => {
    createJob.mockResolvedValue({ jobId: "job-cancel", status: "needs-approval" });
    let rejectApproval!: (error: Error) => void;
    approveJob.mockImplementation(() => new Promise((_resolve, reject) => { rejectApproval = reject; }));
    const unsubscribe = vi.fn();
    subscribeJobEvents.mockImplementationOnce(() => unsubscribe);
    const onRow = vi.fn<(row: ExportRow) => void>();
    let cancelled = false;
    startFormatExport({
      video: "demo-talk", working: doc(), format: "16:9", captionsOn: true,
      onRow, registerCleanup: vi.fn(), isCancelled: (id) => cancelled && id === "job-cancel",
    });
    await waitFor(() => expect(approveJob).toHaveBeenCalledWith("job-cancel"));
    const emitted = onRow.mock.calls.length;
    cancelled = true;
    await act(async () => rejectApproval(new Error("409 job already cancelled")));
    expect(onRow).toHaveBeenCalledTimes(emitted);
    expect(unsubscribe).toHaveBeenCalledTimes(1);
  });

  it("keeps cancellation terminal when pending approval rejects with a conflict", async () => {
    createJob.mockResolvedValue({ jobId: "job-cancel", status: "needs-approval" });
    let rejectApproval!: (error: Error) => void;
    approveJob.mockImplementation(() => new Promise((_resolve, reject) => { rejectApproval = reject; }));
    cancelJob.mockResolvedValue({ id: "job-cancel", status: "cancelled" });
    render(<ExportSheet />);
    await userEvent.click(screen.getByRole("button", { name: "Export 0:00 video" }));
    await waitFor(() => expect(approveJob).toHaveBeenCalledWith("job-cancel"));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(await screen.findByText("Cancelled")).toBeTruthy();
    await act(async () => rejectApproval(new Error("409 job already cancelled")));
    expect(screen.getByText("Cancelled")).toBeTruthy();
    expect(screen.queryByText("409 job already cancelled")).toBeNull();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("keeps cancellation terminal when pending approval succeeds", async () => {
    createJob.mockResolvedValue({ jobId: "job-cancel", status: "needs-approval" });
    let finishApproval!: (job: unknown) => void;
    approveJob.mockImplementation(() => new Promise((resolve) => { finishApproval = resolve; }));
    cancelJob.mockResolvedValue({ id: "job-cancel", status: "cancelled" });
    render(<ExportSheet />);
    await userEvent.click(screen.getByRole("button", { name: "Export 0:00 video" }));
    await waitFor(() => expect(approveJob).toHaveBeenCalledWith("job-cancel"));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await act(async () => finishApproval({ id: "job-cancel", status: "queued" }));
    act(() => subscribeJobEvents.mock.calls[0][1]({ id: "job-cancel", status: "queued" }));
    expect(screen.getByText("Cancelled")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it.each(["done", "failed"])("ignores a late %s event after cancellation is confirmed", async (status) => {
    createJob.mockResolvedValue({ jobId: "job-cancel", status: "running" });
    getComputeJob.mockResolvedValue({ id: "job-cancel", status: "running", progress: 0.4 });
    cancelJob.mockResolvedValue({ id: "job-cancel", status: "cancelled" });
    const { container } = render(<ExportSheet />);
    await userEvent.click(screen.getByRole("button", { name: "Export 0:00 video" }));
    await screen.findByText("Rendering…");
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(await screen.findByText("Cancelled")).toBeTruthy();
    act(() => subscribeJobEvents.mock.calls[0][1]({
      id: "job-cancel", status, progress: 1, output: "/media/compiled/demo-talk.mp4", error: "Late failure",
    }));
    expect(screen.getByText("Cancelled")).toBeTruthy();
    expect((container.querySelector(".ex-bar b") as HTMLElement).style.width).toBe("40%");
    expect(screen.queryByText("Late failure")).toBeNull();
    expect(screen.queryByRole("button", { name: "Play" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("cancels a running export and shows its cancelled state", async () => {
    createJob.mockResolvedValue({ jobId: "job-cancel", status: "running" });
    getComputeJob.mockResolvedValue({ id: "job-cancel", status: "running", progress: 0.4 });
    let finishCancel!: (job: unknown) => void;
    cancelJob.mockImplementation(() => new Promise((resolve) => { finishCancel = resolve; }));
    render(<ExportSheet />);
    await userEvent.click(screen.getByRole("button", { name: "Export 0:00 video" }));
    await screen.findByText("Rendering…");
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(cancelJob).toHaveBeenCalledTimes(1);
    expect(cancelJob).toHaveBeenCalledWith("job-cancel");
    expect((screen.getByRole("button", { name: "Cancelling…" }) as HTMLButtonElement).disabled).toBe(true);
    await act(async () => finishCancel({ id: "job-cancel", status: "cancelled" }));
    expect(screen.getByText("Cancelled")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Play" })).toBeNull();
    expect(screen.queryByText("The render failed.")).toBeNull();
    act(() => subscribeJobEvents.mock.calls[0][1]({ id: "job-cancel", status: "running", progress: 0.5 }));
    expect(screen.getByText("Cancelled")).toBeTruthy();
  });

  it("shows a cancel failure and keeps watching the running export", async () => {
    createJob.mockResolvedValue({ jobId: "job-cancel", status: "running" });
    getComputeJob.mockResolvedValue({ id: "job-cancel", status: "running", progress: 0.4 });
    cancelJob.mockRejectedValue(new Error("Could not cancel the export."));
    render(<ExportSheet />);
    await userEvent.click(screen.getByRole("button", { name: "Export 0:00 video" }));
    await screen.findByText("Rendering…");
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Could not cancel the export.");
    expect((screen.getByRole("button", { name: "Cancel" }) as HTMLButtonElement).disabled).toBe(false);
    act(() => subscribeJobEvents.mock.calls[0][1]({ id: "job-cancel", status: "done", progress: 1, output: "/media/compiled/demo-talk.mp4" }));
    expect(await screen.findByRole("button", { name: "Play" })).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("waits for a cancelled event when the cancel response is still running", async () => {
    createJob.mockResolvedValue({ jobId: "job-cancel", status: "running" });
    getComputeJob.mockResolvedValue({ id: "job-cancel", status: "running" });
    cancelJob.mockResolvedValue({ id: "job-cancel", status: "running" });
    render(<ExportSheet />);
    await userEvent.click(screen.getByRole("button", { name: "Export 0:00 video" }));
    await screen.findByText("Rendering…");
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByText("Rendering…")).toBeTruthy();
    act(() => subscribeJobEvents.mock.calls[0][1]({ id: "job-cancel", status: "cancelled" }));
    expect(screen.getByText("Cancelled")).toBeTruthy();
  });

  it("cancels only the selected format's job", async () => {
    getProject.mockRejectedValue(new Error("404 not found"));
    saveProject.mockResolvedValue({ ok: true, revision: 1 });
    createJob.mockImplementation((_kind: string, project: string) => Promise.resolve({ jobId: `job-${project}`, status: "running" }));
    getComputeJob.mockImplementation((id: string) => Promise.resolve({ id, status: "running", progress: 0.4 }));
    cancelJob.mockResolvedValue({ id: "job-demo-talk", status: "cancelled" });
    render(<ExportSheet />);
    await userEvent.click(screen.getByRole("button", { name: /Shorts & Reels/ }));
    await userEvent.click(screen.getByRole("button", { name: "Export 0:00 video" }));
    await waitFor(() => expect(screen.getAllByText("Rendering…")).toHaveLength(2));
    await userEvent.click(screen.getAllByRole("button", { name: "Cancel" })[0]);
    expect(cancelJob).toHaveBeenCalledWith("job-demo-talk");
    expect(await screen.findByText("Cancelled")).toBeTruthy();
    expect(screen.getByText("Rendering…")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "Cancel" })).toHaveLength(1);
  });

  it("preserves completion that arrives while cancellation is pending", async () => {
    createJob.mockResolvedValue({ jobId: "job-cancel", status: "running" });
    getComputeJob.mockResolvedValue({ id: "job-cancel", status: "running" });
    let finishCancel!: (job: unknown) => void;
    cancelJob.mockImplementation(() => new Promise((resolve) => { finishCancel = resolve; }));
    render(<ExportSheet />);
    await userEvent.click(screen.getByRole("button", { name: "Export 0:00 video" }));
    await screen.findByText("Rendering…");
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    act(() => subscribeJobEvents.mock.calls[0][1]({ id: "job-cancel", status: "done", progress: 1, output: "/media/compiled/demo-talk.mp4" }));
    await act(async () => finishCancel({ id: "job-cancel", status: "cancelled" }));
    expect(screen.getByRole("button", { name: "Play" })).toBeTruthy();
    expect(screen.queryByText("Cancelled")).toBeNull();
  });

  it("preselects the format matching the working doc's own dimensions", () => {
    render(<ExportSheet />);
    expect(pressed(screen.getByRole("button", { name: /^YouTube/ }))).toBe("true");
    expect(pressed(screen.getByRole("button", { name: /^4K/ }))).toBe("false");
    expect(pressed(screen.getByRole("button", { name: /Shorts & Reels/ }))).toBe("false");
    expect(pressed(screen.getByRole("button", { name: /Square/ }))).toBe("false");
  });

  it("defaults captions to Burned in and toggles Off", async () => {
    render(<ExportSheet />);
    expect(pressed(screen.getByRole("button", { name: "Burned in" }))).toBe("true");
    await userEvent.click(screen.getByRole("button", { name: "Off" }));
    expect(pressed(screen.getByRole("button", { name: "Off" }))).toBe("true");
    expect(pressed(screen.getByRole("button", { name: "Burned in" }))).toBe("false");
  });

  it("names the video's length on the Export button and dims the story behind the sheet, closing on a click there", async () => {
    const clip = { id: "c", media: { source: "data/source/talk.mp4" }, srcInF: 0, srcOutF: 1590, tlStartF: 0 };
    useProjectStore.setState({ project: doc({ tracks: [{ id: "speech", kind: "speech", clips: [clip] }] }) });
    const { container } = render(<ExportSheet />);
    expect(screen.getByRole("button", { name: "Export 0:53 video" })).toBeTruthy();
    const dim = container.querySelector(".sheet-backdrop") as HTMLElement;
    expect(dim).toBeTruthy();
    await userEvent.click(dim);
    expect(useStory.getState().exportOpen).toBe(false);
  });

  it("draws each format card's icon in its own frame shape", () => {
    render(<ExportSheet />);
    const ratios = ["YouTube", "4K", "Shorts & Reels", "Square"].map((name) =>
      (screen.getByRole("button", { name: new RegExp(`^${name}`) }).querySelector("i") as HTMLElement).style.aspectRatio);
    expect(ratios).toEqual(["16/9", "16/9", "9/16", "1/1"]);
    expect(screen.getByRole("button", { name: /^4K/ }).textContent).toBe("4KFor YouTube · 16:9");
  });

  it("lists the animated scenes as they render, with Cached for the ones the scene cache already holds", async () => {
    useProjectStore.setState({ project: doc({ scenes: [
      { id: "s1", title: "Open", role: "Hook", startWord: 0, shot: { mode: "animation" } },
      { id: "s2", title: "Middle", role: "Beat", startWord: 2 },
      { id: "s3", title: "Close", role: "Wrap", startWord: 4, shot: { mode: "cameo" } },
    ] }) });
    createJob.mockResolvedValue({ jobId: "job-5", status: "needs-approval" });
    approveJob.mockResolvedValue({ id: "job-5", status: "queued" });
    render(<ExportSheet />);
    await userEvent.click(screen.getByRole("button", { name: "Export 0:00 video" }));
    await waitFor(() => expect(subscribeJobEvents).toHaveBeenCalled());
    const onJob = subscribeJobEvents.mock.calls[0][1];
    const rows = () => Array.from(document.querySelectorAll(".ex-scene")).map((li) => [li.querySelector("span")?.textContent, li.querySelector("small")?.textContent, li.className]);

    act(() => onJob({ id: "job-5", status: "running", progress: 0.05, progressStage: "Rendering animation 1/1",
      scenes: [{ id: "s1", state: "cached" }, { id: "s3", state: "rendering" }] }));
    expect(rows()).toEqual([["1 · Open", "Cached ✓", "ex-scene cached"], ["3 · Close", "Rendering…", "ex-scene rendering"]]);
    expect(screen.getByRole("list", { name: "YouTube scenes" })).toBeTruthy();

    act(() => onJob({ id: "job-5", status: "done", progress: 1, progressStage: "Complete", output: "/media/compiled/demo-talk.mp4",
      scenes: [{ id: "s1", state: "cached" }, { id: "s3", state: "rendered" }] }));
    expect(rows()).toEqual([["1 · Open", "Cached ✓", "ex-scene cached"], ["3 · Close", "Rendered ✓", "ex-scene rendered"]]);
    expect(screen.getByRole("button", { name: "Export again" })).toBeTruthy();
  });

  it("closes on the × button", async () => {
    render(<ExportSheet />);
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(useStory.getState().exportOpen).toBe(false);
  });

  it("renders the working doc directly for a same-dims format, no derived doc save", async () => {
    createJob.mockResolvedValue({ jobId: "job-1", status: "needs-approval" });
    approveJob.mockResolvedValue({ id: "job-1", status: "queued" });
    render(<ExportSheet />);

    await userEvent.click(screen.getByRole("button", { name: "Export 0:00 video" }));

    await waitFor(() => expect(createJob).toHaveBeenCalledWith("render_final", "demo-talk", undefined));
    expect(saveProject).not.toHaveBeenCalled();
    await waitFor(() => expect(approveJob).toHaveBeenCalledWith("job-1"));
    expect(subscribeJobEvents).toHaveBeenCalledWith("demo-talk", expect.any(Function));
  });

  it("builds and saves a derived render doc for a non-matching format", async () => {
    getProject.mockResolvedValue({ project: doc({ name: "demo-talk__9x16" }), revision: 0 });
    saveProject.mockResolvedValue({ ok: true, project: doc({ name: "demo-talk__9x16" }), revision: 1 });
    createJob.mockResolvedValue({ jobId: "job-2", status: "needs-approval" });
    approveJob.mockResolvedValue({ id: "job-2", status: "queued" });
    render(<ExportSheet />);

    await userEvent.click(screen.getByRole("button", { name: /Shorts & Reels/ }));
    await userEvent.click(screen.getByRole("button", { name: "Export 0:00 video" }));

    await waitFor(() => expect(saveProject).toHaveBeenCalled());
    const [savedName, savedDoc] = saveProject.mock.calls[0];
    expect(savedName).toBe("demo-talk__9x16");
    expect(savedDoc).toMatchObject({ name: "demo-talk__9x16", width: 1080, height: 1920, captions: { enabled: true } });
    await waitFor(() => expect(createJob).toHaveBeenCalledWith("render_final", "demo-talk__9x16", undefined));
    await waitFor(() => expect(approveJob).toHaveBeenCalledWith("job-2"));
  });

  it("exports 4K as a 3840x2160 render doc of the 16:9 frame, beside the 1080p one", async () => {
    getProject.mockRejectedValue(new Error("404 not found"));
    saveProject.mockResolvedValue({ ok: true, project: doc({ name: "demo-talk__3840x2160" }), revision: 1 });
    createJob.mockImplementation((_kind: string, project: string) => Promise.resolve({ jobId: `job-${project}`, status: "needs-approval" }));
    approveJob.mockResolvedValue({ status: "queued" });
    render(<ExportSheet />);

    await userEvent.click(screen.getByRole("button", { name: /^4K/ }));
    await userEvent.click(screen.getByRole("button", { name: "Export 0:00 video" }));

    await waitFor(() => expect(saveProject).toHaveBeenCalledTimes(1));
    const [savedName, savedDoc, base] = saveProject.mock.calls[0];
    expect([savedName, base]).toEqual(["demo-talk__3840x2160", 0]);
    expect(savedDoc).toMatchObject({ name: "demo-talk__3840x2160", width: 3840, height: 2160, captions: { enabled: true } });
    await waitFor(() => expect(createJob.mock.calls.map((call) => call[1]).sort()).toEqual(["demo-talk", "demo-talk__3840x2160"]));
    expect(Array.from(document.querySelectorAll(".ex-row-head b")).map((b) => b.textContent)).toEqual(["YouTube · 16:9", "4K · 16:9"]);
  });

  it("shows Play and Show in folder once a job event reports done", async () => {
    createJob.mockResolvedValue({ jobId: "job-3", status: "needs-approval" });
    approveJob.mockResolvedValue({ id: "job-3", status: "queued" });
    render(<ExportSheet />);

    await userEvent.click(screen.getByRole("button", { name: "Export 0:00 video" }));
    await waitFor(() => expect(subscribeJobEvents).toHaveBeenCalled());
    const onJob = subscribeJobEvents.mock.calls[0][1];
    onJob({ id: "job-3", status: "done", progress: 1, progressStage: "Complete", output: "/media/compiled/demo-talk.mp4" });

    await screen.findByRole("button", { name: "Play" });
    expect(screen.getByRole("button", { name: "Show in folder" })).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Show in folder" }));
    expect(reveal).toHaveBeenCalledWith({ project: "demo-talk" });
  });

  it("skips approve for a dedupe-reused job that is already done, and shows it done", async () => {
    // render_final is deduplicated server-side by render doc content: an unchanged re-export reuses
    // the earlier job and its create response reports that job's CURRENT status, not needs-approval.
    createJob.mockResolvedValue({ jobId: "job-4", status: "done" });
    getComputeJob.mockResolvedValue({ id: "job-4", status: "done", progress: 1, progressStage: "Complete", output: "/media/compiled/demo-talk.mp4" });
    render(<ExportSheet />);

    await userEvent.click(screen.getByRole("button", { name: "Export 0:00 video" }));

    await screen.findByRole("button", { name: "Play" });
    expect(approveJob).not.toHaveBeenCalled();
  });

  it("reads every scene of a reused, unchanged export as Cached: this click rendered nothing", async () => {
    useProjectStore.setState({ project: doc({ scenes: [
      { id: "s1", title: "Open", role: "Hook", startWord: 0, shot: { mode: "animation" } },
      { id: "s3", title: "Close", role: "Wrap", startWord: 4, shot: { mode: "cameo" } },
    ] }) });
    createJob.mockResolvedValue({ jobId: "job-4", status: "done" });
    getComputeJob.mockResolvedValue({ id: "job-4", status: "done", progress: 1, progressStage: "Complete", output: "/media/compiled/demo-talk.mp4",
      scenes: [{ id: "s1", state: "rendered" }, { id: "s3", state: "rendered" }] });
    render(<ExportSheet />);

    await userEvent.click(screen.getByRole("button", { name: "Export 0:00 video" }));

    await screen.findByRole("button", { name: "Play" });
    expect(Array.from(document.querySelectorAll(".ex-scene small")).map((s) => s.textContent)).toEqual(["Cached ✓", "Cached ✓"]);
  });
});
