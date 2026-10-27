// Acting in the Shot view: a bubble for every performer (a thought, speech, a "!", a "?" or neutral paired "!!", popping in on
// the "Lands on" word) and the ensemble, up to three more characters from the video's cast after the
// lead and the co-star, each with its own character, expression, pose, move and bubble. Every pick is
// one undo step.
import {
  DEFAULT_CHARACTER, characterOf, BUBBLES, ENSEMBLE_MAX, drawBubble, drawCharacter, shotMode,
  type BubbleId, type Ctx, type MotionEnv, type Performer, type Shot as MotionShot,
} from "motion-core";
import type { Costar, Scene, Shot } from "../../../types/project";
import { characterFor, expressionName, isBuiltInExpression, performerOf, rigOf, type Cast } from "../../library/cast";
import { EXPRESSION_NAMES, MOVE_NAMES, POSE_NAMES, nameOf } from "./names";
import { CHARACTER_REST_T, paintCharacterTile } from "./paint";
import { Strip, type TileOption, type TilePaint } from "./Picker";
import type { ShotPatch } from "./specs";
import "./acting.css";

const NONE = "none";
export const BUBBLE_NAMES: Record<BubbleId | typeof NONE, string> = {
  none: "None", think: "Think", say: "Say", exclaim: "Exclaim", question: "Question", exclaims: "!!",
};
const BUBBLE_OPTIONS: TileOption[] = [NONE, ...BUBBLES].map((id) => ({ id, label: BUBBLE_NAMES[id as BubbleId] }));
const TILE_BG = "#161b21";
// A bubble tile rests on its bubble fully in (the thought with all three dots) and plays it while hovered.
const BUBBLE_REST_U = 1.2, BUBBLE_LOOP = 2.4;

type Look = { pose?: string; expression?: string; move?: string };

const options = (ids: readonly string[], names: Record<string, string>): TileOption[] => ids.map((id) => ({ id, label: nameOf(names, id) }));

export function expressionOptions(cast: Cast, id: string): TileOption[] {
  const profile = characterFor(cast, id);
  return [...options(Object.keys(rigOf(profile).expressions), EXPRESSION_NAMES), ...profile.expressions.filter((e) => !e.draft).map((e) => ({ id: e.id, label: e.name }))];
}

export function wears(cast: Cast, id: string, expression: string | undefined): boolean {
  const profile = characterFor(cast, id);
  return !expression || isBuiltInExpression(expression, rigOf(profile)) || profile.expressions.some((e) => e.id === expression);
}

/** A performer standing low in the tile with its bubble over its head; `t` null is the bubble at rest. */
export function paintBubbleTile(ctx: Ctx, W: number, H: number, performer: Performer, bubble: BubbleId | null, t: number | null, env: MotionEnv): void {
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.fillStyle = TILE_BG;
  ctx.fillRect(0, 0, W, H);
  const spot = { x: W * 0.3, by: H * 0.96, h: H * 0.5 };
  drawCharacter(ctx, spot.x, spot.by, spot.h, performer, env);
  // Sized by the default rig's pixel grid so a bubble reads over any rig at tile size.
  if (bubble) drawBubble(ctx, bubble, spot, characterOf(DEFAULT_CHARACTER), t === null ? BUBBLE_REST_U : t % BUBBLE_LOOP, W, H, env);
}

// The document keeps at most ENSEMBLE_MAX members; the generated type spells that as a tuple.
const ensembleOf = (members: Costar[]): Shot["ensemble"] => members.slice(0, ENSEMBLE_MAX) as Shot["ensemble"];

const withBubble = <T extends { bubble?: BubbleId }>(look: T, id: string): T => {
  const { bubble: _gone, ...rest } = look;
  return (id === NONE ? rest : { ...rest, bubble: id as BubbleId }) as T;
};

