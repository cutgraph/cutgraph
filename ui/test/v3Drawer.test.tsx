// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { TranscriptWord } from "../src/api";
import type { Asset, WorkspaceDetail } from "../src/v3/api";
import type { Clip, GraphicItem, Project } from "../src/types/project";

const MEDIA = "data/source/talk.mp4";
const words: TranscriptWord[] = Array.from({ length: 20 }, (_, i) => ({ w: `w${i}`, start: i * 0.5, end: i * 0.5 + 0.4, segIdx: 0 }));
const saveProject = vi.fn();

vi.mock("../src/api", () => ({
  api: {
    transcription: () => Promise.resolve({ state: "ready", job: null }),
    getTranscript: () => Promise.resolve({ language: "en", duration: 10, words, segments: [], fillers: [] }),
    saveProject: (p: string, project: Project, base: number) => saveProject(p, project, base),
    thumb: (slug: string) => `/media/thumb/${slug}`,
  },
}));

function asset(id: string, name: string, kind: Asset["kind"], extra: Partial<Asset> = {}): Asset {
  const ext = name.slice(name.lastIndexOf("."));
  return { id: id.repeat(16), sha256: "", name, kind, file: `${id.repeat(16)}${ext}`, bytes: 1, tags: [], createdAt: "", ...extra };
}

const LIBRARIES: Record<string, Asset[]> = {
  "garden-1a2b3c": [
    asset("1", "plate.png", "image"),
    asset("2", "seedling.mp4", "video", { durationS: 3 }),
    asset("3", "bed.wav", "audio", { durationS: 45 }),
    asset("4", "whoosh.wav", "audio", { durationS: 1 }),
  ],
  brand: [asset("5", "theme.mp3", "audio", { durationS: 5, tags: ["music"] })],
};

vi.mock("../src/v3/api", () => ({
  v3api: {
    workspace: (id: string): Promise<WorkspaceDetail> => Promise.resolve({
      workspace: { id, name: id === "brand" ? "Brand" : "garden", createdAt: "", updatedAt: "", videoCount: 1, assetCount: 0 },
      videos: [],
      assets: LIBRARIES[id] ?? [],
    }),
    assetThumbUrl: (ws: string, id: string) => `/api/workspaces/${ws}/assets/${id}/thumb`,
    assetFileUrl: (ws: string, id: string) => `/api/workspaces/${ws}/assets/${id}/file`,
  },
}));

import Drawer from "../src/v3/story/Drawer";
import { resetStory, selectCue, setCaret, useStory } from "../src/v3/story/storyStore";
import { adoptSaved, undo, useProjectStore } from "../src/store/useProjectStore";
import { assetsForTab, assetTab, type LibraryAsset } from "../src/v3/story/drawer/library";
import { decodePayload, DRAWER_MIME, dropWordIdx, encodePayload } from "../src/v3/story/drawer/wordDrop";

// jsdom draws nothing; the shot card and the default rig tile paint onto canvases.
HTMLCanvasElement.prototype.getContext = vi.fn(() => null) as unknown as typeof HTMLCanvasElement.prototype.getContext;

function doc(): Project {
  return {
    schemaVersion: 3, name: "talk", fps: 30, width: 1920, height: 1080, revision: 1, workspaceId: "garden-1a2b3c",
    tracks: [{ id: "speech", kind: "speech", clips: [{ id: "src", media: { source: MEDIA, proxy: null }, srcInF: 0, srcOutF: 300, tlStartF: 0 }] }],
  };
}

function Story() {
  return (
    <div className="v3">
      <section className="story">
        <div className="pane" id="story-pane">
          <p>{words.map((w, i) => <span key={i} data-word-idx={i}><b>{w.w}</b> </span>)}</p>
        </div>
        <aside className="side"><Drawer /></aside>
      </section>
    </div>
  );
}

function drag(type: string, target: Element, data: Record<string, string>): Event {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperty(event, "dataTransfer", {
    value: { types: Object.keys(data), getData: (k: string) => data[k] ?? "", dropEffect: "none" },
  });
  act(() => { target.dispatchEvent(event); });
  return event;
}

function clips(kind: string): Clip[] {
  return useProjectStore.getState().project!.tracks.filter((t) => t.kind === kind).flatMap((t) => t.clips ?? []);
}

