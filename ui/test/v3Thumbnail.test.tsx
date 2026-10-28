// @vitest-environment jsdom
// Thumbnails: the preview resolves a video's thumbnail exactly as the export does
// (tests/fixtures/thumbnail_parity.json, tests/test_thumbnail.py), and the Export sheet's Thumbnail
// section and editor edit it one undo step at a time and download the PNGs the studio renders.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { resolvePoster, type ProjectPoster } from "motion-core";
import type { Project } from "../src/types/project";

const { saveProject } = vi.hoisted(() => ({ saveProject: vi.fn() }));
vi.mock("../src/api", () => ({ api: { saveProject, getProject: vi.fn(), createJob: vi.fn(), approveJob: vi.fn(), getComputeJob: vi.fn() } }));
vi.mock("../src/toast", () => ({ toast: () => {} }));
vi.mock("../src/v3/story/export/jobEvents", () => ({ subscribeJobEvents: () => () => {} }));

import ExportSheet from "../src/v3/story/ExportSheet";
import { undo, useProjectStore } from "../src/store/useProjectStore";
import { parseCast, type Cast } from "../src/v3/library/cast";
import { resetCast, scopeKey, useCastStore } from "../src/v3/library/castStore";
import { resetStory, useStory } from "../src/v3/story/storyStore";
import { posterOf, thumbnailOf, withThumbnail } from "../src/v3/story/thumbnail/thumbnail";

const fx = JSON.parse(readFileSync(resolve(__dirname, "../../tests/fixtures/thumbnail_parity.json"), "utf-8")) as {
  cast: unknown;
  cases: { name: string; format: string; project: Project; request: { width: number; height: number; poster: ProjectPoster } }[];
};
const cast = parseCast(fx.cast) as Cast;

describe("thumbnail parity with the export", () => {
  it("draws exactly the poster the Node renderer draws from the export's request", () => {
    for (const c of fx.cases) expect(posterOf(c.project, cast), c.name).toEqual(resolvePoster(c.request.poster));
  });

  it("fills in the video's defaults: its title, its first animated scene's lead and the rest of its cast behind", () => {
    const [defaults] = fx.cases;
    expect(thumbnailOf(defaults.project, cast)).toEqual({
      headline: "Museum Visit", eyebrow: "", character: "default-character",
      ensemble: [{ character: "notes-guest" }, { character: "visitor-reader" }, { character: "reviewer" }],
    });
  });
});

describe("withThumbnail", () => {
  const doc = { name: "v", fps: 30, width: 1920, height: 1080, revision: 1, schemaVersion: 3, tracks: [] } as Project;

  it("sets the thumbnail on the first edit, removes emptied fields, and changes nothing for a no-op", () => {
    const set = withThumbnail(doc, { headline: "Museum Visit" });
    expect(set.thumbnail).toEqual({ headline: "Museum Visit" });
    expect(withThumbnail(set, { headline: "Museum Visit" })).toBe(set);
    expect(withThumbnail(set, { headline: "", eyebrow: null }).thumbnail).toEqual({});
  });
});

const PROJECT: Project = {
  name: "sample-notes", title: "Museum Visit", fps: 30, width: 1920, height: 1080, revision: 3, schemaVersion: 3, workspaceId: "garden",
  tracks: [], scenes: [{ id: "s1", title: "Open", role: "Hook", startWord: 0, shot: { mode: "animation", character: "default-character" } }],
};

