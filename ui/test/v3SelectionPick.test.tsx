// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import type { TranscriptDoc, TranscriptWord } from "../src/api";
import type { Asset, WorkspaceDetail } from "../src/v3/api";
import type { Clip, GraphicItem, Project } from "../src/types/project";

const MEDIA = "data/source/talk.mp4";
// Word i spans [0.5i, 0.5i+0.4) s, so it starts on source frame 15i; words 30–33 read "I want you to".
const PHRASE = ["I", "want", "you", "to"];
const WORDS: TranscriptWord[] = Array.from({ length: 40 }, (_, i) => ({
  w: i >= 30 && i <= 33 ? PHRASE[i - 30] : `w${i}`, start: i * 0.5, end: i * 0.5 + 0.4, segIdx: 0,
}));

const { saveProject } = vi.hoisted(() => ({ saveProject: vi.fn() }));
vi.mock("../src/api", () => ({
  api: {
    saveProject: (p: string, project: Project, base: number) => saveProject(p, project, base),
    thumb: (slug: string) => `/media/thumb/${slug}`,
  },
}));
vi.mock("../src/transcript/useTranscript", () => ({
  useTranscript: () => ({
    doc: { language: "en", duration: 20, words: WORDS, segments: [], fillers: [] } as TranscriptDoc,
    lifecycle: { state: "ready", job: null }, loading: false, error: null, refresh: () => {},
  }),
}));

function asset(id: string, name: string, kind: Asset["kind"], extra: Partial<Asset> = {}): Asset {
  const ext = name.slice(name.lastIndexOf("."));
  return { id: id.repeat(16), sha256: "", name, kind, file: `${id.repeat(16)}${ext}`, bytes: 1, tags: [], createdAt: "", ...extra };
}

const LIBRARY: Asset[] = [
  asset("1", "city.png", "image"),
  asset("2", "coffee.png", "image"),
  asset("3", "team.png", "image"),
  asset("4", "wish-list.png", "image", { tags: ["want"] }),
  asset("5", "seedling.mp4", "video", { durationS: 1.5 }),
  asset("6", "bed.wav", "audio", { durationS: 45 }),
  asset("7", "whoosh.wav", "audio", { durationS: 1 }),
];

vi.mock("../src/v3/api", () => ({
  v3api: {
    workspace: (id: string): Promise<WorkspaceDetail> => Promise.resolve({
      workspace: { id, name: id === "brand" ? "Brand" : "garden", createdAt: "", updatedAt: "", videoCount: 1, assetCount: 0 },
      videos: [],
      assets: id === "garden" ? LIBRARY : [],
    }),
    assetThumbUrl: (ws: string, id: string) => `/api/workspaces/${ws}/assets/${id}/thumb`,
  },
}));

import ScriptLens from "../src/v3/story/lenses/ScriptLens";
import SourceLens from "../src/v3/story/lenses/SourceLens";
import Drawer from "../src/v3/story/Drawer";
import ToastHost from "../src/v3/Toast";
import { resetStory, useStory } from "../src/v3/story/storyStore";
import { adoptSaved, useProjectStore } from "../src/store/useProjectStore";
import { rankAssets, terms } from "../src/v3/story/drawer/rank";
import { cueTarget } from "../src/v3/story/drawer/place";

function doc(): Project {
  return {
    schemaVersion: 3, name: "talk", fps: 30, width: 1920, height: 1080, revision: 1, workspaceId: "garden",
    tracks: [{ id: "speech", kind: "speech", clips: [{ id: "src", media: { source: MEDIA, proxy: null }, srcInF: 0, srcOutF: 600, tlStartF: 0 }] }],
    scenes: [
      { id: "s1", title: "Open", role: "Hook", startWord: 0 },
      { id: "s2", title: "Middle", role: "Beat", startWord: 20 },
    ],
  };
}

function Story() {
  return (
    <div className="v3">
      <section className="story">
        <div className="pane" id="story-pane"><ScriptLens /></div>
        <aside className="side"><Drawer /></aside>
      </section>
      <ToastHost />
    </div>
  );
}

const word = (i: number): HTMLElement => document.querySelector(`[data-word-idx="${i}"]`) as HTMLElement;

function click(i: number): void {
  fireEvent.mouseDown(word(i), { button: 0 });
  fireEvent.mouseUp(window);
}

function select(a: number, b: number): void {
  fireEvent.mouseDown(word(a), { button: 0 });
  fireEvent.mouseOver(word(b));
  fireEvent.mouseUp(window);
}

function elements(): (Clip | GraphicItem)[] {
  return useProjectStore.getState().project!.tracks.flatMap((t) => [...(t.clips ?? []), ...((t.items ?? []) as GraphicItem[])]);
}

function added(id: string): Clip & GraphicItem {
  return elements().find((e) => e.id === id) as Clip & GraphicItem;
}

// The script word a cue chip sits in front of (skipping any other chips there).
function chipWord(id: string): number {
  let el = document.querySelector(`[data-cue="${id}"]`)?.nextElementSibling ?? null;
  while (el && !(el as HTMLElement).dataset.wordIdx) el = el.nextElementSibling;
  return Number((el as HTMLElement).dataset.wordIdx);
}

function pick(button: string): HTMLElement {
  fireEvent.click(screen.getByRole("button", { name: button }));
  return screen.getByRole("dialog", { name: "Pick" });
}

beforeEach(() => {
  saveProject.mockReset();
  saveProject.mockImplementation((_p: string, project: Project, base: number) => Promise.resolve({ ok: true, project, revision: base + 1 }));
  resetStory();
  useProjectStore.temporal.getState().clear();
  adoptSaved(doc(), 1);
});
afterEach(cleanup);

