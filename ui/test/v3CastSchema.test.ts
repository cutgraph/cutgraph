import { CHARACTERS as testRegistry } from "motion-core";
import { SYNTHETIC_RIG as SAMPLE_RIG } from "../../motion-core/test/synthetic-rig.ts";
const previousRig = testRegistry[SAMPLE_RIG.id];
Object.assign(testRegistry, { [SAMPLE_RIG.id]: SAMPLE_RIG });
afterAll(() => {
  if (previousRig) Object.assign(testRegistry, { [SAMPLE_RIG.id]: previousRig });
  else Reflect.deleteProperty(testRegistry, SAMPLE_RIG.id);
});
import { characterOf as defaultRigOf, DEFAULT_CHARACTER as defaultRigId } from "motion-core";
const DEFAULT_RIG = defaultRigOf(defaultRigId);
import { createHash } from "node:crypto";
import { afterAll, describe, expect, it } from "vitest";
import { LENS_EXPRESSIONS,  resolveShot, shotTiming, type PartSpec, type Performer } from "motion-core";
import { nodeEnv, surface } from "../../motion-core/scripts/node-env.ts";
import { paintScene } from "../src/preview/SceneLayer";
import type { CharacterProfile } from "../src/v3/api";
import {
  DEFAULT_PROFILE, NO_CAST, castShot, characterFor, expressionName, homeName, optionName, parseCast, parseCharacter,
  parseExpression, partTitle, performerOf, rigsFor, type Cast,
} from "../src/v3/library/cast";
import { paintHeadTile } from "../src/v3/library/paint";
import { paintCharacterTile } from "../src/v3/story/shot/paint";

const SMUG: PartSpec = { eyes: "side", mouth: "smile" };
const expr = (id: string, name: string, spec: PartSpec) => ({ id, name, spec, createdAt: "2026-09-23T00:00:00Z" });
const profile = (id: string, name: string, workspaceId: string, expressions = [] as ReturnType<typeof expr>[]): CharacterProfile => ({
  id, name, rig: "lens", art: [], palette: "periwinkle", approved: true, expressions, workspaceId, createdAt: "t", available: true,
});
const FOCUS: PartSpec = { brows: "raised", eyes: "open", mouth: "closed" };
const TEAL_GUEST: CharacterProfile = {
  ...profile("sample-performer", "Notes Sample", "garden-1", [expr("focus", "Quiet focus", FOCUS)]), rig: "sample-rig", palette: "fern", approved: false,
};
const DEFAULT_PROFILES = profile("default-character", "Lens", "brand", [expr("smug", "Smug", SMUG), expr("beaming", "Beaming", LENS_EXPRESSIONS.happy)]);
const GUEST = profile("notes-sample", "Notes Sample", "garden-1", [expr("grin", "Grin", LENS_EXPRESSIONS.excited)]);
const CAST: Cast = { characters: [DEFAULT_PROFILES, GUEST], default: "default-character", workspace: { id: "garden-1", name: "Garden" } };

