// Deterministic shot checks: low energy on a Hook or Payoff, a move that overrides the chosen
// arm pose, and a headline too long for the frame.
import { bare, type Shot as MotionShot, type ShotMode } from "motion-core";
import type { CharacterProfile } from "../../api";
import { expressionName } from "../../library/cast";
import { MOVE_NAMES, POSE_NAMES, nameOf } from "./names";

export interface Critique {
  ok: boolean;
  notes: string[];
}

const LOW_ENERGY = new Set(["sleepy", "focused"]);
const HIGH_ENERGY_ROLES = new Set(["Hook", "Payoff"]);
// These moves override the arm pose during drawing.
const ARM_MOVES: Record<string, string> = { wave: "wave", celebrate: "cheer", think: "think", sway: "flex" };
export const HEADLINE_MAX = 42;

export function critic({ mode, layout, role, shot, cueWord, profile }: {
  mode: ShotMode;
  /** The stored staging layout; a character performs only on the `stage`. */
  layout: string | undefined;
  role: string;
  /** The shot as drawn: castShot's resolution, role look included. */
  shot: MotionShot;
  /** The word the shot lands on, or null when the scene has none left. */
  cueWord: string | null;
  profile: CharacterProfile;
}): Critique | null {
  if (mode === "footage") return null;
  const performs = mode === "cameo" || (layout ?? "stage") === "stage";
  const notes: string[] = [];
  if (performs && HIGH_ENERGY_ROLES.has(role) && LOW_ENERGY.has(shot.expression)) {
    notes.push(`${expressionName(shot.expression, profile)} reads low-energy for a ${role.toLowerCase()} scene.`);
  }
  const forced = ARM_MOVES[shot.move];
  if (performs && forced && shot.pose !== forced) {
    notes.push(`“${nameOf(MOVE_NAMES, shot.move)}” sets the arms, so the ${nameOf(POSE_NAMES, shot.pose).toLowerCase()} pose is overridden.`);
  }
  if (mode === "animation" && shot.headline.length > HEADLINE_MAX) {
    notes.push(`The headline is long for this scene; aim for under ${HEADLINE_MAX} characters.`);
  }
  if (notes.length) return { ok: false, notes };
  const word = cueWord === null ? "" : bare(cueWord);
  const line = [
    word && `Lands on “${word}”`,
    mode === "animation" && "text inside title-safe",
    performs && `${profile.name} at whole-pixel scale`,
  ].filter(Boolean).join(", ");
  return { ok: true, notes: [`${line.charAt(0).toUpperCase()}${line.slice(1)}.`] };
}
