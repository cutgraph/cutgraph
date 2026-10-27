import { shotMode } from "motion-core";
import type { Scene } from "../../../types/project";
import { castShot, characterFor, expressionName, NO_CAST, type Cast } from "../../library/cast";
import { useVideoCast } from "../../library/castStore";
import { openShot } from "../storyStore";
import { MODE_NAMES } from "./names";
import "./shot.css";

/**
 * What an animated or cameo scene's chip reads: the expression its character wears, then the
 * character when the video can cast more than one; a duo reads both expressions, lead first. Null
 * for a footage scene.
 */
export function animatedExpression(scene: Scene, cast: Cast = NO_CAST): string | null {
  const mode = shotMode(scene.shot);
  if (mode === "footage") return null;
  const profile = characterFor(cast, scene.shot?.character);
  const shot = castShot(scene.shot, scene.role, scene.title, cast);
  const expression = expressionName(shot.expression, profile);
  const costar = scene.shot?.costar;
  if (mode === "animation" && costar && shot.costar) return `${expression} + ${expressionName(shot.costar.expression, characterFor(cast, costar.character))}`;
  return cast.characters.length > 1 ? `${expression} · ${profile.name}` : expression;
}

// The Script scene header's way into the Shot view: "✦ Shot" on footage, the character's expression
// once the scene is animated.
export default function ShotChip({ scene }: { scene: Scene }) {
  const { cast } = useVideoCast(false);
  const expression = animatedExpression(scene, cast);
  return (
    <button
      type="button"
      className={`shot-chip${expression ? " on" : ""}`}
      title={expression ? `${MODE_NAMES[shotMode(scene.shot)]} shot · ${expression}` : "Animate this scene"}
      aria-label={`Open the shot for ${scene.title}`}
      onClick={() => openShot(scene.id)}
    >
      ✦ {expression ?? "Shot"}
    </button>
  );
}
