// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { TranscriptWord } from "../src/api";
import type { Project } from "../src/types/project";

// Six words, one per second; scenes at words 0/2/4 own source [0,63), [63,123), [123,210).
const WORDS: TranscriptWord[] = Array.from({ length: 6 }, (_, i) => ({ w: `w${i}`, start: i + 0.1, end: i + 0.6, segIdx: 0 }));

vi.mock("../src/api", () => ({
  api: {
    transcription: async () => ({ state: "ready", job: null }),
    getTranscript: async () => ({ words: WORDS, segments: [], fillers: [] }),
  },
}));

import ArcStrip from "../src/v3/story/ArcStrip";
import { arcLayout, arcPlayhead } from "../src/v3/story/arc/layout";
import type { SceneSpan } from "../src/v3/story/model";
import { resetStory, useStory } from "../src/v3/story/storyStore";
import { setPlayhead, useProjectStore } from "../src/store/useProjectStore";

function span(id: string, index: number, tlStartF: number | null, durF: number): SceneSpan {
  const startWord = index * 10;
  return {
    scene: { id, title: `Scene ${id}`, role: "Beat", startWord },
    index, startWord, endWord: startWord + 10, srcStartF: 0, srcEndF: 0, durF, tlStartF,
  };
}

describe("arc layout", () => {
  const layout = arcLayout([span("a", 0, 0, 60), span("b", 1, 60, 30), span("cut", 2, null, 0), span("d", 3, 90, 90)]);

  it("sizes blocks by kept duration in story order and drops fully cut scenes", () => {
    expect(layout.totalF).toBe(180);
    expect(layout.blocks.map((b) => b.span.scene.id)).toEqual(["a", "b", "d"]);
    expect(layout.blocks.map((b) => b.width)).toEqual([1 / 3, 1 / 6, 1 / 2]);
    expect(layout.blocks.map((b) => b.left)).toEqual([0, 1 / 3, 1 / 2]);
  });

  it("places the playhead marker inside the scene that plays it", () => {
    expect(arcPlayhead(layout, 30)).toEqual({ index: 0, at: 30 / 180 });
    expect(arcPlayhead(layout, 75)).toEqual({ index: 1, at: 1 / 3 + 15 / 180 });
    expect(arcPlayhead(layout, 180)).toEqual({ index: 2, at: 1 });
    expect(arcPlayhead(arcLayout([]), 10)).toBeNull();
  });

  it("follows the timeline, not the list, when story order moved the scenes", () => {
    const moved = arcLayout([span("late", 0, 0, 90), span("early", 1, 90, 60)]);
    expect(arcPlayhead(moved, 100)).toEqual({ index: 1, at: 90 / 150 + 10 / 150 });
  });
});

const PROJECT: Project = {
  fps: 30, width: 1920, height: 1080, name: "arc-demo", revision: 1, schemaVersion: 3,
  tracks: [{ id: "speech", kind: "speech", clips: [{ id: "a", media: { source: "talk.mp4" }, srcInF: 0, srcOutF: 210, tlStartF: 0 }] }],
  scenes: [
    { id: "s1", title: "Open", role: "Hook", startWord: 0 },
    { id: "s2", title: "Middle", role: "Beat", startWord: 2 },
    { id: "s3", title: "Close", role: "Wrap", startWord: 4 },
  ],
};

describe("ArcStrip", () => {
  beforeEach(() => {
    resetStory();
    useProjectStore.setState({ project: PROJECT, revision: 1, playheadF: 0 });
  });
  afterEach(cleanup);

  it("renders role-coloured blocks, highlights the playing scene and seeks on click", async () => {
    const { container } = render(<ArcStrip />);
    // The transcript loads asynchronously. Landing it inside act also runs that render's passive effects,
    // which point the store subscriptions at the loaded layout. Polling for the blocks outside act let a
    // loaded machine click before those effects ran, so the seek was checked against the empty layout and
    // the arc never re-rendered.
    await act(async () => {});
    const blocks = screen.getAllByRole("button");
    expect(blocks.map((b) => b.textContent)).toEqual(["HookOpen", "BeatMiddle", "WrapClose"]);
    expect(blocks.map((b) => (b as HTMLElement).style.getPropertyValue("--w"))).toEqual([`${63 / 210}`, `${60 / 210}`, `${87 / 210}`]);
    expect(blocks[0].style.getPropertyValue("--c")).toBe("var(--yellow)");
    expect(blocks[0].className).toContain("on");

    fireEvent.click(blocks[2]);
    expect(useProjectStore.getState().playheadF).toBe(123);
    expect(useStory.getState().caretWord).toBe(4);
    expect(blocks[2].className).toContain("on");
    expect(blocks[0].className).not.toContain("on");

    act(() => setPlayhead(93));
    const head = container.querySelector<HTMLElement>(".arc-head")!;
    expect(head.style.getPropertyValue("--i")).toBe("1");
    expect(Number(head.style.getPropertyValue("--at"))).toBeCloseTo(93 / 210);
  });
});
