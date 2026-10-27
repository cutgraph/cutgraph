// @vitest-environment jsdom
// The first-run 3-step coach tour (Script, player, Ask), skippable, remembered per
// browser via localStorage using the `cg3-coach` key.
import { forwardRef, useImperativeHandle } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
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
vi.mock("../src/player", () => ({
  FramePlayer: forwardRef((_props, ref) => {
    useImperativeHandle(ref, () => ({
      isSoundBlocked: () => false,
      enableSound: () => {},
      readFrame: () => 0, seek: () => {}, listen: () => {}, unlisten: () => {},
      isRunning: () => false, play: () => {}, pause: () => {},
    }));
    return <div data-testid="mock-player" />;
  }),
}));
vi.mock("../src/preview/ProjectComposition", () => ({ ProjectComposition: () => null }));

import Story from "../src/v3/Story";
import Coach from "../src/v3/story/Coach";
import { openExport, openShot } from "../src/v3/story/storyStore";
import { useProjectStore } from "../src/store/useProjectStore";

const doc = (name: string) => ({ name, fps: 30, width: 1920, height: 1080, tracks: [], scenes: [] }) as unknown as Project;

beforeEach(() => {
  getProject.mockReset();
  getProject.mockResolvedValue({ project: doc("herb-care"), revision: 1 });
  useProjectStore.setState({ project: null, revision: 0 });
  localStorage.clear();
});
afterEach(cleanup);

describe("first-run tour", () => {
  it("walks Script, player and Ask, then remembers it was seen", async () => {
    render(<Story ws="garden" video="herb-care" />);
    const tour = await screen.findByRole("dialog", { name: "Guided tour" }, { timeout: 3000 });
    expect(tour.textContent).toContain("This is your script.");
    expect(tour.textContent).toContain("1 / 3");

    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByRole("dialog", { name: "Guided tour" }).textContent).toContain("Your video, always in sync");

    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    const last = screen.getByRole("dialog", { name: "Guided tour" });
    expect(last.textContent).toContain("Ask the Assistant for anything");
    expect(screen.getByRole("button", { name: "Got it" })).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Got it" }));
    expect(screen.queryByRole("dialog", { name: "Guided tour" })).toBeNull();
    expect(localStorage.getItem("cg3-coach")).toBe("done");
  });

  it("can be skipped from any step, still remembered", async () => {
    render(<Story ws="garden" video="herb-care" />);
    await screen.findByRole("dialog", { name: "Guided tour" }, { timeout: 3000 });
    await userEvent.click(screen.getByRole("button", { name: "Skip" }));
    expect(screen.queryByRole("dialog", { name: "Guided tour" })).toBeNull();
    expect(localStorage.getItem("cg3-coach")).toBe("done");
  });

  it("never appears once it has been seen", async () => {
    localStorage.setItem("cg3-coach", "done");
    render(<Story ws="garden" video="herb-care" />);
    await screen.findByRole("heading", { name: "herb-care" });
    await new Promise((r) => setTimeout(r, 450));
    expect(screen.queryByRole("dialog", { name: "Guided tour" })).toBeNull();
  });
});

describe("first-run tour and the sheets", () => {
  function Stage({ active }: { active: boolean }) {
    return (
      <>
        <div id="story-pane" />
        <div className="player" />
        <form className="askform" />
        <Coach active={active} />
      </>
    );
  }
  const tour = () => screen.queryByRole("dialog", { name: "Guided tour" });

  it("steps aside while a sheet is open and picks up at the same step when it closes", async () => {
    const { rerender } = render(<Stage active />);
    await screen.findByRole("dialog", { name: "Guided tour" }, { timeout: 3000 });
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(tour()!.textContent).toContain("2 / 3");

    rerender(<Stage active={false} />);
    expect(tour()).toBeNull();
    await new Promise((r) => setTimeout(r, 450));
    expect(tour()).toBeNull();

    rerender(<Stage active />);
    await waitFor(() => expect(tour()?.textContent).toContain("2 / 3"));
    await new Promise((r) => setTimeout(r, 450));
    expect(tour()!.textContent).toContain("2 / 3");
    expect(localStorage.getItem("cg3-coach")).toBeNull();
  });

  it("stays ended when Skip comes right after a sheet closes", async () => {
    const { rerender } = render(<Stage active />);
    await screen.findByRole("dialog", { name: "Guided tour" }, { timeout: 3000 });
    rerender(<Stage active={false} />);
    rerender(<Stage active />);
    await waitFor(() => expect(tour()).not.toBeNull());
    await userEvent.click(screen.getByRole("button", { name: "Skip" }));
    await new Promise((r) => setTimeout(r, 450));
    expect(tour()).toBeNull();
  });

  it("waits for a sheet open on arrival to close before it starts", async () => {
    const { rerender } = render(<Stage active={false} />);
    await new Promise((r) => setTimeout(r, 450));
    expect(tour()).toBeNull();
    rerender(<Stage active />);
    expect((await screen.findByRole("dialog", { name: "Guided tour" }, { timeout: 3000 })).textContent).toContain("1 / 3");
  });

  it("Story hides it behind the Shot view and the Export sheet", async () => {
    render(<Story ws="garden" video="herb-care" />);
    await screen.findByRole("dialog", { name: "Guided tour" }, { timeout: 3000 });
    act(() => openShot("scene-x"));
    expect(tour()).toBeNull();
    act(() => openShot(null));
    await waitFor(() => expect(tour()).not.toBeNull());
    act(() => openExport(true));
    expect(tour()).toBeNull();
  });
});
