// @vitest-environment jsdom
import { forwardRef, useImperativeHandle } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Project } from "../src/types/project";

const getProject = vi.fn();
vi.mock("../src/api", () => ({
  api: {
    getProject: (p: string) => getProject(p),
    transcription: async () => ({ state: "ready", job: null }),
    getTranscript: async () => ({ words: [], segments: [], fillers: [] }),
  },
}));
vi.mock("../src/sse", () => ({ useProjectSSE: () => {} }));

// The player needs ResizeObserver, which jsdom does not provide.
vi.mock("../src/player", () => ({
  FramePlayer: forwardRef((_props, ref) => {
    useImperativeHandle(ref, () => ({
      isSoundBlocked: () => false,
      enableSound: () => {},
      readFrame: () => 0,
      seek: () => {},
      listen: () => {},
      unlisten: () => {},
      isRunning: () => false,
      play: () => {},
      pause: () => {},
    }));
    return <div data-testid="mock-player" />;
  }),
}));
vi.mock("../src/preview/ProjectComposition", () => ({ ProjectComposition: () => null }));

import Story from "../src/v3/Story";
import { useStory } from "../src/v3/story/storyStore";
import { useProjectStore } from "../src/store/useProjectStore";

const doc = (name: string) => ({ name, fps: 30, width: 1920, height: 1080, tracks: [], scenes: [] }) as unknown as Project;

beforeEach(() => {
  getProject.mockReset();
  useProjectStore.setState({ project: null, revision: 0 });
});
afterEach(cleanup);

describe("v3 Story view skeleton", () => {
  it("loads the video into the shared store and renders every region", async () => {
    getProject.mockResolvedValue({ project: doc("herb-care"), revision: 3 });
    const { container } = render(<Story ws="garden" video="herb-care" />);
    await screen.findByRole("heading", { name: "herb-care" });
    expect(getProject).toHaveBeenCalledWith("herb-care");
    expect(useProjectStore.getState().revision).toBe(3);
    for (const slot of ["arc", "script", "player", "drawer", "ask"]) {
      expect(container.querySelector(`[data-slot="${slot}"]`)).not.toBeNull();
    }
  });

  it("switches lenses from the top bar", async () => {
    getProject.mockResolvedValue({ project: doc("herb-care"), revision: 1 });
    const { container } = render(<Story ws="garden" video="herb-care" />);
    await screen.findByRole("heading", { name: "herb-care" });
    await userEvent.click(screen.getByRole("button", { name: "Board" }));
    expect(useStory.getState().lens).toBe("board");
    expect(container.querySelector('[data-slot="board"]')).not.toBeNull();
    expect(container.querySelector('[data-slot="script"]')).toBeNull();
  });

  it("opens the export sheet", async () => {
    getProject.mockResolvedValue({ project: doc("herb-care"), revision: 1 });
    const { container } = render(<Story ws="garden" video="herb-care" />);
    await screen.findByRole("heading", { name: "herb-care" });
    await userEvent.click(screen.getByRole("button", { name: "Export" }));
    expect(container.querySelector('[data-slot="export"]')).not.toBeNull();
  });

  it("shows a readable error when the video can't be loaded", async () => {
    getProject.mockRejectedValue(new Error("project not found"));
    render(<Story ws="garden" video="missing" />);
    await waitFor(() => expect(screen.getByText("project not found")).toBeTruthy());
  });
});