function word(i: number): Element {
  return document.querySelector(`[data-word-idx="${i}"]`)!;
}

beforeEach(() => {
  saveProject.mockReset();
  saveProject.mockImplementation((_p: string, project: Project, base: number) => Promise.resolve({ ok: true, project, revision: base + 1 }));
  resetStory();
  adoptSaved(doc(), 1);
});
afterEach(cleanup);

describe("drawer library tabs", () => {
  const entries: LibraryAsset[] = Object.entries(LIBRARIES).flatMap(([workspaceId, list]) =>
    list.map((a) => ({ workspaceId, workspaceName: workspaceId, asset: a })));

  it("files video and images under B-roll and splits audio by tag, then length", () => {
    expect(entries.map((e) => [e.asset.name, assetTab(e.asset)])).toEqual([
      ["plate.png", "broll"], ["seedling.mp4", "broll"], ["bed.wav", "music"], ["whoosh.wav", "sfx"], ["theme.mp3", "music"],
    ]);
    expect(assetsForTab(entries, "sfx").map((e) => e.asset.name)).toEqual(["whoosh.wav"]);
    expect(assetsForTab(entries, "graphics")).toEqual([]);
    expect(assetTab(asset("6", "sting.wav", "audio", { durationS: 30, tags: ["SFX"] }))).toBe("sfx");
  });

  it("shows the video's workspace plus the brand kit, one tab at a time", async () => {
    render(<Story />);
    expect(await screen.findByText("plate.png")).toBeTruthy();
    expect(screen.getByText("seedling.mp4")).toBeTruthy();
    expect(screen.queryByText("bed.wav")).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "Music" }));
    expect(useStory.getState().drawerTab).toBe("music");
    expect(screen.getByText("bed.wav")).toBeTruthy();
    expect(screen.getByText("theme.mp3")).toBeTruthy();
    expect(screen.getByText("0:05 · Brand")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Sounds" }));
    expect(screen.getByText("whoosh.wav")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Graphics" }));
    expect(screen.getAllByText("Template")).toHaveLength(5);
  });

  it("plays a video clip's tile while hovered, over its thumbnail, and rests on the thumbnail again", async () => {
    render(<Story />);
    const clip = (await screen.findByText("seedling.mp4")).closest("button")!;
    const id = "2".repeat(16);
    expect(clip.querySelector("video")).toBeNull();
    fireEvent.mouseEnter(clip);
    const video = clip.querySelector("video")!;
    expect(video.getAttribute("src")).toBe(`/api/workspaces/garden-1a2b3c/assets/${id}/file`);
    expect(video.getAttribute("poster")).toBe(`/api/workspaces/garden-1a2b3c/assets/${id}/thumb`);
    expect(video.muted && video.loop && video.autoplay).toBe(true);
    fireEvent.mouseLeave(clip);
    expect(clip.querySelector("video")).toBeNull();
    expect(clip.querySelector("img")?.getAttribute("src")).toBe(`/api/workspaces/garden-1a2b3c/assets/${id}/thumb`);

    const photo = screen.getByText("plate.png").closest("button")!;
    fireEvent.mouseEnter(photo);
    expect(photo.querySelector("video")).toBeNull();
    expect(photo.querySelector("img")).toBeTruthy();
  });
});