describe("character profiles from the studio", () => {
  it("renders expression names and scene looks for incomplete unavailable profiles", () => {
    const stored = { id: "stored-character", rig: "retired-rig", workspaceId: "brand" };
    const parsed = parseCharacter(stored)!;
    expect(parsed.name).toBe(stored.id);
    expect(parsed.expressions).toEqual([]);
    expect(expressionName("stored-look", parsed)).toBe("Stored-look");
    const cast = { ...NO_CAST, characters: [parsed], default: parsed.id };
    expect(castShot({ mode: "animation", character: parsed.id, expression: "stored-look" }, "Beat", "", cast).character)
      .toBe(stored.rig);
    expect(performerOf(parsed, { t: 0 }).custom).toBeUndefined();
  });
  it("lists only nameable expressions of an unavailable profile, so malformed stored ones never crash", () => {
    const parsed = parseCharacter({ id: "stored-character", name: "Stored", rig: "retired-rig", workspaceId: "brand",
      expressions: [null, "loose", { id: "no-name" }, { id: "stored-look", name: "Stored look", spec: {} }] })!;
    expect(parsed.expressions.map((e) => e.id)).toEqual(["stored-look"]);
    expect(expressionName("stored-look", parsed)).toBe("Stored look");
    expect(performerOf(parsed, { t: 0, expression: "no-name" }).custom).toBeUndefined();
  });
  it("keeps exactly what the character's rig can draw", () => {
    expect(parseExpression({ id: "smug", name: "Smug", spec: SMUG, createdAt: "t" }, DEFAULT_RIG)).toEqual({ id: "smug", name: "Smug", spec: SMUG, createdAt: "t" });
    for (const bad of [
      { id: "happy", name: "Shadow", spec: SMUG },
      { id: "../x", name: "Escape", spec: SMUG },
      { id: "Smug", name: "Caps", spec: SMUG },
      { id: "smug", name: "  ", spec: SMUG },
      { id: "smug", name: "Smug", spec: { ...SMUG, eyes: "laser" } },
      { id: "smug", name: "Smug", spec: { ...SMUG, extra: "grin" } },
      { id: "smug", name: "Smug", spec: { eyes: "arc", character: "up" } },
      null,
    ]) {
      expect(parseExpression(bad, DEFAULT_RIG)).toBeNull();
    }
  });

  it("keeps an unavailable profile intact and carries its rig id into the preview", () => {
    const stored = { ...DEFAULT_PROFILES, id: "stored-character", rig: "unregistered-rig", palette: "stored-palette",
      expressions: [expr("stored-look", "Stored look", { storedPart: "stored-option" })], extension: { nested: [1, null] } };
    const parsed = parseCharacter(stored)!;
    expect(parsed).toEqual({ ...stored, available: false });
    const cast = { ...CAST, characters: [parsed], default: parsed.id };
    const shot = { mode: "animation" as const, character: parsed.id, headline: "Edited headline", expression: "stored-look" };
    expect(castShot(shot, "Beat", "Scene", cast).character).toBe(stored.rig);
    expect(castShot(shot, "Beat", "Scene", cast).custom).toBeUndefined();
    expect(shot.character).toBe(parsed.id);
    expect(performerOf(parsed, { t: 0 }).character).toBe(stored.rig);
  });

  it("parses a profile, dropping art and expressions it can't use", () => {
    const parsed = parseCharacter({
      ...GUEST, art: ["0123456789abcdef", "0123456789abcdef", "../x", 3], palette: "clay",
      expressions: [...GUEST.expressions, { id: "bad", name: "Bad", spec: { unknownPart: "wide" } }, GUEST.expressions[0]],
    });
    expect(parsed).toEqual({ ...GUEST, art: ["0123456789abcdef"], palette: "periwinkle" });
    expect(parseCharacter({ ...GUEST, id: "Bad Id" })).toBeNull();
    expect(parseCharacter({ ...GUEST, workspaceId: undefined })).toBeNull();
  });

  it("parses a video's cast, with a default it can see", () => {
    expect(parseCast({ characters: [DEFAULT_PROFILES, GUEST], default: "notes-sample", workspace: { id: "garden-1", name: "Garden" } }))
      .toEqual({ characters: [DEFAULT_PROFILES, GUEST], default: "notes-sample", workspace: { id: "garden-1", name: "Garden" }, brand: { name: "", accent: "#f5c518" } });
    const brand = { name: "MUSEUM VISIT", accent: "#2bb3a3" };
    expect(parseCast({ characters: [DEFAULT_PROFILES], default: "default-character", workspace: null, brand })?.brand).toEqual(brand);
    expect(parseCast({ characters: [DEFAULT_PROFILES], default: "default-character", workspace: null, brand: { name: 3 } })?.brand)
      .toEqual({ name: "", accent: "#f5c518" });
    expect(parseCast({ characters: [DEFAULT_PROFILES], default: "notes-sample", workspace: null })?.default).toBe("default-character");
    expect(parseCast({ characters: [], default: "x" })).toBeNull();
    expect(parseCast("nope")).toBeNull();
  });

  it("reads the approval flag, a client rig's draft by default", () => {
    const { approved: _, ...stored } = TEAL_GUEST;
    expect(parseCharacter(stored)?.approved).toBe(false);
    expect(parseCharacter({ ...stored, approved: true })?.approved).toBe(true);
    expect(parseCharacter({ ...GUEST, approved: undefined })?.approved).toBe(true);
    expect(parseCharacter({ ...TEAL_GUEST, palette: "periwinkle" })?.palette).toBe("fog");
    expect(parseCharacter({ ...TEAL_GUEST, expressions: [...TEAL_GUEST.expressions, expr("smug", "Smug", SMUG)] })?.expressions)
      .toEqual(TEAL_GUEST.expressions);
    expect(DEFAULT_PROFILE.approved).toBe(true);
  });

  it("offers a client's rig only in a client project", () => {
    expect(rigsFor("brand").map((r) => r.id)).toEqual(["lens"]);
    expect(rigsFor("garden-1").map((r) => [r.id, r.name, r.owner])).toEqual([["lens", "Lens", "brand"], ["sample-rig", "Sample", "client"]]);
  });

  it("names parts, options and homes for people", () => {
    expect([partTitle(DEFAULT_RIG, "eyes"), partTitle(DEFAULT_RIG, "mouth"), optionName(DEFAULT_RIG, "eyes", "side"), optionName(DEFAULT_RIG, "mouth", "grin")])
      .toEqual(["Eyes", "Mouth", "Sideways", "Grin"]);
    expect([homeName(DEFAULT_PROFILES, CAST), homeName(GUEST, CAST)]).toEqual(["Brand", "Garden"]);
    expect(NO_CAST.characters).toEqual([DEFAULT_PROFILE]);
    expect(Object.keys(SAMPLE_RIG.parts).map((part) => partTitle(SAMPLE_RIG, part))).toEqual(["Brows", "Eyes", "Mouth"]);
    expect([optionName(SAMPLE_RIG, "brows", "level"), optionName(SAMPLE_RIG, "mouth", "closed"), optionName(SAMPLE_RIG, "eyes", "open")])
      .toEqual(["Level", "Closed", "Open"]);
  });
});

