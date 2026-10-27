import { describe, expect, it } from "vitest";
import type { Project } from "../src/types/project";
import { addTextItem, moveTextItemToLayer, updateTextItem } from "../src/timeline/textItems";

function project(): Project {
  return {
    schemaVersion: 1, name: "p", fps: 30, width: 1920, height: 1080, revision: 1, tracks: [
      { id: "text_1", kind: "text", items: [
        { id: "first", text: "First", preset: "hook", pos: { x: 0.5, y: 0.18 }, tlStartF: 0, durF: 150 },
      ] },
    ],
  };
}

function textItem(doc: Project, id: string) {
  for (const track of doc.tracks) {
    const item = track.items?.find((candidate) => candidate.id === id);
    if (item && "text" in item) return item;
  }
  throw new Error(`missing ${id}`);
}

function visualProject(): Project {
  return {
    ...project(),
    tracks: [
      { id: "speech", kind: "speech", clips: [
        { id: "source", media: { source: "source.mp4", proxy: null }, srcInF: 0, srcOutF: 300, tlStartF: 0 },
      ] },
      ...project().tracks,
    ],
  };
}

describe("title authoring", () => {
  it("adds a selected five-second hook title at the playhead on an open layer", () => {
    const result = addTextItem(project(), 180);
    const item = textItem(result.project, result.itemId);
    expect(result.itemId).toBe("title");
    expect(item).toMatchObject({ text: "Add a title", preset: "hook", tlStartF: 180, durF: 150, pos: { x: 0.5, y: 0.18 } });
    expect(result.project.tracks).toHaveLength(1);
  });

  it("creates a new text layer when the default span would overlap", () => {
    const result = addTextItem(project(), 30);
    expect(result.project.tracks).toHaveLength(2);
    expect(textItem(result.project, result.itemId).tlStartF).toBe(30);
  });

  it("keeps title timing inside the renderable visual spine", () => {
    const result = addTextItem(visualProject(), 300);
    expect(textItem(result.project, result.itemId)).toMatchObject({ tlStartF: 150, durF: 150 });
    const edited = updateTextItem(result.project, result.itemId, { tlStartF: 290, durF: 150 });
    expect(textItem(edited, result.itemId)).toMatchObject({ tlStartF: 290, durF: 10 });
  });

  it("clamps timing and placement so inspector edits cannot invalidate the project", () => {
    const result = addTextItem(project(), 180);
    const edited = updateTextItem(result.project, result.itemId, {
      tlStartF: 20, durF: 900, pos: { x: 4, y: -2 }, text: "Updated",
    });
    const item = textItem(edited, result.itemId);
    expect(item.tlStartF).toBe(150);
    expect(item.durF).toBe(900);
    expect(item.pos).toEqual({ x: 1, y: 0 });
    expect(item.text).toBe("Updated");
  });

  it("moves a title to a new layer and refuses an occupied existing layer", () => {
    const result = addTextItem(project(), 30);
    const toNew = moveTextItemToLayer(result.project, result.itemId, "new");
    expect(toNew.tracks).toHaveLength(3);
    expect(moveTextItemToLayer(toNew, result.itemId, "text_1")).toBe(toNew);
  });
});
