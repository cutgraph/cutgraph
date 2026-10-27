// The Story view's derived model: which words each scene owns, which words are cut, and how long each
// scene runs on the timeline. Pure functions of (project, transcript words) so every region — Script,
// arc, Board, Source, Player — agrees on the same numbers.

import { shotMode } from "motion-core";
import type { TranscriptWord } from "../../api";
import type { Project, Scene } from "../../types/project";
import { coveredSrcRuns, resolveTimed, sec_to_frame, srcToTimeline, wordSrcFrame } from "../../transcript/cutmap";
import { castShot, characterFor, expressionName, type Cast } from "../library/cast";
import { MOVE_NAMES, nameOf, POSE_NAMES } from "./shot/names";

export interface SceneSpan {
  scene: Scene;
  /** Position in story (list) order. */
  index: number;
  /** Flat wordIdx range [startWord, endWord). */
  startWord: number;
  endWord: number;
  /** Source frames of the scene's material: first word start → next scene's first word start. */
  srcStartF: number;
  srcEndF: number;
  /** Timeline frames still covered by speech clips inside [srcStartF, srcEndF). */
  durF: number;
  /** Timeline frame of the first kept frame, or null when the whole scene is cut. */
  tlStartF: number | null;
}

export function speechMedia(project: Project): string | null {
  return project.tracks.find((t) => t.kind === "speech")?.clips?.[0]?.media.source ?? null;
}

// A word is cut when its (resolved-timed) source frame is covered by no speech clip — the same rule
// the transcript uses, so all views strike the same words.
export function cutWordSet(project: Project, media: string | null, words: TranscriptWord[]): Set<number> {
  const cut = new Set<number>();
  if (!media) return cut;
  const eff = resolveTimed(words);
  words.forEach((_w, i) => {
    const t = eff[i];
    if (t >= 0 && srcToTimeline(project, media, wordSrcFrame(words[t], project.fps)) === null) cut.add(i);
  });
  return cut;
}

// Scenes are listed in story order; each owns the words up to the next-larger startWord.
export function sceneSpans(project: Project, media: string | null, words: TranscriptWord[]): SceneSpan[] {
  const scenes = project.scenes ?? [];
  if (!scenes.length || !words.length) return [];
  const fps = project.fps;
  const eff = resolveTimed(words);
  const starts = [...new Set(scenes.map((s) => s.startWord))].sort((a, b) => a - b);
  // The last scene runs to the end of the speech media so trailing room tone still belongs to a scene.
  const lastWordEnd = words.reduce((m, w) => (Number.isFinite(w.end) ? Math.max(m, w.end) : m), 0);
  const mediaEndF = (project.tracks.find((t) => t.kind === "speech")?.clips ?? [])
    .filter((c) => c.media.source === media)
    .reduce((m, c) => Math.max(m, c.srcOutF), sec_to_frame(lastWordEnd, fps));
  const srcAt = (idx: number): number => {
    if (idx >= words.length) return mediaEndF;
    const t = eff[idx];
    return t < 0 ? 0 : wordSrcFrame(words[t], fps);
  };
  return scenes.map((scene, index) => {
    const startWord = Math.min(Math.max(scene.startWord, 0), words.length);
    const endWord = Math.min(starts.find((s) => s > scene.startWord) ?? words.length, words.length);
    const srcStartF = startWord === 0 ? 0 : srcAt(startWord);
    const srcEndF = srcAt(endWord);
    const runs = media ? coveredSrcRuns(project, media, srcStartF, srcEndF) : [];
    const durF = runs.reduce((acc, r) => acc + (r.b - r.a), 0);
    const first = runs[0];
    const tlStartF = first ? first.clip.tlStartF + (first.a - first.clip.srcInF) : null;
    return { scene, index, startWord, endWord, srcStartF, srcEndF, durF, tlStartF };
  });
}

export function sceneOfWord(spans: SceneSpan[], wordIdx: number): SceneSpan | null {
  return spans.find((s) => wordIdx >= s.startWord && wordIdx < s.endWord) ?? null;
}

export interface SceneRecipe {
  actor: string;
  pose: string;
  expression: string;
  move: string;
  headline: string;
}

// What an animated or cameo scene's shot performs: who, its pose, expression and move, and the
// headline it lands on. Null for a footage scene.
export function sceneRecipe(scene: Scene, cast: Cast): SceneRecipe | null {
  if (shotMode(scene.shot) === "footage") return null;
  const profile = characterFor(cast, scene.shot?.character);
  const shot = castShot(scene.shot, scene.role, scene.title, cast);
  return {
    actor: profile.name,
    pose: nameOf(POSE_NAMES, shot.pose),
    expression: expressionName(shot.expression, profile),
    move: nameOf(MOVE_NAMES, shot.move),
    headline: shot.headline,
  };
}