describe("resolving a scene's shot against the video's cast", () => {
  it("casts the named profile the video can see, else its default", () => {
    expect(characterFor(CAST, "notes-sample")).toBe(GUEST);
    expect(characterFor(CAST, undefined)).toBe(DEFAULT_PROFILES);
    expect(characterFor(CAST, "someone-elses")).toBe(DEFAULT_PROFILES);
    expect(characterFor({ ...CAST, default: "notes-sample" }, undefined)).toBe(GUEST);
  });

  it("brings the profile's own custom expression, with its rig and palette", () => {
    const shot = castShot({ mode: "cameo", character: "notes-sample", expression: "grin", pose: "wave" }, "Hook", "Open", CAST);
    expect(shot).toEqual({ ...resolveShot({ pose: "wave", expression: "grin" }, "Hook", "Open"), custom: LENS_EXPRESSIONS.excited });
    expect([shot.character, shot.palette]).toEqual(["lens", "periwinkle"]);
  });

  it("falls back to the role's look for a deleted expression or another character's", () => {
    for (const shot of [{ expression: "gone" }, { character: "notes-sample", expression: "smug" }]) {
      const resolved = castShot({ mode: "cameo", ...shot }, "Wrap", "Close", CAST);
      expect(resolved).toEqual(resolveShot({ mode: "cameo" }, "Wrap", "Close"));
      expect(resolved.custom).toBeUndefined();
    }
  });

  it("casts the Sample on its own rig and palette, with its own expressions", () => {
    const cast: Cast = { ...CAST, characters: [...CAST.characters, TEAL_GUEST] };
    const wave = castShot({ mode: "animation", character: "sample-performer", pose: "wave", expression: "happy" }, "Payoff", "Launch", cast);
    expect(wave).toEqual(resolveShot({ mode: "animation", character: "sample-rig", palette: "fern", pose: "wave", expression: "happy" }, "Payoff", "Launch"));
    expect([wave.character, wave.palette, wave.pose, wave.expression, wave.move]).toEqual(["sample-rig", "fern", "wave", "happy", "idle"]);
    expect(castShot({ character: "sample-performer", expression: "focus" }, "Hook", "Open", cast).custom).toEqual(FOCUS);
    const defaultOnly = castShot({ character: "sample-performer", pose: "shrug", expression: "idea" }, "Beat", "Mid", cast);
    expect([defaultOnly.pose, defaultOnly.expression]).toEqual(["rest", "neutral"]);
  });

  it("leaves built-in and missing expressions to motion-core", () => {
    expect(castShot({ expression: "excited" }, "Beat", "Mid", CAST)).toEqual(resolveShot({ expression: "excited" }, "Beat", "Mid"));
    expect(castShot(undefined, "Beat", "Mid", CAST)).toEqual(resolveShot(undefined, "Beat", "Mid"));
  });

  it("names customs by their name on the profile and built-ins by the library's", () => {
    expect(expressionName("smug", DEFAULT_PROFILES)).toBe("Smug");
    expect(expressionName("grin", GUEST)).toBe("Grin");
    expect(expressionName("idea", GUEST)).toBe("Idea");
  });
});

