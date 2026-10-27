// @vitest-environment jsdom
// Recording or importing the narration of a script video. The runner drives
// upload -> WhisperX transcription -> alignment through injected deps; the top bar shows the action
// only for script videos.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Project } from "../src/types/project";
import type { Applied, TakeStatus } from "../src/v3/newscript/api";
import { IDLE_NARRATION, importTake, resumeNarration, useNarration, type NarrationDeps } from "../src/v3/newscript/narrate";
import TopBar from "../src/v3/story/TopBar";
import { useProjectStore } from "../src/store/useProjectStore";

const SILENT = "data/source/Tools_script.mp4";
const TAKE = "data/source/Tools_take1.mp4";

function project(narration: Project["narration"], source = SILENT): Project {
  return {
    schemaVersion: 3, name: "Tools", fps: 30, width: 1920, height: 1080, revision: 1, narration,
    tracks: [{ id: "speech", kind: "speech", clips: [{ id: "source", media: { source }, srcInF: 0, srcOutF: 300, tlStartF: 0 }] }],
    scenes: [{ id: "scene-1", title: "A quick supper", role: "Hook", startWord: 0, shot: { mode: "animation" } }],
  } as Project;
}

const APPLIED: Applied = { project: project("recorded", TAKE), revision: 5, alignment: { matched: 25, total: 27 } };

function deps(statuses: TakeStatus[], overrides: Partial<NarrationDeps> = {}) {
  const seen: string[] = [];
  const d: NarrationDeps = {
    upload: vi.fn(async (_video, _file, onProgress) => {
      onProgress(0.5);
      seen.push(useNarration.getState().phase);
      return { take: "Tools_take1", transcription: { status: "queued" } };
    }),
    status: vi.fn(async (): Promise<TakeStatus> => {
      seen.push(`${useNarration.getState().phase}:${useNarration.getState().message}`);
      return statuses.shift() ?? { take: "Tools_take1", state: "ready" };
    }),
    revision: vi.fn(async () => 4),
    apply: vi.fn(async () => {
      seen.push(useNarration.getState().phase);
      return APPLIED;
    }),
    adopt: vi.fn(),
    wait: vi.fn(async () => {}),
    ...overrides,
  };
  return { d, seen };
}

const file = () => new File(["RIFF"], "reading.wav", { type: "audio/wav" });

