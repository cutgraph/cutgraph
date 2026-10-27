// The bridge from a project document's scene `shot` (every field optional, ids unchecked) to the fully
// specified Shot the renderer draws, plus the scene-local timing rule. The browser preview and the Node
// renderer both go through here, so an animated scene looks the same in both.

import { BUBBLES, drawBubble } from './acting.ts';
import { characterOf, drawCharacter, performer } from './characters.ts';
import { atScale, clamp, ease } from './draw.ts';
import { INTERACTIONS, drawDuoShot } from './duo.ts';
import { ENSEMBLE_MAX, drawEnsembleShot } from './ensemble.ts';
import { ACCENT, BACKDROPS, DIAGRAMS, EYEBROW, designScale, drawShot } from './scene.ts';
import { beginCamera, drawLayout, drawStageLabels, endCamera, staged, stagingOf, type StoredStaging } from './staging.ts';
import type {
  BackdropId, BubbleId, Costar, Ctx, DiagramId, Interaction, MotionEnv, PartSpec, Shot, ShotTiming,
} from './types.ts';

export type ShotMode = 'footage' | 'animation' | 'cameo';
export const SHOT_MODES: readonly ShotMode[] = ['footage', 'animation', 'cameo'];

/**
 * A scene's shot as the project document stores it. `cueWord` is a flat transcript wordIdx. The document
 * names a character profile; the host resolves it first, so here `character` is the registry id of the
 * profile's rig (the default rig when absent or unknown) and `palette` the profile's colour variant.
 */
export interface ProjectShot extends StoredStaging {
  mode?: string;
  character?: string;
  palette?: string;
  pose?: string;
  expression?: string;
  move?: string;
  backdrop?: string;
  diagram?: string;
  headline?: string;
  /** The line above the headline: the shot's own, else its workspace brand's (the host fills that in); empty hides it. */
  eyebrow?: string;
  /** Animation only: true draws no headline or eyebrow on the stage, a duo or an ensemble; a
   * staged layout (`screen`, `title`, `signoff`) ignores it. */
  hideHeadline?: boolean;
  /** The eyebrow's colour, the workspace brand's accent (#rrggbb), filled in by the host. */
  accent?: string;
  cueWord?: number;
  /** A second character (animation only). Like `character`, the host resolves its profile first. */
  costar?: ProjectCostar | null;
  interaction?: string;
  bubble?: string;
  /** More performers after the lead and the co-star (animation only), each resolved by the host like the co-star. */
  ensemble?: ProjectCostar[] | null;
}

/** A duo shot's co-star as stored, with its profile resolved by the host (plus a custom expression's spec). */
export interface ProjectCostar {
  character?: string;
  palette?: string;
  pose?: string;
  expression?: string;
  move?: string;
  custom?: PartSpec;
  bubble?: string;
}

type Look = Pick<Shot, 'pose' | 'expression' | 'move' | 'backdrop' | 'diagram'>;

// One default look per scene role.
export const ROLE_LOOKS: Readonly<Record<string, Look>> = {
  Hook: { pose: 'shrug', expression: 'confused', move: 'idle', backdrop: 'chaos', diagram: 'scatter' },
  Problem: { pose: 'think', expression: 'focused', move: 'think', backdrop: 'work', diagram: 'blank' },
  Approach: { pose: 'hold', expression: 'idea', move: 'hop', backdrop: 'work', diagram: 'brief' },
  Build: { pose: 'point', expression: 'happy', move: 'enter', backdrop: 'route', diagram: 'route' },
  Example: { pose: 'point', expression: 'curious', move: 'walk', backdrop: 'route', diagram: 'route' },
  Check: { pose: 'rest', expression: 'curious', move: 'nod', backdrop: 'proof', diagram: 'checks' },
  Payoff: { pose: 'cheer', expression: 'excited', move: 'celebrate', backdrop: 'result', diagram: 'glow' },
  Wrap: { pose: 'wave', expression: 'happy', move: 'wave', backdrop: 'result', diagram: 'glow' },
  Beat: { pose: 'rest', expression: 'neutral', move: 'idle', backdrop: 'work', diagram: 'blank' },
};