describe("rendering a character", () => {
  const env = nodeEnv();
  const hash = (paint: (ctx: CanvasRenderingContext2D) => void, w = 192, h = 208): string => {
    const { canvas, ctx } = surface(w, h);
    paint(ctx);
    return createHash("sha256").update(canvas.toBuffer("image/png")).digest("hex");
  };
  const tile = (who: CharacterProfile, state: Omit<Performer, "character" | "palette" | "custom">) =>
    hash((ctx) => paintCharacterTile(ctx, 192, 208, performerOf(who, state), env));
  const base = { pose: "point", move: "idle", t: 1.3 };

  it("is deterministic", () => {
    expect(tile(DEFAULT_PROFILES, { ...base, expression: "smug" })).toBe(tile(DEFAULT_PROFILES, { ...base, expression: "smug" }));
    expect(hash((ctx) => paintHeadTile(ctx, 192, 208, { custom: SMUG, t: 0.6 }, env)))
      .toBe(hash((ctx) => paintHeadTile(ctx, 192, 208, { custom: SMUG, t: 0.6 }, nodeEnv())));
  });

  it("uses the declared head crop and otherwise shows the whole rig with its own parts", () => {
    const head = (performer: Performer) => hash((ctx) => paintHeadTile(ctx, 192, 208, performer, env));
    const sample = (custom: PartSpec) => head({ character: "sample-rig", palette: "fern", custom, t: 0.6 });
    const faces = [FOCUS, { ...FOCUS, brows: "level" }, { ...FOCUS, eyes: "closed" }, { ...FOCUS, mouth: "open" }];
    expect(new Set(faces.map(sample)).size).toBe(4);
    expect(sample(FOCUS)).toBe(sample(FOCUS));
    expect(sample(FOCUS)).toBe(hash((ctx) => paintCharacterTile(ctx, 192, 208, { character: "sample-rig", palette: "fern", custom: FOCUS, t: 0.6 }, env)));
    expect(new Set(["smile", "crooked", "grin", "round", "flat"].map((mouth) =>
      head({ character: "lens", palette: "periwinkle", custom: { eyes: "open", mouth }, t: 0.6 }))).size).toBe(5);
    const lens = { character: "lens", palette: "periwinkle", custom: SMUG, t: 0.6 };
    expect(head(lens)).not.toBe(hash((ctx) => paintCharacterTile(ctx, 192, 208, lens, env)));
  });

  it("draws a profile's own parts: the same parts as a built-in draw that built-in's face", () => {
    expect(tile(DEFAULT_PROFILES, { ...base, expression: "beaming" })).toBe(tile(DEFAULT_PROFILES, { ...base, expression: "happy" }));
    expect(tile(GUEST, { ...base, expression: "grin" })).toBe(tile(DEFAULT_PROFILES, { ...base, expression: "excited" }));
    expect(tile(DEFAULT_PROFILES, { ...base, expression: "smug" })).not.toBe(tile(DEFAULT_PROFILES, { ...base, expression: "happy" }));
    expect(tile(GUEST, { ...base, expression: "smug" })).toBe(tile(DEFAULT_PROFILES, { ...base, expression: "neutral" }));
  });

  it("draws the same scene frame in the player as the shot it resolves to", () => {
    const timing = shotTiming(0, 12, null, 30);
    const frame = (shot: { character?: string; expression: string }) => hash((ctx) => paintScene(ctx, {
      mode: "animation", shot: castShot({ mode: "animation", ...shot }, "Hook", "Open", CAST), timing,
    }, 1.5, 1920, 1080, env), 480, 270);
    expect(frame({ expression: "smug" })).toBe(frame({ expression: "smug" }));
    expect(frame({ expression: "beaming" })).toBe(frame({ expression: "happy" }));
    expect(frame({ character: "notes-sample", expression: "grin" })).toBe(frame({ expression: "excited" }));
    expect(frame({ expression: "gone" })).toBe(frame({ expression: "confused" }));
  });
});
