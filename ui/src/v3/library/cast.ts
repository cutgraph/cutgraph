// The cast on the client: character profiles from the studio, each drawn by a
// motion-core rig, with custom expressions that are data (one option per rig part), validated here
// against the rig before they reach it. `castShot` is the preview's half of the character contract;
// pipeline/cast.py `resolve_shot` is the export's, and tests/fixtures/cast_shot_parity.json holds the
// two together. The video's brand rides along: the eyebrow over its animated headlines.
import {
  ACCENT, CHARACTERS, PACK_RIGS, EYEBROW, characterOf, isSpecOf, resolveShot,
  type Character, type PartSpec, type Performer, type ProjectPoster, type Shot as MotionShot,
} from "motion-core";
import type { Brand, CastExpression, CharacterProfile, VideoCast } from "../api";
import type { Costar, Shot, Thumbnail } from "../../types/project";
import { EXPRESSION_NAMES, nameOf } from "../story/shot/names";

const PART_NAMES: Record<string, { titles: Record<string, string>; options: Record<string, Record<string, string>> }> = {
  lens: {
    titles: { eyes: "Eyes", mouth: "Mouth" },
    options: {
      eyes: { open: "Open", arc: "Smile", wide: "Wide", tall: "Tall", side: "Sideways", narrow: "Narrow", closed: "Closed", uneven: "Uneven" },
      mouth: { smile: "Smile", grin: "Grin", round: "Round", flat: "Flat", small: "Small", crooked: "Crooked" },
    },
  },
};

const labelsOf = (id: string) => (has(PACK_RIGS, id) ? PACK_RIGS[id].labels : undefined)
  ?? (has(PART_NAMES, id) ? PART_NAMES[id] : undefined);
export const partTitle = (rig: Character, part: string): string => nameOf(labelsOf(rig.id)?.titles ?? {}, part);
export const optionName = (rig: Character, part: string, option: string): string => {
  const options = labelsOf(rig.id)?.options;
  return nameOf(options && has(options, part) ? options[part] : {}, option);
};

/** The Lens, the brand's built-in character: what every video casts until it has a cast. */
export const DEFAULT_PROFILE: CharacterProfile = Object.freeze({
  id: "default-character", name: "Lens", rig: "lens", art: [], palette: "periwinkle", approved: true, expressions: [], workspaceId: "brand", createdAt: "", available: true,
});

export type Cast = Readonly<VideoCast>;

/** Our brand: the eyebrow when the studio names no other. */
export const OUR_BRAND: Brand = Object.freeze({ name: EYEBROW, accent: ACCENT });

export const NO_CAST: Cast = Object.freeze({ characters: [DEFAULT_PROFILE], default: DEFAULT_PROFILE.id, workspace: null, brand: OUR_BRAND });

/** A brand as the studio sends it, ours where it names none. */
export function parseBrand(value: unknown): Brand {
  const b = record(value);
  return {
    name: typeof b?.name === "string" ? b.name : OUR_BRAND.name,
    accent: typeof b?.accent === "string" ? b.accent : OUR_BRAND.accent,
  };
}

export const brandOf = (cast: Cast): Brand => cast.brand ?? OUR_BRAND;

const has = (o: object, key: string): boolean => Object.prototype.hasOwnProperty.call(o, key);
const ID = /^[a-z0-9][a-z0-9-]{0,39}$/;
const ASSET_ID = /^[a-f0-9]{16}$/;
const record = (value: unknown): Record<string, unknown> | null =>
  value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;

export const rigOf = (profile: CharacterProfile): Character => characterOf(profile.rig);

export const isBuiltInExpression = (id: string, rig: Character): boolean => has(rig.expressions, id);

/** The rig's first built-in expression: where a new custom expression starts. */
export const firstSpec = (rig: Character): PartSpec => Object.values(rig.expressions)[0];