export function shotMode(shot: ProjectShot | null | undefined): ShotMode {
  return SHOT_MODES.includes(shot?.mode as ShotMode) ? (shot!.mode as ShotMode) : 'footage';
}

const pick = <T extends string>(value: string | undefined, allowed: readonly T[], fallback: T): T =>
  allowed.includes(value as T) ? (value as T) : fallback;

const HEX = /^#[0-9a-f]{6}$/i;

// A co-star's default look: at ease and pleased, so it never mirrors the lead's role look.
const COSTAR_LOOK = { pose: 'rest', expression: 'happy', move: 'idle' };

function resolveCostar(s: ProjectCostar): Costar {
  const c = characterOf(s.character), expressions = Object.keys(c.expressions);
  return {
    character: s.character ?? c.id,
    palette: pick(s.palette, c.palettes, c.palettes[0]),
    pose: pick(s.pose, c.poses, pick(COSTAR_LOOK.pose, c.poses, c.poses[0])),
    expression: s.expression || pick(COSTAR_LOOK.expression, expressions, expressions[0]),
    move: pick(s.move, c.moves, pick(COSTAR_LOOK.move, c.moves, c.moves[0])),
    ...(s.custom && { custom: s.custom }),
    ...bubbleOf(s),
  };
}

const bubbleOf = (s: { bubble?: string }): Pick<Shot, 'bubble'> =>
  BUBBLES.includes(s.bubble as BubbleId) ? { bubble: s.bubble as BubbleId } : {};

function ensembleOf(s: ProjectShot): Pick<Shot, 'ensemble'> {
  const members = Array.isArray(s.ensemble) ? s.ensemble.filter((m) => m && typeof m === 'object').slice(0, ENSEMBLE_MAX) : [];
  return members.length ? { ensemble: members.map(resolveCostar) } : {};
}

/**
 * Fill a stored shot out to a drawable Shot for its character: unknown or missing ids fall back to the
 * role's look (or, where the character has no such id, its first one) and an empty headline falls back
 * to the scene title. An expression that is not built in is kept as-is so a custom expression
 * (resolved by the host into `custom`) survives. A co-star is filled out the same way against its own
 * rig (from a relaxed look rather than the role's) and brings its interaction, `none` by default, and so
 * is each ensemble member (at most ENSEMBLE_MAX). A bubble it can't draw is dropped. The eyebrow is ours
 * (EYEBROW, in ACCENT) unless the host names another, or an empty one to hide it. `hideHeadline`
 * rides through unchanged: true or absent, never a bare false.
 */
export function resolveShot(shot: ProjectShot | null | undefined, role: string, title: string): Shot {
  const s = shot ?? {};
  const look = ROLE_LOOKS[role] ?? ROLE_LOOKS.Beat;
  const performerLook = s.layout === 'signoff' ? ROLE_LOOKS.Beat : look;
  const c = characterOf(s.character), expressions = Object.keys(c.expressions);
  const resolved: Shot = {
    character: s.character ?? c.id,
    palette: pick(s.palette, c.palettes, c.palettes[0]),
    pose: pick(s.pose, c.poses, pick(performerLook.pose, c.poses, c.poses[0])),
    expression: s.expression || pick(performerLook.expression, expressions, expressions[0]),
    move: pick(s.move, c.moves, pick(performerLook.move, c.moves, c.moves[0])),
    backdrop: pick<BackdropId>(s.backdrop, BACKDROPS, look.backdrop),
    diagram: pick<DiagramId>(s.diagram, DIAGRAMS, look.diagram),
    headline: s.headline?.trim() ? s.headline : title,
    eyebrow: typeof s.eyebrow === 'string' ? s.eyebrow : EYEBROW,
    accent: s.accent && HEX.test(s.accent) ? s.accent : ACCENT,
    ...(s.hideHeadline === true && { hideHeadline: true }),
    ...bubbleOf(s),
    ...ensembleOf(s),
    ...stagingOf(s),
  };
  if (!s.costar || typeof s.costar !== 'object') return resolved;
  return { ...resolved, costar: resolveCostar(s.costar), interaction: pick<Interaction>(s.interaction, INTERACTIONS, 'none') };
}

