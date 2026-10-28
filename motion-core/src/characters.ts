// The character registry: every rig motion-core can draw, behind one interface. A shot names its
// character by registry id; hosts map their own character profiles onto these ids.

import { LENS } from './lens.ts';
import { clamp, text } from './draw.ts';
import { rigShift } from './rig.ts';
import type { Character, CharacterState, Ctx, MotionEnv, PartSpec, Shot } from './types.ts';

export const CHARACTERS: Readonly<Record<string, Character>> = { lens: LENS };
const registry = CHARACTERS as Record<string, Character>;
const packRigs: Record<string, PackRig> = Object.create(null);
export const PACK_RIGS: Readonly<Record<string, PackRig>> = packRigs;
export const DEFAULT_CHARACTER = LENS.id;

export interface HeadCrop {
  rows: number;
  foot: number;
  span: number;
  cols: number;
  centre: number;
}

export interface PackRig {
  id: string;
  name: string;
  owner: 'brand' | 'client';
  palettes: string[];
  parts: Record<string, string[]>;
  expressions: string[];
  poses: string[];
  moves: string[];
  labels?: { titles: Record<string, string>; options: Record<string, Record<string, string>> };
  head?: HeadCrop;
}

// JavaScript's $ also accepts a final newline; require the absolute end for Python parity.
export const PACK_ID = /^[a-z0-9][a-z0-9-]{0,39}$(?![\s\S])/;
const record = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const ids = (v: unknown): v is string[] => Array.isArray(v) && v.every((s) => typeof s === 'string' && PACK_ID.test(s));
const strings = (v: unknown): boolean => record(v) && Object.values(v).every((s) => typeof s === 'string');

function validRig(v: unknown): v is PackRig {
  if (!record(v) || typeof v.id !== 'string' || !PACK_ID.test(v.id)
    || typeof v.name !== 'string' || !v.name || !['brand', 'client'].includes(v.owner as string)
    || !ids(v.palettes) || !v.palettes.length || !record(v.parts)
    || !Object.entries(v.parts).every(([part, options]) => PACK_ID.test(part) && ids(options) && !!options.length)
    || !ids(v.expressions) || !v.expressions.length || !ids(v.poses) || !ids(v.moves)) return false;
  if (v.labels !== undefined && (!record(v.labels) || !strings(v.labels.titles)
    || !record(v.labels.options) || !Object.values(v.labels.options).every(strings))) return false;
  if (v.head !== undefined && (!record(v.head)
    || !['rows', 'foot', 'span', 'cols', 'centre'].every((key) => typeof (v.head as Record<string, unknown>)[key] === 'number'
      && Number.isFinite((v.head as Record<string, number>)[key]))
    || !['rows', 'span', 'cols'].every((key) => (v.head as Record<string, number>)[key] > 0))) return false;
  return true;
}

const sameIds = (a: unknown, b: readonly string[]): boolean => ids(a) && a.length === b.length && a.every((s, i) => s === b[i]);

export interface PackManifest {
  id: string;
  rigs: PackRig[];
}

/** Validate metadata without evaluating a pack's source code. */
export function validatePackManifest(manifest: unknown, packId?: string): PackRig[] {
  if (packId !== undefined && !PACK_ID.test(packId)) throw new Error('invalid pack id');
  if (!record(manifest) || !Array.isArray(manifest.characters) || !manifest.characters.every(validRig)) {
    throw new Error('malformed manifest');
  }
  const rigs: PackRig[] = manifest.characters;
  if (new Set(rigs.map((rig) => rig.id)).size !== rigs.length) throw new Error('duplicate rig id in manifest');
  return rigs;
}

/** All valid manifests reserve their ids before any clashing pack is skipped. */
export function selectPackManifests(entries: { id: string; manifest: unknown }[], builtinIds: readonly string[] = [LENS.id]): PackManifest[] {
  const candidates: PackManifest[] = [];
  const counts = new Map<string, number>();
  for (const { id, manifest } of entries) {
    try {
      const rigs = validatePackManifest(manifest, id);
      candidates.push({ id, rigs });
      for (const rig of rigs) counts.set(rig.id, (counts.get(rig.id) ?? 0) + 1);
    } catch (error) {
      console.warn(`Skipping character pack ${id}: ${error instanceof Error ? error.message : String(error)}`);
    }
  }
  return candidates.filter(({ id, rigs }) => {
    const clashes = rigs.filter((rig) => builtinIds.includes(rig.id) || (counts.get(rig.id) ?? 0) > 1);
    if (!clashes.length) return true;
    console.warn(`Skipping character pack ${id}: rig id clashes: ${clashes.map((rig) => rig.id).join(', ')}`);
    return false;
  });
}