/** A custom expression from the studio, or null when it is not one this rig can draw. */
export function parseExpression(value: unknown, rig: Character): CastExpression | null {
  const v = record(value);
  if (!v || typeof v.id !== "string" || !ID.test(v.id) || isBuiltInExpression(v.id, rig)) return null;
  if (typeof v.name !== "string" || !v.name.trim() || !isSpecOf(rig, v.spec)) return null;
  const spec = Object.fromEntries(Object.keys(rig.parts).map((part) => [part, (v.spec as PartSpec)[part]]));
  return { id: v.id, name: v.name, spec, createdAt: String(v.createdAt ?? ""), ...(v.draft === true && { draft: true as const }) };
}

/**
 * A character profile from the studio, keeping only the expressions and art its rig can use. Without an
 * approval flag, a client's rig is a draft and a brand rig is approved, as the studio reads it.
 */
export function parseCharacter(value: unknown): CharacterProfile | null {
  const v = record(value);
  if (!v || typeof v.id !== "string" || !ID.test(v.id) || typeof v.rig !== "string") return null;
  if (typeof v.workspaceId !== "string") return null;
  const available = has(CHARACTERS, v.rig);
  if (!available) return {
    ...v, available: false, name: typeof v.name === "string" && v.name.trim() ? v.name : v.id,
    art: Array.isArray(v.art) ? v.art : [],
    // Stored as-is on the studio; only entries the UI can name are listed here.
    expressions: (Array.isArray(v.expressions) ? v.expressions : []).filter((e) => {
      const x = record(e);
      return !!x && typeof x.id === "string" && typeof x.name === "string";
    }),
  } as unknown as CharacterProfile;
  if (typeof v.name !== "string" || !v.name.trim()) return null;
  const rig = characterOf(v.rig);
  const expressions: CastExpression[] = [];
  for (const raw of Array.isArray(v.expressions) ? v.expressions : []) {
    const e = parseExpression(raw, rig);
    if (e && !expressions.some((x) => x.id === e.id)) expressions.push(e);
  }
  const art = [...new Set((Array.isArray(v.art) ? v.art : []).filter((a): a is string => typeof a === "string" && ASSET_ID.test(a)))];
  const palette = typeof v.palette === "string" && rig.palettes.includes(v.palette) ? v.palette : rig.palettes[0];
  const approved = typeof v.approved === "boolean" ? v.approved : rig.owner !== "client";
  return { id: v.id, name: v.name, rig: v.rig, art, palette, approved, expressions, workspaceId: v.workspaceId, createdAt: String(v.createdAt ?? ""), available };
}

/** A video's cast from the studio (GET /api/cast), or null when it has no character to draw. */
export function parseCast(value: unknown): Cast | null {
  const v = record(value);
  if (!v) return null;
  const characters: CharacterProfile[] = [];
  for (const raw of Array.isArray(v.characters) ? v.characters : []) {
    const c = parseCharacter(raw);
    if (c && !characters.some((x) => x.id === c.id)) characters.push(c);
  }
  if (!characters.length) return null;
  const ws = record(v.workspace);
  return Object.freeze({
    characters,
    default: characters.some((c) => c.id === v.default) ? (v.default as string) : characters[0].id,
    workspace: ws && typeof ws.id === "string" && typeof ws.name === "string" ? { id: ws.id, name: ws.name } : null,
    brand: parseBrand(v.brand),
  });
}

/** The profile a shot casts: the one it names when the video can see it, else the video's default. */
export function characterFor(cast: Cast, id: string | undefined): CharacterProfile {
  const find = (x: string | undefined) => (x === undefined ? undefined : cast.characters.find((c) => c.id === x));
  return find(id) ?? find(cast.default) ?? cast.characters[0] ?? DEFAULT_PROFILE;
}

export function customSpec(profile: CharacterProfile, id: string | undefined): PartSpec | undefined {
  const spec = profile.expressions?.find((e) => e.id === id)?.spec;
  return isSpecOf(rigOf(profile), spec) ? spec : undefined;
}