export default function ActingSet({ scene, shot, cast, pick, env }: {
  scene: Scene;
  /** The scene's shot resolved for drawing (castShot): each performer's look as the player draws it. */
  shot: MotionShot;
  cast: Cast;
  pick: (patch: ShotPatch, label: string) => void;
  env: MotionEnv;
}) {
  const mode = shotMode(scene.shot);
  if (mode === "footage") return null;
  const stored = scene.shot ?? {};
  const lead = characterFor(cast, stored.character);
  const storedCostar = mode === "animation" ? stored.costar : undefined;
  const costarProfile = storedCostar && characterFor(cast, storedCostar.character);
  const members: Costar[] = mode === "animation" ? [...(stored.ensemble ?? [])] : [];
  const lookOf = (id: string, p: Look | undefined) =>
    `${id}|${p?.pose}|${p?.expression}|${p?.move}|${JSON.stringify(characterFor(cast, id).expressions)}`;
  const castLook = JSON.stringify(cast.characters.map((c) => [c.id, c.rig, c.palette]));

  const bubblePaint = (id: string, p: Look | undefined) => (option: string): TilePaint =>
    (ctx, w, h, t) => paintBubbleTile(ctx, w, h, performerOf(characterFor(cast, id), { pose: p?.pose, expression: p?.expression, move: p?.move, t: CHARACTER_REST_T }),
      option === NONE ? null : (option as BubbleId), t, env);
  const lookPaint = (id: string, p: Look, key: "expression" | "pose" | "move") => (option: string): TilePaint => (ctx, w, h, t) =>
    paintCharacterTile(ctx, w, h, performerOf(characterFor(cast, id), { pose: p.pose, expression: p.expression, move: p.move, [key]: option, t: t ?? CHARACTER_REST_T }), env);
  const characterPaint = (id: string): TilePaint => (ctx, w, h, t) =>
    paintCharacterTile(ctx, w, h, performerOf(characterFor(cast, id), { pose: "rest", expression: "happy", move: "idle", t: t ?? CHARACTER_REST_T }), env);

  const setMembers = (next: Costar[], label: string): void => pick({ ensemble: next.length ? ensembleOf(next) : null }, label);
  const onStage = [lead.id, ...(costarProfile ? [costarProfile.id] : []), ...members.map((m) => characterFor(cast, m.character).id)];
  const newcomer = cast.characters.find((c) => !onStage.includes(c.id)) ?? cast.characters.find((c) => c.id !== lead.id) ?? lead;
  const pickMember = (i: number, patch: Partial<Costar>, label: string): void =>
    setMembers(members.map((m, j) => (j === i ? { ...m, ...patch } : m)), label);
  const castMember = (i: number, id: string): void => {
    const { expression, ...rest } = members[i];
    setMembers(members.map((m, j) => (j === i ? (wears(cast, id, expression) ? { ...m, character: id } : { ...rest, character: id }) : m)),
      `Ensemble ${i + 1}: ${characterFor(cast, id).name}`);
  };

  return (
    <div className="acting-set" data-slot="acting">
      <Strip title="Bubble" options={BUBBLE_OPTIONS} selected={shot.bubble ?? NONE} look={lookOf(lead.id, shot)} paint={bubblePaint(lead.id, shot)}
        action={<span className="acting-hint">Pops in on the “Lands on” word</span>}
        onPick={(id) => pick({ bubble: id === NONE ? null : (id as BubbleId) }, `Bubble: ${BUBBLE_NAMES[id as BubbleId]}`)} />
      {storedCostar && costarProfile && (
        <Strip title="Co-star bubble" options={BUBBLE_OPTIONS} selected={storedCostar.bubble ?? NONE} look={lookOf(costarProfile.id, shot.costar)}
          paint={bubblePaint(costarProfile.id, shot.costar)}
          onPick={(id) => pick({ costar: withBubble(storedCostar, id) }, `Co-star bubble: ${BUBBLE_NAMES[id as BubbleId]}`)} />
      )}
      {members.map((member, i) => {
        const profile = characterFor(cast, member.character), rig = rigOf(profile), look = shot.ensemble?.[i] ?? member;
        const n = i + 1, key = lookOf(profile.id, look);
        return (
          <div key={i} className="costar-set ensemble-set" data-slot={`ensemble-${n}`}>
            <Strip title={`Ensemble ${n}`} options={cast.characters.map((c) => ({ id: c.id, label: c.name }))} selected={profile.id}
              action={
                <span className="rhead-actions">
                  {!profile.approved && <span className="shot-draft" title={`${profile.name}: a draft look until approved in Library → Cast.`}>Draft</span>}
                  <button type="button" className="ghost costar-remove" onClick={() => setMembers(members.filter((_, j) => j !== i), `Remove ${profile.name} from the ensemble`)}>
                    Remove
                  </button>
                </span>
              }
              look={castLook} paint={characterPaint} onPick={(id) => castMember(i, id)} />
            <Strip title={`Ensemble ${n} expression`} options={expressionOptions(cast, profile.id)} selected={look.expression ?? ""} look={key}
              paint={lookPaint(profile.id, look, "expression")}
              onPick={(id) => pickMember(i, { expression: id }, `Ensemble ${n} expression: ${expressionName(id, profile)}`)} />
            <Strip title={`Ensemble ${n} pose`} options={options(rig.poses, POSE_NAMES)} selected={look.pose ?? ""} look={key}
              paint={lookPaint(profile.id, look, "pose")}
              onPick={(id) => pickMember(i, { pose: id }, `Ensemble ${n} pose: ${nameOf(POSE_NAMES, id)}`)} />
            <Strip title={`Ensemble ${n} move`} options={options(rig.moves, MOVE_NAMES)} selected={look.move ?? ""} look={key}
              paint={lookPaint(profile.id, look, "move")}
              onPick={(id) => pickMember(i, { move: id }, `Ensemble ${n} move: ${nameOf(MOVE_NAMES, id)}`)} />
            <Strip title={`Ensemble ${n} bubble`} options={BUBBLE_OPTIONS} selected={member.bubble ?? NONE} look={key} paint={bubblePaint(profile.id, look)}
              onPick={(id) => setMembers(members.map((m, j) => (j === i ? withBubble(m, id) : m)), `Ensemble ${n} bubble: ${BUBBLE_NAMES[id as BubbleId]}`)} />
          </div>
        );
      })}
      {mode === "animation" && (
        <div className="rrow ensemble-add">
          {members.length < ENSEMBLE_MAX && (
            <button type="button" className="costar-add" title="More characters in the row, after the lead and the co-star: up to three."
              onClick={() => setMembers([...members, { character: newcomer.id }], `Add to ensemble: ${newcomer.name}`)}>
              + Add to ensemble
            </button>
          )}
          {members.length > 0 && storedCostar && (stored.interaction ?? "none") !== "none" && (
            <p className="mode-hint">An ensemble stands side by side: the interaction plays only in a duo.</p>
          )}
        </div>
      )}
    </div>
  );
}
