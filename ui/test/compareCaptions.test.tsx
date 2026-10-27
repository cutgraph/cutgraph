// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { api, type TranscriptWord } from "../src/api";
import type { Project } from "../src/types/project";

const frameState = vi.hoisted(() => ({ current: 0 }));
vi.mock("../src/player", () => ({
  Layer: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  FrameWindow: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  ClipAudio: () => null,
  FrameImage: () => null,
  ClipVideo: () => null,
  useFrame: () => frameState.current,
}));

import { buildCaptionIndex, buildCaptionPages, findActiveCaption, ProjectComposition } from "../src/preview/ProjectComposition";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// These pin the outline karaoke look, which a caption style keeps with `look: "outline"`; the default
// v3 pill look is covered by pillCaptions.test.tsx.
const project = (tlStartF: number, enabled = true): Project => ({
  schemaVersion: 2, name: "captions", revision: 4, fps: 30, width: 1920, height: 1080,
  captions: { enabled, style: { look: "outline" } },
  tracks: [{ id: "speech", kind: "speech", clips: [{ id: `clip-${tlStartF}`, media: { source: "data/source/clip.mp4", proxy: null }, srcInF: 0, srcOutF: 120, tlStartF, gainDb: 0 }] }],
});

const words: TranscriptWord[] = [{ w: "hello", start: 1, end: 2, segIdx: 0 }];