const paletteOf = (profile: CharacterProfile, rig: Character): string =>
  rig.palettes.includes(profile.palette) ? profile.palette : rig.palettes[0];

// A look (the shot, its co-star or an ensemble member) cast as the profile it names: that profile's rig
// and palette, and the spec of its custom expression; an expression it can't draw is dropped.
function castLook<T extends { character?: string; expression?: string }>(stored: T, cast: Cast): { look: T & { character: string; palette: string }; custom?: PartSpec } {
  const profile = characterFor(cast, stored.character);
  const rig = rigOf(profile);
  const look = { ...stored, character: profile.rig, palette: paletteOf(profile, rig) };
  const id = stored.expression;
  if (!id || isBuiltInExpression(id, rig)) return { look };
  const custom = customSpec(profile, id);
  return custom ? { look, custom } : { look: { ...look, expression: undefined } };
}

/**
 * A scene's stored shot resolved for drawing: its character profile becomes that profile's rig and
 * palette, then motion-core's resolveShot, plus the spec of the profile's custom expression. An
 * expression that is neither the rig's built-in nor the profile's (a deleted one, or another
 * character's) falls back to the role's look, exactly as the export resolves it. A duo's co-star is
 * cast the same way against its own profile, and so is each ensemble member. The eyebrow is the shot's
 * own (an empty one hides it), else the video's brand name, in the brand's accent.
 */
export function castShot(stored: Shot | undefined, role: string, title: string, cast: Cast): MotionShot {
  const brand = brandOf(cast);
  const { look: profileLook, custom } = castLook(stored ?? {}, cast);
  const look = {
    ...profileLook, eyebrow: stored?.eyebrow ?? brand.name, accent: brand.accent,
  };
  const performer = (member: Costar) => {
    const { look: m, custom: spec } = castLook(member, cast);
    return { ...m, custom: spec };
  };
  const shot = resolveShot({
    ...look,
    ...(stored?.costar && { costar: performer(stored.costar) }),
    ...(stored?.ensemble && { ensemble: stored.ensemble.map(performer) }),
  }, role, title);
  return custom ? { ...shot, custom } : shot;
}

/**
 * A thumbnail with its defaults filled in, cast for drawing like a shot (castShot): the lead and
 * each ensemble member become their profiles' rigs and palettes with their custom specs, and the eyebrow
 * takes the brand's accent. The export resolves it the same way (pipeline/thumbnail.py render_request).
 */
export function castPoster(stored: Thumbnail, cast: Cast): ProjectPoster {
  const { look, custom } = castLook(stored, cast);
  const ensemble = stored.ensemble?.map((member) => {
    const { look: m, custom: spec } = castLook(member, cast);
    return spec ? { ...m, custom: spec } : m;
  });
  return { ...look, accent: brandOf(cast).accent, ...(ensemble && { ensemble }), ...(custom && { custom }) };
}

/** A performer of this profile: its rig, its palette, and the custom spec its expression id names. */
export function performerOf(profile: CharacterProfile, state: Omit<Performer, "character" | "palette" | "custom">): Performer {
  const rig = rigOf(profile);
  return { ...state, character: profile.rig, palette: paletteOf(profile, rig), custom: customSpec(profile, state.expression) };
}

export function expressionName(id: string, profile: CharacterProfile): string {
  return profile.expressions?.find((e) => e.id === id)?.name ?? nameOf(EXPRESSION_NAMES, id);
}

/** The rigs a character can be drawn by where it lives: a client's rig never joins the brand cast. */
export const rigsFor = (workspaceId: string): Character[] =>
  Object.values(CHARACTERS).filter((rig) => workspaceId !== "brand" || rig.owner === "brand");

/** Where a character lives, as the cast shows it: the brand, or the video's workspace. */
export function homeName(profile: CharacterProfile, cast: Cast): string {
  return profile.workspaceId === "brand" ? "Brand" : cast.workspace?.id === profile.workspaceId ? cast.workspace.name : "Workspace";
}
