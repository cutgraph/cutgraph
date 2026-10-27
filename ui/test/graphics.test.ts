import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { ReactElement } from "react";
import type { Project } from "../src/types/project";
import { GRAPHIC_DEFINITIONS, graphicDefinition, graphicGeometry, graphicStyle, renderGraphic, validateGraphicProps } from "../src/graphics/registry";
import { GRAPHIC_DEFAULT_DURATION_F, insertGraphic, updateGraphic } from "../src/graphics/ops";
import { GRAPHIC_GEOMETRY_GOLDEN, GRAPHIC_GEOMETRY_GOLDEN_4K } from "./geometry.golden";
import { moveClip, nudgeClip } from "../src/timeline/ops";
import { adoptSaved, applyLocal, redo, undo, useProjectStore } from "../src/store/useProjectStore";

const project = (): Project => ({ schemaVersion: 2, name: "graphics", fps: 30, width: 1920, height: 1080, revision: 1, tracks: [
  { id: "video", kind: "video", clips: [{ id: "clip", media: { source: "media/clip.mp4", proxy: null }, srcInF: 0, srcOutF: 600, tlStartF: 0 }] },
  { id: "graphics", kind: "text", items: [] },
] });

describe("trusted graphics registry", () => {
  it("publishes all five stable definitions with trusted renderer identities", () => {
    expect(GRAPHIC_DEFINITIONS.map((item) => item.id)).toEqual(["lower-third", "callout", "evidence-card", "chapter-card", "simple-chart"]);
    for (const item of GRAPHIC_DEFINITIONS) expect(item).toMatchObject({ version: 1, propSchema: expect.any(Object), thumbnail: expect.any(String), previewRenderer: "player:trusted-registry", finalRenderer: "pipeline.graphics" });
  });

  it("validates exact serializable props and blocks executable references", () => {
    const defaults = graphicDefinition("callout")!.defaults;
    expect(validateGraphicProps("callout", defaults)).toBe(true);
    expect(validateGraphicProps("callout", { ...defaults, extra: "nope" })).toBe(false);
    expect(validateGraphicProps("callout", { ...defaults, body: "javascript:alert(1)" })).toBe(false);
    const item = { type: "graphic", componentId: "callout", componentVersion: 1, id: "c", props: defaults, pos: { x: 0.5, y: 0.5 }, scale: 1, tlStartF: 0, durF: 30 } as const;
    expect(graphicStyle(item)).toEqual({ foreground: "#4cc9f0", border: "#4cc9f0", background: "#121619" });
  });

  it("renders every component through the same declarative layer structure", () => {
    for (const definition of GRAPHIC_DEFINITIONS) {
      const inserted = insertGraphic(project(), definition.id, 0);
      const item = inserted.project.tracks[1].items![0];
      const html = renderToStaticMarkup(renderGraphic(item as Parameters<typeof renderGraphic>[0]) as ReactElement);
      expect(html).toContain("graphic-panel");
      expect(html).toContain(`graphic-${definition.id}`);
      if (definition.id === "simple-chart") expect(html).toContain("graphic-bars");
      if (definition.id === "lower-third") expect(html).toContain("graphic-rail");
    }
  });

  it("inserts one selected item at a deterministic free frame and preserves undo-ready immutability", () => {
    const before = project();
    const first = insertGraphic(before, "lower-third", 10);
    expect(first.startF).toBe(10);
    expect(first.project).not.toBe(before);
    expect(first.project.tracks[1].items).toHaveLength(1);
    expect(first.project.tracks[1].items![0]).toMatchObject({ type: "graphic", componentId: "lower-third", tlStartF: 10, durF: GRAPHIC_DEFAULT_DURATION_F });
    const second = insertGraphic(first.project, "callout", 10);
    expect(second.startF).toBe(10 + GRAPHIC_DEFAULT_DURATION_F);
  });

  it("chooses a graphics track id that avoids every existing project id", () => {
    const base = project();
    const collisionProject = { ...base, tracks: [
      base.tracks[0],
      { id: "graphics-2", kind: "video" as const, clips: [{ id: "graphics", media: { source: "media/clip.mp4", proxy: null }, srcInF: 0, srcOutF: 30, tlStartF: 0 }] },
    ] };
    const result = insertGraphic(collisionProject, "callout", 0);
    expect(result.project.tracks.at(-1)?.id).toBe("graphics-3");
  });

  it("updates only validated props and leaves the project untouched on invalid data", () => {
    const inserted = insertGraphic(project(), "chapter-card", 0);
    const itemId = inserted.itemId;
    const current = updateGraphic(inserted.project, itemId, { chapter: "02", title: "New", subtitle: "Next" });
    expect((current.tracks[1].items![0] as { props: { title: string } }).props.title).toBe("New");
    expect(updateGraphic(current, itemId, { title: "unknown" })).toBe(current);
  });

  it("uses deterministic pixel geometry for every preview layer", () => {
    const expected = {
      "lower-third": [845, 216, 538, 432], "callout": [806, 194, 557, 443],
      "evidence-card": [883, 259, 519, 411], "chapter-card": [883, 238, 519, 421],
      "simple-chart": [922, 292, 499, 394],
    } as const;
    for (const definition of GRAPHIC_DEFINITIONS) {
      const inserted = insertGraphic(project(), definition.id, 0);
      const item = inserted.project.tracks[1].items![0] as Parameters<typeof graphicGeometry>[0];
      const geometry = graphicGeometry(item, 1920, 1080);
      expect([geometry.box.width, geometry.box.height, geometry.box.x, geometry.box.y]).toEqual(expected[definition.id]);
      expect(geometry.style).toMatchObject({ borderPx: 2, backgroundAlpha: 0.86, paddingPx: 16 });
      expect(geometry.layers.every((layer) => Number.isInteger(layer.x) && Number.isInteger(layer.y)
        && Number.isInteger(layer.width) && Number.isInteger(layer.height))).toBe(true);
    }
  });

  it("matches the complete Python-generated descriptor for every component", () => {
    for (const definition of GRAPHIC_DEFINITIONS) {
      const inserted = insertGraphic(project(), definition.id, 0);
      const item = inserted.project.tracks[1].items![0] as Parameters<typeof graphicGeometry>[0];
      expect(JSON.parse(JSON.stringify(graphicGeometry(item, 1920, 1080)))).toEqual(GRAPHIC_GEOMETRY_GOLDEN[definition.id]);
      expect(JSON.parse(JSON.stringify(graphicGeometry(item, 3840, 2160)))).toEqual(GRAPHIC_GEOMETRY_GOLDEN_4K[definition.id]);
    }
  });

  it("clips long text with the descriptor's explicit one-line metrics", () => {
    const inserted = insertGraphic(project(), "lower-third", 0);
    const item = inserted.project.tracks[1].items![0] as Parameters<typeof graphicGeometry>[0];
    const geometry = graphicGeometry(item, 1920, 1080);
    const html = renderToStaticMarkup(renderGraphic({ ...item, props: { eyebrow: "A deliberately long reviewable graphic string ".repeat(4), title: "Title", detail: "" } } as Parameters<typeof renderGraphic>[0], geometry) as ReactElement);
    expect(html).toContain("…");
    expect(html).toContain(`line-height:${geometry.layers.find((layer) => layer.kind === "text")?.lineHeightPx}px`);
    expect(html).toContain("text-align:left");
    expect(html).toContain("box-shadow:none");
  });

  it("round-trips all five graphics through edit history and timeline movement", () => {
    for (const definition of GRAPHIC_DEFINITIONS) {
      const inserted = insertGraphic(project(), definition.id, 10);
      const itemId = inserted.itemId;
      const item = inserted.project.tracks[1].items![0];
      if (!("props" in item)) throw new Error("expected a graphic item");
      const props = JSON.parse(JSON.stringify(item.props)) as Record<string, unknown>;
      const editField = definition.id === "simple-chart"
        ? "title"
        : definition.requiredFields.find((field) => field !== "accent")!;
      props[editField] = "Edited graphic prop";
      const edited = updateGraphic(inserted.project, itemId, props);
      expect(edited.tracks[1].items![0]).toMatchObject({ props: { [editField]: "Edited graphic prop" } });
      const reloaded = JSON.parse(JSON.stringify(edited)) as Project;
      expect(reloaded.tracks[1].items![0]).toMatchObject({ type: "graphic", componentId: definition.id });
      expect(nudgeClip(reloaded, itemId, 5).tracks[1].items![0].tlStartF).toBe(15);
      expect(moveClip(reloaded, itemId, 40).tracks[1].items![0].tlStartF).toBe(40);
      // Stale/invalid revision-style updates are identity no-ops, preserving the current state.
      expect(updateGraphic(reloaded, "missing-graphic", definition.defaults)).toBe(reloaded);
    }
  });

  it("records and restores each graphic through the real project undo/redo wrappers", () => {
    for (const definition of GRAPHIC_DEFINITIONS) {
      const base = project();
      adoptSaved(base, 1);
      const inserted = insertGraphic(base, definition.id, 0);
      applyLocal(() => ({ project: inserted.project, selection: inserted.itemId }), { origin: "human", label: `Insert ${definition.id}` });
      expect(useProjectStore.getState().project?.tracks[1].items?.[0]).toMatchObject({ componentId: definition.id });
      undo();
      expect(useProjectStore.getState().project).toEqual(base);
      redo();
      expect(useProjectStore.getState().project?.tracks[1].items?.[0]).toMatchObject({ componentId: definition.id });
    }
  });
});