describe("Compare caption overlay", () => {
  it("maps one shared word resource to each immutable side's speech timeline", () => {
    const before = project(0);
    const after = project(60);
    const view = () => <div><ProjectComposition project={before} captionsEnabled captionWords={words} captionMedia="data/source/clip.mp4" /><ProjectComposition project={after} captionsEnabled captionWords={words} captionMedia="data/source/clip.mp4" /></div>;
    frameState.current = 30;
    const { rerender } = render(view());
    expect(screen.getAllByLabelText("Caption: hello")).toHaveLength(1);
    frameState.current = 90;
    rerender(view());
    expect(screen.getAllByLabelText("Caption: hello")).toHaveLength(1);
  });

  it("stays unavailable when caption configuration or words are absent", () => {
    frameState.current = 30;
    render(<ProjectComposition project={project(0, false)} captionsEnabled captionWords={[]} captionMedia="data/source/clip.mp4" />);
    expect(screen.queryByLabelText("Caption: hello")).toBeNull();
  });

  it("clips a crossing word at the source trim boundary", () => {
    const trimmed = project(0);
    trimmed.tracks[0].clips![0].srcOutF = 45;
    const crossing: TranscriptWord[] = [{ w: "boundary", start: 1, end: 3, segIdx: 0 }];
    const view = () => <ProjectComposition project={trimmed} captionsEnabled captionWords={crossing} captionMedia="data/source/clip.mp4" />;
    frameState.current = 44;
    const { rerender } = render(view());
    expect(screen.getByLabelText("Caption: boundary")).not.toBeNull();
    frameState.current = 45;
    rerender(view());
    expect(screen.queryByLabelText("Caption: boundary")).toBeNull();
  });

  it("renders an ASS-sized three-word page with only the current word highlighted", () => {
    const p = project(0);
    const pageWords: TranscriptWord[] = [
      { w: "example", start: 1, end: 1.2, segIdx: 0 },
      { w: "clip", start: 1.2, end: 1.5, segIdx: 0 },
      { w: "to", start: 1.5, end: 1.9, segIdx: 0 },
    ];
    expect(buildCaptionPages(p, pageWords)).toHaveLength(1);
    frameState.current = 30;
    const { rerender } = render(<ProjectComposition project={p} captionsEnabled captionWords={pageWords} captionMedia="data/source/clip.mp4" />);
    const overlay = screen.getByLabelText("Caption: example clip to");
    expect(overlay).not.toBeNull();
    // Self-contained layout: above the footage in any host, with or without the shared stylesheet.
    expect(overlay.style.position).toBe("absolute");
    expect(overlay.style.zIndex).toBe("5");
    expect(overlay.style.bottom).not.toBe("");
    expect(overlay.textContent).toContain("example clip to");
    expect(overlay.childNodes).toHaveLength(5);
    expect(overlay.childNodes[1].textContent).toBe(" ");
    expect(overlay.childNodes[3].textContent).toBe(" ");
    expect(screen.getByText("example").className).toContain("caption-word-active");
    frameState.current = 39;
    rerender(<ProjectComposition project={p} captionsEnabled captionWords={pageWords} captionMedia="data/source/clip.mp4" />);
    expect(screen.getByText("clip").className).toContain("caption-word-active");
    expect(screen.getByText("example").className).not.toContain("caption-word-active");
  });

  it("derives cumulative karaoke fill and does not floor a 1.01s word onto frame 30", () => {
    const p = project(0);
    const pageWords: TranscriptWord[] = [
      { w: "example", start: 0.65, end: 0.9, segIdx: 0 },
      { w: "clip", start: 1.01, end: 1.3, segIdx: 0 },
      { w: "to", start: 1.33, end: 1.57, segIdx: 0 },
    ];
    const view = () => <ProjectComposition project={p} captionsEnabled captionWords={pageWords} captionMedia="data/source/clip.mp4" />;
    frameState.current = 30;
    const { rerender } = render(view());
    expect(screen.getByLabelText("Caption: example clip to").getAttribute("data-caption-word")).toBe("example");
    expect(screen.getByText("example").className).toContain("caption-word-active");
    expect(screen.getByText("clip").className).toContain("caption-word-future");
    frameState.current = 35;
    rerender(view());
    expect(screen.getByText("example").className).toContain("caption-word-past");
    expect(screen.getByText("example").style.color).toBe("rgb(255, 255, 0)");
    expect(screen.getByText("clip").className).toContain("caption-word-active");
    expect(screen.getByText("clip").style.backgroundImage).toContain("linear-gradient");
    expect(screen.getByText("to").style.color).toBe("rgb(255, 255, 255)");
    frameState.current = 31;
    rerender(view());
    // The 1.01s `clip` start is ceil(30.3)=31; frame 30 must still show `example`.
    expect(screen.getByLabelText("Caption: example clip to").getAttribute("data-caption-word")).toBe("clip");
    expect(screen.getByText("example").style.color).toBe("rgb(255, 255, 0)");
  });

  it("keeps an explicit emphasis color independent of karaoke progress", () => {
    const p = project(0);
    p.captions = { enabled: true, emphasis: [1], style: { look: "outline", emphasisColor: "#00ff00" } };
    const pageWords: TranscriptWord[] = [
      { w: "example", start: 1, end: 1.2, segIdx: 0 },
      { w: "clip", start: 1.2, end: 1.5, segIdx: 0 },
      { w: "to", start: 1.5, end: 1.9, segIdx: 0 },
    ];
    frameState.current = 30;
    render(<ProjectComposition project={p} captionsEnabled captionWords={pageWords} captionMedia="data/source/clip.mp4" />);
    expect(screen.getByText("clip").style.color).toBe("rgb(0, 255, 0)");
    expect(screen.getByText("clip").className).toContain("caption-word-future");
  });

  it("matches compile channel defaults before applying project caption overrides", async () => {
    vi.spyOn(api, "getChannels").mockResolvedValue({ channels: {
      sample: { captionStyle: { activeColor: "&H00CCFF66", alignment: 2, marginVPct: 0.15 } },
    } });
    const p = project(0);
    p.channel = "sample";
    p.captions = { enabled: true, style: { look: "outline", alignment: 9 } };
    frameState.current = 30;
    render(<ProjectComposition project={p} captionsEnabled captionWords={words} captionMedia="data/source/clip.mp4" />);
    const overlay = screen.getByLabelText("Caption: hello");
    await waitFor(() => expect(screen.getByText("hello").style.backgroundImage).toContain("rgb(102, 255, 204)"));
    expect(overlay.style.fontFamily).toContain("Inter");
    expect(overlay.style.textAlign).toBe("right");
    expect(overlay.style.top).toBe("15%");
    expect(overlay.style.bottom).toBe("");
  });

  it("normalizes overlaps and keeps long-transcript lookups logarithmic", () => {
    const overlappingProject = project(0);
    const overlappingWords: TranscriptWord[] = [
      { w: "long", start: 0, end: 2, segIdx: 0 },
      { w: "short", start: 1, end: 1.5, segIdx: 1 },
    ];
    const overlapping = buildCaptionIndex(overlappingProject, overlappingWords).get("data/source/clip.mp4")!;
    expect(overlapping.every((interval, index) => index === 0 || overlapping[index - 1].endF <= interval.startF)).toBe(true);
    expect(findActiveCaption(overlapping, 15)?.word.w).toBe("long");
    expect(findActiveCaption(overlapping, 35)?.word.w).toBe("short");
    expect(findActiveCaption(overlapping, 50)?.word.w).toBe("long");

    const indexedProject = project(0);
    indexedProject.tracks[0].clips![0].srcOutF = 400_000;
    const longWords: TranscriptWord[] = Array.from({ length: 10_000 }, (_, index) => ({
      w: `word-${index}`,
      start: index,
      end: index + 0.5,
      segIdx: index,
    }));
    const index = buildCaptionIndex(indexedProject, longWords);
    const intervals = index.get("data/source/clip.mp4");
    expect(intervals).toHaveLength(longWords.length);
    let comparisons = 0;
    expect(findActiveCaption(intervals, 9_000 * 30, () => { comparisons += 1; })?.word.w).toBe("word-9000");
    expect(comparisons).toBeLessThanOrEqual(16);
    comparisons = 0;
    expect(findActiveCaption(intervals, 9_000 * 30 + 16, () => { comparisons += 1; })).toBeNull();
    expect(comparisons).toBeLessThanOrEqual(16);
    comparisons = 0;
    expect(findActiveCaption(intervals, 400_001, () => { comparisons += 1; })).toBeNull();
    expect(comparisons).toBeLessThanOrEqual(16);
  });
});
