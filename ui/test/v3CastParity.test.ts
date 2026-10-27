// Character and custom-expression parity with the export: for each stored shot in the shared fixture,
// the preview draws exactly the Shot the Node renderer resolves from the render request the export
// sends it (tests/test_cast_render.py holds pipeline/cast.py resolve_shot to the same `expected`). The
// fixture includes Lens profiles and a minimal synthetic rig in a separate workspace.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterAll, describe, expect, it } from "vitest";
import { CHARACTERS, resolveShot, type PartSpec, type ProjectShot } from "motion-core";
import { castShot, characterFor, NO_CAST, parseCast } from "../src/v3/library/cast";
import type { Shot } from "../src/types/project";

import { SYNTHETIC_RIG } from "../../motion-core/test/synthetic-rig.ts";
const previousRig = CHARACTERS[SYNTHETIC_RIG.id];
Object.assign(CHARACTERS, { [SYNTHETIC_RIG.id]: SYNTHETIC_RIG });
afterAll(() => {
  if (previousRig) Object.assign(CHARACTERS, { [SYNTHETIC_RIG.id]: previousRig });
  else Reflect.deleteProperty(CHARACTERS, SYNTHETIC_RIG.id);
});

interface Case {
  role: string;
  title: string;
  shot: ProjectShot | null;
  expected: { shot: ProjectShot; custom: PartSpec | null };
}

const fx = JSON.parse(readFileSync(resolve(__dirname, "../../tests/fixtures/cast_shot_parity.json"), "utf-8")) as {
  cast: unknown;
  cases: Case[];
};

describe("character and custom expression parity with the export", () => {
  const cast = parseCast(fx.cast)!;

  it("parses the studio's cast for the video", () => {
    expect(cast.characters.map((c) => [c.id, c.rig, c.palette, c.workspaceId])).toEqual([
      ["default-character", "lens", "periwinkle", "brand"],
      ["notes-guest", "lens", "periwinkle", "garden-space"],
      ["visitor-guide", "sample-rig", "fern", "garden-space"],
      ["stored-character", "retired-rig", "stored-palette", "brand"],
    ]);
    expect(cast.default).toBe("default-character");
    expect(cast.workspace).toEqual({ id: "garden-space", name: "Garden" });
    expect(cast.brand).toEqual({ name: "MUSEUM VISIT", accent: "#2bb3a3" });
  });

  it("carries the video's brand as the eyebrow, unless a shot names its own or hides it", () => {
    const eyebrows = fx.cases.map((c) => [c.shot?.eyebrow, c.expected.shot.eyebrow, c.expected.shot.accent]);
    expect(eyebrows).toContainEqual([undefined, "MUSEUM VISIT", "#2bb3a3"]);
    expect(eyebrows).toContainEqual(["FIELD NOTES", "FIELD NOTES", "#2bb3a3"]);
    expect(eyebrows).toContainEqual(["", "", "#2bb3a3"]);
    expect(parseCast({ ...(fx.cast as object), brand: undefined })?.brand).toEqual({ name: "", accent: "#f5c518" });
    expect(castShot(undefined, "Beat", "x", NO_CAST)).toMatchObject({ eyebrow: "", accent: "#f5c518" });
    expect(castShot({ mode: "animation", layout: "signoff" }, "Beat", "x", NO_CAST).mark).toBeUndefined();
    expect(castShot({ mode: "animation", layout: "signoff" }, "Payoff", "x", cast).mark).toBeUndefined();
    expect(castShot({ mode: "animation", layout: "signoff", mark: "END" }, "Payoff", "x", cast).mark).toBe("END");
  });

  it("resolves every fixture shot to what the Node renderer draws", () => {
    for (const { role, title, shot, expected } of fx.cases) {
      const node = resolveShot(expected.shot, role, title);
      expect(castShot((shot ?? undefined) as Shot | undefined, role, title, cast)).toEqual(expected.custom ? { ...node, custom: expected.custom } : node);
    }
  });

  it("covers the synthetic rig: its palette, a supported pose, its custom face and the default rig's ids", () => {
    const guest = fx.cases.filter((c) => c.shot?.character === "visitor-guide");
    expect(new Set(guest.map((c) => `${c.expected.shot.character}/${c.expected.shot.palette}`))).toEqual(new Set(["sample-rig/fern"]));
    expect(guest.map((c) => c.shot?.pose)).toEqual(expect.arrayContaining(["wave", "not-a-pose"]));
    expect(guest.some((c) => c.expected.custom)).toBe(true);
  });

  it("covers the Garden profile's own expression, another character's, a built-in and an unknown profile", () => {
    const guest = fx.cases.filter((c) => c.shot?.character === "notes-guest");
    expect(guest.map((c) => c.shot?.expression)).toEqual(expect.arrayContaining(["grin", "smug", "curious"]));
    expect(guest.some((c) => c.expected.custom)).toBe(true);
    expect(characterFor(cast, "gone-client-character").id).toBe(cast.default);
    const ids = fx.cases.map((c) => c.shot?.expression);
    expect(ids).toEqual(expect.arrayContaining(["smug", "deleted-one", "happy", undefined]));
  });
});
