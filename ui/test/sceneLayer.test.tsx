// @vitest-environment jsdom
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render } from "@testing-library/react";
import type { Project } from "../src/types/project";
import type { SceneRenderSpec } from "../src/preview/SceneLayer";

const frameState = vi.hoisted(() => ({ current: 0 }));
vi.mock("../src/player", () => ({
  Layer: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  FrameWindow: ({ children, startFrame, frameCount }: { children?: React.ReactNode; startFrame: number; frameCount: number }) => (
    <div data-from={startFrame} data-dur={frameCount}>{children}</div>
  ),
  ClipAudio: () => null,
  FrameImage: () => null,
  ClipVideo: () => <video />,
  useFrame: () => frameState.current,
}));
const drawSceneFrame = vi.hoisted(() => vi.fn());
vi.mock("motion-core", async (importOriginal) => ({ ...(await importOriginal<object>()), drawSceneFrame }));

import { ProjectComposition } from "../src/preview/ProjectComposition";

const calls: string[] = [];
const ctx = {
  canvas: { width: 1920, height: 1080 },
  setTransform: () => {},
  clearRect: () => calls.push("clear"),
};

const project: Project = {
  schemaVersion: 3, name: "scenes", revision: 1, fps: 30, width: 1920, height: 1080,
  tracks: [
    { id: "speech", kind: "speech", clips: [{ id: "talk", media: { source: "data/source/talk.mp4" }, srcInF: 0, srcOutF: 300, tlStartF: 0 }] },
    { id: "broll", kind: "video", clips: [{ id: "city", media: { source: "data/broll/city.mp4" }, srcInF: 0, srcOutF: 60, tlStartF: 30 }] },
    { id: "text", kind: "text", items: [{ id: "title", text: "Title", tlStartF: 0, durF: 90, pos: { x: 0.5, y: 0.2 }, preset: "title" }] },
  ],
};

const spec: SceneRenderSpec = {
  sceneId: "s1", mode: "cameo", startF: 60, durF: 90,
  shot: { pose: "wave", expression: "happy", move: "wave", backdrop: "work", diagram: "blank", headline: "Hi" },
  timing: { firstT: 0.2, cueT: 0.2 },
};

beforeEach(() => {
  calls.length = 0;
  drawSceneFrame.mockReset().mockImplementation(() => calls.push("draw"));
  HTMLCanvasElement.prototype.getContext = vi.fn(() => ctx) as unknown as typeof HTMLCanvasElement.prototype.getContext;
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("scene layer in the preview composition", () => {
  it("waits for every bundled face before painting a paused frame", async () => {
    const resolvers: (() => void)[] = [];
    const load = vi.fn((_face: string) => new Promise<void>((resolve) => resolvers.push(resolve)));
    Object.defineProperty(document, "fonts", { value: { load }, configurable: true });
    try {
      render(<ProjectComposition project={project} scenes={[spec]} />);
      expect(load.mock.calls.map((call) => call[0])).toEqual([400, 600, 700, 900].map((w) => `${w} 16px "Inter"`));
      expect(drawSceneFrame).not.toHaveBeenCalled();
      await act(async () => { resolvers.slice(0, 3).forEach((resolve) => resolve()); });
      expect(drawSceneFrame).not.toHaveBeenCalled();
      await act(async () => { resolvers[3](); });
      expect(drawSceneFrame).toHaveBeenCalledTimes(1);
    } finally {
      Reflect.deleteProperty(document, "fonts");
    }
  });
  it("adds nothing when no scenes are passed", () => {
    const { container } = render(<ProjectComposition project={project} />);
    expect(container.querySelector("canvas")).toBeNull();
  });

  it("puts one composition-sized canvas per scene above the footage and B-roll, below text", () => {
    const { container } = render(<ProjectComposition project={project} scenes={[spec, { ...spec, sceneId: "s3", startF: 200, durF: 40 }]} />);
    const canvases = [...container.querySelectorAll("canvas")];
    expect(canvases.map((c) => [c.dataset.sceneLayer, c.width, c.height])).toEqual([["s1", 1920, 1080], ["s3", 1920, 1080]]);
    expect(canvases.map((c) => c.parentElement?.dataset.from)).toEqual(["60", "200"]);
    const order = [...container.querySelectorAll("video, canvas, [aria-label='Title: Title']")].map((el) => el.tagName);
    expect(order).toEqual(["VIDEO", "VIDEO", "CANVAS", "CANVAS", "DIV"]);
  });

  it("goes on top when the project has no text track", () => {
    const bare = { ...project, tracks: project.tracks.slice(0, 1) };
    const { container } = render(<ProjectComposition project={bare} scenes={[spec]} />);
    expect([...container.querySelectorAll("video, canvas")].map((el) => el.tagName)).toEqual(["VIDEO", "CANVAS"]);
  });

  it("clears, then draws the scene at the scene-local time on every frame", () => {
    frameState.current = 15;
    const view = () => <ProjectComposition project={project} scenes={[spec]} />;
    const { rerender } = render(view());
    expect(calls).toEqual(["clear", "draw"]);
    expect(drawSceneFrame).toHaveBeenLastCalledWith(ctx, "cameo", spec.shot, 0.5, spec.timing, 1920, 1080, expect.objectContaining({ createCanvas: expect.any(Function) }));
    frameState.current = 16;
    rerender(view());
    expect(drawSceneFrame).toHaveBeenCalledTimes(2);
    expect(drawSceneFrame.mock.calls[1][3]).toBeCloseTo(16 / 30);
    const envs = new Set(drawSceneFrame.mock.calls.map((c) => c[7]));
    expect(envs.size).toBe(1);
  });
});
