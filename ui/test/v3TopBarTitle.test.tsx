// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Project } from "../src/types/project";

const { saveProject } = vi.hoisted(() => ({ saveProject: vi.fn() }));
vi.mock("../src/api", () => ({ api: { saveProject } }));

import TopBar from "../src/v3/story/TopBar";
import { adoptSaved, metaState, undo, useProjectStore } from "../src/store/useProjectStore";

function project(overrides: Partial<Project> = {}): Project {
  return {
    fps: 30, width: 1920, height: 1080, name: "demo-talk", revision: 5, schemaVersion: 3,
    tracks: [], scenes: [],
    ...overrides,
  };
}

function load(p: Project): ReturnType<typeof render> {
  adoptSaved(p, p.revision);
  return render(<TopBar video={p.name} onUndo={() => undo()} />);
}

beforeEach(() => {
  saveProject.mockReset();
  useProjectStore.setState({ project: null, revision: 0 });
  useProjectStore.temporal.getState().clear();
});
afterEach(cleanup);

describe("TopBar title", () => {
  it("shows the project title when set", () => {
    load(project({ title: "My Great Video" }));
    expect(screen.getByRole("heading", { name: "My Great Video" })).toBeTruthy();
  });

  it("falls back to the project name when no title is set", () => {
    load(project());
    expect(screen.getByRole("heading", { name: "demo-talk" })).toBeTruthy();
  });

  it("renames the video as one undo step and saves it", () => {
    saveProject.mockResolvedValue({ ok: true, revision: 6 });
    load(project());
    const heading = screen.getByRole("heading", { name: "demo-talk" });
    heading.textContent = "A Better Talk";
    fireEvent.blur(heading);

    const p = useProjectStore.getState().project!;
    expect(p.title).toBe("A Better Talk");
    expect(metaState().past.at(-1)?.label).toBe("Renamed video");
    expect(useProjectStore.temporal.getState().pastStates).toHaveLength(1);
    expect(saveProject).toHaveBeenCalledTimes(1);
    expect(saveProject).toHaveBeenCalledWith("demo-talk", p, 5);
  });

  it("commits on Enter", () => {
    saveProject.mockResolvedValue({ ok: true, revision: 6 });
    load(project());
    const heading = screen.getByRole("heading", { name: "demo-talk" });
    heading.focus();
    heading.textContent = "Renamed by Enter";
    fireEvent.keyDown(heading, { key: "Enter" });
    expect(useProjectStore.getState().project!.title).toBe("Renamed by Enter");
  });

  it("cancels on Escape without committing", () => {
    load(project({ title: "Original" }));
    const heading = screen.getByRole("heading", { name: "Original" });
    heading.focus();
    heading.textContent = "Discard me";
    fireEvent.keyDown(heading, { key: "Escape" });
    expect(heading.textContent).toBe("Original");
    expect(useProjectStore.getState().project!.title).toBe("Original");
    expect(saveProject).not.toHaveBeenCalled();
  });

  it("clears the title on an empty commit, falling back to the name", () => {
    saveProject.mockResolvedValue({ ok: true, revision: 6 });
    load(project({ title: "Original" }));
    const heading = screen.getByRole("heading", { name: "Original" });
    heading.textContent = "   ";
    fireEvent.blur(heading);
    expect(useProjectStore.getState().project!.title).toBeUndefined();
    expect(saveProject).toHaveBeenCalledTimes(1);
  });

  it("ignores an unchanged title", () => {
    load(project({ title: "Original" }));
    const heading = screen.getByRole("heading", { name: "Original" });
    heading.textContent = "Original";
    fireEvent.blur(heading);
    expect(saveProject).not.toHaveBeenCalled();
    expect(useProjectStore.temporal.getState().pastStates).toHaveLength(0);
  });

  it("can be undone back to the previous title", () => {
    saveProject.mockResolvedValue({ ok: true, revision: 6 });
    load(project());
    const heading = screen.getByRole("heading", { name: "demo-talk" });
    heading.textContent = "A Better Talk";
    fireEvent.blur(heading);
    expect(useProjectStore.getState().project!.title).toBe("A Better Talk");

    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(useProjectStore.getState().project!.title).toBeUndefined();
  });
});

describe("TopBar kind label", () => {
  it("names a footage video", () => {
    load(project());
    expect(screen.getByText("Footage")).toBeTruthy();
  });

  it("names a script video by its timing source", () => {
    load(project({ narration: "estimated" }));
    expect(screen.getByText("Animation · estimated timing")).toBeTruthy();
    cleanup();
    load(project({ narration: "recorded" }));
    expect(screen.getByText("Animation · narrated")).toBeTruthy();
  });
});