describe("the Thumbnail section and editor", () => {
  let fetched: string[];

  beforeEach(() => {
    resetStory();
    resetCast();
    fetched = [];
    let revision = 3;
    saveProject.mockReset().mockImplementation(async () => ({ ok: true, revision: ++revision }));
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      fetched.push(`${init?.method ?? "GET"} ${String(input)} ${init?.body ?? ""}`);
      const format = JSON.parse(String(init?.body ?? "{}")).format as string;
      return new Response(JSON.stringify({ format, width: 1280, height: 720, url: `/media/thumbnail/${"a".repeat(64)}.png` }),
        { status: 200, headers: { "Content-Type": "application/json" } });
    }));
    HTMLCanvasElement.prototype.getContext = vi.fn(() => null) as unknown as typeof HTMLCanvasElement.prototype.getContext;
    HTMLAnchorElement.prototype.click = vi.fn();
    useCastStore.setState({ scopes: { [scopeKey({ workspace: "garden" })]: { cast, status: "ready", error: null } } });
    useProjectStore.setState({ project: PROJECT, revision: 3, playheadF: 0, lastWarnings: [] });
    useProjectStore.temporal.getState().clear();
    useStory.setState({ exportOpen: true });
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  const thumb = () => useProjectStore.getState().project?.thumbnail;
  const history = () => useProjectStore.temporal.getState().pastStates.length;

  it("previews the default thumbnail at 16:9 with a 9:16 toggle, and downloads each size's PNG from the studio", async () => {
    render(<ExportSheet />);
    const section = screen.getByRole("region", { name: "Thumbnail" });
    expect(within(section).getByRole("img", { name: "16:9 thumbnail preview" })).toBeTruthy();
    fireEvent.click(within(section).getByRole("button", { name: "9:16" }));
    expect(within(section).getByRole("img", { name: "9:16 thumbnail preview" })).toBeTruthy();
    fireEvent.click(within(section).getByRole("button", { name: "Download PNG · 9:16" }));
    await waitFor(() => expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledTimes(1));
    expect(fetched).toEqual([`POST /api/project/sample-notes/thumbnail/render {"format":"9:16"}`]);
    expect(thumb()).toBeUndefined();
  });

  it("replaces a pending title draft with the stored title after an undo", async () => {
    render(<ExportSheet />);
    fireEvent.click(screen.getByRole("button", { name: "Edit thumbnail" }));
    const editor = await screen.findByRole("dialog", { name: "Thumbnail" });
    const title = within(editor).getByRole("textbox", { name: "Title" }) as HTMLInputElement;
    fireEvent.change(title, { target: { value: "Museum Visit" } });
    fireEvent.blur(title);
    expect(thumb()?.headline).toBe("Museum Visit");
    fireEvent.change(title, { target: { value: "Pending" } });
    act(() => undo());
    expect(thumb()?.headline).toBeUndefined();
    expect(title.value).toBe("");
  });

  it("edits the title, the lead and the ensemble, each one undo step saved at once, and Escape closes the editor first", async () => {
    render(<ExportSheet />);
    fireEvent.click(screen.getByRole("button", { name: "Edit thumbnail" }));
    const editor = await screen.findByRole("dialog", { name: "Thumbnail" });

    const title = within(editor).getByRole("textbox", { name: "Title" });
    fireEvent.change(title, { target: { value: "Museum Visit" } });
    fireEvent.blur(title);
    expect(thumb()).toEqual({ headline: "Museum Visit" });
    expect(history()).toBe(1);
    await waitFor(() => expect(saveProject).toHaveBeenCalledTimes(1));

    fireEvent.click(within(within(editor).getByRole("group", { name: "Lead pose" })).getByRole("button", { name: "Wave" }));
    expect(thumb()?.pose).toBe("wave");
    fireEvent.click(within(within(editor).getByRole("group", { name: "Behind 2 pose" })).getByRole("button", { name: "Hands on hips" }));
    expect(thumb()?.ensemble).toEqual([{ character: "notes-guest" }, { character: "visitor-reader", pose: "hips" }, { character: "reviewer" }]);
    fireEvent.click(within(editor).getAllByRole("button", { name: "Remove" })[0]);
    expect(thumb()?.ensemble).toEqual([{ character: "visitor-reader", pose: "hips" }, { character: "reviewer" }]);
    expect(history()).toBe(4);

    act(() => undo());
    expect(thumb()?.ensemble).toHaveLength(3);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Thumbnail" })).toBeNull();
    expect(useStory.getState().exportOpen).toBe(true);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(useStory.getState().exportOpen).toBe(false);
  });

  it("labels a member over its head: its metal by default, another colour on a click, gone when cleared", async () => {
    render(<ExportSheet />);
    fireEvent.click(screen.getByRole("button", { name: "Edit thumbnail" }));
    const editor = await screen.findByRole("dialog", { name: "Thumbnail" });
    const label = within(editor).getByRole("textbox", { name: "Behind 2 label" });
    fireEvent.change(label, { target: { value: "NOTES" } });
    fireEvent.blur(label);
    expect(thumb()?.ensemble?.[1]).toEqual({ character: "visitor-reader", badge: { text: "NOTES", tone: "accent" } });
    fireEvent.click(within(within(editor).getByRole("group", { name: "Behind 2 label colour" })).getByRole("button", { name: "Paper" }));
    expect(thumb()?.ensemble?.[1]?.badge).toEqual({ text: "NOTES", tone: "paper" });
    const cleared = within(editor).getByRole("textbox", { name: "Behind 2 label" });
    fireEvent.change(cleared, { target: { value: "" } });
    fireEvent.blur(cleared);
    expect(thumb()?.ensemble?.[1]).toEqual({ character: "visitor-reader" });
    expect(history()).toBe(3);
  });

  it("resets a thumbnail back to the video's defaults as one undo step", async () => {
    useProjectStore.setState({ project: { ...PROJECT, thumbnail: { headline: "Old", backdrop: "work" } } });
    render(<ExportSheet />);
    fireEvent.click(screen.getByRole("button", { name: "Edit thumbnail" }));
    fireEvent.click(await screen.findByRole("button", { name: "Reset" }));
    expect(thumb()).toBeUndefined();
    act(() => undo());
    expect(thumb()).toEqual({ headline: "Old", backdrop: "work" });
  });
});