/**
 * Scene-local timing on the project timeline. `lt` for frame f is `(f - sceneStartF) / fps`; the
 * headline lands with the first kept word and the diagram with the cue word (the first kept word
 * when the shot names none). A cameo springs in on the cue word the shot names (`inT`), or as the
 * scene starts. Given the scene's length, a push-in lands on its end (`endT`).
 */
export function shotTiming(sceneStartF: number, firstWordF: number | null, cueWordF: number | null, fps: number, durF?: number): ShotTiming {
  const firstT = firstWordF === null ? 0.4 : Math.max(0, (firstWordF - sceneStartF) / fps);
  const cueT = cueWordF === null ? firstT : Math.max(0, (cueWordF - sceneStartF) / fps);
  return { firstT, cueT, ...(cueWordF !== null && { inT: cueT }), ...(durF !== undefined && { endT: durF / fps }) };
}

/** Seconds the cameo Character takes to spring in from below the frame. */
export const CAMEO_IN = 0.5;

/**
 * One frame of a scene's picture at scene-local time `lt`, drawn over whatever the host already
 * painted. `animation` paints the full, opaque frame through its camera move (staging.ts): a staging
 * layout when the shot names one (a composed card: its co-star, ensemble and bubbles are not drawn), else
 * an ensemble when the shot has one, else a duo when it has a co-star, with its stage labels on top (a
 * headline over the co-star, badges under the ensemble). `cameo` paints only the shot's
 * character (the default rig by default; a co-star or an ensemble is not drawn) and its bubble, springing into
 * the lower corner at `timing.inT` (nothing before it) and then holding there through its move, and
 * leaves every other pixel untouched so footage (or a transparent export) shows through. `footage`
 * paints nothing. A frame larger than its 1080-class design draws the design scaled up (`designScale`).
 * The preview and the Node renderer both call this.
 */
export function drawSceneFrame(
  ctx: Ctx, mode: ShotMode, shot: Shot, lt: number, timing: ShotTiming, W: number, H: number, env: MotionEnv,
): void {
  const k = designScale(W, H);
  atScale(ctx, k, () => drawDesignFrame(ctx, mode, shot, lt, timing, W / k, H / k, env));
}

function drawDesignFrame(
  ctx: Ctx, mode: ShotMode, shot: Shot, lt: number, timing: ShotTiming, W: number, H: number, env: MotionEnv,
): void {
  if (mode === 'animation') {
    const camera = beginCamera(ctx, shot, lt, timing, W, H);
    try {
      if (staged(shot)) drawLayout(ctx, shot, lt, W, H, env);
      else {
        if (shot.ensemble?.length) drawEnsembleShot(ctx, shot, lt, timing, W, H, env);
        else if (shot.costar) drawDuoShot(ctx, shot, lt, timing, W, H, env);
        else drawShot(ctx, shot, lt, timing, W, H, env);
        drawStageLabels(ctx, shot, lt, timing, W, H, env);
      }
    } finally {
      endCamera(ctx, camera);
    }
  } else if (mode === 'cameo') {
    const u = lt - (timing.inT ?? 0);
    if (u < 0) return;
    const land = W > H, h = land ? H * 0.34 : H * 0.2, cx = land ? W * 0.86 : W * 0.78;
    const by = (land ? H * 0.95 : H * 0.74) + (1 - ease.back(clamp(u / CAMEO_IN))) * h * 1.2;
    drawCharacter(ctx, cx, by, h, performer(shot, u), env);
    if (shot.bubble) drawBubble(ctx, shot.bubble, { x: cx, by, h }, characterOf(shot.character), Math.min(lt - (timing.cueT ?? 0), u - CAMEO_IN), W, H, env);
  }
}