beforeEach(() => {
  useNarration.setState(IDLE_NARRATION);
  useProjectStore.setState({ project: null, revision: 0 });
  vi.stubGlobal("fetch", vi.fn(async (input) => new Response(JSON.stringify(String(input) === "/api/health"
    ? { capabilities: { localInference: true } } : { take: null, state: "none" }), { status: 200 })));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("importTake", () => {
  it("uploads, waits for WhisperX, applies at the current revision and adopts the narrated video", async () => {
    const { d, seen } = deps([
      { take: "Tools_take1", state: "queued" },
      { take: "Tools_take1", state: "running" },
      { take: "Tools_take1", state: "ready" },
    ]);
    await importTake("Tools", file(), d);
    expect(seen).toEqual([
      "uploading",
      "transcribing:Queued for transcription…",
      "transcribing:Queued for transcription…",
      "transcribing:Transcribing every word with WhisperX…",
      "aligning",
    ]);
    expect(d.apply).toHaveBeenCalledWith("Tools", "Tools_take1", 4);
    expect(d.adopt).toHaveBeenCalledWith("Tools", APPLIED);
    expect(useNarration.getState()).toMatchObject({ video: "Tools", phase: "done", matched: 25, total: 27, take: "Tools_take1" });
  });

  it("stops at the step that failed, with the server's reason", async () => {
    const { d } = deps([{ take: "Tools_take1", state: "failed", message: "whisperx failed" }]);
    await importTake("Tools", file(), d);
    expect(useNarration.getState()).toMatchObject({ phase: "error", failedAt: "transcribing", error: "whisperx failed" });
    expect(d.apply).not.toHaveBeenCalled();

    const refused = deps([], { apply: vi.fn(async () => Promise.reject(new Error("Only 2 of 27 script words were heard"))) });
    await importTake("Tools", file(), refused.d);
    expect(useNarration.getState()).toMatchObject({ phase: "error", failedAt: "aligning" });
    expect(useNarration.getState().error).toContain("Only 2 of 27");
    expect(refused.d.adopt).not.toHaveBeenCalled();
  });

  it("reports a transcription that could not be queued", async () => {
    const { d } = deps([], {
      upload: vi.fn(async () => ({ take: "Tools_take1", transcription: { status: "error", error: "Local transcription failed" } })),
    });
    await importTake("Tools", file(), d);
    expect(useNarration.getState()).toMatchObject({ phase: "error", failedAt: "transcribing", error: "Local transcription failed" });
    expect(d.status).not.toHaveBeenCalled();
  });
});

describe("resumeNarration", () => {
  it("picks a still-transcribing take back up after a reload, and leaves anything else alone", async () => {
    const idle = deps([{ take: "Tools_take1", state: "ready" }]);
    await resumeNarration("Tools", idle.d);
    expect(idle.d.apply).not.toHaveBeenCalled();
    expect(useNarration.getState().phase).toBe("idle");

    const running = deps([{ take: "Tools_take1", state: "running" }, { take: "Tools_take1", state: "running" }]);
    await resumeNarration("Tools", running.d);
    expect(running.d.apply).toHaveBeenCalledWith("Tools", "Tools_take1", 4);
    expect(useNarration.getState().phase).toBe("done");
  });
});

describe("the top bar", () => {
  it("keeps script editing usable and gates narration import when transcription is unavailable", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input) => new Response(JSON.stringify(String(input) === "/api/health"
      ? { capabilities: {} } : { take: null, state: "none" }), { status: 200 })));
    useProjectStore.setState({ project: project("estimated"), revision: 1 });
    render(<TopBar video="Tools" onUndo={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Record narration" }));
    expect(await screen.findByText(/Transcription is not installed/)).toBeTruthy();
    expect(screen.queryByLabelText("Choose a narration file")).toBeNull();
    expect(screen.getByRole("link", { name: "Open Settings, Add-ons" })).toBeTruthy();
    expect(useProjectStore.getState().project?.narration).toBe("estimated");
  });
  it("offers Record narration only on a script video, and opens the record/import sheet", async () => {
    useProjectStore.setState({ project: project(null), revision: 1 });
    const { rerender } = render(<TopBar video="Tools" onUndo={() => {}} />);
    expect(screen.queryByRole("button", { name: /narration/i })).toBeNull();

    act(() => useProjectStore.setState({ project: project("estimated"), revision: 1 }));
    rerender(<TopBar video="Tools" onUndo={() => {}} />);
    expect(screen.getByText("Animation · estimated timing")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Record narration" }));
    const sheet = screen.getByRole("dialog", { name: "Narration" });
    expect(within(sheet).getByLabelText("Choose a narration file")).toBeTruthy();
    expect(within(sheet).getByText("estimated from the script")).toBeTruthy();

    act(() => useNarration.setState({ ...IDLE_NARRATION, video: "Tools", phase: "done", fileName: "reading.wav", matched: 25, total: 27 }));
    expect(within(sheet).getByText("Lined up your script · 25 of 27 words heard")).toBeTruthy();
    expect(sheet.querySelectorAll('[data-status="done"]')).toHaveLength(3);

    act(() => useProjectStore.setState({ project: project("recorded", TAKE), revision: 2 }));
    expect(screen.getByText("Animation · narrated")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Replace narration" })).toBeTruthy();
  });

  it("refetches the words once a change of speech media is saved (an import, or undoing one)", () => {
    const reconciled: string[] = [];
    const onReconcile = (e: Event) => reconciled.push((e as CustomEvent<string>).detail);
    window.addEventListener("cutgraph:transcript-reconcile", onReconcile);
    useProjectStore.setState({ project: project("recorded", TAKE), revision: 5 });
    render(<TopBar video="Tools" onUndo={() => {}} />);
    act(() => useProjectStore.setState({ project: project("estimated", SILENT) })); // local undo, not saved yet
    expect(reconciled).toEqual([]);
    act(() => useProjectStore.setState({ revision: 6 })); // the save landed
    expect(reconciled).toEqual(["Tools"]);
    window.removeEventListener("cutgraph:transcript-reconcile", onReconcile);
  });
});