describe("dropping tiles onto words", () => {
  it("resolves the target word from data-word-idx and ignores anything else", () => {
    const host = document.createElement("div");
    host.innerHTML = '<span data-word-idx="12"><b id="inner">w</b></span><span data-word-idx="x" id="bad"></span><i id="none"></i>';
    document.body.append(host);
    expect(dropWordIdx(host.querySelector("#inner"))).toBe(12);
    expect(dropWordIdx(host.querySelector("#bad"))).toBeNull();
    expect(dropWordIdx(host.querySelector("#none"))).toBeNull();
    expect(dropWordIdx(null)).toBeNull();
    host.remove();
    expect(decodePayload(encodePayload({ type: "graphic", componentId: "callout" }))).toEqual({ type: "graphic", componentId: "callout" });
    expect(decodePayload('{"type":"graphic","componentId":"script"}')).toBeNull();
    expect(decodePayload("not json")).toBeNull();
  });

  it("inserts a dragged asset on the word it lands on, as one undo step, and opens its cue card", async () => {
    render(<Story />);
    const tile = (await screen.findByText("plate.png")).closest("button")!;
    const setData = vi.fn();
    fireEvent.dragStart(tile, { dataTransfer: { setData, effectAllowed: "none" } });
    const [[mime, raw]] = setData.mock.calls;
    expect(mime).toBe(DRAWER_MIME);
    const data = { [DRAWER_MIME]: raw as string };

    const over = drag("dragover", word(4).firstElementChild!, data);
    expect(over.defaultPrevented).toBe(true);
    expect(word(4).classList.contains("word-drop")).toBe(true);
    drag("drop", word(4).firstElementChild!, data);
    expect(word(4).classList.contains("word-drop")).toBe(false);

    const [clip] = clips("video");
    expect(clip).toMatchObject({
      media: { source: `workspaces/garden-1a2b3c/library/${"1".repeat(16)}.png` }, tlStartF: 60,
      anchor: { mode: "word", ref: { media: MEDIA, wordIdx: 4 }, offsetF: 0 },
    });
    expect(useStory.getState().cueId).toBe(clip.id);
    expect(useProjectStore.getState().playheadF).toBe(60);
    await waitFor(() => expect(saveProject).toHaveBeenCalledTimes(1));
    expect(screen.getByText(/starts on “w4”/)).toBeTruthy();

    act(() => undo());
    expect(clips("video")).toEqual([]);
  });

  it("ignores drags that aren't drawer tiles and drops that miss a word", async () => {
    render(<Story />);
    await screen.findByText("plate.png");
    expect(drag("dragover", word(2), { "text/plain": "hello" }).defaultPrevented).toBe(false);
    drag("drop", document.getElementById("story-pane")!, { [DRAWER_MIME]: encodePayload({ type: "graphic", componentId: "callout" }) });
    expect(useProjectStore.getState().project!.tracks).toHaveLength(1);
  });

  it("clicking a tile inserts at the caret word", async () => {
    render(<Story />);
    await screen.findByText("plate.png");
    act(() => setCaret(12));
    fireEvent.click(screen.getByRole("tab", { name: "Graphics" }));
    fireEvent.click(screen.getByText("Lower Third").closest("button")!);
    const item = useProjectStore.getState().project!.tracks.find((t) => t.kind === "text")!.items![0] as GraphicItem;
    expect(item).toMatchObject({ componentId: "lower-third", tlStartF: 180, anchor: { mode: "word", ref: { wordIdx: 12 } } });
  });
});

describe("the cue card", () => {
  async function insertedBroll(): Promise<string> {
    render(<Story />);
    const tile = (await screen.findByText("plate.png")).closest("button")!;
    act(() => setCaret(4));
    fireEvent.click(tile);
    return clips("video")[0].id;
  }

  it("swaps a clip for another asset of the same kind, keeping its word", async () => {
    const id = await insertedBroll();
    const card = document.querySelector(".cue-card") as HTMLElement;
    expect(within(card).getByText("Swap for")).toBeTruthy();
    expect(within(card).queryByText("bed.wav")).toBeNull();
    fireEvent.click(within(card).getByText("seedling.mp4").closest("button")!);
    expect(clips("video")[0]).toMatchObject({
      id, media: { source: `workspaces/garden-1a2b3c/library/${"2".repeat(16)}.mp4` }, srcOutF: 90, tlStartF: 60,
      anchor: { mode: "word", ref: { wordIdx: 4 } },
    });
    expect(screen.getByRole("heading", { name: "seedling.mp4" })).toBeTruthy();
  });

  it("removes the element and returns to the library", async () => {
    await insertedBroll();
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(clips("video")).toEqual([]);
    expect(useStory.getState().cueId).toBeNull();
    expect(screen.getByText("Add to your video")).toBeTruthy();
  });

  it("edits a graphic's text fields and goes back to the library", async () => {
    render(<Story />);
    await screen.findByText("plate.png");
    act(() => setCaret(2));
    fireEvent.click(screen.getByRole("tab", { name: "Graphics" }));
    fireEvent.click(screen.getByText("Lower Third").closest("button")!);
    const title = screen.getByRole("textbox", { name: "Graphic title" }) as HTMLInputElement;
    fireEvent.change(title, { target: { value: "Coach Dana" } });
    fireEvent.blur(title);
    const item = useProjectStore.getState().project!.tracks.find((t) => t.kind === "text")!.items![0] as GraphicItem;
    expect(item.props).toMatchObject({ title: "Coach Dana" });
    fireEvent.click(screen.getByRole("button", { name: "Back to library" }));
    expect(useStory.getState().cueId).toBeNull();
    act(() => selectCue(item.id));
    expect((screen.getByRole("textbox", { name: "Graphic title" }) as HTMLInputElement).value).toBe("Coach Dana");
  });
});