describe("selection-bar cues land on the selection, not the caret", () => {
  it("places B-roll, Graphic, Music and Sound at the selection's first word, B-roll spanning it", async () => {
    render(<Story />);
    await screen.findByText("city.png");
    click(2);
    expect(useStory.getState().caretWord).toBe(2);

    select(30, 33);
    let pop = pick("▣ B-roll");
    expect(within(pop).getByText("Claude suggests")).toBeTruthy();
    fireEvent.click(within(pop).getByText("wish-list.png").closest("button")!);
    const broll = useStory.getState().cueId!;
    // "I want you to" runs 450–507, under the two-second floor, so the B-roll covers 60 frames from word 30.
    expect(added(broll)).toMatchObject({ tlStartF: 450, srcInF: 0, srcOutF: 60, anchor: { ref: { wordIdx: 30 } } });
    expect(chipWord(broll)).toBe(30);
    expect(useStory.getState().range).toBeNull();
    expect(useStory.getState().caretWord).toBe(2);

    select(30, 33);
    pop = pick("◆ Graphic");
    expect(within(pop).getByText("Add graphic")).toBeTruthy();
    fireEvent.click(within(pop).getByText("Lower Third").closest("button")!);
    const graphic = useStory.getState().cueId!;
    expect(added(graphic)).toMatchObject({ componentId: "lower-third", tlStartF: 450, anchor: { ref: { wordIdx: 30 } } });
    expect(chipWord(graphic)).toBe(30);

    select(30, 33);
    pop = pick("♪ Music");
    expect(within(pop).getByText("Add music")).toBeTruthy();
    fireEvent.click(within(pop).getByText("bed.wav").closest("button")!);
    const music = useStory.getState().cueId!;
    expect(added(music)).toMatchObject({ tlStartF: 450, duckDb: -12, anchor: { ref: { wordIdx: 30 } } });
    expect(document.querySelector(`[data-cue="${music}"]`)!.textContent).toBe("♪ bed");

    select(30, 33);
    pop = pick("✧ Sound");
    expect(within(pop).getByText("Add sound")).toBeTruthy();
    fireEvent.click(within(pop).getByText("whoosh.wav").closest("button")!);
    const sound = useStory.getState().cueId!;
    expect(added(sound)).toMatchObject({ tlStartF: 450, anchor: { ref: { wordIdx: 30 } } });
    expect(added(sound).duckDb).toBeUndefined();
    const chip = document.querySelector(`[data-cue="${sound}"]`)!;
    expect(chip.textContent).toBe("✧ whoosh");
    expect(chip.className).toContain("cue-sfx");
    expect(chipWord(sound)).toBe(30);

    expect(useProjectStore.temporal.getState().pastStates).toHaveLength(4);
  });

  it("a drawer tile clicked with words selected lands on the selection too", async () => {
    render(<Story />);
    await screen.findByText("city.png");
    click(2);
    select(30, 37);
    fireEvent.click(screen.getByText("city.png").closest("button")!);
    // Words 30–37 run 450–567: the still spans all of them.
    expect(added(useStory.getState().cueId!)).toMatchObject({ tlStartF: 450, srcOutF: 117, anchor: { ref: { wordIdx: 30 } } });
  });

  it("targets the selection's first kept word, else the caret, else the playhead word", () => {
    const cut = new Set([30, 31]);
    expect(cueTarget({ a: 33, b: 30 }, 2, cut, 0)).toEqual({ at: 32, end: 33 });
    expect(cueTarget(null, 2, cut, 0)).toEqual({ at: 2 });
    expect(cueTarget(null, null, cut, 7)).toEqual({ at: 7 });
    expect(cueTarget(null, null, cut, null)).toBeNull();
  });

  it("suggests the top three B-roll clips and closes with the selection on Escape", async () => {
    render(<Story />);
    await screen.findByText("city.png");
    select(30, 33);
    const pop = pick("▣ B-roll");
    expect(within(pop).getAllByRole("button").map((b) => b.querySelector(".tile-name")!.textContent)).toEqual([
      "wish-list.png", "city.png", "coffee.png",
    ]);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Pick" })).toBeNull();
    expect(useStory.getState().range).toBeNull();
  });
});

describe("the Source lens names library clips", () => {
  it("from its first render, reusing the library the drawer already fetched", async () => {
    render(<Story />);
    await screen.findByText("city.png");
    click(4);
    fireEvent.click(screen.getByText("city.png").closest("button")!);
    cleanup();
    render(<SourceLens />);
    const mdx = screen.getByLabelText("script.mdx").textContent!;
    expect(mdx).toContain('<Broll clip="city" /> w4');
    expect(mdx).not.toContain("1111111111111111");
  });
});

describe("the Claude suggests ranking", () => {
  const entries = LIBRARY.map((a) => ({ workspaceId: "garden", workspaceName: "garden", asset: a }));

  it("matches the co-pilot's terms: no stop words, short words or plural s", () => {
    expect([...terms("I want you to see the seedlings, it's fine")]).toEqual(["want", "see", "seedling", "fine"]);
  });

  it("ranks by words matched, then length fit, then name", () => {
    const broll = entries.filter((e) => e.asset.kind !== "audio");
    expect(rankAssets(broll, "I want you to", 0.4).map((e) => e.asset.name)).toEqual([
      "wish-list.png", "city.png", "coffee.png", "team.png", "seedling.mp4",
    ]);
    expect(rankAssets(broll, "the seedling rack", 1.5)[0].asset.name).toBe("seedling.mp4");
  });
});
