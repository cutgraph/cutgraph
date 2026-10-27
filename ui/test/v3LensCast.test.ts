import { createHash } from "node:crypto";
import { expect, it } from "vitest";
import { CHARACTERS, LENS, type PartSpec } from "motion-core";
import { nodeEnv, surface } from "../../motion-core/scripts/node-env.ts";
import {
  DEFAULT_PROFILE, NO_CAST, castShot, expressionName, optionName, parseCharacter, partTitle, rigsFor,
} from "../src/v3/library/cast";
import { paintHeadTile } from "../src/v3/library/paint";
import { paintCharacterTile } from "../src/v3/story/shot/paint";
import { EXPRESSION_NAMES, MOVE_NAMES, POSE_NAMES } from "../src/v3/story/shot/names";

const spec: PartSpec = { eyes: "side", mouth: "crooked" };
const profile = {
  id: "lens", name: "Lens", rig: "lens", palette: "periwinkle", art: [], workspaceId: "brand", createdAt: "",
  expressions: [{ id: "quizzical", name: "Quizzical", spec, createdAt: "" }],
};

it("offers Lens with valid profile data and names every part and shared look", () => {
  expect(CHARACTERS.lens).toBe(LENS);
  expect(rigsFor("brand")).toContain(LENS);
  expect(parseCharacter(profile)).toEqual({ ...profile, approved: true, available: true });
  expect(parseCharacter({ ...profile, palette: "unknown" })?.palette).toBe("periwinkle");
  expect(NO_CAST.default).toBe(DEFAULT_PROFILE.id);
  expect(expressionName("quizzical", { ...profile, approved: true })).toBe("Quizzical");
  for (const [part, options] of Object.entries(LENS.parts)) {
    expect(partTitle(LENS, part)).toBe(part === "eyes" ? "Eyes" : "Mouth");
    for (const option of options) expect(optionName(LENS, part, option)).not.toBe(option);
  }
  for (const id of Object.keys(LENS.expressions)) expect(EXPRESSION_NAMES[id]).toBeTruthy();
  for (const id of LENS.poses) expect(POSE_NAMES[id]).toBeTruthy();
  for (const id of LENS.moves) expect(MOVE_NAMES[id]).toBeTruthy();
});

it("casts Lens custom expressions on the lead, co-star and ensemble", () => {
  const cast = { ...NO_CAST, characters: [...NO_CAST.characters, { ...profile, approved: true }] };
  const look = { character: "lens", expression: "quizzical", pose: "flex", move: "sway" };
  const shot = castShot({ mode: "animation", ...look, costar: look, ensemble: [look] }, "Beat", "", cast);
  for (const performer of [shot, shot.costar!, shot.ensemble![0]]) {
    expect([performer.character, performer.palette, performer.custom]).toEqual(["lens", "periwinkle", spec]);
    expect([performer.pose, performer.move]).toEqual(["flex", "sway"]);
  }
});

it("crops the sample head and keeps all eye and mouth options visible", () => {
  const env = nodeEnv();
  const head = (custom: PartSpec, whole = false) => {
    const { ctx } = surface(192, 208);
    const paint = whole ? paintCharacterTile : paintHeadTile;
    paint(ctx, 192, 208, { character: "lens", custom, t: 0.6 }, env);
    return createHash("sha256").update(ctx.getImageData(0, 0, 192, 208).data).digest("hex");
  };
  for (const [part, options] of Object.entries(LENS.parts)) {
    expect(new Set(options.map((option) => head({ ...LENS.expressions.neutral, [part]: option }))).size).toBe(options.length);
  }
  expect(head(spec)).not.toBe(head(spec, true));
  expect(head(spec)).toBe(head(spec));
});
