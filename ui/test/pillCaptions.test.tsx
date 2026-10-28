// @vitest-environment jsdom
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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

import { ProjectComposition } from "../src/preview/ProjectComposition";

beforeEach(() => {
  vi.spyOn(api, "getChannels").mockResolvedValue({
    channels: {
      outline: { captionStyle: { look: "outline", wordsPerPage: 2, activeColor: "&H00CCFF66" } },
      plain: { captionStyle: { activeColor: "&H00CCFF66" } },
    },
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const project = (patch: Partial<Project> = {}): Project => ({
  schemaVersion: 2, name: "pill", revision: 1, fps: 30, width: 1920, height: 1080,
  captions: { enabled: true },
  tracks: [{ id: "speech", kind: "speech", clips: [{ id: "c", media: { source: "data/source/clip.mp4", proxy: null }, srcInF: 0, srcOutF: 300, tlStartF: 0, gainDb: 0 }] }],
  ...patch,
});

const words: TranscriptWord[] = [
  { w: "example", start: 1, end: 1.2, segIdx: 0 },
  { w: "clip", start: 1.2, end: 1.5, segIdx: 0 },
  { w: "to", start: 1.6, end: 1.9, segIdx: 0 },
];

const YELLOW = "rgb(245, 197, 24)";
const WHITE = "rgb(244, 246, 247)";
const DIM = "rgba(244, 246, 247, 0.5)";

function at(frame: number, p: Project = project()) {
  frameState.current = frame;
  cleanup();
  render(<ProjectComposition project={p} captionsEnabled captionWords={words} captionMedia="data/source/clip.mp4" />);
  return screen.queryByLabelText("Caption: example clip to");
}

const colors = () => ["example", "clip", "to"].map((w) => screen.getByText(w).style.color);

describe("pill caption overlay (the v3 default)", () => {
  it("colours each word by its state at the frame: spoken yellow, said white, upcoming dim", () => {
    expect(at(30)?.getAttribute("data-caption-word")).toBe("example");
    expect(colors()).toEqual([YELLOW, DIM, DIM]);
    expect(at(40)?.getAttribute("data-caption-word")).toBe("clip");
    expect(colors()).toEqual([WHITE, YELLOW, DIM]);
    expect(screen.getByText("clip").className).toContain("caption-word-active");
    expect(screen.getByText("example").className).toContain("caption-word-past");
    expect(screen.getByText("to").className).toContain("caption-word-future");
    // In the pause between two words nothing is being spoken.
    expect(at(46)?.getAttribute("data-caption-word")).toBe("");
    expect(colors()).toEqual([WHITE, WHITE, DIM]);
    expect(at(50)?.getAttribute("data-caption-word")).toBe("to");
    expect(colors()).toEqual([WHITE, WHITE, YELLOW]);
    // The line holds half a second after its last word, then leaves.
    expect(at(71)).not.toBeNull();
    expect(at(72)).toBeNull();
    expect(at(29)).toBeNull();
  });

  it("draws bold Inter on a rounded translucent pill, no stroke or shadow", () => {
    const overlay = at(40)!;
    const box = overlay.firstElementChild as HTMLElement;
    expect(overlay.style.position).toBe("absolute");
    expect(overlay.style.zIndex).toBe("5");
    expect(parseFloat(overlay.style.top)).toBeCloseTo(1080 * 0.935 - 1.1 * 46, 6);
    expect(box.style.background).toBe("rgba(8, 10, 12, 0.72)");
    expect(parseFloat(box.style.height)).toBeCloseTo(1.6 * 46, 6);
    expect(parseFloat(box.style.borderRadius)).toBeCloseTo(0.35 * 46, 6);
    const [top, side] = box.style.padding.split(" ").map(parseFloat);
    expect([top, side]).toEqual([0, expect.closeTo(0.6 * 46, 6)]);
    expect(box.style.fontSize).toBe("46px");
    expect(box.style.fontWeight).toBe("700");
    expect(box.style.fontFamily).toContain("Inter");
    expect(box.style.whiteSpace).toBe("nowrap");
    expect(overlay.outerHTML).not.toMatch(/stroke|text-shadow/);
  });

  it("sizes and places the line by the frame's format", () => {
    const overlay = at(40, project({ width: 1080, height: 1920 }))!;
    expect(parseFloat(overlay.style.top)).toBeCloseTo(1920 * 0.9 - 1.1 * 58, 6);
    expect((overlay.firstElementChild as HTMLElement).style.fontSize).toBe("58px");
  });

  it("skips cut words", () => {
    const cut = project();
    cut.tracks[0].clips = [
      { id: "a", media: { source: "data/source/clip.mp4", proxy: null }, srcInF: 0, srcOutF: 36, tlStartF: 0, gainDb: 0 },
      { id: "b", media: { source: "data/source/clip.mp4", proxy: null }, srcInF: 45, srcOutF: 300, tlStartF: 36, gainDb: 0 },
    ];
    frameState.current = 30;
    render(<ProjectComposition project={cut} captionsEnabled captionWords={words} captionMedia="data/source/clip.mp4" />);
    expect(screen.getByLabelText("Caption: example to")).not.toBeNull();
  });

  it("ignores a channel's karaoke colours", async () => {
    at(40, project({ channel: "plain" }));
    await waitFor(() => expect(api.getChannels).toHaveBeenCalled());
    expect(colors()).toEqual([WHITE, YELLOW, DIM]);
  });
});

describe("the outline look stays available", () => {
  it("renders for a project caption style with look: outline", () => {
    const overlay = at(40, project({ captions: { enabled: true, style: { look: "outline" } } }))!;
    expect(overlay.style.webkitTextStroke).not.toBe("");
    expect(overlay.style.bottom).toBe("15%");
    expect(screen.getByText("clip").style.backgroundImage).toContain("linear-gradient");
  });

  it("renders the effective channel profile fetched from the API", async () => {
    at(40, project({ channel: "outline" }));
    const overlay = await screen.findByLabelText("Caption: example clip");
    expect(overlay.style.webkitTextStroke).not.toBe("");
    expect(overlay.classList.contains("caption-pill")).toBe(false);
    expect(screen.getByText("clip").style.backgroundImage).toContain("rgb(102, 255, 204)");
    expect(api.getChannels).toHaveBeenCalledWith(expect.any(AbortSignal));
  });

  it("keeps project caption style overrides over the fetched channel", async () => {
    at(40, project({ channel: "outline", captions: { enabled: true, style: { look: "pill" } } }));
    await waitFor(() => expect(api.getChannels).toHaveBeenCalled());
    expect(screen.getByLabelText("Caption: example clip to").style.webkitTextStroke).toBe("");
    expect(colors()).toEqual([WHITE, YELLOW, DIM]);
  });

  it("uses caption defaults when the channel request fails", async () => {
    vi.mocked(api.getChannels).mockRejectedValue(new Error("unavailable"));
    at(40, project({ channel: "outline" }));
    await waitFor(() => expect(api.getChannels).toHaveBeenCalled());
    expect(colors()).toEqual([WHITE, YELLOW, DIM]);
  });
});