/** Register a whole pack only after its metadata and drawing modules agree. */
export function registerPack(manifest: unknown, characters: unknown, packId?: string): PackRig[] | null {
  try {
    const rigs = validatePackManifest(manifest, packId);
    if (!Array.isArray(characters) || characters.length !== rigs.length) throw new Error('expected one Character per manifest entry');
    const seen = new Set<string>();
    const accepted: Character[] = [];
    for (const rig of rigs) {
      if (seen.has(rig.id) || Object.prototype.hasOwnProperty.call(registry, rig.id)) throw new Error(`rig id clashes: ${rig.id}`);
      seen.add(rig.id);
      const matches = characters.filter((c: unknown) => record(c) && c.id === rig.id);
      const c: unknown = matches[0];
      if (matches.length !== 1 || !record(c) || c.name !== rig.name || c.owner !== rig.owner
        || !sameIds(c.palettes, rig.palettes) || !sameIds(c.poses, rig.poses) || !sameIds(c.moves, rig.moves)
        || !record(c.parts) || Object.keys(c.parts).length !== Object.keys(rig.parts).length
        || !Object.entries(rig.parts).every(([part, options]) => sameIds((c.parts as Record<string, unknown>)[part], options))
        || !record(c.expressions) || !Object.keys(c.expressions).length || new Set(rig.expressions).size !== Object.keys(c.expressions).length
        || !Object.keys(c.expressions).every((id) => rig.expressions.includes(id)) || typeof c.draw !== 'function') {
        throw new Error(`Character disagrees with manifest: ${rig.id}`);
      }
      accepted.push(c as unknown as Character);
    }
    for (const c of accepted) registry[c.id] = c;
    for (const rig of rigs) packRigs[rig.id] = rig;
    return rigs;
  } catch (error) {
    console.warn(`Skipping character pack${packId === undefined ? '' : ` ${packId}`}: ${error instanceof Error ? error.message : String(error)}`);
    return null;
  }
}

/** The registered rig, or the default when this build cannot draw its id. */
export function characterOf(id: string | undefined): Character {
  return id !== undefined && Object.prototype.hasOwnProperty.call(CHARACTERS, id) ? CHARACTERS[id] : CHARACTERS[DEFAULT_CHARACTER];
}

/** Exactly one of the character's options for each of its parts. */
export function isSpecOf(character: Character, spec: unknown): spec is PartSpec {
  if (!spec || typeof spec !== 'object' || Array.isArray(spec)) return false;
  const v = spec as Record<string, unknown>, parts = Object.keys(character.parts);
  return Object.keys(v).length === parts.length
    && parts.every((part) => typeof v[part] === 'string' && character.parts[part].includes(v[part] as string));
}

/** One character's part in a shot at scene-local time `t`, as drawCharacter takes it. */
export type Performer = CharacterState & { character?: string };

export function performer(shot: Shot, t: number): Performer {
  const { character, palette, pose, expression, move, custom } = shot;
  return { character, palette, pose, expression, move, t, custom };
}

/**
 * Draws one performer with its feet centred on (cx, by), `h` tall: the default rig when `character` is absent
 * or unknown. A palette or custom spec the character does not have is ignored, so data meant for
 * another rig can never break a frame.
 */
export function drawCharacter(ctx: Ctx, cx: number, by: number, h: number, state: Performer, env: MotionEnv, marker = true): void {
  const { character: id, palette, custom, ...rest } = state;
  const character = characterOf(id);
  character.draw(ctx, cx, by, h, {
    ...rest,
    palette: palette !== undefined && character.palettes.includes(palette) ? palette : undefined,
    custom: isSpecOf(character, custom) ? custom : undefined,
  }, env);
  if (marker) drawMissingCharacter(ctx, cx, by, h, state, env);
}

/**
 * The marker under a performer whose rig this build cannot draw (the default rig stands in), painted in
 * frame coordinates after any performer mirror or squash is restored. It follows the stand-in's body as
 * its own move carries it, `face` being the x scale that body was drawn with (negative when mirrored),
 * and stays inside a W x H `frame` when given one.
 */
export function drawMissingCharacter(
  ctx: Ctx, cx: number, by: number, h: number, state: Performer, env: MotionEnv, face = 1, frame?: { W: number; H: number },
): void {
  const id = state.character;
  if (id === undefined || Object.prototype.hasOwnProperty.call(CHARACTERS, id)) return;
  const size = Math.max(10, Math.round(h / 18)), half = size * 4.5;
  let x = cx + face * rigShift(state, h), lift = 0;
  if (frame) {
    x = clamp(x, half, frame.W - half);
    lift = Math.max(0, by + size * 2 - frame.H);
  }
  ctx.save();
  ctx.fillStyle = '#111214';
  ctx.fillRect(x - half, by + size / 2 - lift, size * 9, size * 1.5);
  text(ctx, 'Missing character', x, by + size * 1.6 - lift, size, '#ffffff', env.fontFamily ?? 'Inter', 600, 'center');
  ctx.restore();
}
