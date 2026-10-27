import type { BackdropId, DiagramId, ExpressionId, Interaction, MoveId, PoseId, ShotMode } from "motion-core";

// Display labels for the shot controls.
// An id without a display name shows capitalised.
// Lens uses the shared expression, pose and move names below.
export const EXPRESSION_NAMES: Record<ExpressionId, string> & Record<string, string> = {
  neutral: "Neutral", happy: "Happy", excited: "Excited", surprised: "Surprised", curious: "Curious",
  focused: "Focused", sleepy: "Sleepy", idea: "Idea", confused: "Confused", determined: "Determined", proud: "Proud",
};
export const POSE_NAMES: Record<PoseId, string> & Record<string, string> = {
  rest: "Rest", point: "Point", hold: "Hold", cheer: "Cheer", shrug: "Shrug", think: "Think", wave: "Wave",
  hips: "Hands on hips", flex: "Flex",
};
export const MOVE_NAMES: Record<MoveId, string> & Record<string, string> = {
  idle: "Idle", enter: "Hop in", hop: "Hop", walk: "Walk", nod: "Nod", think: "Ponder", wave: "Wave", celebrate: "Celebrate",
  sway: "Sway",
};
export const BACKDROP_NAMES: Record<BackdropId, string> = {
  chaos: "Chaos", work: "Workshop", route: "Route", proof: "Proof", result: "Result", black: "Black", vision: "Vision",
};
export const DIAGRAM_NAMES: Record<DiagramId, string> = {
  scatter: "Tools scatter", blank: "Empty boxes", brief: "Brief pops in", route: "Path connects", checks: "Checks tick", glow: "Result lights up",
  none: "No diagram",
  // Independent diagram presets.
  grid: "Grid", chain: "Chain", radial: "Radial", tree: "Tree", list: "List",
};
export const MODE_NAMES: Record<ShotMode, string> = { footage: "Footage", animation: "Animation", cameo: "Cameo" };
export const INTERACTION_NAMES: Record<Interaction, string> = {
  none: "Side by side", face: "Face each other", fistbump: "Fist bump", highfive: "High five", point: "Point",
};

export function nameOf(names: Record<string, string>, id: string): string {
  return Object.prototype.hasOwnProperty.call(names, id) ? names[id] : id.charAt(0).toUpperCase() + id.slice(1);
}
