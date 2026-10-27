import { describe, expect, it } from "vitest";
import type { Project } from "../src/types/project";
import {
  defaultTransform,
  layerInfo,
  moveVisualLayer,
  resetClipTransform,
  resolvedTransform,
  updateClipTransform,
} from "../src/timeline/visuals";

function project(): Project {
  return {
    schemaVersion: 2, name: "p", fps: 30, width: 1920, height: 1080, revision: 1,
    tracks: [
      { id: "speech", kind: "speech", clips: [{ id: "source", media: { source: "source.mp4" }, srcInF: 0, srcOutF: 300, tlStartF: 0 }] },
      { id: "broll", kind: "video", clips: [{ id: "broll1", media: { source: "broll.png" }, srcInF: 0, srcOutF: 120, tlStartF: 0 }] },
      { id: "text", kind: "text", items: [{ id: "title", text: "Hello", preset: "hook", pos: { x: 0.5, y: 0.2 }, tlStartF: 0, durF: 120 }] },
      { id: "music", kind: "audio", clips: [{ id: "music1", media: { source: "music.mp3" }, srcInF: 0, srcOutF: 300, tlStartF: 0 }] },
    ],
  };
}

function clip(doc: Project, id: string) {
  return doc.tracks.flatMap((track) => track.clips ?? []).find((candidate) => candidate.id === id)!;
}

describe("manual visual transforms", () => {
  it("resolves reset defaults without mutating the legacy document", () => {
    const doc = project();
    expect(defaultTransform(doc, clip(doc, "source"))).toEqual({ x: 0.5, y: 0.5, scale: 1, fit: "contain" });
    expect(defaultTransform(doc, clip(doc, "broll1")).fit).toBe("contain");
    expect(resolvedTransform(doc, clip(doc, "source"))).toEqual(defaultTransform(doc, clip(doc, "source")));
    expect(clip(doc, "source").transform).toBeUndefined();
  });

  it("clamps inspector/direct-preview updates and reset removes the override", () => {
    const doc = project();
    const edited = updateClipTransform(doc, "source", { x: -4, y: 2, scale: 9, fit: "cover" });
    expect(clip(edited, "source").transform).toEqual({ x: 0, y: 1, scale: 4, fit: "cover" });
    expect(clip(doc, "source").transform).toBeUndefined();
    const reset = resetClipTransform(edited, "source");
    expect(clip(reset, "source").transform).toBeUndefined();
    expect(resolvedTransform(reset, clip(reset, "source")).fit).toBe("contain");
  });

  it("never stretches: speech footage contains, B-roll video covers, cropped projects cover", () => {
    const doc = project();
    doc.tracks[1].clips!.push({ id: "broll2", media: { source: "stock.mp4" }, srcInF: 0, srcOutF: 60, tlStartF: 0 });
    expect(defaultTransform(doc, clip(doc, "source")).fit).toBe("contain");
    expect(defaultTransform(doc, clip(doc, "broll2")).fit).toBe("cover");
    const cropped = { ...doc, crop: { mode: "center" as const } };
    expect(defaultTransform(cropped, clip(cropped, "source")).fit).toBe("cover");
  });

  it("rejects visual mutations for audio", () => {
    const doc = project();
    expect(updateClipTransform(doc, "music1", { scale: 2 })).toBe(doc);
  });
});

describe("document track layers", () => {
  it("reports visible layer order and swaps only visual tracks", () => {
    const doc = project();
    expect(layerInfo(doc, "broll1")).toEqual({ index: 1, count: 3 });
    const raised = moveVisualLayer(doc, "broll1", 1);
    expect(raised.tracks.map((track) => track.id)).toEqual(["speech", "text", "broll", "music"]);
    expect(layerInfo(raised, "broll1")).toEqual({ index: 2, count: 3 });
    expect(moveVisualLayer(raised, "broll1", 1)).toBe(raised);
  });
});