describe("the shot card", () => {
  function docWithShot(): Project {
    return {
      ...doc(),
      scenes: [
        { id: "s1", title: "Open", role: "Hook", startWord: 0 },
        {
          id: "s2", title: "Middle", role: "Beat", startWord: 10,
          shot: { mode: "animation", pose: "point", expression: "happy", move: "wave", headline: "Shipped it" },
        },
      ],
    };
  }

  it("shows the animated scene under the playhead, and opens its shot", async () => {
    adoptSaved(docWithShot(), 1);
    useProjectStore.setState({ playheadF: 200 });
    render(<Story />);
    await screen.findByText("plate.png");
    expect(screen.getByText("Scene 2 shot")).toBeTruthy();
    expect(screen.getByText("Lens · Point · Happy · Wave")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Open shot" }));
    expect(useStory.getState().shotSceneId).toBe("s2");
  });

  it("shows no shot card for the footage scene under the playhead", async () => {
    adoptSaved(docWithShot(), 1);
    useProjectStore.setState({ playheadF: 0 });
    render(<Story />);
    await screen.findByText("plate.png");
    expect(screen.queryByText(/shot$/)).toBeNull();
  });
});

describe("the Character tab", () => {
  const scenes: NonNullable<Project["scenes"]> = [
    { id: "s1", title: "Open", role: "Hook", startWord: 0 },
    { id: "s2", title: "Middle", role: "Beat", startWord: 10 },
  ];
  const shots = () => useProjectStore.getState().project!.scenes!.map((s) => s.shot ?? null);

  it("offers the cameo of the scene at the cursor, as ✦ Character does, and adds it as one undo step", async () => {
    adoptSaved({ ...doc(), scenes }, 1);
    render(<Story />);
    await screen.findByText("plate.png");
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["B-roll", "Graphics", "Music", "Sounds", "Character"]);
    fireEvent.click(screen.getByRole("tab", { name: "Character" }));
    expect(useStory.getState().drawerTab).toBe("anim");
    act(() => setCaret(12));
    const tile = screen.getByRole("button", { name: /Lens cameo/ });
    expect(tile.textContent).toContain("Springs in on “w12”");
    fireEvent.click(tile);
    expect(shots()).toEqual([null, { mode: "cameo", cueWord: 12 }]);
    expect(useProjectStore.getState().playheadF).toBe(180);
    await waitFor(() => expect(saveProject).toHaveBeenCalledTimes(1));
    act(() => undo());
    expect(shots()).toEqual([null, null]);
  });

  it("drops the cameo on the word it lands on", async () => {
    adoptSaved({ ...doc(), scenes }, 1);
    act(() => useStory.setState({ drawerTab: "anim" }));
    render(<Story />);
    const tile = (await screen.findByText("Lens cameo")).closest("button")!;
    const setData = vi.fn();
    fireEvent.dragStart(tile, { dataTransfer: { setData, effectAllowed: "none" } });
    const [[mime, raw]] = setData.mock.calls;
    expect([mime, decodePayload(raw as string)]).toEqual([DRAWER_MIME, { type: "cameo" }]);
    drag("drop", word(3).firstElementChild!, { [DRAWER_MIME]: raw as string });
    expect(shots()).toEqual([{ mode: "cameo", cueWord: 3 }, null]);
  });

  it("has no Character tab on a script video, whose scenes are already animated", async () => {
    adoptSaved({ ...doc(), scenes, narration: "estimated" }, 1);
    act(() => useStory.setState({ drawerTab: "anim" }));
    render(<Story />);
    await screen.findByText("plate.png");
    expect(screen.queryByRole("tab", { name: "Character" })).toBeNull();
    expect(screen.getByRole("tab", { name: "B-roll" }).getAttribute("aria-selected")).toBe("true");
  });
});
