// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { Project } from "../src/types/project";
import { insertGraphic } from "../src/graphics/ops";

vi.mock("../src/player", () => ({
  Layer: ({ children, ...props }: { children?: React.ReactNode; [key: string]: unknown }) => <div {...props}>{children}</div>,
  FrameWindow: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  ClipAudio: () => <audio />,
  FrameImage: (props: Record<string, unknown>) => <img {...props} />,
  ClipVideo: (props: Record<string, unknown>) => <video src={props.src as string} style={props.style as React.CSSProperties} />,
  useFrame: () => 0,
}));

import { ProjectComposition } from "../src/preview/ProjectComposition";

function project(): Project {
  const base: Project = {
    schemaVersion: 2, name: "preview-interaction", fps: 30, width: 1920, height: 1080, revision: 1,
    tracks: [{ id: "graphics", kind: "text", items: [] }],
  };
  return insertGraphic(base, "callout", 0).project;
}

afterEach(() => cleanup());

describe("rendered preview manipulation controls", () => {
  it("selects at the actual pointer and routes keyboard nudges from a rendered graphic", () => {
    const p = project();
    const id = p.tracks[0].items![0].id;
    const selected: Array<[string, number, number]> = [];
    const drag = vi.fn();
    const nudge = vi.fn();
    render(<ProjectComposition project={p} selection={id} onSelectAt={(...args) => selected.push(args)} onTransformDragStart={drag} onTransformKeyDown={nudge} />);
    const visual = screen.getByLabelText("callout graphic");
    const pointerDown = new Event("pointerdown", { bubbles: true, cancelable: true });
    Object.defineProperties(pointerDown, { clientX: { value: 317 }, clientY: { value: 143 }, pointerId: { value: 1 } });
    visual.dispatchEvent(pointerDown);
    expect(selected).toEqual([[id, 317, 143]]);
    expect(drag).toHaveBeenCalledWith(id, 317, 143, "move");
    fireEvent.keyDown(visual, { key: "ArrowRight", shiftKey: true });
    expect(nudge).toHaveBeenCalledWith(id, "ArrowRight", true);
    const handles = screen.getAllByRole("button", { name: /Resize selected visual/ });
    expect(handles).toHaveLength(4);
    for (const [index, corner] of ["NW", "NE", "SE", "SW"].entries()) {
      const event = new Event("pointerdown", { bubbles: true, cancelable: true });
      Object.defineProperties(event, { clientX: { value: 300 + index }, clientY: { value: 140 + index } });
      handles[index].dispatchEvent(event);
      expect(drag).toHaveBeenCalledWith(id, 300 + index, 140 + index, "resize", corner.toLowerCase(), undefined);
    }
  });

  it("does not render an unsupported graphic as a title fallback", () => {
    const p = project();
    const invalid = {
      type: "graphic", componentId: "unknown", componentVersion: 9, id: "invalid",
      props: { text: "must stay hidden" }, pos: { x: 0.5, y: 0.5 }, scale: 1, tlStartF: 0, durF: 30,
    } as unknown as NonNullable<Project["tracks"][number]["items"]>[number];
    p.tracks[0].items = [invalid];
    render(<ProjectComposition project={p} selection="invalid" />);
    expect(screen.queryByText("must stay hidden")).toBeNull();
    expect(screen.queryByLabelText(/invalid|unknown|Title/)).toBeNull();

    const future = { ...invalid, id: "future", type: "future-graphic" } as unknown as NonNullable<Project["tracks"][number]["items"]>[number];
    p.tracks[0].items = [future];
    render(<ProjectComposition project={p} selection="future" />);
    expect(screen.queryByText("must stay hidden")).toBeNull();
    expect(screen.queryByLabelText(/future|unknown|Title/)).toBeNull();
  });

  it("treats rendered speech footage as a nudgeable visual", () => {
    const p = project();
    p.tracks.unshift({ id: "speech", kind: "speech", clips: [{ id: "speech-1", media: { source: "speech.mp4", proxy: null }, srcInF: 0, srcOutF: 30, tlStartF: 0 }] });
    const nudge = vi.fn();
    render(<ProjectComposition project={p} selection="speech-1" onTransformKeyDown={nudge} />);
    fireEvent.keyDown(screen.getByLabelText("Selected visual"), { key: "ArrowUp", shiftKey: true });
    expect(nudge).toHaveBeenCalledWith("speech-1", "ArrowUp", true);
  });

  it("uses the compiler's contain default for untransformed speech footage (a portrait source is never stretched)", () => {
    const p = project();
    p.tracks.unshift({ id: "speech", kind: "speech", clips: [{ id: "portrait", media: { source: "portrait.mp4", proxy: null }, srcInF: 0, srcOutF: 30, tlStartF: 0 }] });
    render(<ProjectComposition project={p} />);
    expect(document.querySelector("video")?.style.objectFit).toBe("contain");
  });
});
